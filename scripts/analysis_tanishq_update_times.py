"""When does Tanishq update its rate, and when should we visit? (GG decision E3)

Method pre-registered in docs/TANISHQ_TIMED_VISITS.md (commit 5527819a, sha256
dc3df303...). Summary:
  * captures = live entries of data/prices.json (+ entries later removed, from git history);
    timestamp = UTC capture instant set by scraper/scrape.js;
  * a change between consecutive captures is interval-censored: Tanishq updated in
    (previous capture, this capture];
  * Turnbull NPMLE of the IST time-of-day distribution (1-minute bins on the 24 h circle);
  * Monte Carlo staleness for fixed daily schedules, replay for the current (actual) captures;
  * bootstrap over change intervals (B=500) and over replay days; regret rule for (c).

Outputs (derived only -- times, counts, distributions; never a price):
  reports/tanishq_update_times/update_time_distribution.json
  reports/tanishq_update_times/schedule_evaluation.json
  reports/tanishq_update_times/scheduler_lateness.json
  reports/tanishq_update_times/kalyan_city_identity.json

Usage:
  python scripts/analysis_tanishq_update_times.py [--runs-dir DIR] [--boot 500] [--no-git-history]
`--runs-dir` holds runs_sh.json / runs_cp.json from `gh run list --json ...` (the 2026-09-25 fetch is
in reports/tanishq_update_times/gh_runs/); if absent the
script calls gh itself (authenticated gh required).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "reports" / "tanishq_update_times"
SEED = 42
DAY = 1440  # minutes
IST = timedelta(hours=5, minutes=30)
SPLIT_UTC = datetime(2026, 9, 4, 0, 0, tzinfo=UTC)  # scrape-tanishq-selfhosted.yml split
MORNING_WINDOW = (7 * 60, 9 * 60)  # at least one visit here: fresh price by morning
# ml/inference.py _STALE_THRESHOLD_H (8 h) minus a 20 min lateness margin; tested for drift.
STALE_GATE_MIN = 8 * 60
MAX_GAP_MIN = STALE_GATE_MIN - 20
GRID = 5  # candidate visit times every 5 minutes
MC_N = 20_000
RUNNER_WAIT_MIN = 30  # a scheduled run that waited longer than this had no runner online
RUN_FIELDS = "databaseId,event,createdAt,startedAt,updatedAt,conclusion,status"


# ── captures and change intervals ────────────────────────────────────────────


@dataclass(frozen=True)
class Capture:
    ts: datetime  # UTC capture instant
    key: tuple[float, float, float]  # rates, used only to detect a change; never written out


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def _is_live(entry: dict) -> bool:
    src = str(entry.get("source", ""))
    return "timestamp" in entry and "22k" in entry and "backfill" not in src


def captures_from_entries(entries: Sequence[dict]) -> list[Capture]:
    """Unique live captures sorted by time (duplicates by timestamp collapse to one)."""
    by_ts: dict[datetime, Capture] = {}
    for e in entries:
        if not isinstance(e, dict) or not _is_live(e):
            continue
        t = _ts(e["timestamp"])
        by_ts[t] = Capture(t, (e.get("22k"), e.get("24k"), e.get("18k")))
    return [by_ts[t] for t in sorted(by_ts)]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", cwd=REPO_ROOT, check=True
    ).stdout


def git_history_entries() -> list[dict]:
    """Every entry that ever appeared in data/prices.json across its git history."""
    out: list[dict] = []
    for sha in _git("log", "--format=%H", "--", "data/prices.json").split():
        try:
            data = json.loads(_git("show", f"{sha}:data/prices.json"))
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue
        if isinstance(data, list):
            out.extend(x for x in data if isinstance(x, dict))
    return out


def change_intervals(caps: Sequence[Capture]) -> list[tuple[datetime, datetime]]:
    return [(a.ts, b.ts) for a, b in pairwise(caps) if a.key != b.key]


def ist_minute(t: datetime) -> float:
    s = t + IST
    return s.hour * 60 + s.minute + s.second / 60


def day_type(t: datetime) -> str:
    wd = (t + IST).weekday()
    return "sunday" if wd == 6 else "saturday" if wd == 5 else "weekday"


def interval_day_type(a: datetime, b: datetime) -> str:
    ta, tb = day_type(a), day_type(b)
    return ta if ta == tb else "mixed"


# ── Turnbull NPMLE on the daily circle ───────────────────────────────────────


def arc_matrix(arcs: Sequence[tuple[float, float]]) -> np.ndarray:
    """Row i marks the 1-minute bins in arc i = (start_min_ist, length_min), open-closed."""
    m = np.zeros((len(arcs), DAY), dtype=bool)
    for i, (start, length) in enumerate(arcs):
        lo = int(np.floor(start))  # bin containing the start instant (update after start)
        hi = int(np.ceil(start + length))  # bin containing the end instant
        idx = np.arange(lo, max(hi, lo + 1)) % DAY
        m[i, idx] = True
    return m


def turnbull(alpha: np.ndarray, tol: float = 1e-9, max_iter: int = 50_000) -> np.ndarray:
    """Self-consistency EM (Turnbull NPMLE) over 1-minute bins.

    Bins with an identical coverage pattern are collapsed first (exact: EM treats them
    identically), and each collapsed group's mass is spread uniformly over its bins -- mass
    placement inside an innermost interval is not identifiable from censored data.
    """
    n, k = alpha.shape
    if n == 0:
        return np.full(k, 1 / k)
    cols, inverse, counts = np.unique(alpha, axis=1, return_inverse=True, return_counts=True)
    inverse = np.asarray(inverse).reshape(-1)
    a = cols.astype(float)
    g = counts / counts.sum()
    for _ in range(max_iter):
        new = g * (a / (a @ g)[:, None]).sum(axis=0) / n
        if np.abs(new - g).max() < tol:
            g = new
            break
        g = new
    return g[inverse] / counts[inverse]


def innermost_groups(alpha: np.ndarray, p: np.ndarray, eps: float = 1e-9) -> list[dict]:
    """Contiguous bins with identical coverage signature and positive mass."""
    groups: list[dict] = []
    sig_prev = None
    for b in range(DAY):
        if p[b] <= eps:
            sig_prev = None
            continue
        sig = alpha[:, b].tobytes()
        if groups and sig == sig_prev and groups[-1]["end"] == b:
            groups[-1]["end"] = b + 1
            groups[-1]["mass"] += float(p[b])
        else:
            groups.append({"start": b, "end": b + 1, "mass": float(p[b])})
        sig_prev = sig
    return groups


def edge_placement(p: np.ndarray, groups: list[dict], side: str) -> np.ndarray:
    """All of each innermost group's mass on its left or right edge bin."""
    q = np.zeros(DAY)
    for g in groups:
        q[g["start"] if side == "left" else g["end"] - 1] += g["mass"]
    return q / q.sum()


