"""
daily_summary.py — Smart daily gold rate notification.

Runs at 10:30 UTC (4:00 PM IST) via .github/workflows/daily-summary.yml.
Sends a push notification via ntfy.sh ONLY when something interesting happened:
  T1: 22K price moved ≥2% from yesterday's IST-day close
  T2: 22K within ±₹50 of the 30-day low
  T3: 22K within ±₹50 of the 30-day high
  T4: 5-day cumulative move ≥3%
  T5: first reading after a 24h+ scrape gap (no readings on yesterday's IST date)

Most days produce no notification. Threshold tuning note: revisit ~2026-05-28.
See docs/DAILY_SUMMARY_DESIGN.md for calibration rationale.

Usage:
    GROQ_API_KEY=<key> NTFY_TOPIC=<topic> python ml/daily_summary.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DATA_DIR = Path(__file__).parent.parent / "data"
PRICES_PATH = DATA_DIR / "prices.json"
LAST_SUMMARY_PATH = DATA_DIR / "last_summary.json"

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

# Trigger thresholds — first-guess values, tune empirically after ~2 weeks.
# See docs/DAILY_SUMMARY_DESIGN.md for calibration analysis.
PRICE_MOVE_PCT = 0.02   # T1: ≥2% single-day move
BAND_30D = 100          # T2/T3: SMOKE TEST — revert to 50 after workflow_dispatch
FIVE_DAY_PCT = 0.03     # T4: ≥3% 5-day cumulative move

# Asia/Kolkata from the system tz database — direction is explicit and any
# future DST adoption would be handled automatically (no manual ±5:30 arithmetic).
_KOLKATA = ZoneInfo("Asia/Kolkata")

SUMMARY_SYSTEM_PROMPT = (
    "You write very short daily summaries for an Indian retail gold price tracker. "
    "1-2 sentences, under 200 characters total, factual, plain English, no emojis. "
    "Do not give buy/sell/hold advice. Do not use hype words. "
    "Output the summary text only — no preamble, no quotation marks."
)


# ---------------------------------------------------------------------------
# IST helpers
# ---------------------------------------------------------------------------


def _ist_date(ts: datetime) -> date:
    """Convert a UTC datetime to its IST calendar date via Asia/Kolkata.

    NOT a '24-hour lookback'. Examples:
      22:00 UTC → 03:30 IST next calendar day (IST date = tomorrow)
      16:30 UTC → 22:00 IST same calendar day (IST date = today)
    Always use this function; never add/subtract raw hour offsets.
    """
    return ts.astimezone(_KOLKATA).date()


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _latest_for_ist_date(prices: list[dict], target: date) -> dict | None:
    """Return the most recent reading whose IST calendar date equals `target`."""
    result: dict | None = None
    for r in sorted(prices, key=lambda x: x["timestamp"]):
        if _ist_date(_parse_ts(r["timestamp"])) == target:
            result = r
    return result


def _daily_closes_in_window(prices: list[dict], end_ist: date, days: int) -> dict[date, int]:
    """Return {ist_date: 22k_price} for each IST day in the last `days` calendar days.

    Window is (end_ist − days, end_ist] (exclusive start, inclusive end).
    Only the latest reading per IST day is kept.
    """
    cutoff = end_ist - timedelta(days=days)
    closes: dict[date, int] = {}
    for r in sorted(prices, key=lambda x: x["timestamp"]):
        d = _ist_date(_parse_ts(r["timestamp"]))
        if cutoff < d <= end_ist:
            closes[d] = r["22k"]
    return closes


# ---------------------------------------------------------------------------
# Trigger evaluation
# ---------------------------------------------------------------------------


def check_triggers(prices: list[dict], now_utc: datetime) -> tuple[list[str], dict]:
    """Evaluate all 5 triggers.

    Returns (fired_trigger_names, context_stats).
    fired_trigger_names is empty if nothing is interesting enough to notify.
    context_stats contains data needed for commentary / template generation.
    """
    today_ist = _ist_date(now_utc)
    yesterday_ist = today_ist - timedelta(days=1)

    today_reading = _latest_for_ist_date(prices, today_ist)
    if today_reading is None:
        return [], {}

    today_price = today_reading["22k"]
    yesterday_reading = _latest_for_ist_date(prices, yesterday_ist)
    five_ago_reading = _latest_for_ist_date(prices, today_ist - timedelta(days=5))

    # 30-day stats (one close per IST day)
    window_30d = _daily_closes_in_window(prices, today_ist, 30)
    prices_30d = list(window_30d.values())
    low_30d = min(prices_30d) if prices_30d else today_price
    high_30d = max(prices_30d) if prices_30d else today_price

    # 7-day average for LLM context
    window_7d = _daily_closes_in_window(prices, today_ist, 7)
    avg_7d = round(sum(window_7d.values()) / len(window_7d)) if window_7d else today_price

    fired: list[str] = []

    # T1: ≥2% single-day move vs yesterday's IST-day close
    pct_1d: float | None = None
    if yesterday_reading:
        pct_1d = (today_price - yesterday_reading["22k"]) / yesterday_reading["22k"]
        if abs(pct_1d) >= PRICE_MOVE_PCT:
            fired.append("price_move")

    # T2: within ±BAND_30D of 30-day low
    if today_price <= low_30d + BAND_30D:
        fired.append("near_30d_low")

    # T3: within ±BAND_30D of 30-day high
    if today_price >= high_30d - BAND_30D:
        fired.append("near_30d_high")

    # T4: ≥3% 5-day cumulative move
    pct_5d: float | None = None
    if five_ago_reading:
        pct_5d = (today_price - five_ago_reading["22k"]) / five_ago_reading["22k"]
        if abs(pct_5d) >= FIVE_DAY_PCT:
            fired.append("five_day_move")

    # T5: no readings from yesterday's IST date → first reading after gap
    today_readings = [
        r for r in prices if _ist_date(_parse_ts(r["timestamp"])) == today_ist
    ]
    yesterday_readings = [
        r for r in prices if _ist_date(_parse_ts(r["timestamp"])) == yesterday_ist
    ]
    if today_readings and not yesterday_readings:
        fired.append("scrape_gap")

    stats: dict = {
        "today_ist": today_ist.isoformat(),
        "today_price": today_price,
        "yesterday_ist": yesterday_ist.isoformat(),
        "yesterday_price": yesterday_reading["22k"] if yesterday_reading else None,
        "pct_change_1d": round(pct_1d * 100, 2) if pct_1d is not None else None,
        "low_30d": low_30d,
        "high_30d": high_30d,
        "avg_7d": avg_7d,
        "five_day_price": five_ago_reading["22k"] if five_ago_reading else None,
        "pct_change_5d": round(pct_5d * 100, 2) if pct_5d is not None else None,
    }

    return fired, stats


# ---------------------------------------------------------------------------
# Commentary
# ---------------------------------------------------------------------------


def _fmt_inr_ascii(n: int) -> str:
    """Format an INR price as ASCII-only (safe for HTTP headers).

    Matches the fmtHdr pattern from scraper/update-and-notify.js.
    Gold prices are sub-lakh (< 1,00,000), so Indian and Western comma
    placement are identical — standard Python formatting is correct.
    """
    return f"Rs.{n:,}"


def _build_title(triggers: list[str], stats: dict) -> str:
    """Build an ASCII-only ntfy Title header from the highest-priority trigger."""
    p = stats["today_price"]
    if "near_30d_high" in triggers:
        return f"Gold 22K at 30-day high ({_fmt_inr_ascii(p)})"
    if "near_30d_low" in triggers:
        return f"Gold 22K near 30-day low ({_fmt_inr_ascii(p)})"
    if "price_move" in triggers:
        chg = stats.get("pct_change_1d") or 0.0
        sign = "+" if chg >= 0 else ""
        return f"Gold 22K {sign}{chg:.1f}% today ({_fmt_inr_ascii(p)})"
    if "five_day_move" in triggers:
        chg = stats.get("pct_change_5d") or 0.0
        sign = "+" if chg >= 0 else ""
        return f"Gold 22K {sign}{chg:.1f}% over 5 days ({_fmt_inr_ascii(p)})"
    if "scrape_gap" in triggers:
        return f"Gold tracker back online ({_fmt_inr_ascii(p)})"
    return f"Gold 22K update ({_fmt_inr_ascii(p)})"


def _template_commentary(triggers: list[str], stats: dict) -> str:
    """Build a template fallback commentary string when Groq is unavailable."""
    p = stats["today_price"]
    parts: list[str] = []

    if "price_move" in triggers:
        chg = stats.get("pct_change_1d") or 0.0
        sign = "+" if chg >= 0 else ""
        parts.append(f"Gold 22K moved {sign}{chg:.1f}% today to Rs.{p:,}")

    if "near_30d_low" in triggers:
        lo = stats["low_30d"]
        parts.append(f"price near 30-day low of Rs.{lo:,}")

    if "near_30d_high" in triggers:
        hi = stats["high_30d"]
        parts.append(f"price near 30-day high of Rs.{hi:,}")

    if "five_day_move" in triggers:
        chg = stats.get("pct_change_5d") or 0.0
        sign = "+" if chg >= 0 else ""
        parts.append(f"{sign}{chg:.1f}% cumulative over 5 days")

    if "scrape_gap" in triggers:
        parts.append("first reading after scrape outage")

    if not parts:
        parts.append(f"Gold 22K at Rs.{p:,}")

    return ". ".join(parts) + "."


def call_groq_summary(api_key: str, triggers: list[str], stats: dict) -> str:
    """Call Groq for a 1-2 sentence daily summary. Returns text or raises."""
    import requests  # local import — only needed when LLM path is taken

    p = stats["today_price"]
    lines = [f"Today ({stats['today_ist']}): Rs.{p:,}"]

    if stats.get("yesterday_price") is not None:
        yp = stats["yesterday_price"]
        lines.append(f"Yesterday: Rs.{yp:,}")
    if stats.get("pct_change_1d") is not None:
        chg = stats["pct_change_1d"]
        sign = "+" if chg >= 0 else ""
        lines.append(f"1-day change: {sign}{chg:.1f}%")
    if stats.get("pct_change_5d") is not None:
        chg5 = stats["pct_change_5d"]
        sign = "+" if chg5 >= 0 else ""
        lines.append(f"5-day change: {sign}{chg5:.1f}%")

    lines += [
        f"30-day low: Rs.{stats['low_30d']:,}  30-day high: Rs.{stats['high_30d']:,}",
        f"7-day avg: Rs.{stats['avg_7d']:,}",
        "",
        f"Triggered alerts: {', '.join(triggers)}",
    ]
    user_msg = "\n".join(lines)

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.3,
        "max_tokens": 120,
    }
    resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


# ---------------------------------------------------------------------------
# ntfy
# ---------------------------------------------------------------------------


def send_ntfy(topic: str, title: str, body: str) -> bool:
    """Post a notification to ntfy.sh. Returns True on success.

    title must be ASCII-only (HTTP header constraint — see fmtHdr pattern in
    scraper/update-and-notify.js for why ₹ must not appear in header values).
    body may contain Unicode.
    """
    import urllib.request

    url = f"https://ntfy.sh/{topic}"
    req = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Title": title,
            "Priority": "3",
            "Tags": "money_with_wings",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=15)
        print(f"Notification sent: {title}")
        return True
    except Exception as exc:
        print(f"ntfy send failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def _is_already_sent(today_ist: date) -> bool:
    if not LAST_SUMMARY_PATH.exists():
        return False
    try:
        data = json.loads(LAST_SUMMARY_PATH.read_text())
        return data.get("date") == today_ist.isoformat()
    except Exception:
        return False


def _mark_sent(today_ist: date, triggers: list[str], price: int) -> None:
    LAST_SUMMARY_PATH.write_text(
        json.dumps(
            {"date": today_ist.isoformat(), "triggers_fired": triggers, "price_22k": price},
            indent=2,
        )
        + "\n"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    ntfy_topic = os.environ.get("NTFY_TOPIC", "").strip()

    now_utc = datetime.now(timezone.utc)
    today_ist = _ist_date(now_utc)

    if not PRICES_PATH.exists():
        print("prices.json not found — skipping daily summary.")
        sys.exit(0)
    prices: list[dict] = json.loads(PRICES_PATH.read_text())
    if not prices:
        print("prices.json is empty — skipping daily summary.")
        sys.exit(0)

    if _is_already_sent(today_ist):
        print(f"Daily summary already sent for {today_ist} — skipping.")
        sys.exit(0)

    triggers, stats = check_triggers(prices, now_utc)

    if not triggers:
        print(f"No triggers fired for {today_ist} — no notification.")
        sys.exit(0)

    print(f"Triggers fired: {triggers}")

    commentary: str | None = None
    if api_key:
        try:
            commentary = call_groq_summary(api_key, triggers, stats)
            print(f"LLM commentary: {commentary}")
        except Exception as exc:
            print(f"Groq failed ({exc}) — using template fallback.")
    else:
        print("GROQ_API_KEY not set — using template fallback.")

    if commentary is None:
        commentary = _template_commentary(triggers, stats)

    title = _build_title(triggers, stats)
    body = commentary

    if not ntfy_topic:
        print(f"NTFY_TOPIC not set — would send: [{title}] {body}")
        sys.exit(0)

    sent = send_ntfy(ntfy_topic, title, body)
    if sent:
        _mark_sent(today_ist, triggers, stats["today_price"])
    else:
        print("Notification not delivered — marker NOT written.")
        sys.exit(1)


if __name__ == "__main__":
    main()
