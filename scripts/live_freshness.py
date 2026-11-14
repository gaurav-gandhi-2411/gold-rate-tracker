"""Page GG when what VISITORS see is stale: forecast, model forecast, Tanishq reading.

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

- NO MODEL FORECAST for longer than 9 h. forecast.json's next_fix block is the model; "paused" is
  next_fix.active not being the boolean true (2026-11-12 13:10Z to 11-13 21:54Z it was false on
  every run, the page showed a plain "no change" range, and nothing paged: the 9 h forecast limit
  above does not see it because forecast.json kept updating). Holding the latest fix (headline
  method hold_latest_fix_*) is the model working with next_fix.active true, never a page.
  Measured on origin/master b187caea, every commit of data/forecast.json since 2026-09-14
  (495 commits; the next_fix block exists only since 2026-10-02: 387 of them, 372 with active true,
  of which 142 are hold windows, 15 with active false, all from the 2026-11-12/13 ZeroDivisionError):
    * 371 gaps between consecutive active-true forecasts; the 362 that are at or under the 9 h
      freshness limit have a longest of 8.96 h (p99 8.37 h). The 9 gaps over 9 h are the 4 outages
      above plus 5 in 11-08..13 (9.6 h, 12.4 h, 29.8 h, 18.0 h and 35.7 h).
    * paused streaks (first active-false forecast to the next active-true one): 2, of 6.3 h (a one-run
      blip on 11-12 03:18Z that healed by itself on the next cron) and 32.7 h (11-12 13:10Z to 11-13
      21:54Z, the incident). Before 11-12 the model was never paused.
  The limit is 9 h: the longest normal gap between forecasts, so a pause that outlasts it is not one
  missed cron but at least three runs in a row without a model forecast. The 6.3 h blip stays quiet
  (a 6 h limit would have paged it); the real 32.7 h incident pages 9 h after it began, not 32.7 h.
  Since when: ml/inference.py writes next_fix.down_since (the start of the streak). When the key is
  absent (a forecast.json from before it existed) this uses the first time it saw the pause, kept in
  the state, else the forecast's own predicted_at.

One page per INCIDENT, not per signal (2026-11-07..13: GG got 18 pushes for 3 outages; each signal and
each share-card kind was its own incident with its own open and its own recovery page, and the 9 h
limit flapped: forecast gaps of 9-12 h recurred on 11-09, 11-10, 11-11 and 11-12, each a new page).
The state file (carried in the Actions cache) holds ONE site-level incident: when it opened and
every signal that has gone bad since (forecast, tanishq, model, site). A page goes out when the
incident opens, listing every bad signal; a signal that goes bad while it is open joins it with no
page; and ONE recovery message goes out when ALL signals have been fresh for at least 2 checks in a
row AND at least 1 h. A signal going bad again inside that window silently continues the same
incident. A long incident is not silent for good: while it stays open and something is still bad,
ONE "still stale after N h" page goes out every 24 h (state: last_paged_at), so at most one push a
day. A damaged incident record in the state (unparseable time, non-numeric count) counts as no
incident: if the site is stale it pages once like a first run. Why 2 checks and 1 h (tests/test_live_freshness_replay.py replays the real 46 runs of
2026-11-07..14): a single fresh check was followed by a stale one on 11-11 (run at 22:44Z fresh,
03:23Z stale), which with 1 check would be a recovery plus a new incident; checks normally run
about 3 h apart, but two ran 28 min apart on 11-12 (a schedule run and a dispatch) and two 19 s
apart, which must not count as two independent checks, hence the 1 h floor.
If the site cannot be read, that is a signal too (fails closed: a page, never silence). With no
state (first run, cache evicted) an open incident pages again once: a duplicate is acceptable, a
missed page is not.

scripts/live_share_card.py reads this state file (--freshness-state) and stays silent about its
"behind" and "stale" kinds while the incident is open: they are consequences of the same outage.

Exit codes: 0 everything fresh; 1 an incident is open (the step shows red while it lasts, including
the hysteresis wait) or a page could not be sent.
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
# No model forecast (next_fix.active not true) for longer than this: derived in the docstring.
MODEL_PAUSED_H = 9.0
# An incident closes only after this many consecutive fully fresh checks spanning this many hours.
RECOVERY_CHECKS = 2
RECOVERY_H = 1.0
# While an incident stays open it pages again this often: at most one push a day.
REPAGE_H = 24.0
LABELS = {"forecast": "the forecast (predicted_at)", "tanishq": "the latest Tanishq reading"}
RUNS_URL = "https://github.com/gaurav-gandhi-2411/gold-rate-tracker/actions"
# Clock skew allowed before a published timestamp counts as "in the future" (runner vs. scraper).
FUTURE_TOL = timedelta(minutes=5)


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


def model_pause(forecast: Any) -> tuple[bool, str | None] | None:
    """(paused, down_since) from forecast.json's next_fix block, None when forecast is unreadable.

    Paused is next_fix.active not being the boolean true (a missing block, "true" the string and
    false all count). Holding the latest fix is active true, so never paused. down_since is the
    block's own key (ml/inference.py) when it parses, else None: the caller falls back."""
    if not isinstance(forecast, dict):
        return None
    nf = forecast.get("next_fix")
    nf = nf if isinstance(nf, dict) else {}
    if nf.get("active") is True:
        return False, None
    since = nf.get("down_since")
    return True, since if parse_ts(since) else None


