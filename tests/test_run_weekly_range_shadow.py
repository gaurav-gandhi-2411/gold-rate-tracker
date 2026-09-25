"""Tests for scripts/run_weekly_range_shadow.py (item 5 forward shadow). Synthetic data only."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run_weekly_range_shadow.py"
_spec = importlib.util.spec_from_file_location("run_weekly_range_shadow", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["run_weekly_range_shadow"] = mod
_spec.loader.exec_module(mod)


def _data() -> tuple[pd.Series, pd.Series]:
    rng = np.random.default_rng(42)
    pdates = pd.bdate_range("2023-01-02", periods=700)
    proxy = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 700))), index=pdates)
    idates = pdates[-150:]
    ibja = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.008, 150))), index=idates)
    return proxy, ibja


def test_issue_uses_only_data_known_on_the_day() -> None:
    proxy, ibja = _data()
    t = ibja.index[-20]
    later = ibja.copy()
    later.iloc[-19:] *= 2.0  # anything after t must not change the issued range
    assert mod._issue(proxy, ibja, "week", t) == mod._issue(proxy, later, "week", t)


def test_issued_range_contains_the_price_and_has_real_end_dates() -> None:
    proxy, ibja = _data()
    friday = next(d for d in reversed(ibja.index[:-10]) if d.dayofweek == 4)
    week = mod._issue(proxy, ibja, "week", friday)
    day = mod._issue(proxy, ibja, "1d", friday)
    assert week is not None and day is not None
    assert week["lo"] < float(ibja.loc[friday]) < week["hi"]
    assert week["window_end"] == (friday + pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    assert day["window_end"] == (friday + pd.Timedelta(days=3)).strftime("%Y-%m-%d")  # Monday


def test_score_waits_for_a_complete_window() -> None:
    proxy, ibja = _data()
    t = ibja.index[-2]
    entry = mod._issue(proxy, ibja, "week", t)
    assert entry is not None
    assert mod._score(entry, ibja) is None  # the week has not happened yet


def test_summary_reports_rounded_down_coverage() -> None:
    entries = [{"horizon": "week", "result": {"inside": i < 8}} for i in range(10)] + [
        {"horizon": "1d"}
    ]
    s = mod.summary(entries)
    assert s["week"]["n_scored"] == 10
    assert s["week"]["times_out_of_10"] == 8
    assert s["1d"] == {"n_issued": 1, "n_scored": 0}


def test_log_entries_never_carry_a_raw_ibja_rate() -> None:
    # ADR 059/060: the public log must hold only the issued range and the inside/outside verdict,
    # never the IBJA rate itself (the issue-day rate, or the scored path's low/high).
    proxy, ibja = _data()
    t = ibja.index[-20]
    for horizon in ("1d", "week"):
        entry = mod._issue(proxy, ibja, horizon, t)
        assert entry is not None
        result = mod._score(entry, ibja)
        assert result is not None
        # The rates this entry is about: the issue-day rate and every rate on the scored path.
        days = [t] + [pd.Timestamp(d) for d in result["scored_days"]]
        raw = {float(ibja.loc[d]) for d in days} | {round(float(ibja.loc[d]), 1) for d in days}
        for block in (entry, result):
            assert not {"ibja_pm_916", "path_low", "path_high", "price"} & block.keys()
            numbers = [v for v in block.values() if isinstance(v, float)]
            assert not raw & set(numbers), f"raw IBJA value leaked into {sorted(block)}"
