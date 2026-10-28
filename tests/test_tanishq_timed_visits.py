"""Tests for the E3 timed-visit analysis, schedule config and forward-measurement script."""

from __future__ import annotations

import importlib.util
import itertools
import json
import re
import shutil
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


def test_schedule_is_the_mixed_variant_a_six_visits():
    # GG decision 3a (2026-09-26, replaces 4a): six visits across morning and afternoon, the
    # section 4 variant A schedule; every gap under the 8 h freshness gate, 15 min minimum spacing
    sch = _schedule()
    assert sch["mode"] == "mixed"
    mins = sr.to_minutes(sch["visits_ist"])
    assert len(mins) == 6
    assert mins == sorted(mins)
    assert mins == sr.INTERIM
    assert sr.mixed_feasible(mins)
    assert sr.not_fresh_hours(mins, 0.0, float(an.STALE_GATE_MIN)) == 0.0


def test_mixed_feasible_rejects_long_gaps_close_visits_and_no_early_visit():
    assert sr.mixed_feasible(sr.INTERIM)
    assert not sr.mixed_feasible(sr.U30)  # 12:30 -> 10:00 is a 21.5 h gap
    assert not sr.mixed_feasible([100.0, 450.0, 640.0, 650.0, 935.0, 1190.0])  # 10 min apart
    assert not sr.mixed_feasible([100.0, 560.0, 640.0, 670.0, 935.0, 1190.0])  # none in 07-09


def test_optimise_mixed_returns_a_feasible_schedule_no_worse_than_its_start():
    rng = np.random.default_rng(0)
    p = np.zeros(an.DAY)
    p[640:660] = 0.7
    p[930:950] = 0.3
    crn = an.make_crn(p / p.sum(), np.array([2.0]), 0.0, 6, rng)
    crn = an.CRN(crn.u[:500], crn.lat[:500], crn.miss[:500])
    vs = sr.optimise_mixed(crn, sr.INTERIM, np.random.default_rng(1), starts=3)
    assert sr.mixed_feasible(vs)
    assert (
        float(an.staleness_fixed(crn.u, vs, crn.lat, crn.miss).mean())
        <= float(an.staleness_fixed(crn.u, sr.INTERIM, crn.lat, crn.miss).mean()) + 1e-9
    )


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


def test_dispatcher_side_tasks_run_after_the_visit_and_cannot_change_the_result():
    ps1 = (ROOT / "scripts" / "win" / "tanishq_dispatch.ps1").read_text(encoding="utf-8")
    assert ps1.index('$result = "DISPATCHED"') < ps1.index("# --- Side tasks")
    tail = ps1.split("# --- Side tasks", 1)[1]
    assert "gh workflow run ci-health.yml" in tail  # second trigger for the CI health monitor
    assert "laptop_attribution.py" in tail and "$LogDir" in tail  # result stays off the checkout
    assert (
        "data/laptop_attribution.json" in tail
    )  # only fetched from master as a seed, never written
    assert "ROOT" not in tail and "..\\..\\data" not in tail
    assert tail.rstrip().endswith("exit 0")  # a failed side task never fails the visit
    assert "UTF8Encoding $false" in tail  # no BOM for the JSON the Python script reads
    assert "Stop-Process -Id $p.Id" in tail and "-Name" not in tail  # kill by PID only
    assert "side_tasks.log" in tail  # the dispatcher log keeps one kind of line


def test_dispatcher_does_not_count_skipped_cron_runs_as_recent_visits():
    ps1 = (ROOT / "scripts" / "win" / "tanishq_dispatch.ps1").read_text(encoding="utf-8")
    assert "--json createdAt,status,conclusion" in ps1
    guard = ps1.split("foreach ($r in $recent) {", 1)[1]
    assert guard.index('$r.conclusion -eq "skipped"') < guard.index("SKIP recent run")


