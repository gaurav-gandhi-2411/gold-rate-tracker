"""Tanishq morning visit schedule (GG decision 4a): one-off evaluation and weekly refinement.

GG decision 4a: six Tanishq visits a day, all between 10:00 and 12:30 IST, where 72% of rate
changes land (docs/TANISHQ_TIMED_VISITS.md, section 3). The rules used here were pre-registered
in docs/TANISHQ_TIMED_VISITS.md section 9 before any of these numbers were computed.

  evaluate  One-off. Answers "keep the 01:40 visit?", picks the six times (U30 vs the in-window
            optimum O), and reports staleness with bootstrap CIs against today's captures and the
            section 4 interim schedule. Writes
            reports/tanishq_update_times/morning_schedule_evaluation.json.
  weekly    Run by hand once a week after the switch. Refits the update-time distribution on
            every capture so far (history plus the new, denser captures), prints a recommended
            schedule and whether the pre-registered rule says to PROPOSE it to GG. It never edits
            scraper/visit_schedule.json or the workflow crons: a schedule change is GG's.

Timestamp convention: data/prices.json `timestamp` is the UTC capture instant set by
scraper/scrape.js after a successful parse. Times of day here are IST.

Usage:
  python scripts/tanishq_schedule_refine.py evaluate [--boot 500]
  python scripts/tanishq_schedule_refine.py weekly [--boot 200] [--last-proposal 2026-11-02]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from types import ModuleType

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = REPO_ROOT / "reports" / "tanishq_update_times" / "morning_schedule_evaluation.json"
RUNS_DIR = REPO_ROOT / "reports" / "tanishq_update_times" / "gh_runs"
SCHEDULE = REPO_ROOT / "scraper" / "visit_schedule.json"
# The captures sections 2-4 were computed on (commit of the E3 update-time analysis), so the
# 4a evaluation uses exactly the same data.
PRICES_REV = "bb38f8d5"

WINDOW = (10 * 60, 12 * 60 + 30)  # GG decision 4a: every visit in 10:00-12:30 IST
K = 6
MAX_STEP_MIN = 30  # every in-window change bracketed to <= 30 min
# A visit occupies the self-hosted runner from dispatch to the bot-PR merge: created->completed
# median 6.0 min, p95 12.6 min over the 75 successful runs that finished within 45 min
# (reports/tanishq_update_times/gh_runs/runs_sh.json). The dispatcher skips a slot while a run is
# in progress, so visits closer than this cannot both happen. Deviation 1 in section 9.
MIN_STEP_MIN = 15
U30 = [600.0, 630.0, 660.0, 690.0, 720.0, 750.0]  # 10:00 ... 12:30
INTERIM = [100.0, 450.0, 640.0, 670.0, 935.0, 1190.0]  # section 4, A K=6
MORNING = (600, 720)  # changes dated 10:00-11:59
AFTERNOON = (840, 1200)  # changes dated 14:00-19:59
NEAR_0140 = (40, 160)  # 01:40 +- 60 min
MIN_IMPROVEMENT_MIN = 5.0
MIN_BRACKETED = 20
OUTSIDE_SHARE_LIMIT = 0.38
PROPOSAL_SPACING_DAYS = 28
BOOT_DRAWS = 4000  # Monte Carlo draws per bootstrap replicate, as in section 4


def _load_analysis() -> ModuleType:
    name = "analysis_tanishq_update_times"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


an = _load_analysis()
DAY = an.DAY
IST = an.IST


# ── schedule helpers ─────────────────────────────────────────────────────────


def hhmm(minute: float) -> str:
    return str(an.fmt(minute))


def to_minutes(times: Sequence[str]) -> list[float]:
    return [float(int(h) * 60 + int(m)) for h, m in (t.split(":") for t in times)]


def window_feasible(visits: Sequence[float], min_step: float = MIN_STEP_MIN) -> bool:
    """All visits inside the 4a window, consecutive gaps in [min_step, MAX_STEP_MIN]."""
    v = sorted(visits)
    if len(set(v)) != len(v) or not all(WINDOW[0] <= x <= WINDOW[1] for x in v):
        return False
    return all(min_step <= b - a <= MAX_STEP_MIN for a, b in pairwise(v))


def _random_start(rng: np.random.Generator, min_step: float) -> list[float]:
    steps = np.arange(max(min_step, an.GRID), MAX_STEP_MIN + 1, an.GRID)
    for _ in range(1000):
        gaps = rng.choice(steps, size=K - 1)
        span = float(gaps.sum())
        if span > WINDOW[1] - WINDOW[0]:
            continue
        v0 = WINDOW[0] + an.GRID * int(
            rng.integers(0, (WINDOW[1] - WINDOW[0] - span) // an.GRID + 1)
        )
        return [float(v0), *(float(v0 + x) for x in np.cumsum(gaps))]
    return list(U30)


def not_fresh_hours(visits: Sequence[float], lateness_min: float, gate_min: float) -> float:
    """Hours per day the site's freshness gate shows "not fresh" when every visit happens."""
    v = np.sort(np.asarray(visits, dtype=float)) + lateness_min
    gaps = np.diff(np.concatenate([v, [v[0] + DAY]]))
    return float(np.clip(gaps - gate_min, 0, None).sum() / 60)


