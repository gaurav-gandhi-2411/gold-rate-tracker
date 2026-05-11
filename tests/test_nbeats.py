"""
Unit tests for ml/nbeats.py.

Tests require torch; they are skipped automatically in CI if torch is absent.
All tests use CPU; no GPU is required.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

# ── optional-import guards ────────────────────────────────────────────────────
try:
    import torch

    _TORCH = True
except ImportError:
    _TORCH = False

needs_torch = pytest.mark.skipif(not _TORCH, reason="torch not installed")


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_gold_df(n: int = 80, start: str = "2025-01-01") -> pd.DataFrame:
    rng = np.random.default_rng(7)
    t0 = pd.Timestamp(start, tz="UTC")
    ts = [t0 + pd.Timedelta(days=i) for i in range(n)]
    prices = 14000 + rng.integers(-200, 201, size=n).cumsum()
    prices = np.clip(prices, 8000, 25000)
    return pd.DataFrame(
        {
            "timestamp": [t.isoformat() for t in ts],
            "22k": prices.tolist(),
            "24k": (prices * 24 / 22).round().astype(int).tolist(),
            "18k": (prices * 18 / 22).round().astype(int).tolist(),
        }
    )


# ── 1. TestNBeatsArchitecture (needs torch) ───────────────────────────────────


@needs_torch
class TestNBeatsArchitecture:
    def _model(self):
        from ml.nbeats import LOOKBACK, NBeatsNet

        return NBeatsNet(
            lookback=LOOKBACK, n_stacks=1, n_blocks=1, hidden=16, theta=2, dropout=0.0
        ), LOOKBACK

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
        from ml.nbeats import LOOKBACK, NBeatsNet

        model_cpu = NBeatsNet(
            lookback=LOOKBACK, n_stacks=1, n_blocks=1, hidden=16, theta=2, dropout=0.0
        )
        model_gpu = NBeatsNet(
            lookback=LOOKBACK, n_stacks=1, n_blocks=1, hidden=16, theta=2, dropout=0.0
        )
        model_gpu.load_state_dict(model_cpu.state_dict())
        model_gpu = model_gpu.cuda()
        model_cpu.eval()
        model_gpu.eval()
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
        from ml.nbeats import LOOKBACK, build_sequences

        df = _make_gold_df(n=60)
        X, y = build_sequences(df, lookback=LOOKBACK)
        n_expected = 60 - 1 - LOOKBACK  # 60 prices → 59 deltas → 59-28=31 sequences
        assert X.shape == (n_expected, LOOKBACK)
        assert y.shape == (n_expected,)

    def test_x_dtype_float32(self):
        from ml.nbeats import LOOKBACK, build_sequences

        X, y = build_sequences(_make_gold_df(), lookback=LOOKBACK)
        assert X.dtype == np.float32
        assert y.dtype == np.float32

    def test_target_is_next_delta(self):
        """y[i] should equal the delta immediately after window X[i]."""
        from ml.nbeats import LOOKBACK, build_sequences

        df = _make_gold_df(n=50)
        prices = df["22k"].values.astype(float)
        deltas = prices[1:] - prices[:-1]
        X, y = build_sequences(df, lookback=LOOKBACK)
        # y[0] must equal deltas[LOOKBACK]
        assert abs(y[0] - deltas[LOOKBACK]) < 1e-3

    def test_too_short_df_returns_empty(self):
        from ml.nbeats import LOOKBACK, build_sequences

        df = _make_gold_df(n=LOOKBACK)  # only LOOKBACK prices → 0 sequences
        X, y = build_sequences(df, lookback=LOOKBACK)
        assert len(X) == 0

    def test_normalize_batch_clamps_sigma(self):
        from ml.nbeats import MIN_STD, normalize_batch

        # Flat sequence → std = 0 before clamp
        x = torch.ones(4, 28)
        _, _, sigma = normalize_batch(x)
        assert (sigma >= MIN_STD).all(), "sigma not clamped to MIN_STD"