def test_dispatcher_survives_a_failing_gh_call_and_always_logs():
    # 2026-10-12 01:40 IST: PS 5.1 + $ErrorActionPreference Stop + 2>$null made the first failing
    # gh call a terminating error: exit 1, no log line, no retry of the network wait.
    ps1 = (ROOT / "scripts" / "win" / "tanishq_dispatch.ps1").read_text(encoding="utf-8")
    main = ps1.split("if (-not $SideTasksOnly) {", 1)[1].split("# --- Side tasks", 1)[0]
    assert main.index('$ErrorActionPreference = "Continue"') < main.index("gh api rate_limit")
    assert main.rindex('$ErrorActionPreference = "Stop"') > main.index('$result = "DISPATCHED"')
    assert "gh run list" in main and "SKIP cannot list recent runs" in main
    assert main.index("SKIP empty outcomes log") < main.index("ConvertFrom-Json")  # no fail-open
    assert "trap {" in ps1 and "FAIL unexpected" in ps1  # an unforeseen error is logged, not silent


def test_dispatcher_traces_its_phases_in_a_separate_file_the_attribution_never_reads():
    # 2026-10-22 20:55Z: six catch-up processes after a boot registered, then ended 0xC000013A with no
    # outcome line. The trace shows the last phase a killed process reached; it must stay out of the
    # outcome log, which laptop_attribution.py reads line by line as outcomes.
    ps1 = (ROOT / "scripts" / "win" / "tanishq_dispatch.ps1").read_text(encoding="utf-8")
    assert '$Trace = Join-Path $LogDir "dispatch_trace.log"' in ps1
    fn = ps1.split("function Write-Trace", 1)[1].split("\n}", 1)[0]
    assert "$Trace" in fn and "$Log" not in fn.replace("$LogDir", "")
    assert ps1.index('Write-Trace ("START') < ps1.index("trap {") < ps1.index("gh api rate_limit")
    for phase in ('"ONLINE"', '"WAIT lock"', '"LOCK acquired', '"LEADER listing'):
        assert phase in ps1, phase
    attribution = (ROOT / "scripts" / "laptop_attribution.py").read_text(encoding="utf-8")
    assert "dispatch_trace" not in attribution


def test_every_exit_leaves_a_last_trace_line_and_start_records_time_since_boot():
    # 2026-10-28: a follower covered by its leader exited with no final trace line (only the leader wrote DONE),
    # and slot 11:10's START was lost to a sibling's write at the same instant (the error was swallowed).
    ps1 = (ROOT / "scripts" / "win" / "tanishq_dispatch.ps1").read_text(encoding="utf-8")
    log_fn = ps1.split("function Write-Log", 1)[1].split("\n}", 1)[0]
    assert 'Write-Trace ("LOG {0}" -f $Msg)' in log_fn  # every outcome line, on every exit path, ends the trace
    assert ps1.index("function Write-Trace") < ps1.index("function Write-Log")
    trace_fn = ps1.split("function Write-Trace", 1)[1].split("\n}", 1)[0]
    assert "for ($i = 0; $i -lt 20; $i++)" in trace_fn and "Start-Sleep -Milliseconds" in trace_fn
    assert "AppendAllText($Trace" in trace_fn
    assert "START since_boot_s={0} session={1}" in ps1
    head = ps1.split('Write-Trace ("START', 1)[0]
    assert "Get-CimInstance" not in head and "Id = 12" in head  # boot time from the System log, not WMI
    assert 'Write-Trace ("DONE' not in ps1  # replaced by the LOG mirror, which every path reaches


