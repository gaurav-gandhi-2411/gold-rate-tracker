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


# --- Addendum A1 option A (adopted 2026-09-25): parity on the fix clock -------------------------


def _hourly(start: str, periods: int, value0: float) -> pd.Series:
    idx = pd.date_range(start, periods=periods, freq="h", tz="UTC")
    return pd.Series(value0 + np.arange(periods, dtype=float), index=idx)


def test_bar_known_by_takes_the_last_bar_that_ended_not_the_one_still_forming() -> None:
    bars = _hourly("2026-09-25 00:00", 12, 100.0)  # bar starting 05:00 ends 06:00
    got = mod.bar_known_by(bars, pd.Timestamp("2026-09-25 06:30", tz="UTC"))
    assert got == {"bar_start_utc": "2026-09-25T05:00:00+00:00", "close": 105.0}
    # at exactly a bar's end, that bar counts as known
    got = mod.bar_known_by(bars, pd.Timestamp("2026-09-25 06:00", tz="UTC"))
    assert got is not None and got["close"] == 105.0
    assert mod.bar_known_by(bars, pd.Timestamp("2026-09-25 00:30", tz="UTC")) is None
    assert mod.bar_known_by(pd.Series(dtype=float), pd.Timestamp("2026-09-25", tz="UTC")) is None


def test_fix_clock_bars_uses_am_and_pm_cutoffs_and_never_rewrites_the_archive() -> None:
    bars = {
        "GC=F": _hourly("2026-09-24 00:00", 60, 2000.0),
        "INR=X": _hourly("2026-09-24 00:00", 60, 80.0),
    }
    day = pd.Timestamp("2026-09-25")
    arch, mism = mod.fix_clock_bars([day], bars, {})
    e = arch["2026-09-25"]
    assert e["am"]["GC=F"]["bar_start_utc"] == "2026-09-25T05:00:00+00:00"  # ended 06:00 <= 06:30
    assert e["pm"]["GC=F"]["bar_start_utc"] == "2026-09-25T10:00:00+00:00"  # ended 11:00 <= 11:30
    assert mism == 0
    # a re-fetch that disagrees on an archived bar is counted, and the archive wins
    bumped = {k: v + 1 for k, v in bars.items()}
    arch2, mism2 = mod.fix_clock_bars([day], bumped, arch)
    assert arch2 == arch and mism2 == 2
    # missing bars for one ticker: nothing is archived for that day
    arch3, _ = mod.fix_clock_bars([day], {"GC=F": bars["GC=F"]}, {})
    assert arch3 == {}


def test_apply_fix_clock_rebuilds_parity_and_premium_from_the_archived_bars() -> None:
    d = pd.DataFrame(
        {
            "pm_999": [150000.0, 151000.0],
            "duty_rate": [0.06, 0.06],
            "landed_parity": [1.0, 1.0],
            "premium_pct": [9.0, 9.0],
            "stale_repeat": [False, False],
        },
        index=pd.to_datetime(["2026-09-25", "2026-09-26"]),
    )
    bar = {"bar_start_utc": "x", "close": 0.0}
    arch = {
        "2026-09-25": {
            "am": {"GC=F": {**bar, "close": 3000.0}, "INR=X": {**bar, "close": 88.0}},
            "pm": {"GC=F": {**bar, "close": 3010.0}, "INR=X": {**bar, "close": 88.0}},
        }
    }
    x = mod.apply_fix_clock(d, arch)
    conv = 10 / mod.TROY_OZ_TO_GRAM
    assert x["landed_parity"].iloc[0] == pytest.approx(3000 * 88 * conv * 1.06)
    assert x["premium_pct"].iloc[0] == pytest.approx((150000 / (3010 * 88 * conv * 1.06) - 1) * 100)
    assert np.isnan(x["landed_parity"].iloc[1]) and np.isnan(x["premium_pct"].iloc[1])


def test_archive_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "bars.json"
    assert mod.load_archive(path) == {}
    dates = {"2026-09-26": {"am": {}}, "2026-09-25": {"pm": {}}}
    mod.save_archive(path, dates)
    assert mod.load_archive(path) == dates
    assert list(mod.load_archive(path)) == ["2026-09-25", "2026-09-26"]
