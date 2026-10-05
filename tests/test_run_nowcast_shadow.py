"""Tests for scripts/run_nowcast_shadow.py (G3 forward shadow). Synthetic data only."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run_nowcast_shadow.py"
_spec = importlib.util.spec_from_file_location("run_nowcast_shadow", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["run_nowcast_shadow"] = mod
_spec.loader.exec_module(mod)


def _fake_r2(dates: list[str], m0: list[float], m3: list[float], truth: list[float]):
    m = pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "ibja_date": pd.to_datetime(dates),
            "gap_days": [0] * len(dates),
            "t22": truth,
        }
    )
    return SimpleNamespace(
        load_truth=lambda: None,
        load_ibja=lambda: None,
        build=lambda truth, ibja, glob: m,
        predict_all=lambda frame: {"M0_current": np.array(m0), "M3_am_pm": np.array(m3)},
    )


def _after_pm(dates: list[str]) -> dict[str, str]:
    """Target readings at 14:00 UTC: after both IBJA fixes (06:30 / 11:30 UTC)."""
    return {d: f"{d}T14:00:00.000Z" for d in dates}


def test_only_completed_days_after_the_shadow_start_are_logged() -> None:
    dates = ["2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29"]
    r2 = _fake_r2(dates, [1.0] * 4, [1.0] * 4, [1.0] * 4)
    rows = mod.new_rows(
        r2, logged=set(), today_utc="2026-09-29", targets=_after_pm(dates), fetched={}
    )
    # 09-24 is the merge day (not after it); 09-29 is today (truth not final).
    assert [r["date"] for r in rows] == ["2026-09-25", "2026-09-28"]


def test_logged_days_are_never_rescored() -> None:
    r2 = _fake_r2(["2026-09-25", "2026-09-28"], [1.0, 1.0], [1.0, 1.0], [1.0, 1.0])
    rows = mod.new_rows(
        r2,
        logged={"2026-09-25"},
        today_utc="2026-10-01",
        targets=_after_pm(["2026-09-25", "2026-09-28"]),
        fetched={},
    )
    assert [r["date"] for r in rows] == ["2026-09-28"]


def test_missing_prediction_is_none_not_nan() -> None:
    r2 = _fake_r2(["2026-09-25"], [np.nan], [14000.0], [14010.0])
    row = mod.new_rows(
        r2, logged=set(), today_utc="2026-10-01", targets=_after_pm(["2026-09-25"]), fetched={}
    )[0]
    assert row["M0_current"] is None
    json.dumps(row, allow_nan=False)


def test_summary_prefers_the_better_arm() -> None:
    rng = np.random.default_rng(42)
    truth = 14000 + rng.normal(0, 10, 30)
    days = [
        {
            "date": f"2026-10-{i + 1:02d}",
            "gap_days": 0,
            "truth_rs_per_g": float(t),
            "M0_current": float(t + 50 + rng.normal(0, 5)),
            "M3_am_pm": float(t + 10 + rng.normal(0, 5)),
            "inputs_known_after_target": [],
        }
        for i, t in enumerate(truth)
    ]
    s = mod.summarise(days)
    assert s["n_same_day"] == 30
    assert s["mae_m3_rs_per_g"] < s["mae_m0_rs_per_g"]
    assert s["p_one_sided_m3_better"] < 0.05


def test_summary_with_too_few_days_reports_n_only() -> None:
    assert mod.summarise([]) == {
        "n_same_day": 0,
        "n_same_day_excluded_late_ibja": 0,
        "n_all_logged": 0,
        "n_days_input_known_after_target": 0,
    }


def test_same_day_ibja_after_the_target_is_reported_not_dropped() -> None:
    """ADR 061 F2: a target read at 08:22 UTC cannot have used that day's PM (11:30 UTC),
    nor the AM when the repo only fetched it at 13:40 UTC. Report mode: the row is still
    logged with its predictions, and the leak is recorded on it."""
    r2 = _fake_r2(["2026-09-25"], [14000.0], [14000.0], [14010.0])
    rows = mod.new_rows(
        r2,
        logged=set(),
        today_utc="2026-10-01",
        targets={"2026-09-25": "2026-09-25T08:22:07.863Z"},
        fetched={"2026-09-25": "2026-09-25T13:40:14+00:00"},
    )
    assert rows[0]["inputs_known_after_target"] == ["ibja_am", "ibja_pm"]
    assert rows[0]["M0_current"] == 14000.0
    assert mod.summarise(rows)["n_days_input_known_after_target"] == 1


def test_day_without_a_target_timestamp_is_flagged_not_passed() -> None:
    r2 = _fake_r2(["2026-09-25"], [14000.0], [14000.0], [14010.0])
    rows = mod.new_rows(r2, logged=set(), today_utc="2026-10-01", targets={}, fetched={})
    assert rows[0]["inputs_known_after_target"] == ["target_timestamp_missing"]


def _day(i: int, late: list[str] | None, m0_err: float, m3_err: float) -> dict:
    d = {
        "date": f"2026-10-{i + 1:02d}",
        "ibja_date": f"2026-10-{i + 1:02d}",
        "gap_days": 0,
        "truth_rs_per_g": 14000.0,
        "M0_current": 14000.0 + m0_err,
        "M3_am_pm": 14000.0 + m3_err,
    }
    if late is not None:
        d["inputs_known_after_target"] = late
    return d


def test_f2_late_ibja_days_are_excluded_from_the_decision_and_reported_beside_it() -> None:
    """GG decision F2: a day whose IBJA input was published after the reading it estimates
    does not count in the M3-vs-M0 decision; the unfiltered comparison is kept beside it."""
    days = [_day(i, [], 50.0 + i % 3, 10.0 + i % 2) for i in range(10)]
    days += [_day(10 + i, ["ibja_pm"], 50.0, 0.0) for i in range(3)]
    s = mod.summarise(days)
    assert s["n_same_day"] == 10
    assert s["n_same_day_excluded_late_ibja"] == 3
    assert s["last_day"] == "2026-10-10"
    assert s["all_same_day"]["n_same_day"] == 13
    assert s["all_same_day"]["mae_m3_rs_per_g"] < s["mae_m3_rs_per_g"]


def test_f2_a_day_logged_without_the_field_is_certified_from_timestamps() -> None:
    days = [_day(i, None, 50.0 + i % 3, 10.0 + i % 2) for i in range(6)]
    late_on = {"2026-10-02"}
    s = mod.summarise(days, lambda d: ["ibja_pm"] if d["date"] in late_on else [])
    assert s["n_same_day"] == 5
    assert s["n_same_day_excluded_late_ibja"] == 1


def test_f2_without_a_certifier_an_unlabelled_day_does_not_count() -> None:
    s = mod.summarise([_day(i, None, 50.0, 10.0) for i in range(5)])
    assert s["n_same_day"] == 0
    assert s["n_same_day_excluded_late_ibja"] == 5
