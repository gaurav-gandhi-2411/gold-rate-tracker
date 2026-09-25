"""Tests for the E3 timed-visit analysis, schedule config and forward-measurement script."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


an = _load("analysis_tanishq_update_times")
vm = _load("tanishq_visit_metrics")
IST = timedelta(hours=5, minutes=30)


def _ist(y, mo, d, h, mi) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC) - IST


# ── analysis: captures, change intervals, day types ──────────────────────────


def test_captures_exclude_backfill_and_dedupe():
    entries = [
        {
            "timestamp": "2026-09-01T04:00:00Z",
            "22k": 1,
            "24k": 2,
            "18k": 3,
            "source": "x (history backfill)",
        },
        {"timestamp": "2026-09-01T05:00:00Z", "22k": 1, "24k": 2, "18k": 3, "source": "https://t"},
        {
            "timestamp": "2026-09-01T05:00:00.000Z",
            "22k": 1,
            "24k": 2,
            "18k": 3,
            "source": "https://t",
        },
    ]
    caps = an.captures_from_entries(entries)
    assert len(caps) == 1


def test_change_intervals_detect_any_karat_change():
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    caps = [
        an.Capture(t0, (10, 20, 30)),
        an.Capture(t0 + timedelta(hours=3), (10, 20, 30)),
        an.Capture(t0 + timedelta(hours=6), (10, 21, 30)),
    ]
    assert an.change_intervals(caps) == [(caps[1].ts, caps[2].ts)]


def test_day_types_use_ist_calendar():
    # Friday 20:00 UTC = Saturday 01:30 IST
    assert an.day_type(datetime(2026, 9, 25, 20, 0, tzinfo=UTC)) == "saturday"
    assert an.interval_day_type(_ist(2026, 9, 25, 10, 0), _ist(2026, 9, 26, 9, 0)) == "mixed"
    assert an.interval_day_type(_ist(2026, 9, 22, 20, 0), _ist(2026, 9, 23, 9, 0)) == "weekday"


# ── Turnbull ─────────────────────────────────────────────────────────────────


def test_turnbull_concentrates_on_intersection():
    # three overlapping intervals whose common part is 10:30-11:00
    arcs = [(600.0, 60.0), (630.0, 60.0), (570.0, 90.0)]
    alpha = an.arc_matrix(arcs)
    p = an.turnbull(alpha)
    assert p.sum() == pytest.approx(1.0)
    assert p[630:660].sum() > 0.99
    # uniform inside the innermost interval (placement not identifiable)
    assert np.allclose(p[630:660], p[630:660].mean(), rtol=1e-6)


def test_arc_matrix_wraps_midnight():
    m = an.arc_matrix([(1400.0, 100.0)])
    assert m[0, 1430] and m[0, 30] and not m[0, 100]


def test_edge_placement_puts_mass_on_group_edges():
    arcs = [(600.0, 60.0), (630.0, 60.0)]
    alpha = an.arc_matrix(arcs)
    p = an.turnbull(alpha)
    groups = an.innermost_groups(alpha, p)
    left = an.edge_placement(p, groups, "left")
    right = an.edge_placement(p, groups, "right")
    assert left.sum() == pytest.approx(1) and right.sum() == pytest.approx(1)
    assert np.argmax(left) < np.argmax(right)


# ── staleness ────────────────────────────────────────────────────────────────


def test_staleness_fixed_next_visit_and_wraparound():
    updates = np.array([600.0, 1430.0])
    visits = [620.0, 900.0]
    lat = np.zeros((2, 6))
    miss = np.zeros((2, 6), dtype=bool)
    s = an.staleness_fixed(updates, visits, lat, miss)
    assert s[0] == pytest.approx(20.0)
    assert s[1] == pytest.approx(1440 + 620 - 1430)


def test_staleness_fixed_late_visit_still_captures_and_miss_skips():
    updates = np.array([625.0])
    lat = np.full((1, 6), 10.0)  # visit at 620 actually happens at 630
    miss = np.zeros((1, 6), dtype=bool)
    assert an.staleness_fixed(updates, [620.0, 900.0], lat, miss)[0] == pytest.approx(5.0)
    miss[0, 0] = True
    assert an.staleness_fixed(updates, [620.0, 900.0], lat, miss)[0] == pytest.approx(285.0)


def test_coordinate_costs_match_direct_evaluation():
    rng = np.random.default_rng(0)
    p = np.zeros(1440)
    p[600:700] = 1
    crn = an.make_crn(p / p.sum(), np.array([3.0, 6.0]), 0.2, 3, rng)
    crn = an.CRN(crn.u[:500], crn.lat[:500], crn.miss[:500])
    visits = [480.0, 690.0, 1000.0]
    grid = np.array([600.0, 705.0])
    costs = an.coordinate_costs(crn, visits, 1, grid)
    for g, c in zip(grid, costs, strict=True):
        trial = [480.0, float(g), 1000.0]
        assert c == pytest.approx(an.staleness_fixed(crn.u, trial, crn.lat, crn.miss).mean())


def test_feasibility_constraints():
    assert an.feasible([480.0, 660.0, 960.0, 1380.0], None)
    assert not an.feasible([600.0, 660.0], None)  # no morning visit
    assert not an.feasible([480.0, 660.0, 960.0], float(an.MAX_GAP_MIN))  # 960->480 = 16 h gap


def test_max_gap_constant_tracks_inference_stale_gate():
    src = (ROOT / "ml" / "inference.py").read_text(encoding="utf-8")
    hours = int(re.search(r"^_STALE_THRESHOLD_H: int = (\d+)", src, re.M).group(1))
    assert hours * 60 == an.STALE_GATE_MIN


def test_preceding_slot_delay():
    slots = an.cron_slots(
        7, range(0, 24, 3), datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 2, tzinfo=UTC)
    )
    assert len(slots) == 8
    assert an.preceding_slot_delay(datetime(2026, 9, 1, 4, 37, tzinfo=UTC), slots) == pytest.approx(
        90.0
    )


# ── schedule config vs workflow cron ─────────────────────────────────────────


def _schedule() -> dict:
    return json.loads((ROOT / "scraper" / "visit_schedule.json").read_text(encoding="utf-8"))


def test_schedule_has_5_or_6_visits_and_a_morning_visit():
    sch = _schedule()
    mins = [int(h) * 60 + int(m) for h, m in (v.split(":") for v in sch["visits_ist"])]
    assert 5 <= len(mins) <= 6
    assert mins == sorted(mins)
    assert any(an.MORNING_WINDOW[0] <= x <= an.MORNING_WINDOW[1] for x in mins)


def test_workflow_cron_matches_schedule_in_utc():
    wf = (ROOT / ".github" / "workflows" / "scrape-tanishq-selfhosted.yml").read_text(
        encoding="utf-8"
    )
    crons = re.findall(r'^\s*- cron: "([^"]+)"', wf, re.M)
    got = set()
    for c in crons:
        minute, hour, *_ = c.split()
        got.add((int(hour), int(minute)))
    want = set()
    for v in _schedule()["visits_ist"]:
        h, m = (int(x) for x in v.split(":"))
        utc = (h * 60 + m - 330) % 1440
        want.add((utc // 60, utc % 60))
    assert got == want


def test_workflow_skips_github_cron_when_task_scheduler_enabled():
    wf = (ROOT / ".github" / "workflows" / "scrape-tanishq-selfhosted.yml").read_text(
        encoding="utf-8"
    )
    assert "vars.TANISHQ_TRIGGER != 'task_scheduler'" in wf
    # browser settings untouched (GG decision E3)
    js = (ROOT / "scraper" / "scrape.js").read_text(encoding="utf-8")
    assert "--disable-blink-features=AutomationControlled" in js


# ── forward measurement ──────────────────────────────────────────────────────


def _price(t: datetime, v: int) -> dict:
    return {
        "timestamp": t.isoformat().replace("+00:00", "Z"),
        "22k": v,
        "24k": v + 1,
        "18k": v - 1,
        "source": "https://t",
    }


def test_visit_metrics_changes_slots_and_blocks():
    sched = {"visits_ist": ["08:00", "11:15"], "tolerance_min": 45}
    prices = [
        _price(_ist(2026, 10, 5, 8, 4), 100),
        _price(_ist(2026, 10, 5, 11, 20), 105),  # change, width 196 min
        _price(_ist(2026, 10, 6, 8, 3), 105),
        # 10-06 11:15 slot: run attempted, blocked, no capture
    ]
    outcomes = vm.load_outcomes(
        [
            json.dumps({"timestamp": "2026-10-05T02:34:00Z", "outcome": "success"}),
            json.dumps(
                {"timestamp": "2026-10-06T05:50:00Z", "outcome": "failure", "blocked": True}
            ),
            "not json",
        ]
    )
    start, end = _ist(2026, 10, 5, 0, 0), _ist(2026, 10, 7, 0, 0)
    r = vm.compute(vm.load_captures(prices), outcomes, sched, start, end)
    assert r["changes_seen"] == 1
    assert r["changes"][0]["staleness_min_bounds"] == [0.0, 196.0]
    assert r["changes"][0]["ist_day"] == "2026-10-05"
    assert r["share_changes_captured_within_60min_lower_bound"] == 0.0
    assert r["slots"] == {
        "scheduled": 4,
        "captured": 3,
        "attempted_no_capture": 1,
        "missed": 0,
        "lateness_min_median": 4.0,
        "lateness_min_max": 5.0,
    }
    assert r["runs"]["blocked_or_challenged"] == 1


def test_visit_metrics_missed_slot_when_nothing_ran():
    sched = {"visits_ist": ["08:00"], "tolerance_min": 45}
    r = vm.compute([], [], sched, _ist(2026, 10, 5, 0, 0), _ist(2026, 10, 6, 0, 0))
    assert r["slots"]["missed"] == 1 and r["changes_seen"] == 0
