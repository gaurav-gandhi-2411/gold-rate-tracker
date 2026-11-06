"""scripts/gg_digest.py -- the plain-words gold-price notes GG gets: the daily summary and the Monday summary.

Owner decision 2026-11-06: ntfy carries gold-price information ONLY. The earlier version of this
module said "never a price" (it was a health digest for GG while away); the owner now explicitly
wants the prices, so both notes are about gold: today's 22K, 24K and 18K rates as the live site's hero
shows them, the change vs yesterday and vs last week, the next price range the site shows, and the
model's live accuracy in one line. Monday adds the week's high and low. Facts that are about the system
rather than the price (freshness, scrape rate, models, parked items, merged PRs, dates) are NOT in the
note; they go to the job summary through `ops_lines()` ($GITHUB_STEP_SUMMARY), never to ntfy.

Reads only files the workflows already publish in the repo (data/*.json), never a raw or decrypted
series. The workflow (daily-digest.yml, monday-push.yml) sends the text to the private ntfy topic.

    python scripts/gg_digest.py daily  --out "$RUNNER_TEMP/digest.txt" [--merged merged.json] [--now ISO]
    python scripts/gg_digest.py monday --out "$RUNNER_TEMP/monday.txt" [--now ISO]

Every line degrades to "unknown (<reason>)" instead of failing: a note with a missing part is still
worth sending, and the missing part is itself the news.

Where each figure comes from (it mirrors app.js, which is the authority):
  * hero 22K: heroDisplayState() -- the latest non-derived Tanishq row in data/prices.json when it is
    at most 8 h old and within 12% of the IBJA-based estimate; else the estimate
    (data/forecast.json current_22k when price_source is ibja_calibrated / fusion_consensus); else the
    last Tanishq reading; else an IBJA-derived row.
  * 24K / 18K: the latest row's own value, else round(22K x karat / 22) (app.js karatValue).
  * changes and the week's high/low: data/ibja_derived_prices.json, one row per IST day, so every
    comparison is within one series (mixing Tanishq with IBJA gave false moves, app.js E1).
  * next range: forecast.json headline.lower/upper, falling back to lower/upper (what the page shows
    as "Next price update").
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
IST_OFFSET = timedelta(hours=5, minutes=30)
STALE_H = (
    8.0  # app.js STALE_THRESHOLD_H; also the page's "not fresh" line (docs/TANISHQ_TIMED_VISITS.md)
)
TANISHQ_IBJA_MAX_DEVIATION = 0.12  # app.js, mirrors ml/inference.py
DERIVED_SOURCE_PREFIX = "ibja_calibrated"  # app.js: price rows with this source are not Tanishq's
ESTIMATE_SOURCES = ("ibja_calibrated", "fusion_consensus")

# Dated checks GG asked to hear about, as (date, what). A date in the past drops out by itself.
# Job summary only (not gold-price information).
NEXT_DATES = (
    ("2026-10-26", "Monday push and visitor line"),
    ("2026-11-04", "first day a challenger can reach its first look (20 forward days)"),
    ("2026-11-16", "ADR 076 hourly-model look (first qualifying Monday on or after)"),
    ("2026-12-14", "ADR 077 look (first Monday on or after, after ADR 076's look)"),
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe(fn: Callable[[], str], what: str) -> str:
    """The line, or "<what>: not available right now." in plain words.

    The exception class never reaches the push (a reader cannot act on "FileNotFoundError");
    the cause is for the job log, so it is printed to stderr.
    """
    try:
        return fn()
    except (OSError, ValueError, KeyError, TypeError, IndexError, ZeroDivisionError) as err:
        print(f"gg_digest: {what} unavailable ({type(err).__name__}: {err})", file=sys.stderr)
        return f"{what}: not available right now."


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)


def _ist(ts: datetime) -> datetime:
    return ts.astimezone(UTC) + IST_OFFSET


def _inr(v: float) -> str:
    return f"Rs.{round(v):,}"


def _signed(v: float) -> str:
    return ("+" if v > 0 else "-" if v < 0 else "") + _inr(abs(v))


def _day_label(d: date) -> str:
    return f"{d.day} {d.strftime('%b')}"


# --- the hero figure -------------------------------------------------------------------------------


def hero_state(root: Path, now: datetime) -> dict[str, Any]:
    """What the site's hero shows: {price, kind, as_of (UTC datetime), latest (last prices row)}."""
    readings = _load(root / "data" / "prices.json")
    forecast = _load(root / "data" / "forecast.json")
    latest = readings[-1] if readings else None
    if latest is None:
        raise ValueError("no price rows")
    derived = str(latest.get("source", "")).startswith(DERIVED_SOURCE_PREFIX)
    cur = forecast.get("current_22k")
    cur = cur if isinstance(cur, (int, float)) else None
    estimate = cur if forecast.get("price_source") in ESTIMATE_SOURCES and cur is not None else None
    raw = latest if (not derived and isinstance(latest.get("22k"), (int, float))) else None
    ref = estimate if estimate is not None else cur

    def passes(v: float) -> bool:
        return ref is None or abs(v - ref) / ref <= TANISHQ_IBJA_MAX_DEVIATION

    tanishq: tuple[float, datetime] | None = None
    if raw is not None and passes(raw["22k"]):
        tanishq = (raw["22k"], _parse(raw["timestamp"]))
    elif (
        raw is not None
        and estimate is None
        and forecast.get("price_source") == "tanishq_scrape"
        and forecast.get("scraped_at")
        and cur is not None
    ):
        tanishq = (cur, _parse(forecast["scraped_at"]))
    age_h = (now - tanishq[1]).total_seconds() / 3600 if tanishq else float("inf")
    if tanishq and age_h <= STALE_H:
        out = {"price": tanishq[0], "kind": "Tanishq's listed rate", "as_of": tanishq[1]}
    elif estimate is not None:
        out = {
            "price": estimate,
            "kind": "our estimate from the IBJA benchmark",
            "as_of": _parse(forecast["predicted_at"]),
        }
    elif tanishq:
        out = {"price": tanishq[0], "kind": "Tanishq's last listed rate", "as_of": tanishq[1]}
    elif derived and isinstance(latest.get("22k"), (int, float)):
        out = {
            "price": latest["22k"],
            "kind": "our estimate from the IBJA benchmark",
            "as_of": _parse(latest["timestamp"]),
        }
    else:
        raise ValueError("hero would show no price")
    out["latest"] = latest
    return out


