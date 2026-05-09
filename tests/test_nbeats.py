"""
Unit tests for ml/nbeats.py and ml/nbeats_infer.py.

Tests are split into two groups:

  TestNBeatsArchitecture / TestBuildSequences
    — require torch; skipped automatically in CI if torch is absent.

  TestNBeatsInferOnnx / TestPredictNbeatsDelta
    — require onnxruntime + models/production/nbeats.onnx;
      skipped automatically if either is missing.

All tests use CPU; no GPU is required.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# ── optional-import guards ────────────────────────────────────────────────────
try:
    import torch
    _TORCH = True
except ImportError:
    _TORCH = False

try:
    import onnxruntime  # noqa: F401
    _ORT = True
except ImportError:
    _ORT = False

_ONNX_MODEL = Path("models/production/nbeats.onnx")
_MODEL_READY = _ORT and _ONNX_MODEL.exists()

needs_torch = pytest.mark.skipif(not _TORCH, reason="torch not installed")
needs_model = pytest.mark.skipif(not _MODEL_READY,
                                  reason="ONNX model not trained or onnxruntime absent")


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_gold_df(n: int = 80, start: str = "2025-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(7)
    t0  = pd.Timestamp(start, tz="UTC")
    ts  = [t0 + pd.Timedelta(days=i) for i in range(n)]
    prices = 14000 + rng.integers(-200, 201, size=n).cumsum()
    prices = np.clip(prices, 8000, 25000)
    return pd.DataFrame({
        "timestamp": [t.isoformat() for t in ts],
        "22k": prices.tolist(),
        "24k": (prices * 24 / 22).round().astype(int).tolist(),
        "18k": (prices * 18 / 22).round().astype(int).tolist(),
    })


# ── 1. TestNBeatsArchitecture (needs torch) ───────────────────────────────────

@needs_torch
class TestNBeatsArchitecture:
    def _model(self):
        from ml.nbeats import NBeatsNet, LOOKBACK
        return NBeatsNet(lookback=LOOKBACK, n_stacks=1, n_blocks=1,
                         hidden=16, theta=2, dropout=0.0), LOOKBACK

    def test_forward_output_shape(self):
        model, lookback = self._model()
        x = torch.zeros(4, lookback)
        out = model(x)
        assert out.shape == (4, 1), f"Expected (4, 1), got {out.shape}"

    def test_no_nan_in_output(self):
        model, lookback = self._model()
        rng = np.random.default_rng(0)
        x = torch.from_numpy(rng.normal(0, 1, (8, lookback)).astype("float32"))
        out = model(x)
        assert not torch.isnan(out).any(), "NaN in N-BEATS output"

    def test_model_has_parameters(self):
        model, _ = self._model()
        n_params = sum(p.numel() for p in model.parameters())
        assert n_params > 0, "Model has no parameters"

    def test_eval_mode_gives_deterministic_output(self):
        model, lookback = self._model()
        model.eval()
        x = torch.ones(2, lookback)
        with torch.no_grad():
            out1 = model(x)
            out2 = model(x)
        torch.testing.assert_close(out1, out2)

    def test_gpu_model_matches_cpu(self):
        if not torch.cuda.is_available():
            pytest.skip("CUDA not available")
        from ml.nbeats import NBeatsNet, LOOKBACK
        model_cpu = NBeatsNet(lookback=LOOKBACK, n_stacks=1, n_blocks=1,
                              hidden=16, theta=2, dropout=0.0)
        model_gpu = NBeatsNet(lookback=LOOKBACK, n_stacks=1, n_blocks=1,
                              hidden=16, theta=2, dropout=0.0)
        model_gpu.load_state_dict(model_cpu.state_dict())
        model_gpu = model_gpu.cuda()
        model_cpu.eval(); model_gpu.eval()
        x_cpu = torch.ones(1, LOOKBACK)
        x_gpu = x_cpu.cuda()
        with torch.no_grad():
            out_cpu = model_cpu(x_cpu)
            out_gpu = model_gpu(x_gpu).cpu()
        torch.testing.assert_close(out_cpu, out_gpu, atol=1e-5, rtol=1e-5)


# ── 2. TestBuildSequences (needs torch environment, but no GPU) ───────────────

@needs_torch
class TestBuildSequences:
    def test_output_shapes(self):
        from ml.nbeats import build_sequences, LOOKBACK
        df = _make_gold_df(n=60)
        X, y = build_sequences(df, lookback=LOOKBACK)
        n_expected = 60 - 1 - LOOKBACK   # 60 prices → 59 deltas → 59-28=31 sequences
        assert X.shape == (n_expected, LOOKBACK)
        assert y.shape == (n_expected,)

    def test_x_dtype_float32(self):
        from ml.nbeats import build_sequences, LOOKBACK
        X, y = build_sequences(_make_gold_df(), lookback=LOOKBACK)
        assert X.dtype == np.float32
        assert y.dtype == np.float32

    def test_target_is_next_delta(self):
        """y[i] should equal the delta immediately after window X[i]."""
        from ml.nbeats import build_sequences, LOOKBACK
        df = _make_gold_df(n=50)
        prices = df["22k"].values.astype(float)
        deltas = prices[1:] - prices[:-1]
        X, y = build_sequences(df, lookback=LOOKBACK)
        # y[0] must equal deltas[LOOKBACK]
        assert abs(y[0] - deltas[LOOKBACK]) < 1e-3

    def test_too_short_df_returns_empty(self):
        from ml.nbeats import build_sequences, LOOKBACK
        df = _make_gold_df(n=LOOKBACK)   # only LOOKBACK prices → 0 sequences
        X, y = build_sequences(df, lookback=LOOKBACK)
        assert len(X) == 0

    def test_normalize_batch_clamps_sigma(self):
        from ml.nbeats import normalize_batch, MIN_STD
        # Flat sequence → std = 0 before clamp
        x = torch.ones(4, 28)
        _, _, sigma = normalize_batch(x)
        assert (sigma >= MIN_STD).all(), "sigma not clamped to MIN_STD"


# ── 3. TestNBeatsInferOnnx (needs onnxruntime + trained model) ────────────────

@needs_model
class TestNBeatsInferOnnx:
    def test_session_loads_successfully(self):
        from ml.nbeats_infer import load_nbeats_session
        sess = load_nbeats_session()
        assert sess is not None

    def test_input_name_is_input(self):
        from ml.nbeats_infer import load_nbeats_session
        sess = load_nbeats_session()
        names = [i.name for i in sess.get_inputs()]
        assert "input" in names

    def test_output_name_is_output(self):
        from ml.nbeats_infer import load_nbeats_session
        sess = load_nbeats_session()
        names = [o.name for o in sess.get_outputs()]
        assert "output" in names

    def test_returns_scalar_for_batch1(self):
        from ml.nbeats_infer import load_nbeats_session, LOOKBACK
        sess = load_nbeats_session()
        x = np.random.randn(1, LOOKBACK).astype(np.float32)
        result = sess.run(["output"], {"input": x})
        assert result[0].shape == (1, 1)

    def test_model_file_under_30mb(self):
        size_mb = _ONNX_MODEL.stat().st_size / 1e6
        assert size_mb < 30, f"ONNX model is {size_mb:.1f} MB (limit: 30 MB)"


# ── 4. TestPredictNbeatsDelta (needs model + gold history) ────────────────────

@needs_model
class TestPredictNbeatsDelta:
    def test_returns_float(self):
        from ml.nbeats_infer import load_nbeats_session, predict_nbeats_delta
        sess   = load_nbeats_session()
        df     = _make_gold_df(n=60)
        result = predict_nbeats_delta(sess, df)
        assert isinstance(result, float), f"Expected float, got {type(result)}"

    def test_returns_none_for_short_history(self):
        from ml.nbeats_infer import load_nbeats_session, predict_nbeats_delta, LOOKBACK
        sess   = load_nbeats_session()
        df     = _make_gold_df(n=LOOKBACK)   # too short: no complete window
        result = predict_nbeats_delta(sess, df)
        assert result is None

    def test_delta_in_plausible_range(self):
        """Predicted delta should be within ±Rs.2000 of zero for stable prices."""
        from ml.nbeats_infer import load_nbeats_session, predict_nbeats_delta
        sess   = load_nbeats_session()
        df     = _make_gold_df(n=60)
        result = predict_nbeats_delta(sess, df)
        assert abs(result) < 2000, f"Implausible delta: Rs.{result:.0f}"

    def test_missing_model_path_returns_none(self):
        from ml.nbeats_infer import load_nbeats_session
        sess = load_nbeats_session(path=Path("nonexistent/model.onnx"))
        assert sess is None
