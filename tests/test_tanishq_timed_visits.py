"""Tests for the E3 timed-visit analysis, schedule config and forward-measurement script."""

from __future__ import annotations

import importlib.util
import itertools
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
sr = _load("tanishq_schedule_refine")
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


def test_schedule_is_six_visits_inside_the_4a_window():
    # GG decision 4a: exactly six visits, all in 10:00-12:30 IST, 15-30 min apart
    sch = _schedule()
    mins = sr.to_minutes(sch["visits_ist"])
    assert len(mins) == 6
    assert mins == sorted(mins)
    assert sr.window_feasible(mins)


def test_dedupe_and_slot_tolerance_below_the_shortest_visit_gap():
    sch = _schedule()
    mins = sr.to_minutes(sch["visits_ist"])
    shortest = min(b - a for a, b in itertools.pairwise(mins))
    ps1 = (ROOT / "scripts" / "win" / "tanishq_dispatch.ps1").read_text(encoding="utf-8")
    spacing = int(re.search(r"\[int\]\$MinSpacingMin = (\d+)", ps1).group(1))
    assert spacing < shortest
    assert sch["tolerance_min"] < shortest
    assert "CoolOffMin" in ps1 and "SKIP cannot read outcomes log" in ps1


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


# ── GG decision 4a: morning schedule and weekly refinement ───────────────────


def test_window_feasible_bounds_and_spacing():
    assert sr.window_feasible(sr.U30)
    assert not sr.window_feasible([590.0, 620.0, 650.0, 680.0, 710.0, 740.0])  # 09:50
    assert not sr.window_feasible([600.0, 610.0, 640.0, 670.0, 700.0, 730.0])  # 10 min gap
    assert not sr.window_feasible([600.0, 640.0, 670.0, 700.0, 730.0, 750.0])  # 40 min gap
    assert sr.window_feasible([600.0, 610.0, 640.0, 670.0, 700.0, 730.0], min_step=5)


def test_not_fresh_hours():
    # last visit 12:30, next 10:00: 1290 min gap, minus the 480 min gate = 13.5 h
    assert sr.not_fresh_hours(sr.U30, 0.0, 480.0) == pytest.approx(13.5)
    assert sr.not_fresh_hours(sr.INTERIM, 0.0, 480.0) == pytest.approx(0.0)


def test_arc_helpers_wrap_midnight():
    assert sr.arc_contains((1380.0, 180.0), 100.0)  # 23:00 + 3 h covers 01:40
    assert not sr.arc_contains((600.0, 60.0), 100.0)
    assert sr.arc_inside_overnight((1380.0, 180.0))
    assert not sr.arc_inside_overnight((1380.0, 700.0))  # runs past 09:59


def test_forward_counts():
    eff = _ist(2026, 10, 5, 0, 0)
    ivs = [
        (_ist(2026, 10, 4, 10, 0), _ist(2026, 10, 4, 10, 30)),  # before the switch
        (_ist(2026, 10, 5, 10, 30), _ist(2026, 10, 5, 10, 45)),  # in window, 15 min
        (_ist(2026, 10, 5, 12, 30), _ist(2026, 10, 6, 10, 0)),  # outside window
    ]
    assert sr.forward_counts(ivs, eff) == {
        "changes": 2,
        "bracketed_le_30min": 1,
        "first_seen_at_first_visit_of_day": 1,
    }
    assert sr.forward_counts(ivs, None)["changes"] == 0


def test_wilson_lower():
    assert sr.wilson_lower(0, 0) == 0.0
    assert sr.wilson_lower(10, 20) == pytest.approx(0.2993, abs=1e-3)


def test_decide_follows_the_preregistered_rule():
    from datetime import date

    today = date(2026, 12, 1)
    few = {"changes": 10, "bracketed_le_30min": 10, "first_seen_at_first_visit_of_day": 1}
    many = {"changes": 30, "bracketed_le_30min": 25, "first_seen_at_first_visit_of_day": 5}
    assert sr.decide(few, 20.0, 5.0, None, today)["status"] == "WAIT_FOR_DATA"
    assert sr.decide(many, 20.0, 5.0, None, today)["status"] == "PROPOSE"
    assert sr.decide(many, 4.0, 1.0, None, today)["status"] == "KEEP"  # below 5 min
    assert sr.decide(many, 20.0, -1.0, None, today)["status"] == "KEEP"  # CI crosses 0
    assert sr.decide(many, 20.0, 5.0, date(2026, 11, 20), today)["status"] == (
        "HOLD_4_WEEK_SPACING"
    )
    assert not sr.decide(many, 0.0, 0.0, None, today)["escalate_window_misses_changes"]
    out = {"changes": 30, "bracketed_le_30min": 20, "first_seen_at_first_visit_of_day": 20}
    assert sr.decide(out, 0.0, 0.0, None, today)["escalate_window_misses_changes"]


def test_optimise_window_returns_a_feasible_schedule():
    rng = np.random.default_rng(0)
    p = np.zeros(an.DAY)
    p[640:660] = 1.0
    crn = an.make_crn(p / p.sum(), np.array([2.0]), 0.0, 6, rng)
    crn = an.CRN(crn.u[:500], crn.lat[:500], crn.miss[:500])
    vs = sr.optimise_window(crn, np.random.default_rng(1), starts=3)
    assert sr.window_feasible(vs)
    assert (
        float(an.staleness_fixed(crn.u, vs, crn.lat, crn.miss).mean())
        <= float(an.staleness_fixed(crn.u, sr.U30, crn.lat, crn.miss).mean()) + 1e-9
    )
