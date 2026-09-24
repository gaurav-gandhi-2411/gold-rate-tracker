"""Tests for scripts/analysis_premium_nowcast.py (G4d, ADR 046). Synthetic data only."""

from __future__ import annotations

import importlib.util
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "analysis_premium_nowcast.py"
_spec = importlib.util.spec_from_file_location("analysis_premium_nowcast", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["analysis_premium_nowcast"] = mod
_spec.loader.exec_module(mod)


def _frame(premium: np.ndarray, dates: pd.DatetimeIndex, parity: float = 100000.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "landed_parity": parity,
            "premium_pct": premium * 100,
            "pm_999": parity * (1 + premium),
            "stale_repeat": False,
        },
        index=dates,
    )


def test_consecutive_allows_one_missing_weekday_only() -> None:
    ts = pd.Timestamp
    assert mod.consecutive(ts("2026-09-24"), ts("2026-09-25"))  # Thu -> Fri
    assert mod.consecutive(ts("2026-09-25"), ts("2026-09-28"))  # Fri -> Mon
    assert mod.consecutive(ts("2026-09-24"), ts("2026-09-28"))  # one holiday (Fri)
    assert not mod.consecutive(ts("2026-09-23"), ts("2026-09-28"))


def test_fit_ar1_recovers_mean_and_persistence_and_clips() -> None:
    rng = np.random.default_rng(42)
    x = [0.0]
    for _ in range(3000):
        x.append(-0.01 + 0.6 * (x[-1] + 0.01) + rng.normal(0, 0.005))
    mu, rho = mod.fit_ar1(list(itertools.pairwise(x)))
    assert mu == pytest.approx(-0.01, abs=0.001)
    assert rho == pytest.approx(0.6, abs=0.05)
    alt = [(1.0, -1.0), (-1.0, 1.0)] * 20
    assert mod.fit_ar1(alt)[1] == 0.0  # negative rho clipped


def test_score_frame_uses_only_past_pairs_and_skips_gaps() -> None:
    dates = pd.bdate_range("2026-01-01", periods=80)
    prem = np.where(np.arange(80) % 2 == 0, -0.01, -0.02)
    d = _frame(prem, dates)
    s = mod.score_frame(d)
    assert len(s) == 79 - mod.MIN_PAIRS
    # changing a future premium must not change earlier predictions
    d2 = d.copy()
    d2.iloc[-1, d2.columns.get_loc("premium_pct")] = 50.0
    s2 = mod.score_frame(d2)
    pd.testing.assert_frame_equal(s.iloc[:-1][["C", "mu", "rho"]], s2.iloc[:-1][["C", "mu", "rho"]])
    # a gap breaks the pair: the day after it is not scored
    gap = dates.delete(slice(50, 55))
    assert len(mod.score_frame(_frame(prem[: len(gap)], gap))) == len(gap) - 1 - mod.MIN_PAIRS - 1


def test_predictions_follow_the_frozen_formulas() -> None:
    dates = pd.bdate_range("2026-01-01", periods=60)
    rng = np.random.default_rng(42)
    prem = rng.normal(-0.01, 0.01, 60)
    d = _frame(prem, dates)
    s = mod.score_frame(d)
    p0 = pd.Series(prem, index=dates).shift(1).loc[s.index]
    parity = d["landed_parity"].loc[s.index]
    np.testing.assert_allclose(s["B0"], d["pm_999"].shift(1).loc[s.index])
    np.testing.assert_allclose(s["B1"], parity * (1 + p0))
    np.testing.assert_allclose(s["C"], parity * (1 + s["mu"] + s["rho"] * (p0 - s["mu"])))
    assert ((s["rho"] >= 0) & (s["rho"] <= 1)).all()


def test_evaluate_reports_mae_in_rupees_per_gram_and_holm() -> None:
    idx = pd.bdate_range("2026-10-01", periods=60)
    rng = np.random.default_rng(42)
    actual = 150000 + rng.normal(0, 500, 60)
    s = pd.DataFrame(
        {
            "actual": actual,
            "B0": actual + rng.normal(0, 3000, 60),
            "B1": actual + rng.normal(0, 1500, 60),
            "C": actual + rng.normal(0, 500, 60),
        },
        index=idx,
    )
    r = mod.evaluate(s)
    assert r["n"] == 60
    assert r["mae_rs_per_g"]["C"] < r["mae_rs_per_g"]["B1"] < r["mae_rs_per_g"]["B0"]
    assert r["H1_C_vs_B1"]["significant"] is True
    assert r["H2_C_vs_B0"]["holm_significant"] is True