# --- one dispatch per wake (2026-10-14/15: four dispatches, two cancelled by the concurrency group) ------
def test_dispatcher_serialises_the_slots_of_one_wake_and_logs_the_batch():
    ps1 = (ROOT / "scripts" / "win" / "tanishq_dispatch.ps1").read_text(encoding="utf-8")
    main = ps1.split("if (-not $SideTasksOnly) {", 1)[1].split("# --- Side tasks", 1)[0]
    assert "System.Threading.Mutex" in main and "Global\\gold-rate-tracker-tanishq-dispatch" in main
    assert main.index("dispatch_pending.log") < main.index(
        "WaitOne"
    )  # register, then take the lock
    assert main.index("gh run list") > main.index(
        "WaitOne"
    )  # the recent-run check is under the lock
    assert main.count("gh workflow run $Workflow") == 1  # one dispatch site, for the whole batch
    assert "batch of {0}, led by slot {1}; covers {2}" in main  # the log says which slots it covers
    assert "AbandonedMutexException" in main and "finally" in main  # a crashed holder never blocks
    assert "-StartWhenAvailable" in (
        ROOT / "scripts" / "win" / "register_tanishq_tasks.ps1"
    ).read_text(encoding="utf-8")  # the reason several slots start together after a wake


def _run_dispatchers(tmp_path, slots, run_list_json):
    """Start one real dispatcher process per slot at the same moment, with a stub `gh` on PATH."""
    import os
    import subprocess

    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "gh_calls.txt"
    (bindir / "gh.cmd").write_text(
        "@echo off\r\n"
        f'echo %* >> "{calls}"\r\n'
        'if "%1"=="api" if "%2"=="rate_limit" exit /b 0\r\n'
        'if "%1"=="api" (echo {"timestamp":"2026-01-01T00:00:00Z","outcome":"success","blocked":false}& exit /b 0)\r\n'
        f'if "%1"=="run" (echo {run_list_json}& exit /b 0)\r\n'
        'if "%1"=="workflow" exit /b 0\r\n'
        "exit /b 1\r\n",
        encoding="ascii",
    )
    env = {**os.environ, "LOCALAPPDATA": str(tmp_path), "PATH": f"{bindir};{os.environ['PATH']}"}
    ps1 = str(ROOT / "scripts" / "win" / "tanishq_dispatch.ps1")
    procs = [
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                ps1,
                "-Slot",
                s,
                "-SettleSec",
                "20",
                "-LateAfterSec",
                "0",
                "-SkipSideTasks",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for s in slots
    ]
    codes = [pr.wait(timeout=240) for pr in procs]
    log = (tmp_path / "gold-rate-tracker" / "tanishq_dispatch.log").read_text(encoding="ascii")
    ghcalls = calls.read_text(encoding="ascii") if calls.exists() else ""
    return codes, [ln for ln in log.splitlines() if ln.strip()], ghcalls


_WIN = sys.platform == "win32" and shutil.which("powershell.exe") is not None


@pytest.mark.skipif(not _WIN, reason="runs the real Windows PowerShell dispatcher")
def test_four_slots_started_together_dispatch_once_and_each_is_logged(tmp_path):
    slots = ["07:30", "10:40", "11:10", "15:35"]
    codes, lines, ghcalls = _run_dispatchers(tmp_path, slots, "[]")
    assert codes == [0, 0, 0, 0]
    assert sum(1 for c in ghcalls.splitlines() if c.startswith("workflow run")) == 1
    assert len(lines) == 4
    for s in slots:
        mine = [ln for ln in lines if f"slot={s} " in ln]
        assert len(mine) == 1 and " DISPATCHED (batch of 4, led by slot " in mine[0]
        assert "covers " in mine[0] and all(x in mine[0].split("covers ")[1] for x in slots)


@pytest.mark.skipif(not _WIN, reason="runs the real Windows PowerShell dispatcher")
def test_a_single_slot_still_dispatches_once_with_the_plain_line(tmp_path):
    codes, lines, ghcalls = _run_dispatchers(tmp_path, ["19:50"], "[]")
    assert codes == [0] and len(lines) == 1 and lines[0].endswith("slot=19:50 DISPATCHED")
    assert sum(1 for c in ghcalls.splitlines() if c.startswith("workflow run")) == 1