def fmt(minute: float) -> str:
    m = round(minute) % DAY
    return f"{m // 60:02d}:{m % 60:02d}"


def summarise(p: np.ndarray) -> dict:
    hourly = [round(float(p[h * 60 : (h + 1) * 60].sum()), 4) for h in range(24)]
    return {"mass_by_ist_hour": hourly}


# ── staleness simulation ─────────────────────────────────────────────────────


def sample_updates(p: np.ndarray, n: int, rng: np.random.Generator) -> np.ndarray:
    bins = rng.choice(DAY, size=n, p=p / p.sum())
    return bins + rng.random(n)


def staleness_fixed(
    updates: np.ndarray,
    visits: Sequence[float],
    lateness: np.ndarray,
    miss: np.ndarray,
) -> np.ndarray:
    """Minutes from each update to the first successful capture at or after it.

    `lateness`/`miss` are (n, >=3*K) pre-drawn common random numbers; column d*K + j is visit j
    on day d (d = 0, 1, 2), so every visit has its own lateness and miss draw per day.
    """
    v = np.asarray(visits, dtype=float)
    k = len(v)
    base = np.concatenate([v, v + DAY, v + 2 * DAY])[None, :]
    cap = base + lateness[:, : 3 * k]
    cap = np.where(miss[:, : 3 * k], np.inf, cap)
    cap = np.where(cap >= updates[:, None], cap, np.inf)
    s = cap.min(axis=1) - updates
    return np.where(np.isfinite(s), s, 3 * DAY - updates)  # all 3 days missed: censor


def coordinate_costs(crn: CRN, visits: Sequence[float], i: int, grid: np.ndarray) -> np.ndarray:
    """Mean staleness for every grid position of visit i, others fixed (vectorised)."""
    k, u = len(visits), crn.u
    s_oth = np.full(len(u), np.inf)
    for d in range(3):
        for j in range(k):
            if j == i:
                continue
            c = visits[j] + d * DAY + crn.lat[:, d * k + j]
            ok = ~crn.miss[:, d * k + j] & (c >= u)
            s_oth = np.where(ok, np.minimum(s_oth, c - u), s_oth)
    s_c = np.full((len(u), len(grid)), np.inf)
    for d in range(3):
        c = grid[None, :] + d * DAY + crn.lat[:, d * k + i][:, None]
        ok = (~crn.miss[:, d * k + i])[:, None] & (c >= u[:, None])
        s_c = np.where(ok, np.minimum(s_c, c - u[:, None]), s_c)
    s = np.minimum(s_c, s_oth[:, None])
    s = np.where(np.isfinite(s), s, (3 * DAY - u)[:, None])
    return s.mean(axis=0)


