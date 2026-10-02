"""Forward measurement of the timed Tanishq visits (GG decision E3). Run weekly by hand.

Reads the capture log only -- data/prices.json (UTC capture instants set by scraper/scrape.js)
and data/tanishq_scrape_outcomes.jsonl (one line per workflow run) -- plus the schedule in
scraper/visit_schedule.json. Not wired into anything user-facing.

Per detected rate change (consecutive captures that differ): the IST day it was seen, and its
staleness BOUNDS. Tanishq's real update instant is not observable; it lies in
(previous capture, this capture], so staleness is between 0 and that interval's width. The share
of changes whose width is <= 30/60 min is therefore a LOWER bound on the share captured within
30/60 min.

Per scheduled slot: captured (a live capture within the tolerance after the slot), attempted but
no capture (an outcomes-log run in the window without a capture), or missed (nothing at all --
laptop off/asleep, runner offline, or trigger never fired). Lateness = capture minus slot.

Per run: failures, and runs flagged "blocked" (Cloudflare challenge or HTTP 429/Retry-After
seen in the scraper's stderr; the flag is recorded from the E3 workflow change onwards).

Usage:
  python scripts/tanishq_visit_metrics.py [--days 7] [--end 2026-10-02] [--out FILE]
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PRICES = REPO_ROOT / "data" / "prices.json"
OUTCOMES = REPO_ROOT / "data" / "tanishq_scrape_outcomes.jsonl"
SCHEDULE = REPO_ROOT / "scraper" / "visit_schedule.json"
IST = timedelta(hours=5, minutes=30)


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def load_captures(prices: Sequence[dict]) -> list[tuple[datetime, tuple]]:
    """Live captures (history backfill excluded), sorted, one per timestamp."""
    by_ts: dict[datetime, tuple] = {}
    for e in prices:
        if "timestamp" not in e or "backfill" in str(e.get("source", "")):
            continue
        by_ts[_ts(e["timestamp"])] = (e.get("22k"), e.get("24k"), e.get("18k"))
    return sorted(by_ts.items())


def load_outcomes(lines: Sequence[str]) -> list[dict]:
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict) and "timestamp" in rec:
            rec["_t"] = _ts(rec["timestamp"])
            out.append(rec)
    return out


def slots_between(visits_ist: Sequence[str], start: datetime, end: datetime) -> list[datetime]:
    """Every scheduled slot (UTC instant) in [start, end)."""
    out = []
    day = (start + IST).date() - timedelta(days=1)
    last = (end + IST).date()
    while day <= last:
        for hhmm in visits_ist:
            h, m = (int(x) for x in hhmm.split(":"))
            t = datetime(day.year, day.month, day.day, h, m, tzinfo=UTC) - IST
            if start <= t < end:
                out.append(t)
        day += timedelta(days=1)
    return sorted(out)


def compute(
    captures: Sequence[tuple[datetime, tuple]],
    outcomes: Sequence[dict],
    schedule: dict,
    start: datetime,
    end: datetime,
) -> dict:
    tol = timedelta(minutes=int(schedule.get("tolerance_min", 45)))
    cap_times = [t for t, _ in captures]

    changes = []
    for (a, ka), (b, kb) in pairwise(captures):
        if ka != kb and start <= b < end:
            width = (b - a).total_seconds() / 60
            changes.append(
                {
                    "seen_ist": (b + IST).strftime("%Y-%m-%d %H:%M"),
                    "ist_day": (b + IST).date().isoformat(),
                    "previous_capture_ist": (a + IST).strftime("%Y-%m-%d %H:%M"),
                    "staleness_min_bounds": [0.0, round(width, 1)],
                }
            )

    slots = []
    for s in slots_between(schedule["visits_ist"], start, end):
        got = [t for t in cap_times if s <= t <= s + tol]
        runs = [r for r in outcomes if s <= r["_t"] <= s + tol]
        if got:
            status = "captured"
        elif runs:
            status = "attempted_no_capture"
        else:
            status = "missed"
        slots.append(
            {
                "slot_ist": (s + IST).strftime("%Y-%m-%d %H:%M"),
                "status": status,
                "lateness_min": round((got[0] - s).total_seconds() / 60, 1) if got else None,
            }
        )

    runs = [r for r in outcomes if start <= r["_t"] < end]
    widths = [c["staleness_min_bounds"][1] for c in changes]
    late = sorted(x["lateness_min"] for x in slots if x["lateness_min"] is not None)

    def share(pred: int) -> float | None:
        return round(sum(w <= pred for w in widths) / len(widths), 3) if widths else None

    def count(status: str) -> int:
        return sum(1 for x in slots if x["status"] == status)

    return {
        "window_utc": [start.isoformat(), end.isoformat()],
        "schedule_ist": list(schedule["visits_ist"]),
        "timestamp_convention": "prices.json timestamp = UTC capture instant; days are IST days",
        "changes_seen": len(changes),
        "staleness_upper_bound_min": {
            "median": sorted(widths)[len(widths) // 2] if widths else None,
            "max": max(widths) if widths else None,
        },
        "share_changes_captured_within_30min_lower_bound": share(30),
        "share_changes_captured_within_60min_lower_bound": share(60),
        "slots": {
            "scheduled": len(slots),
            "captured": count("captured"),
            "attempted_no_capture": count("attempted_no_capture"),
            "missed": count("missed"),
            "lateness_min_median": late[len(late) // 2] if late else None,
            "lateness_min_max": late[-1] if late else None,
        },
        "runs": {
            "total": len(runs),
            "success": sum(1 for r in runs if r.get("outcome") == "success"),
            "failure": sum(1 for r in runs if r.get("outcome") == "failure"),
            "blocked_or_challenged": sum(1 for r in runs if r.get("blocked") is True),
            "blocked_flag_recorded": sum(1 for r in runs if "blocked" in r),
        },
        "changes": changes,
        "slot_detail": slots,
    }


def main(argv: Sequence[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--end", type=date.fromisoformat, default=None, help="IST date (exclusive)")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    if args.end is None:
        end = datetime.now(UTC)
    else:
        end = datetime(args.end.year, args.end.month, args.end.day, tzinfo=UTC) - IST
    start = end - timedelta(days=args.days)
    schedule = json.loads(SCHEDULE.read_text(encoding="utf-8"))
    eff = schedule.get("effective_from_utc")
    if eff:
        start = max(start, _ts(eff))
    captures = load_captures(json.loads(PRICES.read_text(encoding="utf-8")))
    outcomes = load_outcomes(OUTCOMES.read_text(encoding="utf-8").splitlines())
    result = compute(captures, outcomes, schedule, start, end)
    text = json.dumps(result, indent=2) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
