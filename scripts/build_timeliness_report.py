"""scripts/build_timeliness_report.py -- how on time were the model's inputs this week? (brief 3d)

A model cannot beat its inputs' lateness. Two measurements, both from records GitHub keeps, so the
numbers are reproducible and nothing is typed in by hand:

  1. Tanishq visit slots (self-hosted scraper on GG's laptop): for each scheduled slot in the last
     ``DAYS`` days, was there a SUCCESSFUL run started by a dispatch within ``SLOT_WINDOW_MIN``
     minutes after the slot? A catch-up burst after the laptop was off serves the slot late, so a
     slot counts as served only inside the window. Approximation (INFERRED): a manual dispatch that
     lands inside a window counts as serving it.
  2. The overnight model window: for each recent night, how long after the US close (22:15 UTC)
     did the first check-price run that wrote a model forecast (``mode`` after_us_close) finish,
     and how many nights fell back to holding instead. Needs full git history of data/forecast.json;
     when the history is not available (shallow clone) this part says so and reports nothing.

Writes data/input_timeliness_weekly.json (data/ so the bot-pr-sync allow-list is unchanged). Never
fails the weekly job: any error is recorded in the JSON and the exit code is 0 (`--strict` for
local use).

Usage:
    python scripts/build_timeliness_report.py [--days 7] [--strict]
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "input_timeliness_weekly.json"
IST = timezone(timedelta(hours=5, minutes=30))
# Windows Task Scheduler slots on GG's laptop (IST); docs/FOR_GG.md and the schedule ADR.
SLOTS_IST = ["01:40", "07:30", "10:40", "11:10", "15:35", "19:50"]
SCHEDULE_START_IST = "2026-10-05"  # the day the timed visits began; earlier days had no slots
SLOT_WINDOW_MIN = 45
US_CLOSE_UTC = (22, 15)
DISPATCH_EVENTS = {"workflow_dispatch", "repository_dispatch"}
SCRAPE_WORKFLOW = "scrape-tanishq-selfhosted.yml"


def _run(cmd: list[str]) -> str:
    return subprocess.run(
        cmd, capture_output=True, text=True, check=True, cwd=ROOT, timeout=120
    ).stdout


def slot_report(now: datetime, days: int) -> dict[str, Any]:
    # One listing per dispatch event with the success filter applied by GitHub: an unfiltered list
    # is dominated by push and schedule runs and would not reach back far enough.
    runs: list[tuple[datetime, dict]] = []
    for event in sorted(DISPATCH_EVENTS):
        raw = _run(
            [
                "gh",
                "run",
                "list",
                "--workflow",
                SCRAPE_WORKFLOW,
                "--event",
                event,
                "--status",
                "success",
                "--limit",
                "300",
                "--json",
                "createdAt,event,conclusion",
            ]
        )
        runs += [
            (datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00")), r)
            for r in json.loads(raw)
            if r["event"] in DISPATCH_EVENTS and r["conclusion"] == "success"
        ]
    start = (now.astimezone(IST) - timedelta(days=days)).replace(hour=0, minute=0, second=0)
    rows, served, total = [], 0, 0
    day = max(start, datetime.fromisoformat(SCHEDULE_START_IST).replace(tzinfo=IST))
    while day.date() <= now.astimezone(IST).date():
        for hm in SLOTS_IST:
            h, m = map(int, hm.split(":"))
            slot = day.replace(hour=h, minute=m, second=0, microsecond=0)
            if slot + timedelta(minutes=SLOT_WINDOW_MIN) > now:
                continue  # window not over yet
            hit = [
                t
                for t, _ in runs
                if slot.astimezone(UTC)
                <= t
                < slot.astimezone(UTC) + timedelta(minutes=SLOT_WINDOW_MIN)
            ]
            total += 1
            served += bool(hit)
            rows.append({"slot_ist": slot.isoformat(), "served": bool(hit)})
        day += timedelta(days=1)
    return {
        "days": days,
        "slots": total,
        "served": served,
        "missed": total - served,
        "window_minutes": SLOT_WINDOW_MIN,
        "missed_slots_ist": [r["slot_ist"] for r in rows if not r["served"]],
        "marking": "INFERRED",
    }


def window_report(now: datetime, days: int) -> dict[str, Any]:
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    log = _run(["git", "log", f"--since={since}", "--format=%H %cI", "--", "data/forecast.json"])
    commits = [ln.split() for ln in log.splitlines() if ln.strip()]
    if not commits:
        return {"available": False, "reason": "no forecast.json history in this clone"}
    # The overnight window opens at the US close (22:15 UTC) and ends when IBJA's morning fix is out
    # (06:30 UTC): 495 minutes. Commits outside it say nothing about this window.
    window_min = 495
    nights: dict[str, dict[str, Any]] = {}
    for sha, iso in reversed(commits):
        t = datetime.fromisoformat(iso).astimezone(UTC)
        day = (t - timedelta(hours=US_CLOSE_UTC[0], minutes=US_CLOSE_UTC[1])).date()
        if day.weekday() >= 5:
            continue  # no US session on Saturday/Sunday, so no new world close to forecast from
        base = datetime(day.year, day.month, day.day, *US_CLOSE_UTC, tzinfo=UTC)
        offset = round((t - base).total_seconds() / 60)
        if not 0 <= offset < window_min:
            continue
        try:
            fc = json.loads(_run(["git", "show", f"{sha}:data/forecast.json"]))
        except (subprocess.CalledProcessError, ValueError):
            continue
        rec = nights.setdefault(day.isoformat(), {"offset": None})
        if (fc.get("next_fix") or {}).get("mode") == "after_us_close" and rec["offset"] is None:
            rec["offset"] = offset
    late = [r["offset"] for r in nights.values() if r["offset"] is not None]
    return {
        "available": True,
        "nights": len(nights),
        "nights_without_model_forecast": len(nights) - len(late),
        "nights_with_model_forecast": len(late),
        "minutes_after_us_close": sorted(late),
        "median_minutes_after_us_close": sorted(late)[len(late) // 2] if late else None,
        "marking": "VERIFIED",
    }


def build(now: datetime | None = None, days: int = 7, strict: bool = False) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    out: dict[str, Any] = {"schema_version": 1, "generated_at": now.isoformat(), "days": days}
    for key, fn in (("tanishq_slots", slot_report), ("overnight_window", window_report)):
        try:
            out[key] = fn(now, days)
        except Exception as exc:  # recorded, never fatal for the weekly job
            if strict:
                raise
            out[key] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()
    doc = build(days=args.days, strict=args.strict)
    OUT.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"OK: {OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
