"""Tests for scripts/analysis_derived_premium.py (G4b). Synthetic data only, no network."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "analysis_derived_premium.py"
_spec = importlib.util.spec_from_file_location("analysis_derived_premium", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["analysis_derived_premium"] = mod
_spec.loader.exec_module(mod)

TABLE = [
    {"effective_date": "2024-01-01", "total_duty_pct": 15.0, "notification": "A"},
    {"effective_date": "2024-01-10", "total_duty_pct": 6.0, "notification": "B"},
]


def test_duty_in_force_uses_effective_date_inclusive_and_never_guesses_earlier() -> None:
    idx = pd.date_range("2023-12-30", "2024-01-11", freq="D")
    s = mod.duty_rate_series(idx, TABLE)
    assert np.isnan(s["2023-12-31"])
    assert s["2024-01-01"] == pytest.approx(0.15)
    assert s["2024-01-09"] == pytest.approx(0.15)
    assert s["2024-01-10"] == pytest.approx(0.06)


def test_premium_uses_prior_day_drivers_and_the_duty_in_force() -> None:
    days = pd.date_range("2024-01-01", "2024-01-12", freq="D")
    # driver closes change every day; build() must use the lagged (t-1) value
    drivers_raw = pd.DataFrame(
        {"comex_usd_oz": np.arange(2000.0, 2012.0), "usd_inr": np.full(12, 83.0)}, index=days
    )
    drivers = drivers_raw.shift(1)  # what load_drivers returns
    ibja = pd.DataFrame({"date": days[1:].date.astype(str), "pm_999": 62000.0})
    d = mod.build(TABLE, ibja, drivers)
    t = pd.Timestamp("2024-01-10")
    parity = 2008.0 / mod.TROY_OZ_TO_GRAM * 10 * 83.0 * 1.06  # COMEX of 01-09, duty of 01-10
    assert d.loc[t, "landed_parity"] == pytest.approx(parity)
    assert d.loc[t, "premium_pct"] == pytest.approx((62000.0 / parity - 1) * 100)


def test_stale_repeats_and_segments() -> None:
    days = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-20"])
    drivers = pd.DataFrame(
        {"comex_usd_oz": 2000.0, "usd_inr": 83.0},
        index=pd.date_range("2024-01-01", "2024-01-21", freq="D"),
    )
    ibja = pd.DataFrame({"date": days.date.astype(str), "pm_999": [1.0, 1.0, 2.0, 3.0]})
    d = mod.build(TABLE, ibja, drivers)
    assert d["stale_repeat"].tolist() == [False, True, False, False]
    assert d["segment"].nunique() == 2  # the 16-day gap starts a new segment


def test_ar1_recovers_persistence() -> None:
    rng = np.random.default_rng(42)
    x = [0.0]
    for _ in range(999):
        x.append(0.8 * x[-1] + rng.normal())
    r = mod._ar1(pd.Series(x), pd.Series(np.zeros(1000, dtype=int)))
    assert r["ar1"] == pytest.approx(0.8, abs=0.05)
    assert r["half_life_rows"] == pytest.approx(np.log(0.5) / np.log(r["ar1"]))
