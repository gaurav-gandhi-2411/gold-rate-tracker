"""
Unit/integration tests for ml/models/nbeats.py (NBeatsForecaster).

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


def _tiny_cfg(n_epochs: int = 2):
    from ml.config import load_config

    overrides = [
        "model=nbeats",
        "model.params.input_chunk_length=10",
        f"model.params.n_epochs={n_epochs}",
        "model.params.batch_size=8",
        "model.params.num_stacks=2",
        "model.params.num_blocks=2",
        "model.params.num_layers=2",
        "model.params.layer_widths=16",
        "model.trainer.accelerator=cpu",
        "model.trainer.precision=32",
        "model.early_stopping.patience=50",
    ]
    return load_config(overrides=overrides)


def _make_history(n: int = 60) -> pd.DataFrame:
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


# ---------------------------------------------------------------------------
# 1. Instantiation
# ---------------------------------------------------------------------------


class TestNBeatsForecasterInstantiation:
    def test_instantiates_without_config(self):
        from ml.models.nbeats import NBeatsForecaster

        f = NBeatsForecaster()
        assert f.name == "nbeats"

    def test_instantiates_with_config(self):
        from ml.models.nbeats import NBeatsForecaster

        cfg = _tiny_cfg()
        f = NBeatsForecaster(cfg)
        assert f.name == "nbeats"
        assert f._get_icl() == 10
        assert f._get_n_epochs() == 2


# ---------------------------------------------------------------------------
# 2. Fit
# ---------------------------------------------------------------------------


class TestNBeatsFit:
    def test_fit_no_crash(self):
        from ml.models.nbeats import NBeatsForecaster

        f = NBeatsForecaster(_tiny_cfg())
        meta = f.fit(_make_history(60))
        assert isinstance(meta, dict)

    def test_fit_returns_required_keys(self):
        from ml.models.nbeats import NBeatsForecaster

        f = NBeatsForecaster(_tiny_cfg())
        meta = f.fit(_make_history(60))
        for key in ("best_epoch", "epochs_run", "val_mae", "naive_mae", "n_train", "n_val"):
            assert key in meta, f"Missing key: {key}"

    def test_fit_sets_darts_model(self):
        from ml.models.nbeats import NBeatsForecaster

        f = NBeatsForecaster(_tiny_cfg())
        assert f._darts_model is None
        f.fit(_make_history(60))
        assert f._darts_model is not None

    def test_fit_sets_normalizer(self):
        from ml.models.nbeats import NBeatsForecaster

        f = NBeatsForecaster(_tiny_cfg())
        f.fit(_make_history(60))
        assert f._normalizer is not None
        assert f._normalizer.std > 0

    def test_fit_n_past_total_is_one(self):
        """N-BEATS is univariate — n_past_total is always 1."""
        from ml.models.nbeats import NBeatsForecaster

        f = NBeatsForecaster(_tiny_cfg())
        f.fit(_make_history(60))
        assert f._n_past_total == 1

    def test_fit_n_future_total_is_zero(self):
        from ml.models.nbeats import NBeatsForecaster

        f = NBeatsForecaster(_tiny_cfg())
        f.fit(_make_history(60))
        assert f._n_future_total == 0

    def test_macro_ignored(self):
        """fit() accepts macro kwarg but ignores it — should not raise."""
        from ml.models.nbeats import NBeatsForecaster

        rng = np.random.default_rng(0)
        dummy_macro = pd.DataFrame({"usd_inr": rng.uniform(83, 85, 60)})
        f = NBeatsForecaster(_tiny_cfg())
        meta = f.fit(_make_history(60), macro=dummy_macro)
        assert isinstance(meta, dict)


# ---------------------------------------------------------------------------
# 3. ONNX export
# ---------------------------------------------------------------------------


class TestNBeatsOnnxExport:
    def test_onnx_export_produces_file(self, tmp_path):
        from ml.models.nbeats import NBeatsForecaster

        f = NBeatsForecaster(_tiny_cfg())
        f.fit(_make_history(60))
        onnx_path = tmp_path / "nbeats.onnx"
        f.export_onnx(onnx_path)
        assert onnx_path.exists()
        assert onnx_path.stat().st_size > 1000

    def test_onnx_is_valid_model(self, tmp_path):
        import onnx
        from ml.models.nbeats import NBeatsForecaster

        f = NBeatsForecaster(_tiny_cfg())
        f.fit(_make_history(60))
        onnx_path = tmp_path / "nbeats.onnx"
        f.export_onnx(onnx_path)
        onnx.checker.check_model(onnx.load(str(onnx_path)))

    def test_onnx_has_single_input(self, tmp_path):
        """N-BEATS ONNX must have exactly one input named past_input."""
        import onnx
        from ml.models.nbeats import NBeatsForecaster

        f = NBeatsForecaster(_tiny_cfg())
        f.fit(_make_history(60))
        onnx_path = tmp_path / "nbeats.onnx"
        f.export_onnx(onnx_path)
        model = onnx.load(str(onnx_path))
        assert [i.name for i in model.graph.input] == ["past_input"]

    def test_onnx_runnable_via_onnxruntime(self, tmp_path):
        import onnxruntime as ort
        from ml.models.nbeats import NBeatsForecaster

        f = NBeatsForecaster(_tiny_cfg())
        f.fit(_make_history(60))
        onnx_path = tmp_path / "nbeats.onnx"
        f.export_onnx(onnx_path)

        x0 = np.zeros((1, f._icl, f._n_past_total), dtype=np.float32)
        sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        out = sess.run(["point_estimate"], {"past_input": x0})
        assert out[0].shape == (1, 1)


# ---------------------------------------------------------------------------
# 4. Parity check
# ---------------------------------------------------------------------------


class TestNBeatsParity:
    def test_parity_within_tolerance(self, tmp_path):
        from ml.models.nbeats import NBeatsForecaster
        from ml.training.utils import verify_pytorch_onnx_parity

        f = NBeatsForecaster(_tiny_cfg())
        f.fit(_make_history(60))
        onnx_path = tmp_path / "nbeats.onnx"
        f.export_onnx(onnx_path)

        rng = np.random.default_rng(7)
        x0 = rng.standard_normal((4, f._icl, f._n_past_total)).astype(np.float32)

        pt_preds = f.predict_batch_native(x0)
        onnx_preds = f.predict_batch_onnx(onnx_path, x0)
        result = verify_pytorch_onnx_parity(pt_preds, onnx_preds, tolerance=1e-3)
        assert result["max_abs_diff"] <= 1e-3


# ---------------------------------------------------------------------------
# 5. save_native / load_native round-trip
# ---------------------------------------------------------------------------


class TestNBeatsSaveLoadRoundTrip:
    def test_round_trip_preserves_predictions(self, tmp_path):
        from ml.models.nbeats import NBeatsForecaster

        f = NBeatsForecaster(_tiny_cfg())
        f.fit(_make_history(60))

        rng = np.random.default_rng(99)
        x0 = rng.standard_normal((2, f._icl, f._n_past_total)).astype(np.float32)
        orig_preds = f.predict_batch_native(x0)

        save_dir = tmp_path / "nbeats_checkpoint"
        f.save_native(save_dir)
        f2 = NBeatsForecaster.load_native(save_dir)
        loaded_preds = f2.predict_batch_native(x0)
        np.testing.assert_allclose(loaded_preds, orig_preds, atol=1e-5)

    def test_round_trip_normalizer_preserved(self, tmp_path):
        from ml.models.nbeats import NBeatsForecaster

        f = NBeatsForecaster(_tiny_cfg())
        f.fit(_make_history(60))
        save_dir = tmp_path / "nbeats_chk"
        f.save_native(save_dir)
        f2 = NBeatsForecaster.load_native(save_dir)
        assert abs(f2._normalizer.mean - f._normalizer.mean) < 1e-6
        assert abs(f2._normalizer.std - f._normalizer.std) < 1e-6

    def test_round_trip_shapes_preserved(self, tmp_path):
        from ml.models.nbeats import NBeatsForecaster

        f = NBeatsForecaster(_tiny_cfg())
        f.fit(_make_history(60))
        save_dir = tmp_path / "nbeats_chk2"
        f.save_native(save_dir)
        f2 = NBeatsForecaster.load_native(save_dir)
        assert f2._n_past_total == f._n_past_total
        assert f2._n_future_total == f._n_future_total
        assert f2._icl == f._icl