def karat_value(row: dict[str, Any], k: int) -> float:
    """app.js karatValue: the row's own karat value, else the 22K value scaled."""
    own = row.get(f"{k}k")
    if isinstance(own, (int, float)):
        return own
    return round(row["22k"] * k / 22)


def rates_lines(root: Path, now: datetime) -> list[str]:
    def build() -> str:
        h = hero_state(root, now)
        when = _ist(h["as_of"])
        stamp = f"{when.day} {when.strftime('%b')}, {when:%H:%M} IST"
        return (
            f"Gold rate per gram, {h['kind']} as of {stamp}: "
            f"22K {_inr(h['price'])}, 24K {_inr(karat_value(h['latest'], 24))}, "
            f"18K {_inr(karat_value(h['latest'], 18))}."
        )

    return [_safe(build, "Gold rate per gram")]


# --- the daily series ------------------------------------------------------------------------------


def daily_series(root: Path) -> list[tuple[date, float]]:
    """(IST day, 22K) from data/ibja_derived_prices.json, oldest first, one row per day."""
    rows = _load(root / "data" / "ibja_derived_prices.json")
    by_day: dict[date, float] = {}
    for r in rows:
        by_day[_ist(_parse(r["timestamp"])).date()] = float(r["22k"])
    return sorted(by_day.items())


def _delta(now_v: float, then_v: float) -> str:
    pct = (now_v - then_v) / then_v * 100
    return f"{_signed(now_v - then_v)} ({pct:+.1f}%)"


def change_line(root: Path) -> str:
    def build() -> str:
        series = daily_series(root)
        if len(series) < 2:
            raise ValueError("fewer than two days")
        day, today = series[-1]
        prev_day, prev = series[-2]
        week = [(d, v) for d, v in series if d <= day - timedelta(days=7)]
        text = (
            f"Change in the daily 22K benchmark rate ({_day_label(day)}, {_inr(today)}): "
            f"vs yesterday ({_day_label(prev_day)}) {_delta(today, prev)}"
        )
        if week:
            wd, wv = week[-1]
            return text + f"; vs last week ({_day_label(wd)}) {_delta(today, wv)}."
        return text + "; vs last week unknown (series too short)."

    return _safe(build, "Change in the daily 22K benchmark rate")


