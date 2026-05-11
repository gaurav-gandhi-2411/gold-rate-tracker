"""LightGBMForecaster: tabular gradient-boosting with quantile confidence intervals."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ml.features import (
    ALL_FEATURE_COLS,
    FEATURE_COLS,
    build_feature_matrix,
    get_predict_row,
    get_train_Xy,
)
from ml.models.base import BaseForecaster, ForecastResult

try:
    import lightgbm as lgb

    _LGB_AVAILABLE = True
except ImportError:
    _LGB_AVAILABLE = False


class LightGBMForecaster(BaseForecaster):
    """LightGBM mean + quantile models, delta-based target (next Δ22k)."""

    name = "lightgbm"

    def __init__(self, cfg: Any = None) -> None:
        self._cfg = cfg
        self._booster_mean: Any = None
        self._booster_p10: Any = None
        self._booster_p90: Any = None
        self._feature_cols: list[str] | None = None
        self._version: str = "lgbm-unknown"

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

    def _make_lgbm(self, objective: str, alpha: float | None = None) -> Any:
        if not _LGB_AVAILABLE:
            raise ImportError("lightgbm is required for LightGBMForecaster.")
        p = self._p("model", "params")
        kwargs: dict[str, Any] = dict(
            objective=objective,
            n_estimators=int(p.num_iterations) if p else 500,
            num_leaves=int(p.num_leaves) if p else 31,
            learning_rate=float(p.learning_rate) if p else 0.05,
            feature_fraction=float(p.feature_fraction) if p else 0.9,
            subsample=float(p.bagging_fraction) if p else 0.8,
            subsample_freq=int(p.bagging_freq) if p else 5,
            random_state=42,
            verbose=-1,
        )
        if alpha is not None:
            kwargs["alpha"] = alpha
        return lgb.LGBMRegressor(**kwargs)

    def _early_stopping_rounds(self) -> int:
        p = self._p("model", "params")
        return int(p.early_stopping_round) if p else 50

    # ------------------------------------------------------------------
    # BaseForecaster interface
    # ------------------------------------------------------------------

    def fit(
        self,
        history: pd.DataFrame,
        macro: pd.DataFrame | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not _LGB_AVAILABLE:
            raise ImportError("lightgbm is required for LightGBMForecaster.")
        from ml.regime import REGIME_FEATURE_COLS

        t0 = time.time()

        feat_df = build_feature_matrix(history, macro_df=macro)

        if macro is not None:
            feature_cols: list[str] = list(ALL_FEATURE_COLS)
            if all(c in feat_df.columns for c in REGIME_FEATURE_COLS):
                feature_cols = feature_cols + REGIME_FEATURE_COLS
        else:
            feature_cols = list(FEATURE_COLS)

        X, y = get_train_Xy(feat_df, feature_cols=feature_cols)

        if len(X) < 20:
            raise RuntimeError(f"Too few training rows ({len(X)}); need ≥20")

        n_val = max(10, int(len(X) * 0.15))
        n_train = len(X) - n_val
        X_train, y_train = X.iloc[:n_train], y.iloc[:n_train]
        X_val, y_val = X.iloc[n_train:], y.iloc[n_train:]

        early_rounds = self._early_stopping_rounds()
        callbacks = [
            lgb.early_stopping(early_rounds, verbose=False),
            lgb.log_evaluation(-1),
        ]

        m_mean = self._make_lgbm("regression_l1")
        m_mean.fit(X_train, y_train, eval_set=[(X_val, y_val)], callbacks=callbacks)

        q_alphas = self._p("model", "quantile_alphas") or [0.1, 0.9]
        alpha_p10, alpha_p90 = float(q_alphas[0]), float(q_alphas[1])

        m_p10 = self._make_lgbm("quantile", alpha=alpha_p10)
        m_p10.fit(X_train, y_train, eval_set=[(X_val, y_val)], callbacks=callbacks)

        m_p90 = self._make_lgbm("quantile", alpha=alpha_p90)
        m_p90.fit(X_train, y_train, eval_set=[(X_val, y_val)], callbacks=callbacks)

        self._booster_mean = m_mean.booster_
        self._booster_p10 = m_p10.booster_
        self._booster_p90 = m_p90.booster_
        self._feature_cols = feature_cols

        pred_val = m_mean.predict(X_val)
        val_mae = float(np.mean(np.abs(pred_val - y_val)))
        naive_mae = float(np.mean(np.abs(y_val.values)))  # predict delta=0 baseline

        best_epoch = (
            int(m_mean.best_iteration_) if m_mean.best_iteration_ else int(m_mean.n_estimators)
        )

        return {
            "best_epoch": best_epoch,
            "epochs_run": int(m_mean.n_estimators),
            "val_mae": round(val_mae, 2),
            "val_mape": 0.0,
            "naive_mae": round(naive_mae, 2),
            "beats_naive": val_mae < naive_mae,
            "n_train": n_train,
            "n_val": n_val,
            "wall_clock_s": round(time.time() - t0, 1),
            "feature_count": len(feature_cols),
        }

    def predict(self, history: pd.DataFrame, macro: pd.DataFrame | None = None) -> ForecastResult:
        if self._booster_mean is None or self._feature_cols is None:
            raise RuntimeError("Call fit() or load_native() before predict().")

        feat_df = build_feature_matrix(history, macro_df=macro)
        x_pred, _ = get_predict_row(feat_df, feature_cols=self._feature_cols)
        if x_pred is None:
            raise RuntimeError("Prediction row has NaN features — not enough history.")

        delta_mean = float(self._booster_mean.predict(x_pred)[0])
        delta_p10 = float(self._booster_p10.predict(x_pred)[0])
        delta_p90 = float(self._booster_p90.predict(x_pred)[0])

        delta_p10 = min(delta_p10, delta_mean)
        delta_p90 = max(delta_p90, delta_mean)

        now = datetime.now(UTC)
        target_time = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

        return ForecastResult(
            point=round(delta_mean, 2),
            lower_q10=round(delta_p10, 2),
            upper_q90=round(delta_p90, 2),
            target_time=target_time.isoformat(),
            feature_count=len(self._feature_cols),
            model_version=self._version,
        )

    def export_onnx(self, path: Path) -> None:
        raise NotImplementedError("LightGBM ONNX export is not supported in this pipeline.")

    def save_native(self, dir: Path) -> None:
        if self._booster_mean is None or self._feature_cols is None:
            raise RuntimeError("Call fit() before save_native().")
        dir.mkdir(parents=True, exist_ok=True)
        self._booster_mean.save_model(str(dir / "lgbm-mean.txt"))
        self._booster_p10.save_model(str(dir / "lgbm-p10.txt"))
        self._booster_p90.save_model(str(dir / "lgbm-p90.txt"))
        meta = {"feature_cols": self._feature_cols, "version": self._version}
        (dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    @classmethod
    def load_native(cls, dir: Path) -> LightGBMForecaster:
        if not _LGB_AVAILABLE:
            raise ImportError("lightgbm is required for LightGBMForecaster.")
        instance = cls()
        instance._booster_mean = lgb.Booster(model_file=str(dir / "lgbm-mean.txt"))
        instance._booster_p10 = lgb.Booster(model_file=str(dir / "lgbm-p10.txt"))
        instance._booster_p90 = lgb.Booster(model_file=str(dir / "lgbm-p90.txt"))
        meta = json.loads((dir / "meta.json").read_text())
        instance._feature_cols = meta["feature_cols"]
        instance._version = meta.get("version", "lgbm-unknown")
        return instance
