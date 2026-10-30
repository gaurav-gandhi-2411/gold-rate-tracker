"""Page GG when what VISITORS see is stale: the live site's forecast and its latest Tanishq reading.

    python3 scripts/live_freshness.py --state-in S --state-out S [--simulate stale|fresh] [--dry-run]

Every other monitor reads repo or workflow state (lint on master, check-price's runs). On 2026-10-29
all of those looked fine or paged late while the page froze for ~19.5 h: an unpinned pandas-stubs
release turned lint red on bot PRs only, so no data reached master. This reads the published JSON
the page itself reads, so it catches a stop with ANY cause (CI, tokens, Pages, the laptop, a merge).

Limits, derived from the last 60 days of history (2026-10-30; run_cadence_log.jsonl and the live
data/prices.json):
- forecast predicted_at older than 9 h. check-price runs every 3 h; the 60-day p99 gap is 8.6 h and
  the only 4 gaps over 9 h were real outages (10-06 25.9 h, 10-08 13.4 h, 10-14 24.2 h, 10-29 19.3 h).
  A 6 h limit would have paged 50 times in the same 60 days (missed crons, not outages).
- latest Tanishq reading older than 20 h. Readings depend on the laptop, and 14-20 h overnight gaps
  are routine (20 of them over 14 h in 60 days); the 8 gaps over 20 h were all outages (CF blocks,
  the 10-06 and 10-14 stops, the 10-20 laptop overheat).

Once per incident: an incident is identified by the stale value itself (the predicted_at or the
reading timestamp that is too old), kept in a small state file the workflow carries between runs in
the Actions cache. A page goes out when an incident opens, one recovery message when the value is
fresh again, and nothing in between. If the site cannot be read, that is its own incident (fails
closed: a page, never silence). With no state (first run, cache evicted) an open incident pages
again once: a duplicate is acceptable, a missed page is not.

Exit codes: 0 everything fresh; 1 something stale or unreadable (the step shows red while it lasts).
Stdlib only: ci-health runs it on the runner's own python3 with nothing installed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SITE = "https://gaurav-gandhi-2411.github.io/gold-rate-tracker"
LIMITS_H = {"forecast": 9.0, "tanishq": 20.0}
LABELS = {"forecast": "the forecast (predicted_at)", "tanishq": "the latest Tanishq reading"}
RUNS_URL = "https://github.com/gaurav-gandhi-2411/gold-rate-tracker/actions"


def parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def latest_values(forecast: Any, prices: Any) -> dict[str, str | None]:
    """The timestamp each signal is judged on, as published; None when it cannot be read."""
    out: dict[str, str | None] = {"forecast": None, "tanishq": None}
    if isinstance(forecast, dict) and parse_ts(forecast.get("predicted_at")):
        out["forecast"] = forecast["predicted_at"]
    if isinstance(prices, list):
        stamps = [r.get("timestamp") for r in prices if isinstance(r, dict)]
        parsed = [(parse_ts(s), s) for s in stamps if parse_ts(s)]
        if parsed:
            out["tanishq"] = max(parsed)[1]
    return out


def decide(
    state: dict[str, Any], values: dict[str, str | None], now: datetime
) -> tuple[list[dict[str, str]], dict[str, Any], bool]:
    """(messages to send, new state, healthy). Pure: no I/O."""
    new = {k: v for k, v in state.items() if k in (*LIMITS_H, "site")}
    msgs: list[dict[str, str]] = []
    healthy = True
    unreadable = [k for k, v in values.items() if v is None]
    if unreadable:
        healthy = False
        if not new.get("site"):
            new["site"] = now.isoformat()
            msgs.append(
                {
                    "title": "Gold Tracker: cannot read the live site's data",
                    "priority": "high",
                    "body": f"Could not read {', '.join(LABELS[k] for k in unreadable)} from {SITE} "
                    "-- failing closed. Visitors may be seeing a broken or stale page.",
                }
            )
    elif new.get("site"):
        msgs.append(
            {
                "title": "Gold Tracker: live site data readable again",
                "priority": "default",
                "body": f"{SITE} data reads again (unreadable since {new.pop('site')}).",
            }
        )
    for key, limit in LIMITS_H.items():
        value = values.get(key)
        if value is None:
            continue  # judged as unreadable above; keep any open incident as it is
        age_h = (now - parse_ts(value)).total_seconds() / 3600  # type: ignore[operator]
        if age_h > limit:
            healthy = False
            if new.get(key) != value:
                new[key] = value
                msgs.append(
                    {
                        "title": f"Gold Tracker: visitors see stale data -- {key} is {age_h:.1f} h old",
                        "priority": "high",
                        "body": f"The live page's {LABELS[key]} is {value}, {age_h:.1f} h old "
                        f"(limit {limit:g} h). Nothing new has reached the site since then. "
                        f"Check check-price, bot PRs and Pages: {RUNS_URL}",
                    }
                )
        elif new.get(key):
            opened = new.pop(key)
            msgs.append(
                {
                    "title": f"Gold Tracker: {key} fresh again",
                    "priority": "default",
                    "body": f"The live page's {LABELS[key]} is {value} ({age_h:.1f} h old) -- "
                    f"recovered (stale value was {opened}).",
                }
            )
    return msgs, new, healthy


def simulated(kind: str, now: datetime) -> tuple[Any, Any]:
    """A payload for proving the alert end to end without touching real state."""
    if kind == "stale":
        f, t = now - timedelta(hours=12), now - timedelta(hours=30)
    else:
        f, t = now - timedelta(hours=1), now - timedelta(hours=2)
    return {"predicted_at": f.isoformat()}, [{"timestamp": t.isoformat(), "22k": 0}]


def fetch_json(url: str, attempts: int = 3) -> Any:
    last: Exception | None = None
    for i in range(attempts):
        try:
            sep = "&" if "?" in url else "?"
            req = urllib.request.Request(
                f"{url}{sep}nocache={int(time.time())}", headers={"Cache-Control": "no-cache"}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            last = exc
            time.sleep(5 * (i + 1))
    print(f"cannot read {url}: {type(last).__name__}")
    return None


def send(msg: dict[str, str], topic: str) -> bool:
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=msg["body"].encode("utf-8"),
        method="POST",
        headers={
            "Title": msg["title"],
            "Priority": msg["priority"],
            "Tags": "warning,hourglass" if msg["priority"] == "high" else "white_check_mark",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:
        print(f"ntfy send failed: {type(exc).__name__}")
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--state-in", type=Path)
    ap.add_argument("--state-out", type=Path)
    ap.add_argument("--now", help="ISO time (tests); default: the clock")
    ap.add_argument(
        "--simulate",
        choices=["stale", "fresh"],
        help="judge a made-up payload with empty state; titles get [TEST]",
    )
    ap.add_argument("--dry-run", action="store_true", help="print the messages, send nothing")
    args = ap.parse_args(argv)

    now = parse_ts(args.now) if args.now else datetime.now(UTC)
    assert now is not None
    state: dict[str, Any] = {}
    if args.simulate:
        forecast, prices = simulated(args.simulate, now)
    else:
        if args.state_in and args.state_in.exists():
            try:
                state = json.loads(args.state_in.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                print("state unreadable; starting empty (an open incident may page once more)")
        forecast = fetch_json(f"{SITE}/data/forecast.json")
        prices = fetch_json(f"{SITE}/data/prices.json")

    values = latest_values(forecast, prices)
    msgs, new_state, healthy = decide(state, values, now)
    for key in LIMITS_H:
        print(f"{key}: {values[key]} (limit {LIMITS_H[key]:g} h)")
    topic = os.environ.get("NTFY_TOPIC", "")
    sent_ok = True
    for msg in msgs:
        if args.simulate:
            msg = {**msg, "title": "[TEST] " + msg["title"]}
        print(f"PAGE: {msg['title']} -- {msg['body']}")
        if args.dry_run:
            continue
        if not topic:
            print("::error::a page is due but NTFY_TOPIC is empty")
            sent_ok = False
        elif not send(msg, topic):
            sent_ok = False
    if not msgs:
        print("no page: " + ("all fresh" if healthy else "incident already paged"))
    if args.state_out and not args.simulate:
        if not sent_ok:
            new_state = state  # keep the old state so the next run tries the page again
        args.state_out.parent.mkdir(parents=True, exist_ok=True)
        args.state_out.write_text(json.dumps(new_state, sort_keys=True), encoding="utf-8")
    return 0 if healthy and sent_ok else 1


if __name__ == "__main__":
    sys.exit(main())
