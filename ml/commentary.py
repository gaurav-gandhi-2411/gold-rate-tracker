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
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

DATA_DIR = Path(__file__).parent.parent / "data"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
MAX_COMMENTARY_ENTRIES = 30

# Single source of truth for jargon the LLM must never surface to family
# subscribers. Referenced by BOTH the system prompt below AND the output-side
# guard (_violates_content_policy) so the two can never drift apart — a
# generation that ignores the prompt (a known, non-zero-temperature failure
# mode) is still caught before it reaches data/commentary.json.
BANNED_WORDS: tuple[str, ...] = (
    "Chronos",
    "model",
    "baseline",
    "samples",
    "percentile",
    "naive",
    "MAE",
    "backtest",
    "folds",
    "fold",
    "Wilcoxon",
    "bullish",
    "bearish",
    "soaring",
    "plunging",
)

SYSTEM_PROMPT = (
    "You write brief, friendly notes for an Indian retail gold price tracker aimed at everyday "
    "family buyers — not traders or analysts. "
    "Write 2 to 4 short sentences, up to 90 words, in warm everyday English. "
    "FRAMING: "
    "(1) The current price is the expected price for the next few days — the tracker does not predict "
    "a different number. Never say 'the forecast is Rs.X' as if it differs from today's price. "
    "(2) If directional_signal_available is true, describe the recent price direction AT MOST ONCE, "
    "from ONE timeframe, in plain past-tense language "
    "('prices have eased a little over the past week' / 'gold edged up slightly this week'). "
    "Use only the 3-day or 7-day delta from Recent moves — do NOT relay or interpret the Lean value. "
    "Do NOT describe the same direction twice in a different phrasing. "
    "Do NOT imply future direction. Never say prices 'will', 'look likely to', or 'may' rise or fall. "
    "If directional_signal_available is false, omit price direction entirely. "
    "(3) You may mention where the current price sits relative to recent history in everyday language: "
    "'around what it's been lately', 'a bit above its recent range', 'near the lower end recently'. "
    "Never use the word 'percentile'. "
    "NEVER USE these words or phrases: " + ", ".join(f"'{w}'" for w in BANNED_WORDS) + ". "
    "Never give buy/sell/hold advice. "
    "IMPORTANT: When sufficient_for_short_term_stats is false, do NOT mention 3-day or "
    "7-day price changes — instead note that more trend detail will build up over the coming weeks. "
    "Output the note only, no preamble."
)

# --- Output-side content guard --------------------------------------------------
# The system prompt above is not a guarantee — an LLM can ignore instructions on
# a given generation, especially at non-zero temperature (this call uses 0.3).
# These patterns are the enforcement layer: scanned against the ACTUAL generated
# text before it is ever written to data/commentary.json or rendered in the UI.

_BANNED_WORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in BANNED_WORDS) + r")\b", re.IGNORECASE
)

# Forward-looking directional language the prompt explicitly forbids:
# "Never say prices 'will', 'look likely to', or 'may' rise or fall."
_FORWARD_LOOKING_MODALS = ("will", "likely to", "may", "might", "could")

# Directional verbs/adjectives (all common inflections) covering both the
# forward-looking check above and the "omit direction entirely when
# directional_signal_available is false" rule below.
_DIRECTION_WORDS = (
    "rise",
    "rises",
    "rising",
    "risen",
    "rose",
    "fall",
    "falls",
    "falling",
    "fallen",
    "fell",
    "climb",
    "climbs",
    "climbing",
    "climbed",
    "drop",
    "drops",
    "dropping",
    "dropped",
    "increase",
    "increases",
    "increasing",
    "increased",
    "decrease",
    "decreases",
    "decreasing",
    "decreased",
    "surge",
    "surges",
    "surging",
    "surged",
    "dip",
    "dips",
    "dipping",
    "dipped",
    "rally",
    "rallies",
    "rallying",
    "rallied",
    "correct",
    "corrects",
    "correcting",
    "corrected",
    "gain",
    "gains",
    "gaining",
    "gained",
    "ease",
    "eases",
    "easing",
    "eased",
)

_DIRECTION_WORD_RE = re.compile(r"\b(" + "|".join(_DIRECTION_WORDS) + r")\b", re.IGNORECASE)

_FORWARD_LOOKING_RE = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in _FORWARD_LOOKING_MODALS) + r")\b"
    r".{0,20}?"
    r"\b(" + "|".join(_DIRECTION_WORDS) + r"|up|down)\b",
    re.IGNORECASE | re.DOTALL,
)