def optimise_window(
    crn: object, rng: np.random.Generator, starts: int = 20, min_step: float = MIN_STEP_MIN
) -> list[float]:
    """Coordinate descent for K visits on the 5-min grid inside WINDOW (window_feasible)."""
    grid = np.arange(WINDOW[0], WINDOW[1] + 1, an.GRID, dtype=float)
    best: tuple[list[float], float] = (list(U30), math.inf)
    for s in range(starts):
        vs = list(U30) if s == 0 else _random_start(rng, min_step)
        c = float(an.staleness_fixed(crn.u, vs, crn.lat, crn.miss).mean())
        improved = True
        while improved:
            improved = False
            for i in range(K):
                costs = an.coordinate_costs(crn, vs, i, grid)
                for gi in np.argsort(costs):
                    if costs[gi] >= c - 1e-9:
                        break
                    trial = [*vs[:i], float(grid[gi]), *vs[i + 1 :]]
                    if window_feasible(trial, min_step):
                        vs, c, improved = trial, float(costs[gi]), True
                        break
        if c < best[1]:
            best = (sorted(vs), c)
    return best[0]


def mixed_feasible(visits: Sequence[float], min_step: float = MIN_STEP_MIN) -> bool:
    """GG decision 3a (2026-09-26) mixed schedule: section 4 variant A (a visit in 07:00-09:00,
    every circular gap <= an.MAX_GAP_MIN so the 8 h freshness gate never flips) plus the 15 min
    minimum spacing that a visit's runner occupancy forces (section 9, deviation 1)."""
    v = sorted(visits)
    if not an.feasible(v, float(an.MAX_GAP_MIN)):
        return False
    gaps = [b - a for a, b in pairwise([*v, v[0] + DAY])]
    return min(gaps) >= min_step


def optimise_mixed(
    crn: object,
    start: Sequence[float],
    rng: np.random.Generator,
    starts: int = 20,
    min_step: float = MIN_STEP_MIN,
) -> list[float]:
    """Coordinate descent for K visits on the 5-min grid of the whole day (mixed_feasible)."""
    grid = np.arange(0, DAY, an.GRID, dtype=float)
    best: tuple[list[float], float] = (sorted(start), math.inf)
    for s in range(starts):
        if s == 0:
            vs = sorted(float(x) for x in start)
        else:
            vs = sorted(float(x) for x in rng.choice(grid, size=K, replace=False))
            if not mixed_feasible(vs, min_step):
                continue
        c = float(an.staleness_fixed(crn.u, vs, crn.lat, crn.miss).mean())
        improved = True
        while improved:
            improved = False
            for i in range(K):
                costs = an.coordinate_costs(crn, vs, i, grid)
                for gi in np.argsort(costs):
                    if costs[gi] >= c - 1e-9:
                        break
                    trial = [*vs[:i], float(grid[gi]), *vs[i + 1 :]]
                    if mixed_feasible(trial, min_step):
                        vs, c, improved = sorted(trial), float(costs[gi]), True
                        break
        if c < best[1]:
            best = (sorted(vs), c)
    return best[0]


# ── metrics ──────────────────────────────────────────────────────────────────


def seg_metrics(tod: np.ndarray, s: np.ndarray) -> dict:
    """Overall metrics plus mean staleness for morning and afternoon changes."""
    m = an.metrics(s)
    for name, (lo, hi) in (("morning", MORNING), ("afternoon", AFTERNOON)):
        mask = (tod >= lo) & (tod < hi)
        m[f"{name}_mean_staleness_min"] = float(s[mask].mean()) if mask.any() else float("nan")
        m[f"{name}_share_of_changes"] = float(mask.mean())
    return m


