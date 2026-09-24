"""Tests for scripts/analysis_nowcast.py (R2). Synthetic data only."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "analysis_nowcast.py"
_spec = importlib.util.spec_from_file_location("analysis_nowcast", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["analysis_nowcast"] = mod
_spec.loader.exec_module(mod)


def test_weighted_median() -> None:
    assert mod.weighted_median(np.array([1.0, 2.0, 3.0]), np.ones(3)) == 2.0
    assert mod.weighted_median(np.array([1.0, 2.0, 3.0]), np.array([0.1, 0.1, 5.0])) == 3.0


def _frame(n: int = 60) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    pm = 14000 + np.cumsum(rng.normal(0, 30, n))
    return pd.DataFrame(
        {
            "date": dates,
            "t22": pm * 1.017,
            "pm": pm,
            "am": pm + rng.normal(0, 5, n),
            "pm_prev": np.r_[np.nan, pm[:-1]],
            "gap_days": 0,
            "move": 1.0,
        }
    )


def test_predictions_never_use_the_scored_day_or_later() -> None:
    m = _frame()
    base = mod.predict_all(m)
    m2 = m.copy()
    m2.loc[45:, "t22"] = m2.loc[45:, "t22"] * 2  # change truth from day 45 on
    changed = mod.predict_all(m2)
    for name in base:
        np.testing.assert_allclose(base[name][:46], changed[name][:46], equal_nan=True)


def test_exact_ratio_is_recovered() -> None:
    m = _frame()
    p = mod.predict_all(m)
    ok = np.isfinite(p["M1_ratio"])
    assert ok.sum() == len(m) - mod.MIN_TRAIN
    assert np.allclose(p["M1_ratio"][ok], m["t22"].to_numpy()[ok])
    assert np.abs(p["M0_current"][ok] - m["t22"].to_numpy()[ok]).max() == pytest.approx(0, abs=5)
