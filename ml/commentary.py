"""
commentary.py — Generate LLM-written market notes via Groq.

Usage (from repo root):
    GROQ_API_KEY=<key> python ml/commentary.py

Reads:  data/prices.json, data/forecast.json, data/backtest.json
Writes: data/commentary.json  (rolling list, keeps last 30 entries)

The LLM (llama-3.3-70b-versatile via Groq) receives only structured facts;
it writes English from those facts. It does NOT predict prices and does NOT
give buy/sell advice — the system prompt enforces this.

If GROQ_API_KEY is missing or the API errors, the script logs and exits 0
so that CI continues regardless.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

DATA_DIR = Path(__file__).parent.parent / "data"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
MAX_COMMENTARY_ENTRIES = 30

SYSTEM_PROMPT = (
    "You write factual one-paragraph market notes for an Indian retail gold price tracker. "
    "You write 2 to 3 sentences, under 60 words total, in plain calm English. "
    "You may mention recent moves, notable patterns, and what the model expects, "
    "but you must never give buy/sell/hold advice, never make confident predictions, "
    "and never use hype words like 'soaring', 'plunging', 'bullish', 'bearish'. "
    "If the data shows nothing notable, say so plainly. "
    "IMPORTANT: When sufficient_for_short_term_stats is false, do NOT mention 3-day or "
    "7-day price changes — instead acknowledge the warmup state, e.g. 'Only N real readings "
    "collected so far; trend metrics will be available after ~14 days of data.' "
    "Output the note text only, no preamble."
)


def _load_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _percentile_in_window(current: float, prices_90d: list[float]) -> float:
    if not prices_90d:
        return 50.0
    below = sum(1 for p in prices_90d if p <= current)
    return round(below / len(prices_90d) * 100, 1)


def build_user_message(
    real_prices: list,
    all_prices: list,
    forecast: dict,
    backtest: dict | None,
) -> str:
    """
    Construct the structured data block sent to the LLM.

    real_prices : only data/prices.json (real Tanishq readings) — used for
                  short-term deltas and days-since-drop (avoids seed boundary jump).
    all_prices  : calibrated seed + real prices — used for 90d percentile context.
    """
    import pandas as pd  # local import to keep top-level lightweight

    # Latest price from real data if available, else from combined history
    real_sorted = sorted(real_prices, key=lambda x: x.get("timestamp", ""))
    all_sorted = sorted(all_prices, key=lambda x: x.get("timestamp", ""))

    sufficient = len(real_prices) >= 4
    latest = real_sorted[-1] if real_sorted else (all_sorted[-1] if all_sorted else {})

    p22 = latest.get("22k", "N/A")
    p24 = latest.get("24k", "N/A")
    p18 = latest.get("18k", "N/A")
    ts = latest.get("timestamp", "unknown")

    def _delta(days: int) -> str:
        if not sufficient or not real_sorted:
            return "N/A (insufficient real readings)"
        try:
            cutoff_ts = (pd.Timestamp(ts, tz="UTC") - pd.Timedelta(days=days)).isoformat()
        except Exception:
            return "N/A"
        past = [r for r in real_sorted if r.get("timestamp", "") <= cutoff_ts and r.get("22k")]
        if not past:
            return "N/A"
        ref = past[-1]["22k"]
        if isinstance(p22, (int, float)):
            diff = p22 - ref
            pct = diff / ref * 100
            sign = "+" if diff >= 0 else ""
            return f"{sign}{diff:.0f} ({sign}{pct:.2f}%)"
        return "N/A"

    # 90-day percentile uses calibrated combined data (seed bias smoothed)
    prices_90d: list[float] = []
    try:
        cutoff_90 = (pd.Timestamp(ts, tz="UTC") - pd.Timedelta(days=90)).isoformat()
        prices_90d = [
            r["22k"]
            for r in all_sorted
            if r.get("timestamp", "") >= cutoff_90 and isinstance(r.get("22k"), (int, float))
        ]
    except Exception:
        pass
    pctile = _percentile_in_window(float(p22) if isinstance(p22, (int, float)) else 0, prices_90d)

    # Days since last drop >= ₹100 — real data only
    days_since_drop = "N/A (insufficient real readings)"
    if sufficient and real_sorted:
        try:
            latest_ts = pd.Timestamp(ts, tz="UTC")
            drop_found = False
            for i in range(len(real_sorted) - 1, 0, -1):
                delta = real_sorted[i]["22k"] - real_sorted[i - 1]["22k"]
                if delta <= -100:
                    drop_ts = pd.Timestamp(real_sorted[i]["timestamp"], tz="UTC")
                    days_since_drop = str((latest_ts - drop_ts).days)
                    drop_found = True
                    break
            if not drop_found:
                days_since_drop = f">{len(real_sorted)} readings (no ≥100 drop yet)"
        except Exception:
            days_since_drop = "N/A"

    # Forecast stats
    fc_price = forecast.get("predicted_22k", "N/A") if forecast else "N/A"
    fc_lower = forecast.get("lower", "N/A") if forecast else "N/A"
    fc_upper = forecast.get("upper", "N/A") if forecast else "N/A"
    fc_time = forecast.get("target_time", "N/A") if forecast else "N/A"

    # Festival flags
    near_akshaya = near_dhanteras = "no"
    try:
        from ml.features import _AKSHAYA_TRITIYA, _DHANTERAS, _is_festival_window

        d = pd.Timestamp(ts, tz="UTC").date()
        if _is_festival_window(d, _AKSHAYA_TRITIYA):
            near_akshaya = "yes"
        if _is_festival_window(d, _DHANTERAS):
            near_dhanteras = "yes"
    except Exception:
        pass

    # Backtest summary
    bt_mae = bt_dir = "N/A"
    if backtest and "model" in backtest:
        bt_mae = f"Rs.{backtest['model'].get('mae', 'N/A')}"
        bt_dir = f"{backtest['model'].get('direction_acc', 0) * 100:.1f}%"

    lines = [
        f"sufficient_for_short_term_stats: {'true' if sufficient else 'false'}",
        f"real_readings_count: {len(real_prices)}",
        "",
        f"Latest 22K reading: Rs.{p22} (as of {ts})",
        f"24K: Rs.{p24}  18K: Rs.{p18}",
        "",
        "Recent moves (22K, real Tanishq data only):",
        f"  3-day delta : {_delta(3)}",
        f"  7-day delta : {_delta(7)}",
        f"  Price percentile in last 90 days (calibrated history): {pctile}th",
        "",
        "Forecast (next reading):",
        f"  Point estimate : Rs.{fc_price}",
        f"  80% interval   : Rs.{fc_lower} - Rs.{fc_upper}",
        f"  Target time    : {fc_time}",
        "",
        "Notable patterns:",
        f"  Days since last >=Rs.100 drop : {days_since_drop}",
        f"  Near Akshaya Tritiya window   : {near_akshaya}",
        f"  Near Dhanteras window         : {near_dhanteras}",
        "",
        "Model performance (90-day walk-forward backtest):",
        f"  MAE             : {bt_mae}",
        f"  Direction acc.  : {bt_dir}",
    ]
    return "\n".join(lines)


def call_groq(api_key: str, user_message: str) -> str:
    """Call Groq chat completions. Returns the note text or raises."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.3,
        "max_tokens": 200,
    }
    resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def append_commentary(entry: dict):
    """Append to commentary.json, keeping the last MAX_COMMENTARY_ENTRIES."""
    path = DATA_DIR / "commentary.json"
    entries = _load_json(path) or []
    if not isinstance(entries, list):
        entries = []
    entries.append(entry)
    entries = entries[-MAX_COMMENTARY_ENTRIES:]
    DATA_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(entries, indent=2) + "\n")