def _short(key: str, age_h: float) -> str:
    return {
        "forecast": f"forecast is {age_h:.1f} h old",
        "tanishq": f"tanishq is {age_h:.1f} h old",
        "model": f"no model forecast for {age_h:.1f} h",
        "site": "site data unreadable",
    }[key]


def _valid_incident(inc: dict[str, Any]) -> bool:
    """True when every field decide() reads from a stored incident is usable."""
    checks = inc.get("fresh_checks", 0)
    return (
        isinstance(inc.get("signals"), dict)
        and parse_ts(inc.get("opened_at")) is not None
        and (inc.get("last_paged_at") is None or parse_ts(inc["last_paged_at"]) is not None)
        and (inc.get("fresh_since") is None or parse_ts(inc["fresh_since"]) is not None)
        and isinstance(checks, int)
        and not isinstance(checks, bool)
    )


def decide(
    state: dict[str, Any],
    values: dict[str, str | None],
    now: datetime,
    model: tuple[bool, str | None] | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any], bool]:
    """(messages to send, new state, healthy). Pure: no I/O.

    ONE site-level incident (state["incident"]): opens with one page listing every bad signal, takes
    in signals that go bad later without paging, closes with one recovery page after RECOVERY_CHECKS
    fully fresh checks spanning RECOVERY_H hours. healthy is False while it is open.
    `model` is model_pause(forecast), or None when it was not judged (forecast unreadable)."""
    new: dict[str, Any] = {k: v for k, v in state.items() if k in ("incident", "model_since")}
    inc = new.get("incident") if isinstance(new.get("incident"), dict) else None
    legacy = [k for k in (*LIMITS_H, "site") if state.get(k)]  # state of the per-signal version
    if inc is None and legacy:  # an incident already paged under the old scheme: adopt it quietly
        inc = {
            "opened_at": now.isoformat(),
            "last_paged_at": now.isoformat(),
            "signals": {k: str(state[k]) for k in legacy},
        }
    # A timestamp in the future gives a negative age and would read as fresh for as long as it stays
    # ahead of the clock, hiding a real outage behind it, so it counts as unreadable (fail closed).
    future = [k for k, v in values.items() if v is not None and parse_ts(v) > now + FUTURE_TOL]  # type: ignore[operator]
    values = {k: None if k in future else v for k, v in values.items()}
    unreadable = [k for k, v in values.items() if v is None]
    bad: dict[str, str] = {}  # signal -> short phrase for the title
    detail: dict[str, str] = {}  # signal -> sentence for the body
    if unreadable:
        why = " (a timestamp is in the future)" if future else ""
        bad["site"] = _short("site", 0)
        detail["site"] = (
            f"Could not read {', '.join(LABELS[k] for k in unreadable)} from {SITE}{why} "
            "-- failing closed. Visitors may be seeing a broken or stale page."
        )
    for key, limit in LIMITS_H.items():
        value = values.get(key)
        if value is None:
            continue  # judged as unreadable above
        age_h = (now - parse_ts(value)).total_seconds() / 3600  # type: ignore[operator]
        if age_h > limit:
            bad[key] = _short(key, age_h)
            detail[key] = (
                f"The live page's {LABELS[key]} is {value}, {age_h:.1f} h old (limit {limit:g} h). "
                "Nothing new has reached the site since then."
            )
    if model is not None and model[0]:
        since = model[1] or new.get("model_since") or values.get("forecast")
        if since and parse_ts(since):
            new["model_since"] = since
            age_h = (now - parse_ts(since)).total_seconds() / 3600  # type: ignore[operator]
            if age_h > MODEL_PAUSED_H:
                bad["model"] = _short("model", age_h)
                detail["model"] = (
                    f"The live forecast has had no model forecast since {since} ({age_h:.1f} h, "
                    f"limit {MODEL_PAUSED_H:g} h): the page shows a plain 'no change' range."
                )
    elif model is not None:  # active again: the streak is over (an unjudged run keeps it)
        new.pop("model_since", None)
    if inc is not None and not _valid_incident(inc):
        # Damaged state is no incident, never a silently wrong one: if the site is stale this opens
        # a new incident and pages like a first run (a duplicate, never a silence); if it is fresh
        # the damaged record is dropped.
        inc = None
    msgs: list[dict[str, str]] = []
    if bad and inc is None:
        inc = {
            "opened_at": now.isoformat(),
            "last_paged_at": now.isoformat(),
            "signals": {k: now.isoformat() for k in bad},
        }
        only_site = set(bad) == {"site"}
        msgs.append(
            {
                "title": "Gold Tracker: cannot read the live site's data"
                if only_site
                else "Gold Tracker: visitors see stale data -- " + "; ".join(bad.values()),
                "priority": "high",
                "body": " ".join(detail.values())
                + f" Check check-price, bot PRs and Pages: {RUNS_URL}",
            }
        )
    elif inc is not None:
        if bad:
            for k in bad:  # joins the open incident: no new page
                inc["signals"].setdefault(k, now.isoformat())
            inc.pop("fresh_checks", None)
            inc.pop("fresh_since", None)
            # A long incident must not go silent for good: one "still stale" page per REPAGE_H.
            last = parse_ts(inc.get("last_paged_at")) or parse_ts(inc["opened_at"])
            held_h = (now - last).total_seconds() / 3600  # type: ignore[operator]
            if held_h >= REPAGE_H:
                inc["last_paged_at"] = now.isoformat()
                opened_h = (now - parse_ts(inc["opened_at"])).total_seconds() / 3600  # type: ignore[operator]
                msgs.append(
                    {
                        "title": f"Gold Tracker: still stale after {opened_h:.0f} h -- "
                        + "; ".join(bad.values()),
                        "priority": "high",
                        "body": f"The incident opened {inc['opened_at']} is still open "
                        + " ".join(detail.values())
                        + f" Check check-price, bot PRs and Pages: {RUNS_URL}",
                    }
                )
        else:
            inc["fresh_checks"] = int(inc.get("fresh_checks", 0)) + 1
            inc.setdefault("fresh_since", now.isoformat())
            waited_h = (now - parse_ts(inc["fresh_since"])).total_seconds() / 3600  # type: ignore[operator]
            if inc["fresh_checks"] >= RECOVERY_CHECKS and waited_h >= RECOVERY_H:
                msgs.append(
                    {
                        "title": "Gold Tracker: live site data is fresh again",
                        "priority": "default",
                        "body": f"Recovered: everything the live site shows has been fresh for "
                        f"{inc['fresh_checks']} checks over {waited_h:.1f} h. The incident opened "
                        f"{inc['opened_at']} and covered {', '.join(inc['signals'])}.",
                    }
                )
                inc = None
    if inc is None:
        new.pop("incident", None)
    else:
        new["incident"] = inc
    return msgs, new, inc is None


