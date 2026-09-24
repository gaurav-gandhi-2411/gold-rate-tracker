"""Tests for scripts/analysis_vol_regime_prereg.py (ADR 044). Synthetic data only."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "analysis_vol_regime_prereg.py"
_spec = importlib.util.spec_from_file_location("analysis_vol_regime_prereg", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["analysis_vol_regime_prereg"] = mod
_spec.loader.exec_module(mod)


def _close(returns: np.ndarray) -> pd.Series:
    idx = pd.bdate_range("2001-01-01", periods=len(returns) + 1)
    return pd.Series(100 * np.exp(np.concatenate([[0.0], np.cumsum(returns)])), index=idx)


def test_regime_uses_only_past_volatility() -> None:
    rng = np.random.default_rng(42)
    r = rng.normal(0, 0.01, 600)
    base = mod.frame(_close(r))
    r2 = r.copy()
    r2[500:] *= 5.0  # a volatility shock later must not change earlier regimes
    shocked = mod.frame(_close(r2))
    early = base.index[base.index < shocked.index[0] + pd.offsets.BDay(200)]
    pd.testing.assert_series_equal(base.loc[early, "calm"], shocked.loc[early, "calm"])
    assert len(base) == 600 - mod.VOL_WINDOW - mod.MEDIAN_WINDOW


def test_rule_is_momentum_when_calm_and_reversal_when_volatile() -> None:
    df = pd.DataFrame({"r": [0.01, -0.01, 0.01, -0.01, 0.0, 0.0], "calm": [1, 1, 0, 0, 1, 0]})
    df["calm"] = df["calm"].astype(bool)
    same = (df["r"] > 0) | (df["r"] == 0)
    pred = np.where(df["calm"], same, ~same | (df["r"] == 0))
    assert pred.tolist() == [True, False, False, True, True, True]
    # and frame() applies exactly this rule
    rng = np.random.default_rng(1)
    f = mod.frame(_close(rng.normal(0, 0.01, 400)))
    expect = np.where(f["calm"], f["r"] >= 0, f["r"] <= 0)
    assert (f["pred_up"].to_numpy() == expect).all()


def test_a_planted_regime_effect_is_detected() -> None:
    rng = np.random.default_rng(42)
    n = 3000
    r = np.zeros(n)
    vol = np.where((np.arange(n) // 250) % 2 == 0, 0.005, 0.02)
    for t in range(1, n):
        phi = 0.5 if vol[t] < 0.01 else -0.5
        r[t] = phi * r[t - 1] + rng.normal(0, vol[t])
    res = mod.rule_vs_up(mod.frame(_close(r)))
    assert res["rule_accuracy"] > res["always_up_accuracy"]
    assert res["p_one_sided_vs_always_up"] < 0.05
