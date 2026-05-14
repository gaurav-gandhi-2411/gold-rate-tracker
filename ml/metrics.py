"""
metrics.py — Track and evaluate forecast decision accuracy.

Decision rule (Rule A, ₹100 fixed threshold):
  delta ≤ -100  → "wait"
  delta ≥ +100  → "buy_now"
  else          → "neutral"

Outcome (5-trading-day window, carry-forwards excluded):
  "correct"   if min(next 5 non-carry-forward prices) ≤ current_22k − 100
  "incorrect" if all 5 prices > current_22k − 100
  "pending"   if fewer than 5 qualifying prices exist yet
  "resolved"  for neutral/buy_now once actual_next_22k is available

Threshold revisit note: fixed ₹100 is appropriate at ~₹14k–16k/10g.
Revisit when gold reaches ₹20,000+/10g or 5 years from 2026-05-14, whichever first.

Usage:
    python -m ml.metrics --record    # append today's pending entry
    python -m ml.metrics --resolve   # resolve pending entries
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
METRICS_PATH = DATA_DIR / "metrics_history.json"
PRICES_PATH = DATA_DIR / "prices.json"
FORECAST_PATH = DATA_DIR / "forecast.json"

DROP_THRESHOLD = 100.0
OUTCOME_WINDOW = 5  # trading days


def compute_decision(delta: float, threshold: float = DROP_THRESHOLD) -> str:
    """Map model delta to 'wait' | 'neutral' | 'buy_now'."""
    if delta <= -threshold:
        return "wait"
    if delta >= threshold:
        return "buy_now"
    return "neutral"


def _load_prices(path: Path = PRICES_PATH) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        return []
    return sorted(data, key=lambda x: x["timestamp"])


def _is_carry_forward(entry: dict, prev_22k: int | None) -> bool:
    """True if entry is a weekend carry-forward (Sat/Sun with same price as previous)."""
    if prev_22k is None:
        return False
    try:
        dt = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
        return dt.weekday() >= 5 and entry["22k"] == prev_22k
    except (KeyError, ValueError):
        return False


def _get_trading_window(
    decision_date: str, prices: list[dict], n: int = OUTCOME_WINDOW
) -> list[int]:
    """Return up to n non-carry-forward 22k prices strictly after decision_date."""
    result: list[int] = []
    prev_22k: int | None = None

    for entry in prices:
        entry_date = entry["timestamp"][:10]
        if entry_date <= decision_date:
            prev_22k = entry.get("22k")
            continue
        if _is_carry_forward(entry, prev_22k):
            prev_22k = entry.get("22k")
            continue
        result.append(entry["22k"])
        prev_22k = entry.get("22k")
        if len(result) >= n:
            break

    return result


def resolve_outcome(entry: dict, prices: list[dict]) -> dict:
    """
    Fill in outcome fields for a pending entry. Returns updated entry (copy).
    Skips if already resolved.
    """
    entry = dict(entry)
    if entry.get("outcome") != "pending":
        return entry

    window = _get_trading_window(entry["decision_date"], prices, n=OUTCOME_WINDOW)
    actual_next = window[0] if window else None

    if actual_next is not None and entry.get("actual_next_22k") is None:
        entry["actual_next_22k"] = actual_next

    decision = entry.get("decision", "neutral")
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if decision == "wait":
        if len(window) < OUTCOME_WINDOW:
            return entry  # not enough future prices yet
        min_price = min(window)
        entry["min_future_price"] = min_price
        entry["outcome_resolved_at"] = now_str
        threshold = float(entry.get("drop_threshold", DROP_THRESHOLD))
        entry["outcome"] = "correct" if min_price <= entry["current_22k"] - threshold else "incorrect"
    else:
        # neutral/buy_now: mark resolved once we have next-day price for MAE
        if actual_next is not None:
            entry["outcome_resolved_at"] = now_str
            entry["outcome"] = "resolved"

    return entry


def aggregate_metrics(entries: list[dict], window_days: int = 30) -> dict:
    """
    Compute decision_accuracy, MAE, directional_accuracy from resolved entries
    within the last window_days. None values mean insufficient data.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).strftime("%Y-%m-%d")

    resolved = [
        e for e in entries
        if e.get("outcome") not in ("pending", None)
        and e.get("decision_date", "") >= cutoff
    ]

    wait_resolved = [e for e in resolved if e.get("decision") == "wait"]
    n_wait = len(wait_resolved)
    decision_accuracy: float | None
    if n_wait > 0:
        n_correct = sum(1 for e in wait_resolved if e.get("outcome") == "correct")
        decision_accuracy = n_correct / n_wait
    else:
        decision_accuracy = None

    mae_entries = [
        e for e in resolved
        if isinstance(e.get("actual_next_22k"), (int, float))
        and isinstance(e.get("predicted_22k"), (int, float))
    ]
    n_mae = len(mae_entries)
    mae: float | None
    if n_mae > 0:
        mae = sum(abs(e["predicted_22k"] - e["actual_next_22k"]) for e in mae_entries) / n_mae
    else:
        mae = None

    dir_entries = [
        e for e in mae_entries
        if isinstance(e.get("current_22k"), (int, float))
        and isinstance(e.get("delta"), (int, float))
    ]
    n_dir = len(dir_entries)
    directional_accuracy: float | None
    if n_dir > 0:
        n_correct_dir = sum(
            1 for e in dir_entries
            if (e["actual_next_22k"] - e["current_22k"]) * e["delta"] > 0
        )
        directional_accuracy = n_correct_dir / n_dir
    else:
        directional_accuracy = None

    return {
        "decision_accuracy": decision_accuracy,
        "n_wait_resolved": n_wait,
        "mae": mae,
        "n_mae": n_mae,
        "directional_accuracy": directional_accuracy,
        "n_dir": n_dir,
        "n_resolved": len(resolved),
        "window_days": window_days,
    }