@pytest.mark.skipif(not _WIN, reason="runs the real Windows PowerShell dispatcher")
def test_when_a_run_is_already_in_progress_the_whole_batch_skips_and_says_so(tmp_path):
    running = '[{"createdAt":"2026-01-01T00:00:00Z","status":"in_progress","conclusion":""}]'
    codes, lines, ghcalls = _run_dispatchers(tmp_path, ["07:30", "10:40"], running)
    assert codes == [0, 0] and len(lines) == 2
    assert all(" SKIP recent run " in ln and "batch of 2" in ln for ln in lines)
    assert not any(c.startswith("workflow run") for c in ghcalls.splitlines())


def _run_with_pending(tmp_path, pending_text, slots, run_list_json="[]", gh_workflow_exit=0):
    """Like _run_dispatchers, with a prepared dispatch_pending.log and a switchable workflow-run exit."""
    import os
    import subprocess

    state = tmp_path / "gold-rate-tracker"
    state.mkdir(parents=True, exist_ok=True)
    if pending_text is not None:
        (state / "dispatch_pending.log").write_bytes(pending_text)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "gh_calls.txt"
    (bindir / "gh.cmd").write_text(
        "@echo off\r\n"
        f'echo %* >> "{calls}"\r\n'
        'if "%1"=="api" if "%2"=="rate_limit" exit /b 0\r\n'
        'if "%1"=="api" (echo {"timestamp":"2026-01-01T00:00:00Z","outcome":"success","blocked":false}& exit /b 0)\r\n'
        f'if "%1"=="run" (echo {run_list_json}& exit /b 0)\r\n'
        f'if "%1"=="workflow" exit /b {gh_workflow_exit}\r\n'
        "exit /b 1\r\n",
        encoding="ascii",
    )
    env = {**os.environ, "LOCALAPPDATA": str(tmp_path), "PATH": f"{bindir};{os.environ['PATH']}"}
    ps1 = str(ROOT / "scripts" / "win" / "tanishq_dispatch.ps1")
    procs = [
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                ps1,
                "-Slot",
                s,
                "-SettleSec",
                "20",
                "-LateAfterSec",
                "0",
                "-SkipSideTasks",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for s in slots
    ]
    codes = [pr.wait(timeout=240) for pr in procs]
    log = (state / "tanishq_dispatch.log").read_text(encoding="ascii")
    ghcalls = calls.read_text(encoding="ascii") if calls.exists() else ""
    return codes, [ln for ln in log.splitlines() if ln.strip()], ghcalls


@pytest.mark.skipif(not _WIN, reason="runs the real Windows PowerShell dispatcher")
def test_a_torn_line_in_the_pending_log_never_stops_a_dispatch(tmp_path):
    # a power loss mid-append leaves garbage; the verifier showed one bad line used to fail every later slot
    codes, lines, ghcalls = _run_with_pending(
        tmp_path, b"2026-10-15T08:5 garbage\r\n\x00\x00\x00\r\n", ["19:50"]
    )
    assert codes == [0] and lines[-1].endswith("slot=19:50 DISPATCHED")
    assert sum(1 for c in ghcalls.splitlines() if c.startswith("workflow run")) == 1


@pytest.mark.skipif(not _WIN, reason="runs the real Windows PowerShell dispatcher")
def test_a_failed_dispatch_is_retried_by_each_sibling_not_copied(tmp_path):
    # right after a wake the network is flaky: a sibling must try for itself, not adopt the leader's FAIL
    codes, lines, ghcalls = _run_with_pending(
        tmp_path, None, ["07:30", "10:40"], gh_workflow_exit=1
    )
    assert (
        codes == [1, 1] and len(lines) == 2 and all(" FAIL gh workflow run " in ln for ln in lines)
    )
    assert sum(1 for c in ghcalls.splitlines() if c.startswith("workflow run")) == 2  # each tried