def week_line(root: Path) -> str:
    def build() -> str:
        series = daily_series(root)
        last = series[-1][0]
        window = [(d, v) for d, v in series if d > last - timedelta(days=7)]
        hi = max(window, key=lambda x: x[1])
        lo = min(window, key=lambda x: x[1])
        return (
            f"This week's 22K benchmark: high {_inr(hi[1])} ({_day_label(hi[0])}), "
            f"low {_inr(lo[1])} ({_day_label(lo[0])})."
        )

    return _safe(build, "This week's 22K benchmark high and low")


# --- forecast and accuracy -------------------------------------------------------------------------


def next_range_line(root: Path) -> str:
    def build() -> str:
        fc = _load(root / "data" / "forecast.json")
        hl = fc.get("headline") or {}
        lo = hl.get("lower", fc.get("lower"))
        hi = hl.get("upper", fc.get("upper"))
        if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
            raise ValueError("no range")
        text = f"Next price update: likely 22K {_inr(lo)} to {_inr(hi)}"
        target = hl.get("predicted_22k", fc.get("predicted_22k"))
        if isinstance(target, (int, float)):
            text += f", central estimate {_inr(target)}"
        tt = fc.get("target_time")
        if tt:
            text += f" (for {_day_label(_ist(_parse(tt)).date())})"
        return text + "."

    return _safe(build, "Next price update")


def accuracy_line(root: Path) -> str:
    def build() -> str:
        live = _load(root / "data" / "model_status_weekly.json")["live"]
        n = int(live["n"])
        since = datetime.strptime(live["since"], "%Y-%m-%d").date()
        held = round(live["coverage"] * live["coverage_n"])
        as_of = _day_label(datetime.strptime(live["last_day"], "%Y-%m-%d").date())
        text = (
            f"Model accuracy as of {as_of}, live since {_day_label(since)}, {n} days: off by "
            f"Rs.{live['mae_model']:.0f} on average vs Rs.{live['mae_hold']:.0f} for 'no change'; "
            f"range held {held} of {live['coverage_n']} days."
        )
        return text + (" Too few days to judge." if n < 20 else "")

    return _safe(build, "Model accuracy")


# --- system facts: job summary only, never the note ------------------------------------------------


def last_reading(root: Path) -> datetime:
    """Timestamp of the newest successful Tanishq reading (data/tanishq_scrape_outcomes.jsonl)."""
    last = None
    for line in (
        (root / "data" / "tanishq_scrape_outcomes.jsonl").read_text(encoding="utf-8").splitlines()
    ):
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("outcome") == "success":
            t = _parse(row["timestamp"])
            last = t if last is None or t > last else last
    if last is None:
        raise ValueError("no successful reading")
    return last


def freshness_line(root: Path, now: datetime) -> str:
    def build() -> str:
        age = (now - last_reading(root)).total_seconds() / 3600
        flag = "STALE" if age > STALE_H else "fresh"
        forecast_age = (
            now - _parse(_load(root / "data" / "forecast.json")["predicted_at"])
        ).total_seconds() / 3600
        return f"Data: {flag}, last Tanishq reading {age:.1f} h ago; forecast {forecast_age:.1f} h old."

    return _safe(build, "Data freshness")


def scrape_line(root: Path) -> str:
    def build() -> str:
        d = _load(root / "data" / "tanishq_scrape_success_rate.json")
        return f"Scrapes (7 days): {d['n_success']} of {d['n']} succeeded."

    return _safe(build, "Scrapes (7 days)")