def _commentary_age_hours(ts_str: str) -> float:
    """Return age in hours of a commentary entry given its 'ts' field."""
    try:
        ts = time.mktime(time.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ"))
        return (time.time() - ts) / 3600
    except Exception:
        return 0.0


def _last_good_commentary() -> dict | None:
    """Return the most recent commentary.json entry, or None if unavailable."""
    path = DATA_DIR / "commentary.json"
    entries = _load_json(path)
    if not isinstance(entries, list) or not entries:
        return None
    return entries[-1]


def main():
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        print("GROQ_API_KEY not set — skipping commentary generation")
        sys.exit(0)

    # real_prices: only live Tanishq readings — used for short-term deltas
    real_prices = _load_json(DATA_DIR / "prices.json") or []

    # all_prices: calibrated seed + live — used for 90d percentile context
    try:
        from ml.forecast import load_combined_history

        all_df = load_combined_history()
        all_prices = all_df[["timestamp", "22k", "24k", "18k"]].to_dict("records")
    except Exception:
        all_prices = real_prices  # fallback if seed missing

    forecast = _load_json(DATA_DIR / "forecast.json")
    backtest = _load_json(DATA_DIR / "backtest.json")

    if not all_prices and not real_prices:
        print("No price data available — skipping commentary")
        sys.exit(0)

    note_text = None
    prompt_hash = None
    try:
        user_msg = build_user_message(real_prices, all_prices, forecast, backtest)
        prompt_hash = hashlib.md5(user_msg.encode()).hexdigest()[:12]
        note_text = call_groq(api_key, user_msg)
        if not note_text:
            raise ValueError("Groq returned empty response")
    except Exception as exc:
        print(f"Groq API error: {exc} — falling back to last commentary")
        last = _last_good_commentary()
        if last:
            age_h = _commentary_age_hours(last.get("ts", ""))
            # Re-surface the last good entry with updated staleness metadata
            fallback = dict(last)
            fallback["commentary_age_hours"] = round(age_h, 1)
            fallback["fallback"] = True
            append_commentary(fallback)
            print(f"Fallback commentary appended (age={age_h:.1f}h): {last.get('text','')[:60]}…")
        else:
            print("No prior commentary to fall back to — skipping")
        sys.exit(0)

    now_str = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry = {
        "ts": now_str,
        "text": note_text,
        "model": GROQ_MODEL,
        "prompt_hash": prompt_hash,
        "commentary_age_hours": 0.0,
    }
    append_commentary(entry)
    print(f"Commentary appended: {note_text[:80]}…")


if __name__ == "__main__":
    main()