def _violates_content_policy(text: str, directional_signal_available: bool = True) -> str | None:
    """Scan generated commentary text against the SYSTEM_PROMPT's content rules.

    Pure function — no I/O. Returns a human-readable reason string if the text
    violates policy, or None if it is clean. Called from main() before the text
    is accepted for data/commentary.json; a violation triggers the existing
    last-known-good fallback path (see main()'s except block).

    Args:
        text: The raw commentary text returned by call_groq().
        directional_signal_available: Mirrors the same field sent to the LLM in
            build_user_message(). When False, the prompt instructs the LLM to
            omit price direction entirely — any directional word at all (not
            just forward-looking ones) is a DARK-gate contradiction.

    Returns:
        A reason string describing the violation, or None if the text is clean.
    """
    banned = _BANNED_WORD_RE.search(text)
    if banned:
        return f"banned word/phrase detected: {banned.group(0)!r}"

    forward = _FORWARD_LOOKING_RE.search(text)
    if forward:
        return f"forward-looking directional language detected: {forward.group(0)!r}"

    if not directional_signal_available:
        direction = _DIRECTION_WORD_RE.search(text)
        if direction:
            return (
                "directional language present while directional_signal_available=False "
                f"(DARK-gate contradiction): {direction.group(0)!r}"
            )

    return None


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

    # Forecast stats (naive flat-hold baseline)
    fc_price = forecast.get("predicted_22k", "N/A") if forecast else "N/A"
    fc_lower = forecast.get("lower", "N/A") if forecast else "N/A"
    fc_upper = forecast.get("upper", "N/A") if forecast else "N/A"

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

    # Chronos companion directional signal
    companion: dict = {}
    if forecast:
        companion = forecast.get("chronos_companion") or {}
    companion_status = companion.get("status", "unknown")
    companion_available = companion_status == "success"
    if companion_available:
        _lean_dir = companion.get("lean_direction", "N/A")
        _lean_pct = companion.get("lean_strength_pct")
        lean_str = (
            f"{_lean_dir} ({_lean_pct:.1f}% from current)"
            if isinstance(_lean_pct, (int, float))
            else str(_lean_dir)
        )
    else:
        lean_str = "N/A"

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
        "Forecast context (naive flat-hold baseline — predicts no change; equals current price):",
        f"  Naive baseline : Rs.{fc_price} (= current price; no directional signal from this number)",
        f"  80% interval   : Rs.{fc_lower} - Rs.{fc_upper}",
        "",
        "Directional signal (Chronos) — treat separately from naive baseline:",
        f"  directional_signal_available: {'true' if companion_available else 'false'}",
        f"  Lean            : {lean_str}",
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
    """Call Groq chat completions. Returns the note text or raises.

    PROMPT CACHING NOTE — why caching is not applied here
    (full analysis in docs/adr/013-prompt-caching-scope.md):

    Provider: Groq (llama-3.3-70b-versatile). Groq applies prefix KV-caching
    automatically; no client-side cache_control is available or needed.
    However, automatic caching requires ≥1024 prompt tokens and a repeated
    prefix within ~1 hour. This call site fails both gates:
      - Prompt size: system (~80 tokens) + user message (~200 tokens) ≈ 280 tokens
        — well below the 1024-token minimum.
      - Cadence: runs every 6 hours — far outside the ~1-hour Groq cache TTL.
    Anthropic prompt caching is also inapplicable: the live path uses Groq, not
    a Claude model. Migrate to Claude AND grow the system prompt to ≥1024 tokens
    before wiring cache_control here.
    """
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

    all_prices = real_prices

    forecast = _load_json(DATA_DIR / "forecast.json")
    backtest = _load_json(DATA_DIR / "backtest.json")

    if not all_prices and not real_prices:
        print("No price data available — skipping commentary")
        sys.exit(0)

    # Mirrors the same field computed inside build_user_message() — kept in
    # sync manually rather than threaded through the return value, since
    # build_user_message()'s contract is "returns the prompt string" and this
    # guard is a separate concern (output validation, not prompt construction).
    companion = (forecast or {}).get("chronos_companion") or {}
    directional_signal_available = companion.get("status") == "success"

    note_text = None
    prompt_hash = None
    try:
        user_msg = build_user_message(real_prices, all_prices, forecast, backtest)
        prompt_hash = hashlib.md5(user_msg.encode()).hexdigest()[:12]
        note_text = call_groq(api_key, user_msg)
        if not note_text:
            raise ValueError("Groq returned empty response")
        violation = _violates_content_policy(note_text, directional_signal_available)
        if violation:
            raise ValueError(f"content policy violation — {violation}")
    except Exception as exc:
        print(f"Commentary generation failed: {exc} — falling back to last commentary")
        last = _last_good_commentary()
        if last:
            age_h = _commentary_age_hours(last.get("ts", ""))
            # Re-surface the last good entry with updated staleness metadata
            fallback = dict(last)
            fallback["commentary_age_hours"] = round(age_h, 1)
            fallback["fallback"] = True
            append_commentary(fallback)
            print(f"Fallback commentary appended (age={age_h:.1f}h): {last.get('text', '')[:60]}…")
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
