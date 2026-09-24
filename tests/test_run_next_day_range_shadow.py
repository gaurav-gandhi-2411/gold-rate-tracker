"""Tests for scripts/run_next_day_range_shadow.py (ADR 047). Synthetic data only."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run_next_day_range_shadow.py"
_spec = importlib.util.spec_from_file_location("run_next_day_range_shadow", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["run_next_day_range_shadow"] = mod
_spec.loader.exec_module(mod)


def test_stats_empty_is_all_none() -> None:
    s = mod._stats([], [])
    assert s == {
        "n": 0,
        "k": 0,
        "coverage": None,
        "wilson95": [None, None],
        "p_below_80": None,
        "mean_width": None,
    }


def test_stats_basic_coverage_and_width() -> None:
    hits = [True, True, True, True, False]  # 4/5 = 0.8
    widths = [10.0, 20.0, 30.0, 40.0, 50.0]
    s = mod._stats(hits, widths)
    assert s["n"] == 5
    assert s["k"] == 4
    assert s["coverage"] == pytest.approx(0.8)
    assert s["mean_width"] == pytest.approx(30.0)
    assert 0.0 <= s["p_below_80"] <= 1.0


def test_by_stratum_splits_mon_thu_and_fri_sun() -> None:
    rows = [
        {
            "decision_date": "2026-09-14",  # Monday
            "hit_v2": True,
            "width_v2": 1.0,
            "hit_displayed": False,
            "width_displayed": 2.0,
        },
        {
            "decision_date": "2026-09-18",  # Friday
            "hit_v2": False,
            "width_v2": 3.0,
            "hit_displayed": True,
            "width_displayed": 4.0,
        },
    ]
    out = mod._by_stratum(rows)
    assert out["mon_thu"]["v2"]["n"] == 1
    assert out["fri_sun"]["v2"]["n"] == 1
    assert out["mon_thu"]["v2"]["k"] == 1
    assert out["fri_sun"]["displayed"]["k"] == 1


def test_block_success_flags_on_a_clean_win() -> None:
    # 9/10 hits (90%, Wilson CI comfortably contains 80%), narrower than displayed's 5/10 (50%).
    rows = [
        {
            "decision_date": f"2026-09-{i:02d}",
            "hit_v2": i != 1,
            "width_v2": 10.0,
            "hit_displayed": i % 2 == 0,
            "width_displayed": 10.0,
        }
        for i in range(1, 11)
    ]
    block = mod._block(rows)
    assert block["n"] == 10
    assert block["success"]["coverage_ge_displayed"] is True
    assert block["success"]["width_not_more_than_25pct_wider"] is True


def test_block_empty_has_no_success_verdict() -> None:
    block = mod._block([])
    assert block["n"] == 0
    assert block["success"] is None


def test_score_one_uses_metrics_history_price_not_ibja_price() -> None:
    rng = np.random.default_rng(7)
    pdates = pd.bdate_range("2023-01-02", periods=700)
    proxy = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 700))), index=pdates)
    idates = pdates[-150:]
    ibja = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.008, 150))), index=idates)
    d = ibja.index[-5]
    entry = {
        "decision_date": d.strftime("%Y-%m-%d"),
        "current_22k": 14000.0,  # deliberately far from the synthetic IBJA level
        "actual_next_22k": 14050.0,
        "lower": 13900.0,
        "upper": 14100.0,
    }
    row = mod._score_one(proxy, ibja, entry)
    assert row is not None
    assert row["current_22k"] == 14000.0
    # v2's range must be centred near 14000 (current_22k), not near the synthetic IBJA price.
    assert 13000.0 < row["lo_v2"] < 14000.0 < row["hi_v2"] < 15000.0
