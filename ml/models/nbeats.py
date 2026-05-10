"""NBeatsForecaster: darts NBEATSModel with QuantileRegression likelihood."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ml.models.base import BaseForecaster, ForecastResult
from ml.training.utils import TargetNormalizer

try:
    import pytorch_lightning as pl
    import torch
    from darts import TimeSeries
    from darts.models import NBEATSModel
    from darts.utils.likelihood_models import QuantileRegression

    _DEPS_AVAILABLE = True
except ImportError:
    _DEPS_AVAILABLE = False

# Quantile index for point estimate (median) when quantiles=[0.1, 0.5, 0.9]
_Q50_IDX = 1


class _MetricsCollector(pl.Callback):
    """Collects val_loss per epoch for training metadata."""

    def __init__(self) -> None:
        self.best_val_loss: float = float("inf")
        self.best_epoch: int = 0
        self.epochs_run: int = 0

    def on_validation_epoch_end(self, trainer: Any, pl_module: Any) -> None:
        val_loss = trainer.callback_metrics.get("val_loss")
        if val_loss is None:
            return
        v = float(val_loss.item() if hasattr(val_loss, "item") else val_loss)
        self.epochs_run = trainer.current_epoch + 1
        if v < self.best_val_loss:
            self.best_val_loss = v
            self.best_epoch = self.epochs_run


class _NBeatsOnnxWrapper(torch.nn.Module):
    """Thin ONNX wrapper: past_input → point estimate (median quantile).

    N-BEATS takes (past_target, None, None). None args are Python constants
    folded at trace time, so the ONNX graph has a single input.
    """

    def __init__(self, net: torch.nn.Module) -> None:
        super().__init__()
        self.net = net

    def forward(self, x0: torch.Tensor) -> torch.Tensor:
        out = self.net((x0, None, None))  # (batch, ocl, n_targets, n_quantiles)
        return out[:, 0, 0, _Q50_IDX : _Q50_IDX + 1]  # (batch, 1)


class NBeatsForecaster(BaseForecaster):
    """N-BEATS forecaster using darts with QuantileRegression likelihood."""

    name = "nbeats"

    def __init__(self, cfg: Any = None) -> None:
        self._cfg = cfg
        self._darts_model: Any = None
        self._normalizer: TargetNormalizer | None = None
        self._metrics: _MetricsCollector | None = None
        self._n_past_total: int | None = None
        self._n_future_total: int | None = None
        self._icl: int = 30
        self._ocl: int = 1
        self._version: str = "nbeats-unknown"

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def _p(self, *keys: str, default: Any = None) -> Any:
        obj = self._cfg
        for k in keys:
            if obj is None:
                return default
            obj = getattr(obj, k, None)
        return obj if obj is not None else default

    def _get_icl(self) -> int:
        return int(self._p("model", "params", "input_chunk_length", default=30))

    def _get_ocl(self) -> int:
        return int(self._p("model", "params", "output_chunk_length", default=1))

    def _get_n_epochs(self) -> int:
        return int(self._p("model", "params", "n_epochs", default=200))

    def _get_patience(self) -> int:
        return int(self._p("model", "early_stopping", "patience", default=10))

    def _get_min_delta(self) -> float:
        return float(self._p("model", "early_stopping", "min_delta", default=0.0001))

    def _get_val_fraction(self) -> float:
        return float(self._p("training", "val_fraction", default=0.15))

    def _get_model_params(self) -> dict:
        p = self._p("model", "params")
        if p is None:
            return dict(
                input_chunk_length=self._get_icl(),
                output_chunk_length=self._get_ocl(),
                generic_architecture=True,
                num_stacks=2,
                num_blocks=3,
                num_layers=4,
                layer_widths=128,
                batch_size=32,
                n_epochs=self._get_n_epochs(),
                optimizer_kwargs={"lr": 1e-4},
                random_state=42,
            )
        return dict(
            input_chunk_length=int(p.input_chunk_length),
            output_chunk_length=int(p.output_chunk_length),
            generic_architecture=bool(p.generic_architecture),
            num_stacks=int(p.num_stacks),
            num_blocks=int(p.num_blocks),
            num_layers=int(p.num_layers),
            layer_widths=int(p.layer_widths),
            batch_size=int(p.batch_size),
            n_epochs=int(p.n_epochs),
            optimizer_kwargs={"lr": float(p.optimizer_kwargs.lr)},
            random_state=int(p.random_state),
        )

    def _get_trainer_kwargs(self, extra_callbacks: list | None = None) -> dict:
        t = self._p("model", "trainer")
        base = dict(enable_progress_bar=True, callbacks=extra_callbacks or [])
        if t is None:
            base["accelerator"] = "cpu"
            return base
        base["accelerator"] = str(t.accelerator)
        base["precision"] = str(t.precision) if hasattr(t, "precision") else "32"
        if hasattr(t, "devices"):
            base["devices"] = int(t.devices)
        base["enable_progress_bar"] = bool(t.enable_progress_bar)
        return base

    # ------------------------------------------------------------------
    # Data preparation (univariate — target only, no covariates)
    # ------------------------------------------------------------------

    def _build_target_ts(self, history: pd.DataFrame) -> tuple[Any, TargetNormalizer, pd.Series]:
        """Convert history DataFrame to normalized darts TimeSeries."""
        df = history.copy()
        df["ts"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("ts").set_index("ts")
        prices_raw = df["22k"].astype(float)

        full_idx = pd.date_range(
            prices_raw.index.min().normalize(),
            prices_raw.index.max().normalize(),
            freq="D",
            tz="UTC",
        )
        prices_daily = prices_raw.reindex(full_idx, method="ffill")
        normalizer = TargetNormalizer.fit(prices_daily.values)
        norm_arr = normalizer.normalize(prices_daily.values).astype(np.float32)
        target_ts = TimeSeries.from_series(pd.Series(norm_arr, index=full_idx))
        return target_ts, normalizer, prices_daily

    # ------------------------------------------------------------------
    # BaseForecaster interface
    # ------------------------------------------------------------------

    def fit(
        self,
        history: pd.DataFrame,
        macro: pd.DataFrame | None = None,
        extra_callbacks: list | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not _DEPS_AVAILABLE:
            raise ImportError("darts and pytorch-lightning are required for NBeatsForecaster.")
        from pytorch_lightning.callbacks import EarlyStopping

        t0 = time.time()
        icl = self._get_icl()
        ocl = self._get_ocl()

        target_ts, normalizer, prices_daily = self._build_target_ts(history)
        self._normalizer = normalizer
        self._icl = icl
        self._ocl = ocl

        n_total = len(target_ts)
        n_val = max(icl + ocl + 1, int(n_total * self._get_val_fraction()))
        n_val = min(n_val, n_total - icl - 1)
        n_train = n_total - n_val

        train_target = target_ts[:n_train]
        val_target = target_ts[n_train:]

        self._metrics = _MetricsCollector()
        early_stop = EarlyStopping(
            monitor="val_loss",
            patience=self._get_patience(),
            min_delta=self._get_min_delta(),
            mode="min",
        )

        all_callbacks = [self._metrics, early_stop] + (extra_callbacks or [])
        self._darts_model = NBEATSModel(
            **self._get_model_params(),
            likelihood=QuantileRegression(quantiles=[0.1, 0.5, 0.9]),
            pl_trainer_kwargs=self._get_trainer_kwargs(all_callbacks),
        )

        self._darts_model.fit(
            series=train_target,
            val_series=val_target,
            verbose=True,
        )

        # Derive input shapes from train_sample_shape
        shapes = self._darts_model.model.train_sample_shape
        n_past = int(shapes[0][1])  # target components (always 1 for N-BEATS)
        if shapes[1] is not None:
            n_past += int(shapes[1][1])
        if shapes[2] is not None:
            n_past += int(shapes[2][1])
        self._n_past_total = n_past
        self._n_future_total = int(shapes[3][1]) if shapes[3] is not None else 0

        best_val_loss = self._metrics.best_val_loss
        val_mae_rs = float(best_val_loss * normalizer.std) if best_val_loss < 1e9 else float("nan")
        prices_arr = prices_daily.values
        naive_mae = float(np.mean(np.abs(np.diff(prices_arr[-n_val - 1 :]))))

        return {
            "best_epoch": self._metrics.best_epoch,
            "epochs_run": self._metrics.epochs_run,
            "val_mae": round(val_mae_rs, 2),
            "val_mape": 0.0,
            "naive_mae": round(naive_mae, 2),
            "beats_naive": val_mae_rs < naive_mae,
            "n_train": n_train,
            "n_val": n_val,
            "wall_clock_s": round(time.time() - t0, 1),
            "feature_count": self._n_past_total or 1,
        }

    def predict(self, history: pd.DataFrame, macro: pd.DataFrame | None = None) -> ForecastResult:
        if self._darts_model is None or self._normalizer is None:
            raise RuntimeError("Call fit() before predict().")

        target_ts, _, prices_daily = self._build_target_ts(history)
        pred_ts = self._darts_model.predict(n=1, series=target_ts)

        vals = pred_ts.all_values()  # (1, n_components, n_quantiles) or similar
        flat = vals.reshape(-1)
        q10 = float(self._normalizer.denormalize(np.array([flat[0]]))[0])
        q50 = float(self._normalizer.denormalize(np.array([flat[_Q50_IDX]]))[0])
        q90 = float(self._normalizer.denormalize(np.array([flat[2]]))[0])

        current_price = float(prices_daily.iloc[-1])

        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        target_time = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

        return ForecastResult(
            point=round(q50 - current_price, 2),
            lower_q10=round(q10 - current_price, 2),
            upper_q90=round(q90 - current_price, 2),
            target_time=target_time.isoformat(),
            feature_count=self._n_past_total or 1,
            model_version=self._version,
        )

    def predict_batch_native(self, x0: np.ndarray, x1: np.ndarray | None = None) -> np.ndarray:
        """Forward pass via native PyTorch. Returns (batch,) median predictions.

        x1 is accepted but unused — N-BEATS has no future covariates.
        """
        if self._darts_model is None:
            raise RuntimeError("Call fit() first.")
        net = self._darts_model.model
        net.eval()
        with torch.no_grad():
            t0 = torch.from_numpy(x0.astype(np.float32))
            out = net((t0, None, None))  # (batch, ocl, n_targets, n_quantiles)
            return out[:, 0, 0, _Q50_IDX].cpu().numpy()

    def predict_batch_onnx(
        self, onnx_path: Path, x0: np.ndarray, x1: np.ndarray | None = None
    ) -> np.ndarray:
        """Forward pass via ONNX. Returns (batch,) median predictions."""
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        session = ort.InferenceSession(
            str(onnx_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        result = session.run(
            ["point_estimate"],
            {"past_input": x0.astype(np.float32)},
        )
        return result[0][:, 0]

    def export_onnx(self, path: Path) -> None:
        if self._darts_model is None:
            raise RuntimeError("Call fit() first.")
        if self._n_past_total is None:
            raise RuntimeError("Shape metadata missing — fit() may not have completed.")

        path.parent.mkdir(parents=True, exist_ok=True)

        net = self._darts_model.model
        net.eval()
        wrapper = _NBeatsOnnxWrapper(net)

        dummy_x0 = torch.zeros(1, self._icl, self._n_past_total, dtype=torch.float32)

        with torch.no_grad():
            torch.onnx.export(
                wrapper,
                dummy_x0,
                str(path),
                input_names=["past_input"],
                output_names=["point_estimate"],
                dynamic_axes={"past_input": {0: "batch"}, "point_estimate": {0: "batch"}},
                opset_version=17,
            )

        import onnx

        onnx.checker.check_model(str(path))

    def save_native(self, dir: Path) -> None:
        if self._darts_model is None or self._normalizer is None:
            raise RuntimeError("Call fit() first.")
        dir.mkdir(parents=True, exist_ok=True)
        self._darts_model.save(str(dir / "model.pt"))
        self._normalizer.save(dir / "normalizer.json")
        meta = {
            "n_past_total": self._n_past_total,
            "n_future_total": self._n_future_total,
            "icl": self._icl,
            "ocl": self._ocl,
            "version": self._version,
        }
        (dir / "shape_meta.json").write_text(json.dumps(meta, indent=2))

    @classmethod
    def load_native(cls, dir: Path) -> NBeatsForecaster:
        import functools

        import torch

        # PyTorch 2.6+ weights_only=True blocks darts custom classes.
        _orig_load = torch.load

        @functools.wraps(_orig_load)
        def _load_compat(*args, **kwargs):
            kwargs["weights_only"] = False
            return _orig_load(*args, **kwargs)

        torch.load = _load_compat
        try:
            instance = cls()
            instance._darts_model = NBEATSModel.load(str(dir / "model.pt"))
        finally:
            torch.load = _orig_load

        instance._normalizer = TargetNormalizer.load(dir / "normalizer.json")
        meta = json.loads((dir / "shape_meta.json").read_text())
        instance._n_past_total = meta["n_past_total"]
        instance._n_future_total = meta["n_future_total"]
        instance._icl = meta["icl"]
        instance._ocl = meta["ocl"]
        instance._version = meta.get("version", "nbeats-unknown")
        return instance