def metrics(s: np.ndarray) -> dict:
    return {
        "mean_staleness_min": float(np.mean(s)),
        "median_staleness_min": float(np.median(s)),
        "share_within_30min": float(np.mean(s <= 30)),
        "share_within_60min": float(np.mean(s <= 60)),
    }


@dataclass
class CRN:
    """Common random numbers so schedule comparisons are paired."""

    u: np.ndarray
    lat: np.ndarray
    miss: np.ndarray


def make_crn(
    p: np.ndarray, lateness_pool: np.ndarray, q: float, k_max: int, rng: np.random.Generator
) -> CRN:
    u = sample_updates(p, MC_N, rng)
    lat = rng.choice(lateness_pool, size=(MC_N, 3 * k_max))
    miss = rng.random((MC_N, 3 * k_max)) < q
    return CRN(u, lat, miss)


def max_circular_gap(visits: Sequence[float]) -> float:
    v = np.sort(np.asarray(visits, dtype=float))
    gaps = np.diff(np.concatenate([v, [v[0] + DAY]]))
    return float(gaps.max())


def feasible(visits: Sequence[float], gap_limit: float | None) -> bool:
    if not any(MORNING_WINDOW[0] <= v <= MORNING_WINDOW[1] for v in visits):
        return False
    if len(set(visits)) != len(visits):
        return False
    return gap_limit is None or max_circular_gap(visits) <= gap_limit