def simulated(kind: str, now: datetime) -> tuple[Any, Any]:
    """A payload for proving the alert end to end without touching real state."""
    if kind == "stale":
        f, t = now - timedelta(hours=12), now - timedelta(hours=30)
    else:
        f, t = now - timedelta(hours=1), now - timedelta(hours=2)
    forecast = {"predicted_at": f.isoformat(), "next_fix": {"active": True}}
    return forecast, [{"timestamp": t.isoformat(), "22k": 0}]


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
            if not isinstance(state, dict):
                print(
                    "state is not an object; starting empty (an open incident may page once more)"
                )
                state = {}
        forecast = fetch_json(f"{SITE}/data/forecast.json")
        prices = fetch_json(f"{SITE}/data/prices.json")

    values = latest_values(forecast, prices)
    model = model_pause(forecast)
    msgs, new_state, healthy = decide(state, values, now, model)
    for key in LIMITS_H:
        print(f"{key}: {values[key]} (limit {LIMITS_H[key]:g} h)")
    print(f"model: {'paused' if model and model[0] else 'active' if model else 'unreadable'}")
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
        print("no page: " + ("all fresh" if healthy else "incident already paged, or recovering"))
    if args.state_out and not args.simulate:
        if not sent_ok:
            new_state = state  # keep the old state so the next run tries the page again
        args.state_out.parent.mkdir(parents=True, exist_ok=True)
        args.state_out.write_text(json.dumps(new_state, sort_keys=True), encoding="utf-8")
    return 0 if healthy and sent_ok else 1


if __name__ == "__main__":
    sys.exit(main())