def schedule_metrics(crn: object, visits: Sequence[float]) -> dict:
    s = an.staleness_fixed(crn.u, visits, crn.lat, crn.miss)
    return seg_metrics(crn.u % DAY, s)


def replay(
    caps_utc: Sequence[datetime],
    days: Sequence[datetime],
    p: np.ndarray,
    rng: np.random.Generator,
    n: int,
) -> dict:
    """Today's captures replayed (as an.replay_staleness, keeping each update's time of day)."""
    origin = an.SPLIT_UTC
    cap_min = np.array([(c - origin).total_seconds() / 60 for c in caps_utc])
    day_idx = rng.integers(0, len(days), size=n)
    day_start = np.array([(d - origin).total_seconds() / 60 for d in days])[day_idx]
    tod = an.sample_updates(p, n, rng)
    u = day_start + tod
    pos = np.searchsorted(cap_min, u, side="left")
    ok = pos < len(cap_min)
    return seg_metrics(tod[ok], cap_min[pos[ok]] - u[ok])


def ci95(xs: Sequence[float]) -> list[float]:
    arr = np.asarray(xs, dtype=float)
    arr = arr[np.isfinite(arr)]
    return [round(float(np.quantile(arr, 0.025)), 3), round(float(np.quantile(arr, 0.975)), 3)]


def wilson_lower(k: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    ph = k / n
    den = 1 + z * z / n
    centre = ph + z * z / (2 * n)
    half = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n))
    return (centre - half) / den


# ── data ─────────────────────────────────────────────────────────────────────