def record_prediction(
    forecast_path: Path = FORECAST_PATH,
    prices_path: Path = PRICES_PATH,
    out_path: Path = METRICS_PATH,
) -> bool:
    """
    Append today's pending entry to metrics_history.json.
    Idempotent — skips if an entry for today already exists.
    Returns True if a new entry was written.
    """
    if not forecast_path.exists():
        print("forecast.json not found — skipping metrics record.")
        return False

    fc = json.loads(forecast_path.read_text())
    predicted_22k = fc.get("predicted_22k")
    if not isinstance(predicted_22k, (int, float)):
        print("forecast.json has no valid predicted_22k — skipping.")
        return False

    prices = _load_prices(prices_path)
    if not prices:
        print("prices.json empty — skipping metrics record.")
        return False

    current_22k = prices[-1]["22k"]
    delta = float(predicted_22k - current_22k)
    decision_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    existing: list[dict] = []
    if out_path.exists():
        raw = json.loads(out_path.read_text())
        if isinstance(raw, list):
            existing = raw

    if any(e.get("decision_date") == decision_date for e in existing):
        print(f"Entry for {decision_date} already exists — skipping.")
        return False

    entry: dict = {
        "decision_date": decision_date,
        "predicted_at": fc.get("predicted_at", ""),
        "current_22k": current_22k,
        "predicted_22k": int(predicted_22k),
        "delta": round(delta, 1),
        "lower": fc.get("lower"),
        "upper": fc.get("upper"),
        "decision": compute_decision(delta),
        "outcome_window_days": OUTCOME_WINDOW,
        "outcome_resolved_at": None,
        "outcome": "pending",
        "min_future_price": None,
        "actual_next_22k": None,
        "drop_threshold": DROP_THRESHOLD,
        "track": "real",
        "model_version": fc.get("model_version", "lgbm-only"),
        "real_readings_count": fc.get("real_readings_count", 0),
    }

    existing.append(entry)
    existing.sort(key=lambda x: x["decision_date"])
    out_path.write_text(json.dumps(existing, indent=2) + "\n")
    print(f"Recorded {decision_date}: decision={entry['decision']}, delta={delta:+.1f}")
    return True


def resolve_pending(
    out_path: Path = METRICS_PATH,
    prices_path: Path = PRICES_PATH,
) -> int:
    """
    Resolve all pending entries whose outcome window has closed.
    Returns count of newly resolved entries.
    """
    if not out_path.exists():
        print("metrics_history.json not found — nothing to resolve.")
        return 0

    raw = json.loads(out_path.read_text())
    if not isinstance(raw, list):
        return 0

    entries: list[dict] = raw
    prices = _load_prices(prices_path)
    resolved_count = 0
    changed = False

    for i, entry in enumerate(entries):
        if entry.get("outcome") != "pending":
            continue
        updated = resolve_outcome(entry, prices)
        if updated != entry:
            entries[i] = updated
            changed = True
            if updated.get("outcome") != "pending":
                resolved_count += 1
                print(f"Resolved {entry['decision_date']}: {updated['outcome']}")

    if changed:
        out_path.write_text(json.dumps(entries, indent=2) + "\n")

    print(f"Resolved {resolved_count} pending entries.")
    return resolved_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Gold rate forecast metrics tracker")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--record", action="store_true", help="Append today's pending entry")
    group.add_argument("--resolve", action="store_true", help="Resolve pending entries")
    args = parser.parse_args()

    if args.record:
        record_prediction()
    else:
        resolve_pending()


if __name__ == "__main__":
    main()