def optimise(
    crn: CRN, k: int, gap_limit: float | None, rng: np.random.Generator, starts: int = 20
) -> tuple[list[float], float]:
    """Coordinate descent on the 5-minute grid from `starts` seeded feasible starts."""
    grid = np.arange(0, DAY, GRID, dtype=float)
    best: tuple[list[float], float] = ([], np.inf)
    for _ in range(starts):
        phase = float(rng.integers(0, DAY // (GRID * k)) * GRID)
        vs = [float((x // GRID * GRID) % DAY) for x in np.arange(k) * (DAY / k) + phase]
        vs[0] = float(rng.integers(MORNING_WINDOW[0] // GRID, MORNING_WINDOW[1] // GRID) * GRID)
        if not feasible(vs, gap_limit):
            vs = [float((7 * 60 + x) % DAY) for x in np.arange(k) * (DAY // k) // GRID * GRID]
        c = float(staleness_fixed(crn.u, vs, crn.lat, crn.miss).mean())
        improved = True
        while improved:
            improved = False
            for i in range(k):
                costs = coordinate_costs(crn, vs, i, grid)
                for gi in np.argsort(costs):
                    if costs[gi] >= c - 1e-9:
                        break
                    trial = [*vs[:i], float(grid[gi]), *vs[i + 1 :]]
                    if feasible(trial, gap_limit):
                        vs, c, improved = trial, float(costs[gi]), True
                        break
        if c < best[1]:
            best = (sorted(vs), c)
    return best


# ── replay of the actual (current) captures ──────────────────────────────────


def replay_staleness(
    caps_utc: Sequence[datetime],
    days: Sequence[datetime],
    p: np.ndarray,
    rng: np.random.Generator,
    n: int = MC_N,
) -> np.ndarray:
    """Draw (day, time) updates; staleness = first actual capture at or after it."""
    cap_min = np.array([(c - SPLIT_UTC).total_seconds() / 60 for c in caps_utc])
    day_idx = rng.integers(0, len(days), size=n)
    day_start = np.array([(d - SPLIT_UTC).total_seconds() / 60 for d in days])[day_idx]
    u = day_start + sample_updates(p, n, rng)
    pos = np.searchsorted(cap_min, u, side="left")
    ok = pos < len(cap_min)
    s = np.full(n, np.nan)
    s[ok] = cap_min[pos[ok]] - u[ok]
    return s[ok]


# ── measurement window proposal ──────────────────────────────────────────────


# Bounded measurement windows (proposal only -- STOP for GG). Dense probes where the update
# mass sits, fixed visits elsewhere. Evaluated by the width of the censoring interval each
# update would get (today's captures replayed for comparison).
def _every(start: str, end: str, step: int) -> list[float]:
    h0, m0 = (int(x) for x in start.split(":"))
    h1, m1 = (int(x) for x in end.split(":"))
    return [float(t) for t in range(h0 * 60 + m0, h1 * 60 + m1 + 1, step)]


MEASUREMENT_DESIGNS: dict[str, dict] = {
    # the interim schedule's 10:40/11:10 pair replaced by 15-minute probes over 10:00-11:15
    "M1_morning_dense": {
        "probes": [100, 450, *_every("10:00", "11:15", 15), 935, 1190],
        "days": 15,
    },
    # M1 plus 30-minute probes over 14:00-18:00 (replacing 15:35)
    "M2_morning_and_afternoon_dense": {
        "probes": [100, 450, *_every("10:00", "11:15", 15), *_every("14:00", "18:00", 30), 1190],
        "days": 10,
    },
}


def _interval_widths(u: np.ndarray, probes: np.ndarray) -> np.ndarray:
    """Width of the (previous probe, next probe] interval containing each update (circular)."""
    ext = np.concatenate([probes - DAY, probes, probes + DAY], axis=1)
    nxt = np.where(ext >= u[:, None], ext, np.inf).min(axis=1)
    prv = np.where(ext < u[:, None], ext, -np.inf).max(axis=1)
    return nxt - prv


def measurement_window_widths(
    p: np.ndarray, caps_utc: Sequence[datetime], days: Sequence[datetime], rng: np.random.Generator
) -> dict:
    n = MC_N
    u = sample_updates(p, n, rng)
    morning = (u >= 9 * 60) & (u < 12 * 60)
    afternoon = (u >= 13 * 60) & (u < 20 * 60)

    def summ(w: np.ndarray, mask_m: np.ndarray, mask_a: np.ndarray) -> dict:
        return {
            "median_width_min_all": round(float(np.median(w)), 1),
            "median_width_min_morning_09_12": round(float(np.median(w[mask_m])), 1),
            "median_width_min_afternoon_13_20": round(float(np.median(w[mask_a])), 1),
            "p90_width_min_all": round(float(np.quantile(w, 0.9)), 1),
        }

    out: dict = {}
    for name, d in MEASUREMENT_DESIGNS.items():
        probes = np.tile(np.asarray(d["probes"], dtype=float), (n, 1))
        out[name] = {
            "visits_per_day": len(d["probes"]),
            "days_mon_sat": d["days"],
            "total_visits": len(d["probes"]) * d["days"],
            "visits_ist": [fmt(x) for x in d["probes"]],
            **summ(_interval_widths(u, probes), morning, afternoon),
        }
    cap_min = np.array([(c - SPLIT_UTC).total_seconds() / 60 for c in caps_utc])
    day0 = np.array([(d - SPLIT_UTC).total_seconds() / 60 for d in days])[
        rng.integers(0, len(days), n)
    ]
    pos = np.searchsorted(cap_min, day0 + u)
    ok = (pos > 0) & (pos < len(cap_min))
    cur = cap_min[pos[ok]] - cap_min[pos[ok] - 1]
    out["current_captures_replay"] = summ(cur, morning[ok], afternoon[ok])
    return out


# ── GitHub scheduler lateness ────────────────────────────────────────────────


def cron_slots(minute: int, hours: Sequence[int], start: datetime, end: datetime) -> list[datetime]:
    t = start.replace(minute=0, second=0, microsecond=0)
    out = []
    while t <= end:
        if t.hour in hours:
            out.append(t.replace(minute=minute))
        t += timedelta(hours=1)
    return [s for s in out if start <= s <= end]


def preceding_slot_delay(created: datetime, slots: Sequence[datetime]) -> float | None:
    prior = [s for s in slots if s <= created]
    return (created - prior[-1]).total_seconds() / 60 if prior else None


def pct(xs: Sequence[float], qs: Sequence[float] = (0.1, 0.5, 0.9, 1.0)) -> dict:
    if not xs:
        return {"n": 0}
    a = np.asarray(xs, dtype=float)
    return {"n": len(a)} | {f"p{int(q * 100)}": round(float(np.quantile(a, q)), 1) for q in qs}


def lateness_report(
    runs_sh: list[dict], runs_cp: list[dict], caps: Sequence[Capture]
) -> tuple[dict, np.ndarray, float]:
    cap_ts = [c.ts for c in caps]

    def capture_in(run: dict) -> datetime | None:
        a, b = _ts(run["createdAt"]), _ts(run["updatedAt"])
        inside = [t for t in cap_ts if a <= t <= b + timedelta(minutes=2)]
        return inside[0] if inside else None

    sched = sorted((r for r in runs_sh if r["event"] == "schedule"), key=lambda r: r["createdAt"])
    first, last = _ts(sched[0]["createdAt"]), _ts(sched[-1]["createdAt"])
    slots = cron_slots(7, range(0, 24, 3), first - timedelta(hours=3), last)
    delays = [preceding_slot_delay(_ts(r["createdAt"]), slots) for r in sched]
    waits = [(_ts(r["updatedAt"]) - _ts(r["createdAt"])).total_seconds() / 60 for r in sched]
    capture_lags = []
    for r in sched:
        c = capture_in(r)
        if c is not None:
            capture_lags.append((c - _ts(r["createdAt"])).total_seconds() / 60)
    n_days = (last - first).total_seconds() / 86400
    no_runner = sum(
        1
        for r, w in zip(sched, waits, strict=True)
        if w > RUNNER_WAIT_MIN or r["conclusion"] == "cancelled"
    )
    q_unavail = no_runner / len(sched)

    disp = [
        r for r in runs_sh if r["event"] == "workflow_dispatch" and r["conclusion"] == "success"
    ]
    disp_lags = []
    for r in disp:
        c = capture_in(r)
        if c is not None:
            disp_lags.append((c - _ts(r["createdAt"])).total_seconds() / 60)

    cp = sorted(
        (
            r
            for r in runs_cp
            if r["event"] == "schedule"
            and _ts(r["createdAt"]) >= datetime(2026, 9, 11, 3, tzinfo=UTC)
        ),
        key=lambda r: r["createdAt"],
    )
    cp_first, cp_last = _ts(cp[0]["createdAt"]), _ts(cp[-1]["createdAt"])
    cp_slots = cron_slots(37, range(1, 23, 3), cp_first - timedelta(hours=3), cp_last)
    cp_delays = [preceding_slot_delay(_ts(r["createdAt"]), cp_slots) for r in cp]
    cp_days = (cp_last - cp_first).total_seconds() / 86400

    report = {
        "method": (
            "Delay = run createdAt minus the latest nominal cron slot at or before it. This is a "
            "LOWER BOUND per run: with 1-3 h delays against 3 h slot spacing a run cannot be tied "
            "to its slot, and GitHub creates fewer runs than slots (dropped triggers)."
        ),
        "scrape_tanishq_selfhosted": {
            "cron_utc": "7 */3 * * *",
            "window_utc": [first.isoformat(), last.isoformat()],
            "nominal_slots_per_day": 8,
            "scheduled_runs_created": len(sched),
            "runs_created_per_day": round(len(sched) / n_days, 2),
            "creation_delay_lower_bound_min": pct([d for d in delays if d is not None]),
            "queue_plus_run_duration_min": pct(waits),
            "runs_waiting_over_30min_or_cancelled": no_runner,
            "share_runner_unavailable": round(q_unavail, 3),
            "created_to_capture_min_successful": pct(capture_lags),
            "conclusions": {
                c: sum(1 for r in sched if r["conclusion"] == c)
                for c in sorted({r["conclusion"] for r in sched})
            },
        },
        "workflow_dispatch_selfhosted": {
            "successful_runs": len(disp),
            "created_to_capture_min": pct(disp_lags),
        },
        "check_price": {
            "cron_utc": "37 1-22/3 * * *",
            "note": "Does not visit Tanishq; measured only as a second sample of GitHub scheduling.",
            "window_utc": [cp_first.isoformat(), cp_last.isoformat()],
            "scheduled_runs_created": len(cp),
            "runs_created_per_day": round(len(cp) / cp_days, 2),
            "creation_delay_lower_bound_min": pct([d for d in cp_delays if d is not None]),
        },
    }
    # Deviation from the pre-registration (stated in the doc): only 1 of the 9 successful
    # workflow_dispatch runs could be matched to a capture, too few to be a lateness pool. The
    # pool is every self-hosted run (schedule or dispatch) whose capture came within
    # RUNNER_WAIT_MIN of run creation, i.e. "runner online: pickup + scrape" -- exactly what a
    # Task Scheduler dispatch adds on top of the trigger instant.
    online = [x for x in capture_lags + disp_lags if x <= RUNNER_WAIT_MIN]
    report["runner_online_created_to_capture_min"] = pct(online)
    pool = np.asarray(online if online else [5.0], dtype=float)
    return report, pool, q_unavail


def load_runs(runs_dir: Path | None) -> tuple[list[dict], list[dict]]:
    def gh(wf: str) -> list[dict]:
        out = subprocess.run(
            ["gh", "run", "list", "--workflow", wf, "-L", "2000", "--json", RUN_FIELDS],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=REPO_ROOT,
            check=True,
        ).stdout
        return json.loads(out)

    if runs_dir is not None:
        return (
            json.loads((runs_dir / "runs_sh.json").read_text(encoding="utf-8")),
            json.loads((runs_dir / "runs_cp.json").read_text(encoding="utf-8")),
        )
    return gh("scrape-tanishq-selfhosted.yml"), gh("check-price.yml")


# ── Kalyan per-city identity ─────────────────────────────────────────────────


def kalyan_identity() -> dict:
    import pandas as pd

    df = pd.read_parquet(REPO_ROOT / "data" / "fusion_snapshots.parquet")
    k = df[df["source"] == "kalyan"]
    g = k.groupby("capture_utc")["rate_22k"]
    multi = g.nunique()[g.size() >= 2]
    return {
        "source_file": "data/fusion_snapshots.parquet",
        "cycles_with_2plus_cities": len(multi),
        "cycles_all_cities_identical": int((multi == 1).sum()),
        "cycles_with_city_difference": int((multi > 1).sum()),
        "first_capture_utc": str(k["capture_utc"].min()),
        "last_capture_utc": str(k["capture_utc"].max()),
        "cities_seen": sorted(k["city"].unique().tolist()),
    }


# ── main ─────────────────────────────────────────────────────────────────────


def fit(arcs: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    alpha = arc_matrix(arcs)
    return alpha, turnbull(alpha)


def ci(xs: Sequence[float]) -> list[float]:
    return [round(float(np.quantile(xs, 0.025)), 3), round(float(np.quantile(xs, 0.975)), 3)]


def evaluate_boot(values: list[dict]) -> dict:
    keys = values[0].keys()
    return {k: ci([v[k] for v in values]) for k in keys}


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-dir", type=Path, default=None)
    ap.add_argument("--boot", type=int, default=500)
    ap.add_argument("--reopt-boot", type=int, default=200)
    ap.add_argument("--no-git-history", action="store_true")
    args = ap.parse_args(argv)
    rng = np.random.default_rng(SEED)

    current = json.loads((REPO_ROOT / "data" / "prices.json").read_text(encoding="utf-8"))
    extra = [] if args.no_git_history else git_history_entries()
    caps = captures_from_entries([*extra, *current])
    n_removed = len(caps) - len(captures_from_entries(current))
    ivs = change_intervals(caps)
    gaps_h = [(b.ts - a.ts).total_seconds() / 3600 for a, b in pairwise(caps)]
    recent_gaps = [
        (b.ts - a.ts).total_seconds() / 3600 for a, b in pairwise(caps) if a.ts >= SPLIT_UTC
    ]

    arcs_all, types = [], []
    for a, b in ivs:
        length = (b - a).total_seconds() / 60
        if length >= DAY:
            continue
        arcs_all.append((ist_minute(a), length))
        types.append(interval_day_type(a, b))
    n_long = len(ivs) - len(arcs_all)
    alpha, p = fit(arcs_all)
    groups = innermost_groups(alpha, p)
    top = sorted(groups, key=lambda g: -g["mass"])[:8]

    per_type = {}
    p_types: dict[str, np.ndarray] = {}
    for t in ("weekday", "saturday", "sunday"):
        arcs_t = [a for a, ty in zip(arcs_all, types, strict=True) if ty == t]
        if arcs_t:
            al_t, p_t = fit(arcs_t)
            p_types[t] = p_t
            g_t = sorted(innermost_groups(al_t, p_t), key=lambda g: -g["mass"])[:6]
            per_type[t] = {
                "n_change_intervals": len(arcs_t),
                **summarise(p_t),
                "top_innermost_intervals_ist": [
                    {"from": fmt(g["start"]), "to": fmt(g["end"]), "mass": round(g["mass"], 3)}
                    for g in g_t
                ],
            }
        else:
            per_type[t] = {"n_change_intervals": 0}

    # day coverage: IST days with >= 1 capture, and days whose captures show >= 1 change
    days_obs: dict[str, set] = {"weekday": set(), "saturday": set(), "sunday": set()}
    for c in caps:
        days_obs[day_type(c.ts)].add((c.ts + IST).date())
    days_chg: dict[str, set] = {"weekday": set(), "saturday": set(), "sunday": set()}
    for a, b in ivs:
        if (b - a).total_seconds() < DAY * 60 / 2:  # <12 h: change day is unambiguous enough
            days_chg[day_type(b)].add((b + IST).date())
    day_rates = {
        t: {
            "ist_days_with_captures": len(days_obs[t]),
            "days_with_a_change_seen_lt12h": len(days_chg[t]),
        }
        for t in days_obs
    }

    # bootstrap of the pooled distribution (change intervals resampled)
    boot_p = []
    for _ in range(args.boot):
        idx = rng.integers(0, len(arcs_all), len(arcs_all))
        boot_p.append(turnbull(alpha[idx]))
    boot_p_arr = np.array(boot_p)
    hourly_ci = [ci(boot_p_arr[:, h * 60 : (h + 1) * 60].sum(axis=1).tolist()) for h in range(24)]

    dist = {
        "timestamp_convention": "prices.json timestamp = UTC capture instant (scraper/scrape.js); shown here in IST",
        "captures_live": len(caps),
        "captures_only_in_git_history": n_removed,
        "first_capture_utc": caps[0].ts.isoformat(),
        "last_capture_utc": caps[-1].ts.isoformat(),
        "change_intervals": len(ivs),
        "change_intervals_24h_or_longer_dropped": n_long,
        "change_intervals_used": len(arcs_all),
        "by_day_type_counts": {
            t: types.count(t) for t in ("weekday", "saturday", "sunday", "mixed")
        },
        "resolution_limit": {
            "gap_between_captures_h_all": pct(gaps_h, (0.5, 0.9, 1.0)),
            "gap_between_captures_h_since_split": pct(recent_gaps, (0.5, 0.9, 1.0)),
            "change_interval_width_min_used": pct([a[1] for a in arcs_all], (0.1, 0.5, 0.9)),
        },
        "pooled": {
            **summarise(p),
            "mass_by_ist_hour_ci95": hourly_ci,
            "mass_by_ist_window": {
                name: {
                    "mass": round(float(p[lo:hi].sum()), 3),
                    "ci95": ci(boot_p_arr[:, lo:hi].sum(axis=1).tolist()),
                }
                for name, lo, hi in (
                    ("10:00-11:59", 600, 720),
                    ("12:00-13:59", 720, 840),
                    ("14:00-19:59", 840, 1200),
                )
            }
            | {
                "20:00-09:59": {
                    "mass": round(float(p[1200:].sum() + p[:600].sum()), 3),
                    "ci95": ci(
                        (
                            boot_p_arr[:, 1200:].sum(axis=1) + boot_p_arr[:, :600].sum(axis=1)
                        ).tolist()
                    ),
                }
            },
            "top_innermost_intervals_ist": [
                {
                    "from": fmt(g["start"]),
                    "to": fmt(g["end"]),
                    "width_min": g["end"] - g["start"],
                    "mass": round(g["mass"], 3),
                }
                for g in top
            ],
        },
        "by_day_type": per_type,
        "day_rates": day_rates,
        "bootstrap": {"B": args.boot, "seed": SEED, "unit": "change interval"},
    }

    runs_sh, runs_cp = load_runs(args.runs_dir)
    lat_report, disp_pool, q_unavail = lateness_report(runs_sh, runs_cp, caps)

    # current schedule: replay of actual captures since the split, Mon-Sat days only
    split_caps = [c.ts for c in caps if c.ts >= SPLIT_UTC]
    first_day = (SPLIT_UTC + IST).date() + timedelta(days=1)
    last_day = (split_caps[-1] + IST).date() - timedelta(days=1)
    days = []
    d = first_day
    while d <= last_day:
        if d.weekday() != 6:
            days.append(datetime(d.year, d.month, d.day, tzinfo=UTC) - IST)
        d += timedelta(days=1)
    alpha_ms = alpha[[i for i, t in enumerate(types) if t != "sunday"]]
    p_ms = turnbull(alpha_ms)
    boot_ms = np.array(
        [
            turnbull(alpha_ms[rng.integers(0, len(alpha_ms), len(alpha_ms))])
            for _ in range(args.boot)
        ]
    )
    cur = metrics(replay_staleness(split_caps, days, p_ms, rng))
    cur_boot = []
    for b in range(args.boot):
        dd = [days[i] for i in rng.integers(0, len(days), len(days))]
        cur_boot.append(metrics(replay_staleness(split_caps, dd, boot_ms[b], rng, n=4000)))

    # proposed schedules
    results: dict = {}
    k_max = 6
    scenarios = {"q0": 0.0, "q_measured": q_unavail}
    crn = {
        name: make_crn(p_ms, disp_pool, q, k_max, np.random.default_rng(SEED))
        for name, q in scenarios.items()
    }
    for variant, gap_limit in (("A_gap_le_460min", float(MAX_GAP_MIN)), ("B_no_gap_limit", None)):
        for k in (5, 6):
            vs, _ = optimise(crn["q0"], k, gap_limit, np.random.default_rng(SEED))
            entry: dict = {"visits_ist": [fmt(v) for v in vs], "max_gap_min": max_circular_gap(vs)}
            for name, c in crn.items():
                entry[name] = metrics(staleness_fixed(c.u, vs, c.lat, c.miss))
            left = make_crn(
                edge_placement(p_ms, groups, "left"),
                disp_pool,
                0.0,
                k_max,
                np.random.default_rng(SEED),
            )
            right = make_crn(
                edge_placement(p_ms, groups, "right"),
                disp_pool,
                0.0,
                k_max,
                np.random.default_rng(SEED),
            )
            entry["q0_edge_placement_left"] = metrics(
                staleness_fixed(left.u, vs, left.lat, left.miss)
            )
            entry["q0_edge_placement_right"] = metrics(
                staleness_fixed(right.u, vs, right.lat, right.miss)
            )
            boots = []
            for b in range(args.boot):
                cb = make_crn(boot_ms[b], disp_pool, 0.0, k_max, np.random.default_rng(SEED + b))
                cb.u = cb.u[:4000]
                cb.lat, cb.miss = cb.lat[:4000], cb.miss[:4000]
                boots.append(metrics(staleness_fixed(cb.u, vs, cb.lat, cb.miss)))
            entry["q0_ci95"] = evaluate_boot(boots)
            entry["_visits_min"] = vs
            results[f"{variant}_K{k}"] = entry

    # regret rule (c) on the recommended variant (A, K=6)
    rec_key = "A_gap_le_460min_K6"
    rec = results[rec_key]["_visits_min"]
    regrets, opt_times = [], []
    for b in range(args.reopt_boot):
        cb = make_crn(boot_ms[b], disp_pool, 0.0, k_max, np.random.default_rng(SEED + 10_000 + b))
        cb = CRN(cb.u[:4000], cb.lat[:4000], cb.miss[:4000])
        vs_b, c_b = optimise(cb, 6, float(MAX_GAP_MIN), np.random.default_rng(SEED + b), starts=4)
        c_rec = float(staleness_fixed(cb.u, rec, cb.lat, cb.miss).mean())
        regrets.append(c_rec - c_b)
        opt_times.append(vs_b)
    regret_p90 = float(np.quantile(regrets, 0.9))

    # GitHub-cron delivery of the same times, for comparison (delay pool = measured creation delay
    # lower bound + created->capture median; dropped triggers as misses)
    sh = lat_report["scrape_tanishq_selfhosted"]
    drop = max(0.0, 1 - sh["runs_created_per_day"] / sh["nominal_slots_per_day"])
    gh_delays = []
    sched = [r for r in runs_sh if r["event"] == "schedule"]
    first = min(_ts(r["createdAt"]) for r in sched)
    last = max(_ts(r["createdAt"]) for r in sched)
    slots = cron_slots(7, range(0, 24, 3), first - timedelta(hours=3), last)
    for r in sched:
        dl = preceding_slot_delay(_ts(r["createdAt"]), slots)
        if dl is not None:
            gh_delays.append(dl + float(np.median(disp_pool)))
    c_gh = make_crn(p_ms, np.asarray(gh_delays), drop, k_max, np.random.default_rng(SEED))
    gh_cron = metrics(staleness_fixed(c_gh.u, rec, c_gh.lat, c_gh.miss))

    window = measurement_window_widths(p_ms, split_caps, days, np.random.default_rng(SEED))

    for v in results.values():
        v.pop("_visits_min")
    evaluation = {
        "update_distribution": "pooled Mon-Sat change intervals (Sunday excluded)",
        "lateness_pool": "self-hosted runs with the runner online: created->capture minutes",
        "q_measured_share_runner_unavailable": round(q_unavail, 3),
        "current_schedule_replay": {
            "what": "actual capture instants since 2026-09-04, Mon-Sat IST days",
            "days": len(days),
            **cur,
            "ci95": evaluate_boot(cur_boot),
        },
        "proposed": results,
        "recommended": rec_key,
        "recommended_via_github_cron_instead": {
            "assumption": "same times as GitHub cron: measured delay lower bound + dispatch lag, "
            f"trigger drop rate {round(drop, 3)}",
            **gh_cron,
        },
        "measurement_window_proposal": window,
        "decision_rule_c": {
            "rule": "p90 regret <= 15 min over re-optimised bootstrap replicates",
            "replicates": args.reopt_boot,
            "regret_min": pct(regrets, (0.5, 0.9, 1.0)),
            "resolved": regret_p90 <= 15,
            "bootstrap_optimal_times_ist_p5_p95": [
                [
                    fmt(np.quantile([t[i] for t in opt_times], 0.05)),
                    fmt(np.quantile([t[i] for t in opt_times], 0.95)),
                ]
                for i in range(6)
            ],
        },
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, obj in (
        ("update_time_distribution", dist),
        ("schedule_evaluation", evaluation),
        ("scheduler_lateness", lat_report),
        ("kalyan_city_identity", kalyan_identity()),
    ):
        (OUT_DIR / f"{name}.json").write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "recommended": results[rec_key],
                "current": evaluation["current_schedule_replay"],
                "rule_c": evaluation["decision_rule_c"],
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
