"""Tests for scripts/analysis_scorecard.py's statistics helpers (item 0). No data."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "analysis_scorecard.py"
_spec = importlib.util.spec_from_file_location("analysis_scorecard", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["analysis_scorecard"] = mod
_spec.loader.exec_module(mod)


def test_wilson_and_exact_binomial() -> None:
    lo, hi = mod.wilson(46, 63)
    assert lo == pytest.approx(0.6097, abs=1e-3) and hi == pytest.approx(0.8242, abs=1e-3)
    assert mod.wilson(0, 0) is None
    assert mod.p_below(80, 100) > 0.5  # exactly on target is not "below"
    assert mod.p_below(60, 100) < 0.001


def test_hac_interval_widens_with_positive_autocorrelation() -> None:
    rng = np.random.default_rng(42)
    iid = rng.normal(size=2000)
    ar = np.empty(2000)
    ar[0] = 0.0
    for i in range(1, 2000):
        ar[i] = 0.8 * ar[i - 1] + rng.normal()
    w_iid = np.diff(mod.hac_mean_ci(iid)["ci95"])[0]
    w_ar = np.diff(mod.hac_mean_ci(ar)["ci95"])[0]
    assert w_ar / np.std(ar) > w_iid / np.std(iid)


def test_day_type_split() -> None:
    dates = pd.Series(pd.to_datetime(["2026-09-24", "2026-09-26", "2026-09-25"]))
    gaps = pd.Series([0, 1, 1])
    assert mod.day_type(dates, gaps).tolist() == ["ibja_day", "weekend", "weekday_no_ibja"]


def test_point_block_reports_same_days_and_one_sided_test() -> None:
    rng = np.random.default_rng(42)
    y = 14000 + rng.normal(0, 50, 60)
    preds = {"m": y + rng.normal(0, 20, 60), "b": y + rng.normal(0, 80, 60)}
    strata = pd.Series(["ibja_day"] * 40 + ["weekend"] * 20)
    out = mod.point_block(y, preds, "m", strata)
    assert out["all"]["n"] == 60 and out["weekend"]["n"] == 20
    assert out["all"]["m"]["mean"] < out["all"]["b"]["mean"]
    assert out["all"]["m_vs_b"]["p_one_sided"] < 0.01