def load_prices(rev: str | None) -> list[dict]:
    if rev is None:
        text = (REPO_ROOT / "data" / "prices.json").read_text(encoding="utf-8")
    else:
        text = subprocess.run(
            ["git", "show", f"{rev}:data/prices.json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=REPO_ROOT,
            check=True,
        ).stdout
    data = json.loads(text)
    return [x for x in data if isinstance(x, dict)]


def build(entries: Sequence[dict]) -> dict:
    caps = an.captures_from_entries(entries)
    ivs = an.change_intervals(caps)
    arcs, types, raw = [], [], []
    for a, b in ivs:
        length = (b - a).total_seconds() / 60
        if length >= DAY:
            continue
        arcs.append((an.ist_minute(a), length))
        types.append(an.interval_day_type(a, b))
        raw.append((a, b))
    alpha = an.arc_matrix(arcs)
    keep = [i for i, t in enumerate(types) if t != "sunday"]
    return {
        "caps": caps,
        "arcs": arcs,
        "types": types,
        "intervals": raw,
        "alpha": alpha,
        "alpha_ms": alpha[keep],
        "arcs_ms": [arcs[i] for i in keep],
    }


def arc_contains(arc: tuple[float, float], minute: float) -> bool:
    start, length = arc
    return (minute - start) % DAY <= length


def arc_inside_overnight(arc: tuple[float, float]) -> bool:
    """Arc lies entirely inside 20:00-09:59 IST (so it forces an overnight update)."""
    start, length = arc
    off = (start - 1200) % DAY  # minutes after 20:00
    return off + length <= 14 * 60


# ── evaluate (one-off, GG decision 4a) ───────────────────────────────────────


def current_days(split_caps: Sequence[datetime]) -> list[datetime]:
    first_day = (an.SPLIT_UTC + IST).date() + timedelta(days=1)
    last_day = (split_caps[-1] + IST).date() - timedelta(days=1)
    days, d = [], first_day
    while d <= last_day:
        if d.weekday() != 6:
            days.append(datetime(d.year, d.month, d.day, tzinfo=UTC) - IST)
        d += timedelta(days=1)
    return days


def evaluate(boot: int) -> dict:
    entries = [*an.git_history_entries(), *load_prices(PRICES_REV)]
    d = build(entries)
    caps = d["caps"]
    alpha_ms = d["alpha_ms"]
    p_ms = an.turnbull(alpha_ms)
    runs_sh, runs_cp = an.load_runs(RUNS_DIR)
    _, pool, q_unavail = an.lateness_report(runs_sh, runs_cp, caps)

    rng = np.random.default_rng(an.SEED)
    boot_ms = [
        an.turnbull(alpha_ms[rng.integers(0, len(alpha_ms), len(alpha_ms))]) for _ in range(boot)
    ]

    # Question 1: keep 01:40?
    near = [float(b[NEAR_0140[0] : NEAR_0140[1]].sum()) for b in boot_ms]
    over = [float(b[1200:].sum() + b[:600].sum()) for b in boot_ms]
    near_ci = ci95(near)
    arcs = d["arcs_ms"]
    q1 = {
        "rule": "keep 01:40 iff the bootstrap 95% CI of Mon-Sat NPMLE mass in 00:40-02:40 IST "
        "excludes zero",
        "mass_00_40_02_40": round(float(p_ms[NEAR_0140[0] : NEAR_0140[1]].sum()), 4),
        "mass_00_40_02_40_ci95": near_ci,
        "share_of_bootstrap_replicates_with_any_mass_00_40_02_40": round(
            float(np.mean(np.asarray(near) > 1e-9)), 3
        ),
        "mass_20_00_09_59": round(float(p_ms[1200:].sum() + p_ms[:600].sum()), 4),
        "mass_20_00_09_59_ci95": ci95(over),
        "change_intervals_in_mon_sat_pool": len(arcs),
        "intervals_containing_01_40": sum(arc_contains(a, 100) for a in arcs),
        "intervals_inside_20_00_09_59": sum(arc_inside_overnight(a) for a in arcs),
        "intervals_inside_20_00_09_59_containing_01_40": sum(
            arc_inside_overnight(a) and arc_contains(a, 100) for a in arcs
        ),
        "keep_01_40": bool(near_ci[0] > 0),
    }

    # Question 2: U30 vs O, and the comparison schedules
    crn = an.make_crn(p_ms, pool, 0.0, K, np.random.default_rng(an.SEED))
    o = optimise_window(crn, np.random.default_rng(an.SEED))
    # As registered (no minimum spacing): reported only, not runnable (deviation 1).
    o_reg = optimise_window(crn, np.random.default_rng(an.SEED), min_step=an.GRID)
    o_reg_m = schedule_metrics(crn, o_reg)
    scheds = {"U30": U30, "O": o, "interim_section4": INTERIM}
    point = {k: schedule_metrics(crn, v) for k, v in scheds.items()}
    crn_q = an.make_crn(p_ms, pool, q_unavail, K, np.random.default_rng(an.SEED))
    point_q = {k: schedule_metrics(crn_q, v) for k, v in scheds.items()}
    # Reported, not used in the rule (as in section 4): mass inside each innermost interval is
    # not identifiable, so evaluate with all of it on the left or on the right edge.
    groups = an.innermost_groups(alpha_ms, p_ms)
    edge = {}
    for side in ("left", "right"):
        ce = an.make_crn(
            an.edge_placement(p_ms, groups, side), pool, 0.0, K, np.random.default_rng(an.SEED)
        )
        edge[side] = {k: schedule_metrics(ce, v) for k, v in scheds.items()}

    split_caps = [c.ts for c in caps if c.ts >= an.SPLIT_UTC]
    days = current_days(split_caps)
    cur_point = replay(split_caps, days, p_ms, np.random.default_rng(an.SEED), an.MC_N)

    boots: dict[str, list[dict]] = {k: [] for k in [*scheds, "current"]}
    delta = []
    rb = np.random.default_rng(an.SEED + 1)
    for b in range(boot):
        cb = an.make_crn(boot_ms[b], pool, 0.0, K, np.random.default_rng(an.SEED + b))
        cb = an.CRN(cb.u[:BOOT_DRAWS], cb.lat[:BOOT_DRAWS], cb.miss[:BOOT_DRAWS])
        for k, v in scheds.items():
            boots[k].append(schedule_metrics(cb, v))
        dd = [days[i] for i in rb.integers(0, len(days), len(days))]
        boots["current"].append(replay(split_caps, dd, boot_ms[b], rb, BOOT_DRAWS))
        delta.append(boots["U30"][-1]["mean_staleness_min"] - boots["O"][-1]["mean_staleness_min"])
    d_point = point["U30"]["mean_staleness_min"] - point["O"]["mean_staleness_min"]
    d_lb = float(np.quantile(delta, 0.05))
    choose_o = d_point >= MIN_IMPROVEMENT_MIN and d_lb > 0
    chosen = "O" if choose_o else "U30"

    def with_ci(name: str, pt: dict) -> dict:
        keys = pt.keys()
        return {
            **{k: round(v, 4) for k, v in pt.items()},
            "ci95": {k: ci95([x[k] for x in boots[name]]) for k in keys},
        }

    lat_med = float(np.median(pool))
    gate = float(an.STALE_GATE_MIN)
    # afternoon changes: how long the pre-change Tanishq price is still shown as fresh
    last_cap = max(scheds[chosen]) + lat_med
    rng_a = np.random.default_rng(an.SEED)
    u = an.sample_updates(p_ms, an.MC_N, rng_a)
    aft = u[(u >= AFTERNOON[0]) & (u < AFTERNOON[1])]
    shown_old = np.clip(last_cap + gate - aft, 0, None)
    return {
        "preregistration": "docs/TANISHQ_TIMED_VISITS.md section 9 (commit 11d7dbba)",
        "data": {
            "prices_rev": PRICES_REV,
            "captures_live": len(caps),
            "change_intervals_used": len(d["arcs"]),
            "change_intervals_in_mon_sat_pool": len(arcs),
            "mon_sat_pool": "weekday + saturday + mixed intervals (sunday-only excluded), as section 4",
            "timestamp_convention": "prices.json timestamp = UTC capture instant; times of day IST",
        },
        "bootstrap": {"B": boot, "seed": an.SEED, "draws_per_replicate": BOOT_DRAWS},
        "monte_carlo_draws_point": an.MC_N,
        "lateness_pool_median_min": round(lat_med, 2),
        "q_measured_share_runner_unavailable": round(q_unavail, 3),
        "question_1_keep_0140": q1,
        "question_2_schedule": {
            "U30_ist": [hhmm(x) for x in U30],
            "O_ist": [hhmm(x) for x in o],
            "O_min_step_min": MIN_STEP_MIN,
            "O_as_registered_no_min_step_ist": [hhmm(x) for x in o_reg],
            "O_as_registered_point": o_reg_m,
            "improvement_O_over_U30_min": round(d_point, 3),
            "improvement_one_sided_95_lower_bound_min": round(d_lb, 3),
            "rule": f"O only if improvement >= {MIN_IMPROVEMENT_MIN} min and one-sided 95% "
            "lower bound > 0",
            "chosen": chosen,
            "chosen_ist": [hhmm(x) for x in scheds[chosen]],
        },
        "q0": {
            "current_replay": with_ci("current", cur_point),
            **{k: with_ci(k, v) for k, v in point.items()},
        },
        "q_measured_point": point_q,
        "q0_edge_placement_point": edge,
        "not_fresh_hours_per_day_all_visits_on_time": {
            k: round(not_fresh_hours(v, lat_med, gate), 2) for k, v in scheds.items()
        },
        "afternoon_changes_old_tanishq_price_still_inside_8h_gate_min": {
            "mean": round(float(shown_old.mean()), 1),
            "median": round(float(np.median(shown_old)), 1),
            "note": "after an afternoon change the pre-change Tanishq price stays on the page "
            "(it is under 8 h old) until the gate flips; then the estimate, until the next "
            "morning visit",
        },
    }


# ── weekly refinement ────────────────────────────────────────────────────────


def forward_counts(
    intervals: Sequence[tuple[datetime, datetime]], effective_from: datetime | None
) -> dict:
    """Changes seen since the switch: bracket width, and first-visit-of-day (outside window)."""
    if effective_from is None:
        return {"changes": 0, "bracketed_le_30min": 0, "first_seen_at_first_visit_of_day": 0}
    fwd = [(a, b) for a, b in intervals if a >= effective_from]
    return {
        "changes": len(fwd),
        "bracketed_le_30min": sum((b - a).total_seconds() <= MAX_STEP_MIN * 60 for a, b in fwd),
        "first_seen_at_first_visit_of_day": sum(
            (a + IST).date() < (b + IST).date() for a, b in fwd
        ),
    }


def decide(
    counts: dict,
    improvement_min: float,
    improvement_lb_min: float,
    last_proposal: date | None,
    today: date,
) -> dict:
    """The pre-registered proposal rule (docs/TANISHQ_TIMED_VISITS.md section 9)."""
    n = counts["changes"]
    out_share_lb = wilson_lower(counts["first_seen_at_first_visit_of_day"], n)
    escalate = n >= MIN_BRACKETED and out_share_lb > OUTSIDE_SHARE_LIMIT
    if counts["bracketed_le_30min"] < MIN_BRACKETED:
        status = "WAIT_FOR_DATA"
    elif last_proposal is not None and (today - last_proposal).days < PROPOSAL_SPACING_DAYS:
        status = "HOLD_4_WEEK_SPACING"
    elif improvement_min >= MIN_IMPROVEMENT_MIN and improvement_lb_min > 0:
        status = "PROPOSE"
    else:
        status = "KEEP"
    return {
        "status": status,
        "escalate_window_misses_changes": escalate,
        "outside_window_share_wilson_lower": round(out_share_lb, 3),
    }


def weekly(boot: int, last_proposal: date | None) -> dict:
    schedule = json.loads(SCHEDULE.read_text(encoding="utf-8"))
    current = to_minutes(schedule["visits_ist"])
    eff_s = schedule.get("effective_from_utc")
    eff = an._ts(eff_s) if eff_s else None
    d = build([*an.git_history_entries(), *load_prices(None)])
    counts = forward_counts(d["intervals"], eff)
    alpha_ms = d["alpha_ms"]
    p_ms = an.turnbull(alpha_ms)
    runs_sh, runs_cp = an.load_runs(RUNS_DIR)
    _, pool, _ = an.lateness_report(runs_sh, runs_cp, d["caps"])
    crn = an.make_crn(p_ms, pool, 0.0, K, np.random.default_rng(an.SEED))
    mode = schedule.get("mode", "window_4a")
    if mode == "mixed":
        cand = optimise_mixed(crn, current, np.random.default_rng(an.SEED))
    else:
        cand = optimise_window(crn, np.random.default_rng(an.SEED))
    cur_m = schedule_metrics(crn, current)
    cand_m = schedule_metrics(crn, cand)
    rng = np.random.default_rng(an.SEED)
    delta = []
    for b in range(boot):
        pb = an.turnbull(alpha_ms[rng.integers(0, len(alpha_ms), len(alpha_ms))])
        cb = an.make_crn(pb, pool, 0.0, K, np.random.default_rng(an.SEED + b))
        cb = an.CRN(cb.u[:BOOT_DRAWS], cb.lat[:BOOT_DRAWS], cb.miss[:BOOT_DRAWS])
        delta.append(
            schedule_metrics(cb, current)["mean_staleness_min"]
            - schedule_metrics(cb, cand)["mean_staleness_min"]
        )
    imp = cur_m["mean_staleness_min"] - cand_m["mean_staleness_min"]
    imp_lb = float(np.quantile(delta, 0.05))
    today = (datetime.now(UTC) + IST).date()
    verdict = decide(counts, imp, imp_lb, last_proposal, today)
    if mode == "mixed":
        # The window check (section 9, rule 3) asks whether a morning-only window misses
        # changes. A mixed schedule visits morning and afternoon, so it does not apply.
        verdict["escalate_window_misses_changes"] = False
        verdict["window_check"] = "not applicable in mixed mode (section 10)"
    hourly = [round(float(p_ms[h * 60 : (h + 1) * 60].sum()), 3) for h in range(24)]
    return {
        "run_ist_date": today.isoformat(),
        "mode": mode,
        "current_schedule_ist": schedule["visits_ist"],
        "effective_from_utc": eff_s,
        "forward": counts,
        "mon_sat_mass_by_ist_hour": hourly,
        "recommended_schedule_ist": [hhmm(x) for x in cand],
        "current_mean_staleness_min": round(cur_m["mean_staleness_min"], 2),
        "recommended_mean_staleness_min": round(cand_m["mean_staleness_min"], 2),
        "improvement_min": round(imp, 2),
        "improvement_one_sided_95_lower_bound_min": round(imp_lb, 2),
        **verdict,
        "note": "A schedule change is GG's. This script never edits scraper/visit_schedule.json.",
    }


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    ev = sub.add_parser("evaluate")
    ev.add_argument("--boot", type=int, default=500)
    wk = sub.add_parser("weekly")
    wk.add_argument("--boot", type=int, default=200)
    wk.add_argument("--last-proposal", type=date.fromisoformat, default=None)
    args = ap.parse_args(argv)
    if args.cmd == "evaluate":
        res = evaluate(args.boot)
        OUT.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8", newline="\n")
    else:
        res = weekly(args.boot, args.last_proposal)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
