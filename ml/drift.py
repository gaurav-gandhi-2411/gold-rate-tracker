"""Drift monitoring — compares previous forecast to actual price.

Runs in CI after price scrape, before new forecast is written. Appends a
residual entry to data/drift_metrics.json (30-day rolling retention). Sends
an ntfy notification when the rolling 7-day MAE exceeds
DRIFT_THRESHOLD × baseline_mae (default threshold: 1.5).

Usage:
    python -m ml.drift

Env vars:
    NTFY_TOPIC       ntfy.sh topic (same secret used by price-drop notifs)
    NTFY_SERVER      defaults to https://ntfy.sh
    DRIFT_THRESHOLD  float, default 1.5
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data"
PROD_DIR = ROOT / "models" / "production"

DRIFT_METRICS_PATH = DATA_DIR / "drift_metrics.json"
FORECAST_PATH = DATA_DIR / "forecast.json"
PRICES_PATH = DATA_DIR / "prices.json"
LGBM_META_PATH = PROD_DIR / "lgbm-meta.json"

_30_DAYS = timedelta(days=30)
_7_DAYS = timedelta(days=7)

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
DRIFT_THRESHOLD = float(os.environ.get("DRIFT_THRESHOLD", "1.5"))


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _load_drift_metrics() -> list[dict]:
    data = _load_json(DRIFT_METRICS_PATH, [])
    return data if isinstance(data, list) else []


def _prune_old(entries: list[dict], now: datetime) -> list[dict]:
    cutoff = (now - _30_DAYS).isoformat()
    return [e for e in entries if e.get("ts", "") >= cutoff]


def _rolling_7d_mae(entries: list[dict], now: datetime) -> float | None:
    cutoff = (now - _7_DAYS).isoformat()
    recent = [e for e in entries if e.get("ts", "") >= cutoff and "residual" in e]
    if not recent:
        return None
    return sum(abs(e["residual"]) for e in recent) / len(recent)


def _load_baseline_mae() -> float | None:
    meta = _load_json(LGBM_META_PATH, {})
    if isinstance(meta, dict) and "val_mae" in meta:
        try:
            return float(meta["val_mae"])
        except (TypeError, ValueError):
            pass
    return None


def _send_ntfy(title: str, message: str, priority: int = 4) -> None:
    if not NTFY_TOPIC:
        print("NTFY_TOPIC not set — skipping drift notification")
        return
    url = f"{NTFY_SERVER}/{NTFY_TOPIC}"
    req = urllib.request.Request(
        url,
        data=message.encode(),
        headers={
            "Title": title,
            "Tags": "warning,chart_with_upwards_trend",
            "Priority": str(priority),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"Drift notification sent ({resp.status}): {url}")
    except Exception as exc:
        print(f"ntfy push failed: {exc}")


def run_drift_check() -> dict | None:
    """Compare previous forecast to latest actual price.

    Returns the new entry dict appended to drift_metrics.json, or None if
    the comparison was not possible (no prior forecast, no prices, duplicate).
    """
    now = datetime.now(UTC)

    # Load PREVIOUS forecast (written by last CI run, about to be overwritten)
    fc = _load_json(FORECAST_PATH, None)
    if not isinstance(fc, dict) or "predicted_22k" not in fc:
        print("drift: no valid previous forecast — skipping")
        return None

    prev_forecast_22k = float(fc["predicted_22k"])
    target_time_str = fc.get("target_time", "")
    model_version = fc.get("model_version", "unknown")

    # Latest actual price reading
    prices = _load_json(PRICES_PATH, [])
    if not isinstance(prices, list) or not prices:
        print("drift: prices.json empty — skipping")
        return None

    latest = sorted(prices, key=lambda r: r.get("timestamp", ""))[-1]
    actual_22k = latest.get("22k")
    actual_ts = latest.get("timestamp", "")
    if actual_22k is None:
        print("drift: latest price has no 22k field — skipping")
        return None

    actual_22k = float(actual_22k)
    residual = actual_22k - prev_forecast_22k

    # Load and prune existing entries
    entries = _load_drift_metrics()
    entries = _prune_old(entries, now)

    # Deduplicate on actual_ts to avoid double-counting the same reading
    if any(e.get("actual_ts") == actual_ts for e in entries):
        print(f"drift: entry for {actual_ts} already recorded — skipping")
        return None

    baseline_mae = _load_baseline_mae()

    new_entry: dict = {
        "ts": now.isoformat(),
        "target_time": target_time_str,
        "actual_ts": actual_ts,
        "actual_22k": actual_22k,
        "forecast_22k": prev_forecast_22k,
        "residual": residual,
        "model_version": model_version,
    }
    if baseline_mae is not None:
        new_entry["baseline_mae"] = baseline_mae

    entries.append(new_entry)

    DRIFT_METRICS_PATH.parent.mkdir(exist_ok=True)
    DRIFT_METRICS_PATH.write_text(json.dumps(entries, indent=2) + "\n")
    print(
        f"drift: residual={residual:+.0f} "
        f"(actual={actual_22k:.0f}, forecast={prev_forecast_22k:.0f})"
    )

    # Threshold check
    rolling_mae = _rolling_7d_mae(entries, now)
    if rolling_mae is None:
        print("drift: no entries in 7d window yet — no threshold check")
        return new_entry

    print(f"drift: rolling 7d MAE = {rolling_mae:.1f}")

    if baseline_mae is None:
        print("drift: no baseline_mae available — skipping threshold check")
        return new_entry

    drift_ratio = rolling_mae / baseline_mae
    print(f"drift: drift_ratio = {drift_ratio:.3f} (threshold={DRIFT_THRESHOLD})")

    if drift_ratio > DRIFT_THRESHOLD:
        title = f"Gold forecast drift: ratio={drift_ratio:.2f}"
        message = (
            f"Model: {model_version}\n"
            f"Rolling 7-day MAE: ₹{rolling_mae:.1f}\n"
            f"Training baseline MAE: ₹{baseline_mae:.1f}\n"
            f"Drift ratio: {drift_ratio:.2f}\n"
            f"Retraining recommended."
        )
        _send_ntfy(title, message)

    return new_entry


if __name__ == "__main__":
    run_drift_check()
