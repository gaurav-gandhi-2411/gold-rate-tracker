"""Page GG when the published share card (og.png) is broken, wrong, gives advice, or is out of date.

    python3 scripts/live_share_card.py --state-in S --state-out S [--simulate bad|good] [--dry-run]

From its first og.png (2026-05-15) to 2026-11-01 the card read a price key no row has, so every shared
link showed "₹NaN" and "buy when convenient", and nothing noticed for 5.5 months: every run was green.
This reads what the site publishes. `data/og-card.json` is the sidecar scripts/screenshot-og.mjs writes
beside og.png from the very values it drew, so it is judged here against the live data/prices.json and
data/forecast.json, the files the page itself reads.

Checks, one incident kind each:
- unreadable: the sidecar, prices.json or forecast.json cannot be read (fails closed: a page).
- broken:     a value is missing or not a number, or the card's text has NaN / undefined / null /
              Infinity / Invalid Date in it (the 2026-05 to 2026-11 failure).
- advice:     the card's text tells anyone to buy, sell, wait or invest. The card describes the week; it
              never advises (og.html, i18n.js verdictHeadline*).
- disagree:   the card's price is not the published value at the card's own as-of time, or the card is
              as of a time the published data does not have.
- behind:     newer data of the card's kind has been published for more than BEHIND_H, so the card
              should have been rebuilt and was not.
- stale:      the card's as-of time is older than the freshness limit for its kind
              (scripts/live_freshness.py LIMITS_H: a Tanishq reading 20 h, the model's estimate 9 h).

Kinds of card (og.html heroPrice, sidecar "kind"):
- "tanishq":  Tanishq's listed rate, as of a non-derived prices.json row or forecast.scraped_at.
- "estimate": the model's figure for today, forecast.current_22k as of forecast.predicted_at.
- "derived":  the newest prices.json row when it is an IBJA-calibrated estimate row.

BEHIND_H = 9: over the 30 days to 2026-11-02, og.png reached master within 0.33 h (median) of a
prices.json change, p90 5.7 h, p95 7.6 h; the gaps over 9 h fell on outage days (10-06, 10-12, 10-14).

Once per incident, as scripts/live_freshness.py: the state file (carried in the Actions cache) holds
each open kind and when it opened. A page when a kind opens, one recovery message when it clears,
nothing in between. With no state (first run, cache evicted) an open incident pages once more: a
duplicate is acceptable, a missed page is not.

Exit codes: 0 the card is fine; 1 an incident is open (the step shows red while it lasts).
Stdlib only: ci-health runs it on the runner's own python3 with nothing installed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from live_freshness import FUTURE_TOL, LIMITS_H, SITE, fetch_json, parse_ts, send

BEHIND_H = 9.0
TANISHQ_MAX_DEV = 0.12  # og.html / app.js TANISHQ_IBJA_MAX_DEVIATION: the page's plausibility gate
STALE_H = {
    "tanishq": LIMITS_H["tanishq"],
    "derived": LIMITS_H["tanishq"],
    "estimate": LIMITS_H["forecast"],
}
KINDS = ("unreadable", "broken", "advice", "disagree", "behind", "stale")
# Page titles: what is wrong, then what it is now that it has cleared.
TITLES = {
    "unreadable": ("cannot be checked (site data unreadable)", "can be checked again"),
    "broken": ("is broken (NaN or a missing value)", "is no longer broken"),
    "advice": ("gives buy/sell advice", "no longer gives advice"),
    "disagree": ("disagrees with the published price", "agrees with the published price again"),
    "behind": ("was not rebuilt after new data", "has caught up with the data"),
    "stale": ("is out of date", "is up to date again"),
}
CARD_URL = f"{SITE}/og.png"
SIDECAR = "data/og-card.json"
REQUIRED = (
    "price",
    "price_text",
    "kind",
    "label",
    "as_of",
    "verdict_text",
    "card_text",
    "built_at",
)
BROKEN_TEXT = re.compile(r"NaN|undefined|\bnull\b|Infinity|Invalid Date")
# Advice wording. "Not financial advice" (the footer) and "Is it a good price?" (the eyebrow) are not
# advice; anything that tells the reader what to do with their money is.
ADVICE = re.compile(
    r"\b(buy|buying|sell|selling|invest|investing|purchase)\b"
    r"|\b(good|right|best|bad) time to\b|\bwait (for|before|to|until)\b|\bhold off\b|\bstock up\b",
    re.IGNORECASE,
)
PRICE_TEXT = re.compile(r"^(≈ )?\d{1,3}(,\d{2})*(,\d{3})?$|^(≈ )?\d+$")
DERIVED_PREFIX = "ibja_calibrated"


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _points(prices: Any, forecast: Any) -> dict[str, list[tuple[datetime, float]]]:
    """Published (time, 22K value) points by card kind."""
    pts: dict[str, list[tuple[datetime, float]]] = {"tanishq": [], "derived": [], "estimate": []}
    for r in prices if isinstance(prices, list) else []:
        if not isinstance(r, dict) or not _num(r.get("22k")):
            continue
        ts = parse_ts(r.get("timestamp"))
        if ts is None:
            continue
        derived = isinstance(r.get("source"), str) and r["source"].startswith(DERIVED_PREFIX)
        pts["derived" if derived else "tanishq"].append((ts, float(r["22k"])))
    if isinstance(forecast, dict) and _num(forecast.get("current_22k")):
        for key, kind in (("scraped_at", "tanishq"), ("predicted_at", "estimate")):
            ts = parse_ts(forecast.get(key))
            if ts is not None:
                pts[kind].append((ts, float(forecast["current_22k"])))
    return pts


def findings(card: Any, prices: Any, forecast: Any, now: datetime) -> dict[str, str]:
    """{kind: one-line reason} for every problem found. Pure: no I/O."""
    if not isinstance(card, dict) or not isinstance(prices, list) or not isinstance(forecast, dict):
        what = [
            name
            for name, ok in (
                (SIDECAR, isinstance(card, dict)),
                ("data/prices.json", isinstance(prices, list)),
                ("data/forecast.json", isinstance(forecast, dict)),
            )
            if not ok
        ]
        return {"unreadable": f"could not read {', '.join(what)} from {SITE}"}

    out: dict[str, str] = {}
    missing = [k for k in REQUIRED if card.get(k) in (None, "", "—")]
    text = " ".join(
        str(card.get(k, "")) for k in ("price_text", "label", "verdict_text", "card_text")
    )
    as_of = parse_ts(card.get("as_of"))
    built = parse_ts(card.get("built_at"))
    bad = []
    if missing:
        bad.append(f"missing {', '.join(missing)}")
    if "price" not in missing and not _num(card.get("price")):
        bad.append(f"price is {card.get('price')!r}, not a number")
    if "price_text" not in missing and not PRICE_TEXT.match(str(card["price_text"])):
        bad.append(f"the price reads {card['price_text']!r}")
    if m := BROKEN_TEXT.search(text):
        bad.append(f"the card says {m.group(0)!r}")
    if "as_of" not in missing and as_of is None:
        bad.append(f"as_of {card['as_of']!r} is not a time")
    if "built_at" not in missing and built is None:
        bad.append(f"built_at {card['built_at']!r} is not a time")
    for name, ts in (("as_of", as_of), ("built_at", built)):
        if ts is not None and ts > now + FUTURE_TOL:
            bad.append(f"{name} {card[name]} is in the future")
    kind = card.get("kind")
    if kind not in STALE_H:
        bad.append(f"kind {kind!r} is not one of {', '.join(STALE_H)}")
    if bad:
        out["broken"] = "; ".join(bad)
    if m := ADVICE.search(text):
        out["advice"] = f"the card's text contains {m.group(0)!r}: {text[:160]}"
    if "broken" in out:
        return out  # the agreement and age checks need a sound card

    assert as_of is not None and kind in STALE_H
    price = float(card["price"])
    pts = _points(prices, forecast)
    same = [v for ts, v in pts[kind] if ts == as_of]
    newest = max((ts for ts, _ in pts[kind]), default=None)
    if same:
        if all(abs(v - price) >= 0.5 for v in same):
            out["disagree"] = (
                f"the card shows ₹{price:,.0f} as of {card['as_of']}, the published value then is "
                f"₹{same[-1]:,.0f}"
            )
    elif newest is None or as_of > newest:
        out["disagree"] = (
            f"the card is as of {card['as_of']} ({kind}), a time the published data does not have"
        )

    # Newer data the card should have picked up: of its own kind, and for an estimate card also a
    # newer real Tanishq reading (the page shows a fresh reading before the estimate), but only one
    # the page would accept: a reading more than TANISHQ_MAX_DEV off the estimate is rejected by the
    # page's plausibility gate (og.html / app.js TANISHQ_IBJA_MAX_DEVIATION) and never reaches the
    # hero, so it must not make the card look behind.
    newer = [ts for ts, _ in pts[kind] if ts > as_of]
    if kind == "estimate":
        newer += [
            ts
            for ts, v in pts["tanishq"]
            if ts > as_of and abs(v - price) / price <= TANISHQ_MAX_DEV
        ]
    if newer:
        first = min(newer)
        lag_h = (now - first).total_seconds() / 3600
        if lag_h > BEHIND_H:
            out["behind"] = (
                f"newer data ({first.isoformat()}) has been out {lag_h:.1f} h and the card is still "
                f"as of {card['as_of']} (limit {BEHIND_H:g} h)"
            )

    age_h = (now - as_of).total_seconds() / 3600
    if age_h > STALE_H[kind]:
        out["stale"] = (
            f"the card's price is {age_h:.1f} h old (limit {STALE_H[kind]:g} h for {kind})"
        )
    return out


def decide(
    state: dict[str, Any], found: dict[str, str], now: datetime
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """(messages, new state). Pure: no I/O."""
    new = {k: v for k, v in state.items() if k in KINDS}
    msgs: list[dict[str, str]] = []
    for kind in KINDS:
        if kind in found and kind not in new:
            new[kind] = now.isoformat()
            msgs.append(
                {
                    "title": f"Gold Tracker: the share card {TITLES[kind][0]}",
                    "priority": "high",
                    "body": f"{found[kind]}. This is the picture shown when the site is shared: "
                    f"{CARD_URL}. Sidecar: {SITE}/{SIDECAR}",
                }
            )
        elif kind not in found and kind in new:
            opened = new.pop(kind)
            msgs.append(
                {
                    "title": f"Gold Tracker: the share card {TITLES[kind][1]}",
                    "priority": "default",
                    "body": f"Recovered: the share card {TITLES[kind][1]} (problem opened "
                    f"{opened}). {CARD_URL}",
                }
            )
    return msgs, new


def simulated(kind: str, now: datetime) -> tuple[Any, Any, Any]:
    """(card, prices, forecast) for proving the page end to end without touching real state."""
    t = now - timedelta(hours=1)
    prices = [{"timestamp": t.isoformat(), "22k": 12780, "source": "tanishq_scrape"}]
    forecast = {"predicted_at": t.isoformat(), "current_22k": 12790}
    card = {
        "price": 12780,
        "price_text": "12,780",
        "approx": False,
        "kind": "tanishq",
        "label": "Tanishq's listed 22K rate",
        "as_of": t.isoformat(),
        "verdict": "down",
        "verdict_text": "Getting cheaper this week",
        "card_text": "Is it a good price? ₹12,780 Getting cheaper this week Not financial advice",
        "built_at": t.isoformat(),
    }
    if kind == "bad":  # the 2026-05 to 2026-11 card
        card.update(
            price=None,
            price_text="NaN",
            verdict_text="Roughly flat — buy when convenient",
            card_text="Is it a good price? ₹NaN Roughly flat — buy when convenient",
        )
    return card, prices, forecast


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--state-in", type=Path)
    ap.add_argument("--state-out", type=Path)
    ap.add_argument("--now", help="ISO time (tests); default: the clock")
    ap.add_argument(
        "--simulate", choices=["bad", "good"], help="judge a made-up card; titles get [TEST]"
    )
    ap.add_argument("--dry-run", action="store_true", help="print the messages, send nothing")
    args = ap.parse_args(argv)

    now = parse_ts(args.now) if args.now else datetime.now(UTC)
    assert now is not None
    state: dict[str, Any] = {}
    if args.simulate:
        card, prices, forecast = simulated(args.simulate, now)
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
        card = fetch_json(f"{SITE}/{SIDECAR}")
        prices = fetch_json(f"{SITE}/data/prices.json")
        forecast = fetch_json(f"{SITE}/data/forecast.json")

    found = findings(card, prices, forecast, now)
    msgs, new_state = decide(state, found, now)
    if isinstance(card, dict):
        print(
            f"card: {card.get('kind')} ₹{card.get('price')} as of {card.get('as_of')}, "
            f"built {card.get('built_at')}, verdict {card.get('verdict_text')!r}"
        )
    for kind, why in found.items():
        print(f"{kind}: {why}")
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
        print("no page: " + ("the card is fine" if not found else "incident already paged"))
    if args.state_out and not args.simulate:
        if not sent_ok:
            new_state = state  # keep the old state so the next run tries the page again
        args.state_out.parent.mkdir(parents=True, exist_ok=True)
        args.state_out.write_text(json.dumps(new_state, sort_keys=True), encoding="utf-8")
    return 0 if not found and sent_ok else 1


if __name__ == "__main__":
    sys.exit(main())
