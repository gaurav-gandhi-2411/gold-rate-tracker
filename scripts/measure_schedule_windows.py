"""Measure the two 7-day windows opened on 2026-09-11 (audit AK4) and write a report.

Window 1 (PR #1541): did moving check-price.yml's cron phase change when scheduled runs
are delivered, and did the recurring 6.5h+ gaps in data/run_cadence_log.jsonl go away?
Window 2 (PR #1403): how often would the dead-man's switch have reached WARN (>=10h)?

Two sources are used so neither carries the whole result:
  - `git log -- data/forecast.json` for the predicted_at series the Worker reads (its age
    is what pages), replayed against the Worker's own */30 tick schedule;
  - `gh run list --event schedule` for the runs GitHub actually created.

Per-slot delay attribution is deliberately NOT reported: delays of 1-5h are routine against a
3h slot spacing, so a run cannot be tied to the slot that produced it. The hour of day at which
runs were actually created needs no attribution and is what the comparison uses.

Usage:  python scripts/measure_schedule_windows.py --out reports/schedule_windows_2026-09-18.json
Requires: git, gh (authenticated), run from anywhere (repo root is derived from this file).
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import subprocess
from collections import Counter
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WINDOW = timedelta(days=7)
# The moments each window opened (PR #1541 merge, and the Worker deploy for PR #1403).
CADENCE_START = datetime(2026, 9, 11, 2, 45, 48, tzinfo=UTC)
WARN_START = datetime(2026, 9, 11, 8, 39, 10, tzinfo=UTC)
WARN_H, ESCALATE_H = 10, 16  # worker-deadman/src/deadman.mjs
TICK_MIN = 30  # worker-deadman/wrangler.toml cron */30


def _run(cmd: list[str]) -> str:
    return subprocess.run(
        cmd, capture_output=True, text=True, cwd=REPO_ROOT, check=True, encoding="utf-8"
    ).stdout


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def measure_warn_window() -> dict:
    out = _run(
        [
            "git",
            "log",
            "origin/master",
            "--since=2026-09-09",
            "--until=2026-09-19",
            "--format=%H %cI",
            "--",
            "data/forecast.json",
        ]
    )
    series: list[tuple[datetime, datetime]] = []
    for line in out.splitlines():
        sha, committed = line.split(" ", 1)
        body = json.loads(_run(["git", "show", f"{sha}:data/forecast.json"]))
        series.append((_ts(committed), _ts(body["predicted_at"])))
    series.sort()
    end = WARN_START + WINDOW
    ages: list[float] = []
    t = WARN_START.replace(
        minute=0 if WARN_START.minute < TICK_MIN else TICK_MIN, second=0, microsecond=0
    )
    while t < end:
        if t >= WARN_START:
            seen = [pa for ct, pa in series if ct <= t]
            if seen:
                ages.append((t - max(seen)).total_seconds() / 3600)
        t += timedelta(minutes=TICK_MIN)
    pa_sorted = sorted(pa for _, pa in series)
    gaps = [
        (b - a).total_seconds() / 3600 for a, b in pairwise(pa_sorted) if WARN_START <= b <= end
    ]
    return {
        "window": [WARN_START.isoformat(), end.isoformat()],
        "forecast_commits_parsed": len(series),
        "ticks": len(ages),
        "age_h": {
            "median": round(st.median(ages), 2),
            "p90": round(_pct(ages, 0.9), 2),
            "max": round(max(ages), 2),
        },
        "ticks_ge_warn": sum(a >= WARN_H for a in ages),
        "ticks_ge_escalate": sum(a >= ESCALATE_H for a in ages),
        "gaps": {
            "n": len(gaps),
            "median_h": round(st.median(gaps), 2),
            "p90_h": round(_pct(gaps, 0.9), 2),
            "max_h": round(max(gaps), 2),
            "n_ge_warn": sum(g >= WARN_H for g in gaps),
        },
        # rule of three: 0 events in n trials bounds the per-trial rate at ~1-0.05^(1/n)
        "zero_event_95pct_upper_bound_per_gap": round(1 - 0.05 ** (1 / len(gaps)), 3),
    }


def _runs(workflow: str) -> list[datetime]:
    raw = _run(
        [
            "gh",
            "run",
            "list",
            "--workflow",
            workflow,
            "--event",
            "schedule",
            "--limit",
            "400",
            "--json",
            "createdAt",
        ]
    )
    return sorted(_ts(r["createdAt"]) for r in json.loads(raw))


def measure_delivery() -> dict:
    pre_start, end = CADENCE_START - WINDOW, CADENCE_START + WINDOW
    result: dict = {"cadence_window": [CADENCE_START.isoformat(), end.isoformat()]}
    for wf in ("check-price.yml", "scrape-tanishq-selfhosted.yml"):
        runs = _runs(wf)
        pre = [r for r in runs if pre_start <= r < CADENCE_START]
        post = [r for r in runs if CADENCE_START <= r < end]
        result[wf] = {
            "runs_pre_7d": len(pre),
            "runs_post_7d": len(post),
            "expected_if_every_3h_slot_fired": 56,
            "created_hour_utc_pre": dict(sorted(Counter(r.hour for r in pre).items())),
            "created_hour_utc_post": dict(sorted(Counter(r.hour for r in post).items())),
            "days_with_run_created_06_to_09Z_pre": len({r.date() for r in pre if 6 <= r.hour < 9}),
            "days_with_run_created_06_to_09Z_post": len(
                {r.date() for r in post if 6 <= r.hour < 9}
            ),
        }
    for wf, label in (("lint.yml", "lint_06:00"), ("shadow-fusion.yml", "shadow_fusion_06:15")):
        runs = [r for r in _runs(wf) if CADENCE_START <= r < end]
        slot_min = 0 if wf == "lint.yml" else 15
        delays = [
            (r - r.replace(hour=6, minute=slot_min, second=0, microsecond=0)).total_seconds() / 60
            for r in runs
            if 6 <= r.hour < 12
        ]
        result[label] = {
            "n": len(delays),
            "median_delay_min": round(st.median(delays), 1),
            "p90_delay_min": round(_pct(delays, 0.9), 1),
        }
    return result


def measure_cadence_log() -> dict:
    lines = (REPO_ROOT / "data/run_cadence_log.jsonl").read_text(encoding="utf-8").splitlines()
    ts = sorted(_ts(json.loads(x)["timestamp"]) for x in lines if x.strip())
    gaps = [((b - a).total_seconds() / 3600, b) for a, b in pairwise(ts)]
    out: dict = {}
    for name, lo, hi in (
        ("pre", CADENCE_START - WINDOW, CADENCE_START),
        ("post", CADENCE_START, CADENCE_START + WINDOW),
    ):
        g = [h for h, b in gaps if lo <= b < hi]
        out[name] = {
            "n_gaps": len(g),
            "median_h": round(st.median(g), 2),
            "p90_h": round(_pct(g, 0.9), 2),
            "max_h": round(max(g), 2),
            "n_ge_6_5h": sum(x >= 6.5 for x in g),
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    report = {
        "generated_from_origin_master": _run(["git", "rev-parse", "origin/master"]).strip(),
        "warn_window": measure_warn_window(),
        "delivery": measure_delivery(),
        "cadence_log": measure_cadence_log(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
