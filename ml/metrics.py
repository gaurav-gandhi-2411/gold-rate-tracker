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
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

DATA_DIR = Path(__file__).parent.parent / "data"
METRICS_PATH = DATA_DIR / "metrics_history.json"
PRICES_PATH = DATA_DIR / "prices.json"
FORECAST_PATH = DATA_DIR / "forecast.json"
COVERAGE_PATH = DATA_DIR / "coverage_metrics.json"

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
    now_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    if decision == "wait":
        if len(window) < OUTCOME_WINDOW:
            return entry  # not enough future prices yet
        min_price = min(window)
        entry["min_future_price"] = min_price
        entry["outcome_resolved_at"] = now_str
        threshold = float(entry.get("drop_threshold", DROP_THRESHOLD))
        entry["outcome"] = (
            "correct" if min_price <= entry["current_22k"] - threshold else "incorrect"
        )
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
    cutoff = (datetime.now(UTC) - timedelta(days=window_days)).strftime("%Y-%m-%d")

    resolved = [
        e
        for e in entries
        if e.get("outcome") not in ("pending", None) and e.get("decision_date", "") >= cutoff
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
        e
        for e in resolved
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
        e
        for e in mae_entries
        if isinstance(e.get("current_22k"), (int, float))
        and isinstance(e.get("delta"), (int, float))
    ]
    n_dir = len(dir_entries)
    directional_accuracy: float | None
    if n_dir > 0:
        n_correct_dir = sum(
            1 for e in dir_entries if (e["actual_next_22k"] - e["current_22k"]) * e["delta"] > 0
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
    decision_date = datetime.now(UTC).strftime("%Y-%m-%d")

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
    save_coverage_metrics(metrics_path=out_path)
    return resolved_count


def compute_band_coverage(entries: list[dict]) -> dict:
    """Empirical coverage of the displayed naive flat-hold PI (lower/upper) vs
    realized outcomes.

    This is the range actually shown to users (fc.headline.lower/upper), which
    differs from Chronos's own quantile PI reported in backtest.json — the two
    must not be conflated (see app.js renderMethodology). Grows monotonically
    over all resolved decisions; no time window, so n only ever increases.
    """
    resolved = [
        e
        for e in entries
        if e.get("outcome") not in ("pending", None)
        and isinstance(e.get("lower"), (int, float))
        and isinstance(e.get("upper"), (int, float))
        and isinstance(e.get("actual_next_22k"), (int, float))
    ]
    n = len(resolved)
    if n == 0:
        return {"coverage": None, "n": 0, "n_in_band": 0}

    n_in_band = sum(1 for e in resolved if e["lower"] <= e["actual_next_22k"] <= e["upper"])
    return {"coverage": round(n_in_band / n, 4), "n": n, "n_in_band": n_in_band}


def save_coverage_metrics(
    metrics_path: Path = METRICS_PATH,
    out_path: Path = COVERAGE_PATH,
) -> dict:
    """Recompute displayed-band coverage from metrics_history.json and persist it.

    Called from resolve_pending() so the figure updates every time the weekly
    backtest workflow resolves outcomes, rather than being a one-time snapshot.
    """
    entries: list[dict] = []
    if metrics_path.exists():
        raw = json.loads(metrics_path.read_text())
        if isinstance(raw, list):
            entries = raw

    result = compute_band_coverage(entries)
    payload = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "band_source": "naive_flat_hold conformal PI (headline.lower/upper), "
        "calibrated on the last 30 backtest folds' next-trading-day (h=1) naive errors "
        "(ADR 022)",
        "nominal_pct": 80,
        "coverage": result["coverage"],
        "n": result["n"],
        "n_in_band": result["n_in_band"],
        "note": "Recalibrated 2026-07 from h=5 to h=1 (ADR 022) to match the horizon this "
        "metric actually tests. n accumulates across the change with no time window, so "
        "coverage may read above 80% for a while as pre-recalibration decisions are still "
        "counted; it converges toward the new calibration's true rate as n grows. Per ADR 023: "
        "while this figure is still pre-fix-dominated, it is NOT yet independent out-of-sample "
        "evidence for the h=1 band — that confirmation arrives only once enough decisions made "
        "after 2026-07-17 have resolved. The 84.7% figure in ADR 022 is a retrospective, "
        "overlapping-window sanity check, not OOS validation.",
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


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


# ============================================================
# h=5 backtest metric functions (added for PR F walk-forward)
# ============================================================


def compute_mae_per_horizon(
    actuals: np.ndarray,
    preds: np.ndarray,
) -> list[float]:
    """MAE at each horizon step.

    Parameters
    ----------
    actuals, preds : (n_folds, horizon) float arrays.
    """
    actuals = np.asarray(actuals, dtype=float)
    preds = np.asarray(preds, dtype=float)
    return [float(np.mean(np.abs(actuals[:, h] - preds[:, h]))) for h in range(actuals.shape[1])]


def compute_dir_acc_h5(
    context_lasts: np.ndarray,
    p50_h5: np.ndarray,
    actuals_h5: np.ndarray,
) -> float:
    """Direction accuracy at h=5.

    Fraction of folds where sign(p50_h5 - context_last) == sign(actual_h5 - context_last).
    Folds where actual_h5 == context_last (zero actual move) are counted as wrong.
    """
    context_lasts = np.asarray(context_lasts, dtype=float)
    p50_h5 = np.asarray(p50_h5, dtype=float)
    actuals_h5 = np.asarray(actuals_h5, dtype=float)
    pred_dir = np.sign(p50_h5 - context_lasts)
    actual_dir = np.sign(actuals_h5 - context_lasts)
    correct = (pred_dir == actual_dir) & (actual_dir != 0)
    if len(correct) == 0:
        return 0.0
    return float(np.mean(correct))


def compute_pi_coverage(
    actuals: np.ndarray,
    p10: np.ndarray,
    p90: np.ndarray,
) -> list[float]:
    """80% PI coverage per horizon step (fraction of actuals in [p10, p90]).

    Returns list of length horizon.
    """
    actuals = np.asarray(actuals, dtype=float)
    p10 = np.asarray(p10, dtype=float)
    p90 = np.asarray(p90, dtype=float)
    return [
        float(np.mean((actuals[:, h] >= p10[:, h]) & (actuals[:, h] <= p90[:, h])))
        for h in range(actuals.shape[1])
    ]


def compute_decision_accuracy_h5(
    context_lasts: np.ndarray,
    p50_all_h: np.ndarray,
    actuals_all_h: np.ndarray,
    threshold: float = 100.0,
) -> dict:
    """Decision accuracy: when min(Chronos p50 h1..5) predicts a >=threshold drop,
    how often does min(actual h1..5) also drop >=threshold from context_last?

    Returns dict with precision, recall, and supporting counts.
    """
    context_lasts = np.asarray(context_lasts, dtype=float)
    p50_all_h = np.asarray(p50_all_h, dtype=float)
    actuals_all_h = np.asarray(actuals_all_h, dtype=float)

    predicted_drop = np.min(p50_all_h, axis=1) <= context_lasts - threshold
    actual_drop = np.min(actuals_all_h, axis=1) <= context_lasts - threshold

    n_predicted = int(np.sum(predicted_drop))
    n_actual_when_predicted = int(np.sum(actual_drop & predicted_drop))
    n_actual_total = int(np.sum(actual_drop))

    precision: float | None = n_actual_when_predicted / n_predicted if n_predicted > 0 else None
    recall: float | None = n_actual_when_predicted / n_actual_total if n_actual_total > 0 else None

    return {
        "n_chronos_predicted_100_drop": n_predicted,
        "n_actual_100_drop_when_predicted": n_actual_when_predicted,
        "precision": round(precision, 4) if precision is not None else None,
        "n_actual_100_drops_total": n_actual_total,
        "recall": round(recall, 4) if recall is not None else None,
    }


def compute_peak_timing_error(
    p50_all_h: np.ndarray,
    actuals_all_h: np.ndarray,
) -> float | None:
    """Median |argmin(p50_h1..5) - argmin(actual_h1..5)| in days over all folds.

    Returns None if no folds.
    """
    p50_all_h = np.asarray(p50_all_h, dtype=float)
    actuals_all_h = np.asarray(actuals_all_h, dtype=float)
    if len(p50_all_h) == 0:
        return None
    errors = [
        abs(int(np.argmin(p50_all_h[i])) - int(np.argmin(actuals_all_h[i])))
        for i in range(len(p50_all_h))
    ]
    return float(np.median(errors))


def compute_wilcoxon_p(paired_diffs: list[float]) -> float | None:
    """Wilcoxon signed-rank test (two-tailed) on paired MAE differences.

    paired_diffs[i] = mae_chronos_fold_i - mae_naive_fold_i.
    Negative median means Chronos wins on average.
    Returns p-value, or None if n < 6 or scipy unavailable.
    """
    if len(paired_diffs) < 6:
        return None
    try:
        from scipy.stats import wilcoxon

        diffs = np.asarray(paired_diffs, dtype=float)
        nonzero = diffs[diffs != 0]
        if len(nonzero) == 0:
            return 1.0
        _, p = wilcoxon(nonzero)
        return round(float(p), 4)
    except ImportError:
        return None
