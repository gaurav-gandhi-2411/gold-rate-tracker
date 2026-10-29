"""scripts/check_price_health.py -- decide whether ci-health.yml should page GG about check-price.

check-price's own alerts (the T-catalogue, the stuck-bot-PR alert) run INSIDE check-price, so a run
that stops early cannot send them. Since #3316 a failed parquet round trip stops the run on purpose,
before any data is written; ci-health (an independent hourly workflow) is what notices instead.

Judged on check-price's completed runs on master, newest first (``--runs``):
  * FAILING when the newest completed run ended ``failure``, ``timed_out`` or ``startup_failure``;
    the failed step names are read from that run's jobs (``--jobs``) and put in the message.
    ``cancelled`` and ``skipped`` are not failures (a newer run can cancel an older one); the
    staleness rule below still catches a run of them.
  * STALE when no run has succeeded within ``--max-gap-hours`` of ``--now``. Over 100 runs from
    2026-10-18 to 10-29 the longest gap between successes was 7.87 h (median 2.46 h), so 9 h.

Fails CLOSED (exit 2) on anything unreadable: no runs, or a run without a conclusion or time.

Exit codes: 0 OK; 1 FAILING or STALE; 2 cannot judge. Prints one line: ``OK ...`` / ``FAILING: ...``
/ ``STALE: ...`` / ``CANNOT JUDGE: ...``.

    python scripts/check_price_health.py --runs runs.json --jobs jobs.json --now 2026-10-29T06:00:00Z
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

FAILED = {"failure", "timed_out", "startup_failure"}
MAX_GAP_HOURS = 9.0


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def failed_steps(jobs: list[dict[str, Any]]) -> list[str]:
    return [
        str(step.get("name"))
        for job in jobs
        for step in job.get("steps") or []
        if step.get("conclusion") in FAILED
    ]


def judge(
    runs: list[dict[str, Any]],
    jobs: list[dict[str, Any]],
    now: datetime,
    max_gap_hours: float = MAX_GAP_HOURS,
) -> tuple[int, str]:
    if not runs:
        return 2, "CANNOT JUDGE: no completed check-price run on master was listed"
    for r in runs:
        if not r.get("conclusion") or not r.get("created_at"):
            return 2, f"CANNOT JUDGE: run {r.get('id')} has no conclusion or creation time"
    newest = runs[0]
    if newest["conclusion"] in FAILED:
        steps = failed_steps(jobs)
        where = ", ".join(steps) if steps else "step not identified"
        return 1, (
            f"FAILING: check-price run {newest['id']} ({newest['created_at']}) ended "
            f"{newest['conclusion']} at: {where}. The forecast and data were not updated by it."
        )
    ok = [r for r in runs if r["conclusion"] == "success"]
    last = max((_ts(r["created_at"]) for r in ok), default=None)
    if last is None or (now - last).total_seconds() > max_gap_hours * 3600:
        since = last.isoformat() if last else "none in the listed runs"
        return (
            1,
            f"STALE: no successful check-price run on master within {max_gap_hours:g} h (last: {since})",
        )
    return (
        0,
        f"OK: newest check-price run {newest['id']} {newest['conclusion']}; last success {last.isoformat()}",
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--runs", type=Path, required=True)
    ap.add_argument("--jobs", type=Path, required=True)
    ap.add_argument("--now", required=True)
    ap.add_argument("--max-gap-hours", type=float, default=MAX_GAP_HOURS)
    args = ap.parse_args(argv)
    try:
        runs = json.loads(args.runs.read_text(encoding="utf-8"))
        jobs = json.loads(args.jobs.read_text(encoding="utf-8"))
        if not isinstance(runs, list) or not isinstance(jobs, list):
            raise ValueError("runs and jobs must be JSON lists")
        code, line = judge(runs, jobs, _ts(args.now), args.max_gap_hours)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        code, line = 2, f"CANNOT JUDGE: {type(exc).__name__}: {exc}"
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