def alerts_line(root: Path) -> str:
    def build() -> str:
        dem = _load(root / "data" / "model_demotion_state.json")
        nf = _load(root / "data" / "forecast.json").get("next_fix") or {}
        champ = (nf.get("champion") or {}).get("id", "unknown")
        lm = (nf.get("demotion") or {}).get("live_monitor") or {}
        parts = [f"champion {champ}", "demoted" if dem.get("demoted") else "not demoted"]
        if lm.get("checked"):
            parts.append(
                f"live monitor {lm.get('n_live')} live days, error breach {'yes' if lm.get('error_breach') else 'no'}, "
                f"range alert {'yes' if lm.get('coverage_alert') else 'no'}"
            )
        else:
            parts.append("live monitor not checked")
        return "Models: " + "; ".join(parts) + "."

    return _safe(build, "Models")


def merged_line(merged: list[dict[str, Any]] | None) -> str:
    if merged is None:
        return "Merged today: not available right now."
    human = [
        m for m in merged if not str(m.get("headRefName", "")).startswith(("bot/", "dependabot/"))
    ]
    if not human:
        return f"Merged today: no non-bot PRs ({len(merged)} bot PRs)."
    return (
        "Merged today: " + "; ".join(f"#{m['number']} {m['title'][:70]}" for m in human[:6]) + "."
    )


def parked_line(root: Path) -> str:
    def build() -> str:
        text = (root / "docs" / "FOR_GG.md").read_text(encoding="utf-8")
        section = text.split("## 2.", 1)[1] if "## 2." in text else ""
        rows = [ln for ln in section.splitlines() if ln.startswith("| ") and ln[2:3].isdigit()]
        return f"Parked for GG: {len(rows)} item(s) in docs/FOR_GG.md section 2."

    return _safe(build, "Parked for GG")


def next_dates_line(now: datetime) -> str:
    today = now.strftime("%Y-%m-%d")
    upcoming = [f"{d}: {w}" for d, w in NEXT_DATES if d >= today]
    return "Next: " + ("; ".join(upcoming[:3]) if upcoming else "none listed") + "."


def challengers_line(root: Path) -> str:
    def build() -> str:
        ch = _load(root / "data" / "model_status_weekly.json")["challengers"]
        bits = []
        for name, c in ch.items():
            if c.get("retired"):
                bits.append(f"{name} retired")
            elif c.get("promotable"):
                bits.append(f"{name} PROMOTABLE")
            else:
                bits.append(f"{name} {c.get('n')} days (first look at {c.get('first_look_day')})")
        return "Challengers: " + "; ".join(bits) + "."

    return _safe(build, "Challengers")


def ops_lines(root: Path, now: datetime, merged: list[dict[str, Any]] | None) -> list[str]:
    """System facts for the job summary (public repo: no visitor numbers, nothing private)."""
    return [
        freshness_line(root, now),
        scrape_line(root),
        alerts_line(root),
        challengers_line(root),
        merged_line(merged),
        parked_line(root),
        next_dates_line(now),
    ]


# --- the two notes ---------------------------------------------------------------------------------


def daily(root: Path, now: datetime) -> str:
    return "\n".join(
        [*rates_lines(root, now), change_line(root), next_range_line(root), accuracy_line(root)]
    )


def monday(root: Path, now: datetime) -> str:
    return "\n".join(
        [
            *rates_lines(root, now),
            change_line(root),
            week_line(root),
            next_range_line(root),
            accuracy_line(root),
        ]
    )


def _write_summary(lines: list[str], title: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not path:
        return
    try:
        with Path(path).open("a", encoding="utf-8") as fh:
            fh.write(f"### {title}\n\n" + "\n".join(f"- {ln}" for ln in lines) + "\n\n")
    except OSError:
        pass  # a summary is a convenience; it must never fail the note


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GG's daily / Monday gold-price note")
    ap.add_argument("mode", choices=("daily", "monday"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--merged", type=Path, help="JSON list of PRs merged today (gh pr list --json)")
    ap.add_argument("--now", help="ISO time instead of the clock (tests)")
    ap.add_argument("--root", type=Path, default=ROOT)
    args = ap.parse_args(argv)
    now = _parse(args.now) if args.now else datetime.now(UTC)
    merged = None
    if args.merged is not None:
        try:
            merged = _load(args.merged)
        except (OSError, ValueError):
            merged = None
    text = daily(args.root, now) if args.mode == "daily" else monday(args.root, now)
    args.out.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)  # gold prices, public on the site
    _write_summary(ops_lines(args.root, now, merged), f"{args.mode} note: system facts (not sent)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
