"""Tests for scripts/analysis_dow_prereg.py (ADR 052). Synthetic data only: the frozen snapshots
are not read here, so running the tests never touches the confirmatory data."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "analysis_dow_prereg.py"
_spec = importlib.util.spec_from_file_location("analysis_dow_prereg", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["analysis_dow_prereg"] = mod
_spec.loader.exec_module(mod)


def _series(effect_bp_by_dow: dict[int, float], n_days: int = 3000, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2001-01-01", periods=n_days)
    r = (
        rng.normal(0, 0.01, n_days)
        + np.array([effect_bp_by_dow.get(d, 0.0) for d in idx.dayofweek]) / 1e4
    )
    return pd.Series(1000 * np.exp(np.cumsum(r)), index=idx)


def test_joint_test_catches_a_planted_weekday_effect_and_not_noise() -> None:
    planted = mod.joint_weekday_test(_series({4: -25.0}))  # Fridays fall 25 bp
    assert planted["p_value"] < 0.001 and planted["passes"]
    noise = [mod.joint_weekday_test(_series({}, seed=s))["p_value"] for s in range(20)]
    assert sum(p < 0.05 for p in noise) <= 3  # ~5% false positives expected


def test_weekly_pair_maps_indian_days_to_the_previous_us_close() -> None:
    idx = pd.bdate_range("2026-09-21", periods=5)  # Mon..Fri
    s = pd.Series([100.0, 101.0, 102.0, 99.0, 105.0], index=idx)
    x = mod.weekly_pair(s, mod.INDIAN_TUE_SEES, mod.INDIAN_FRI_SEES)
    assert len(x) == 1
    assert np.isclose(x.iloc[0], np.log(99.0 / 100.0))  # Thursday close over Monday close


def test_weekly_pair_is_empty_not_an_error_without_the_weekdays() -> None:
    s = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    assert mod.weekly_pair(s, 1, 4).empty
    assert mod.later_cheaper_test(mod.weekly_pair(s, 1, 4), 0.05, 1)["passes"] is False


def test_later_cheaper_test_is_one_sided() -> None:
    cheaper = mod.later_cheaper_test(
        pd.Series(np.full(200, -0.003) + np.random.default_rng(1).normal(0, 0.01, 200)), 0.025, 1
    )
    dearer = mod.later_cheaper_test(
        pd.Series(np.full(200, 0.003) + np.random.default_rng(1).normal(0, 0.01, 200)), 0.025, 1
    )
    assert cheaper["passes"] and cheaper["p_one_sided"] < 0.025
    assert not dearer["passes"] and dearer["p_one_sided"] > 0.9
    assert (
        mod.later_cheaper_test(pd.Series([0.01] * 5), 0.05, 1)["passes"] is False
    )  # too few weeks


def test_frozen_constants() -> None:
    assert mod.REGISTERED_AFTER == "2026-09-24"
    assert mod.FORWARD_MIN_WEEKS == 52
    assert mod.ALPHA_PRIMARY == 0.025
    assert (mod.INDIAN_TUE_SEES, mod.INDIAN_FRI_SEES) == (0, 3)
