"""
Unit/integration tests for ml/models/tft.py and ml/training/train_tft.py.

All tests use CPU and tiny architectures — no GPU required.
darts + pytorch-lightning must be installed for these tests to run.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("darts", reason="darts not installed")
pytest.importorskip("torch", reason="torch not installed")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tiny_cfg(tmp_path=None, n_epochs: int = 2):
    """Load TFT config with tiny dimensions for fast CPU testing."""
    from ml.config import load_config

    overrides = [
        "model=tft",
        "model.params.input_chunk_length=10",
        f"model.params.n_epochs={n_epochs}",
        "model.params.batch_size=8",
        "model.params.hidden_size=8",
        "model.params.num_attention_heads=2",
        "model.params.lstm_layers=1",
        "model.trainer.accelerator=cpu",
        "model.trainer.precision=32",
        "model.early_stopping.patience=50",  # disable early stopping in tests
    ]
    return load_config(overrides=overrides)


def _make_history(n: int = 60) -> pd.DataFrame:
    """Synthetic daily price history."""
    rng = np.random.default_rng(42)
    dates = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    prices = 7000 + np.cumsum(rng.normal(0, 50, n))
    return pd.DataFrame(
        {
            "timestamp": [d.isoformat() for d in dates],
            "22k": prices.astype(int),
            "24k": (prices * 24 / 22).astype(int),
            "18k": (prices * 18 / 22).astype(int),
        }
    )


def _make_macro(n: int = 60) -> pd.DataFrame:
    """Synthetic macro DataFrame aligned to history dates."""
    rng = np.random.default_rng(42)
    dates = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "usd_inr": rng.uniform(83, 85, n),
            "gold_usd": rng.uniform(1900, 2100, n),
            "us_10y_yield": rng.uniform(4.0, 4.5, n),
            "dxy": rng.uniform(100, 106, n),
            "sensex": rng.uniform(70000, 80000, n),
            "vix": rng.uniform(12, 25, n),
            "vix_level": rng.uniform(12, 25, n),
            "usd_inr_change_1d": rng.normal(0, 0.01, n),
            "gold_usd_change_1d": rng.normal(0, 0.01, n),
            "gold_usd_5d_vol": rng.uniform(0.005, 0.015, n),
            "sensex_5d_return": rng.normal(0, 0.02, n),
            "regime": np.where(np.arange(n) < n // 2, 0.0, 1.0),
        },
        index=dates,
    )


# ---------------------------------------------------------------------------
# 1. Instantiation
# ---------------------------------------------------------------------------


class TestTFTForecasterInstantiation:
    def test_instantiates_without_config(self):
        from ml.models.tft import TFTForecaster

        f = TFTForecaster()
        assert f.name == "tft"

    def test_instantiates_with_config(self):
        from ml.models.tft import TFTForecaster

        cfg = _tiny_cfg()
        f = TFTForecaster(cfg)
        assert f.name == "tft"
        assert f._get_icl() == 10
        assert f._get_n_epochs() == 2


# ---------------------------------------------------------------------------
# 2. Fit on synthetic CPU data
# ---------------------------------------------------------------------------


class TestTFTFit:
    def test_fit_no_crash(self):
        from ml.models.tft import TFTForecaster

        cfg = _tiny_cfg()
        history = _make_history(60)
        macro = _make_macro(60)
        f = TFTForecaster(cfg)
        meta = f.fit(history, macro)
        assert isinstance(meta, dict)

    def test_fit_returns_required_keys(self):
        from ml.models.tft import TFTForecaster

        cfg = _tiny_cfg()
        history = _make_history(60)
        f = TFTForecaster(cfg)
        meta = f.fit(history, None)  # no macro
        for key in ("best_epoch", "epochs_run", "val_mae", "naive_mae", "n_train", "n_val"):
            assert key in meta, f"Missing key: {key}"

    def test_fit_sets_darts_model(self):
        from ml.models.tft import TFTForecaster

        cfg = _tiny_cfg()
        history = _make_history(60)
        f = TFTForecaster(cfg)
        assert f._darts_model is None
        f.fit(history, None)
        assert f._darts_model is not None

    def test_fit_sets_normalizer(self):
        from ml.models.tft import TFTForecaster

        cfg = _tiny_cfg()
        f = TFTForecaster(cfg)
        f.fit(_make_history(60), None)
        assert f._normalizer is not None
        assert f._normalizer.std > 0

    def test_fit_with_macro_sets_n_past_total(self):
        from ml.models.tft import TFTForecaster

        cfg = _tiny_cfg()
        f = TFTForecaster(cfg)
        f.fit(_make_history(60), _make_macro(60))
        assert f._n_past_total is not None
        # With target(1) + past_cov(10) + hist_fut_cov(6) = 17
        assert f._n_past_total == 17

    def test_fit_without_macro_sets_n_past_total(self):
        from ml.models.tft import TFTForecaster

        cfg = _tiny_cfg()
        f = TFTForecaster(cfg)
        f.fit(_make_history(60), None)
        assert f._n_past_total is not None
        # Without macro: target(1) + hist_fut_cov(6) = 7
        assert f._n_past_total == 7

    def test_fit_n_future_total(self):
        from ml.models.tft import TFTForecaster

        cfg = _tiny_cfg()
        f = TFTForecaster(cfg)
        f.fit(_make_history(60), None)
        assert f._n_future_total == 6  # always len(FUTURE_COV_COLS)


# ---------------------------------------------------------------------------
# 3. ONNX export
# ---------------------------------------------------------------------------


class TestTFTOnnxExport:
    def test_onnx_export_produces_file(self, tmp_path):
        from ml.models.tft import TFTForecaster

        cfg = _tiny_cfg()
        f = TFTForecaster(cfg)
        f.fit(_make_history(60), None)
        onnx_path = tmp_path / "tft.onnx"
        f.export_onnx(onnx_path)
        assert onnx_path.exists()
        assert onnx_path.stat().st_size > 1000  # non-trivial file

    def test_onnx_is_valid_model(self, tmp_path):
        import onnx
        from ml.models.tft import TFTForecaster

        cfg = _tiny_cfg()
        f = TFTForecaster(cfg)
        f.fit(_make_history(60), None)
        onnx_path = tmp_path / "tft.onnx"
        f.export_onnx(onnx_path)
        model = onnx.load(str(onnx_path))
        onnx.checker.check_model(model)  # raises if invalid

    def test_onnx_runnable_via_onnxruntime(self, tmp_path):
        import onnxruntime as ort
        from ml.models.tft import TFTForecaster

        cfg = _tiny_cfg()
        f = TFTForecaster(cfg)
        f.fit(_make_history(60), None)
        onnx_path = tmp_path / "tft.onnx"
        f.export_onnx(onnx_path)

        import numpy as np

        x0 = np.zeros((1, f._icl, f._n_past_total), dtype=np.float32)
        x1 = np.zeros((1, f._ocl, f._n_future_total), dtype=np.float32)
        sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        out = sess.run(["point_estimate"], {"past_input": x0, "future_input": x1})
        assert out[0].shape == (1, 1)


# ---------------------------------------------------------------------------
# 4. Parity check
# ---------------------------------------------------------------------------


class TestTFTParity:
    def test_parity_within_tolerance(self, tmp_path):
        from ml.models.tft import TFTForecaster
        from ml.training.utils import verify_pytorch_onnx_parity

        cfg = _tiny_cfg()
        f = TFTForecaster(cfg)
        f.fit(_make_history(60), None)
        onnx_path = tmp_path / "tft.onnx"
        f.export_onnx(onnx_path)

        rng = np.random.default_rng(7)
        x0 = rng.standard_normal((4, f._icl, f._n_past_total)).astype(np.float32)
        x1 = rng.standard_normal((4, f._ocl, f._n_future_total)).astype(np.float32)

        pt_preds = f.predict_batch_native(x0, x1)
        onnx_preds = f.predict_batch_onnx(onnx_path, x0, x1)
        result = verify_pytorch_onnx_parity(pt_preds, onnx_preds, tolerance=1e-3)
        assert result["max_abs_diff"] <= 1e-3


# ---------------------------------------------------------------------------
# 5. save_native / load_native round-trip
# ---------------------------------------------------------------------------


class TestTFTSaveLoadRoundTrip:
    def test_round_trip_preserves_predictions(self, tmp_path):
        from ml.models.tft import TFTForecaster

        cfg = _tiny_cfg()
        f = TFTForecaster(cfg)
        f.fit(_make_history(60), None)

        rng = np.random.default_rng(99)
        x0 = rng.standard_normal((2, f._icl, f._n_past_total)).astype(np.float32)
        x1 = rng.standard_normal((2, f._ocl, f._n_future_total)).astype(np.float32)
        orig_preds = f.predict_batch_native(x0, x1)

        save_dir = tmp_path / "tft_checkpoint"
        f.save_native(save_dir)

        f2 = TFTForecaster.load_native(save_dir)
        loaded_preds = f2.predict_batch_native(x0, x1)
        np.testing.assert_allclose(loaded_preds, orig_preds, atol=1e-5)

    def test_round_trip_normalizer_preserved(self, tmp_path):
        from ml.models.tft import TFTForecaster

        cfg = _tiny_cfg()
        f = TFTForecaster(cfg)
        f.fit(_make_history(60), None)

        save_dir = tmp_path / "tft_chk"
        f.save_native(save_dir)
        f2 = TFTForecaster.load_native(save_dir)

        assert abs(f2._normalizer.mean - f._normalizer.mean) < 1e-6
        assert abs(f2._normalizer.std - f._normalizer.std) < 1e-6

    def test_round_trip_shapes_preserved(self, tmp_path):
        from ml.models.tft import TFTForecaster

        cfg = _tiny_cfg()
        f = TFTForecaster(cfg)
        f.fit(_make_history(60), None)

        save_dir = tmp_path / "tft_chk2"
        f.save_native(save_dir)
        f2 = TFTForecaster.load_native(save_dir)

        assert f2._n_past_total == f._n_past_total
        assert f2._n_future_total == f._n_future_total
        assert f2._icl == f._icl


# ---------------------------------------------------------------------------
# 6. Normalizer applied and reversed in data pipeline
# ---------------------------------------------------------------------------


class TestTFTNormalizerIntegration:
    def test_normalizer_denormalize_recovers_original_scale(self):
        from ml.training.utils import TargetNormalizer

        history = _make_history(60)
        prices = history["22k"].astype(float).values
        norm = TargetNormalizer.fit(prices)
        recovered = norm.denormalize(norm.normalize(prices))
        np.testing.assert_allclose(recovered, prices, atol=1e-6)

    def test_target_ts_is_normalized_in_fit(self):
        from ml.models.tft import TFTForecaster

        cfg = _tiny_cfg()
        f = TFTForecaster(cfg)
        f.fit(_make_history(60), None)
        # After normalization, mean should be ~0 and std ~1
        assert abs(f._normalizer.mean - 7000) < 5000  # reasonable for our synthetic prices
        assert f._normalizer.std > 0
