"""
inference.py — Naive flat-hold headline forecast + Chronos directional companion.

Production design per ADR 012 and ADR 014:
  - Headline forecast: naive flat-hold (predicted = current 22K price).
  - Confidence interval: 80th-percentile of the last 30 folds' naive h=5 errors
    (conformal prediction, same percentile as the legacy LightGBM path).
  - Chronos directional companion: read from chronos_probe.json (written by the
    Chronos probe step). NOT called directly — single source of Chronos data.

Reads:
    data/prices.json        → current 22K price and scraped_at timestamp
    data/backtest.json      → last-30-fold naive h=5 errors for conformal PI
    data/chronos_probe.json → Chronos directional signal
    data/calibration.json   → IBJA→Tanishq calibration (optional, valid=false until
                              30 IBJA-Tanishq overlap pairs accumulate)

Writes:
    data/forecast.json      → new two-block schema (headline + chronos_companion)
                              with top-level aliases for PWA backward compat

Usage:
    python -m ml.inference
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

logger = logging.getLogger(__name__)

# 80th percentile — matches the conformal PI percentile used in the legacy LightGBM path.
_CONFORMAL_PCT: int = 80
# Number of recent backtest folds used for conformal PI and naive_mae_recent computation.
_CONFORMAL_FOLDS: int = 30


def _load_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _compute_conformal_pi(backtest: dict) -> tuple[float, float]:
    """80th-percentile conformal PI from the last 30 folds' naive h=5 absolute errors.

    Returns (conformal_pi_half, naive_mae_recent_30).
    Falls back to aggregate naive MAE x 1.5 when fold-level data is unavailable.
    """
    folds: list[dict] = backtest.get("folds", [])
    recent = folds[-_CONFORMAL_FOLDS:]

    errors: list[float] = []
    for fold in recent:
        actuals = fold.get("actuals", [])
        naive = fold.get("naive", [])
        if len(actuals) >= 5 and len(naive) >= 5:
            errors.append(abs(actuals[4] - naive[4]))

    if not errors:
        fallback_mae = float(backtest.get("mae_5d_avg_naive", 300.0))
        return round(fallback_mae * 1.5, 1), round(fallback_mae, 1)

    arr = np.array(errors)
    return round(float(np.percentile(arr, _CONFORMAL_PCT)), 1), round(float(np.mean(arr)), 1)


def _build_chronos_companion(
    probe: dict,
    backtest: dict,
    calibration: dict,
) -> dict:
    """Build the chronos_companion block from probe + backtest + calibration.

    Applies IBJA->Tanishq calibration to horizon arrays when calibration.valid is True
    and the probe succeeded. Calibration is currently a stub (21/30 pairs; valid=False).
    """
    from ml.notifications import compute_chronos_lean, compute_dir_acc_30f

    if probe.get("status") != "success":
        return {
            "status": probe.get("status", "failed"),
            "lean_direction": "neutral",
            "lean_strength_pct": 0.0,
            "direction_acc_30f": None,
            "horizon_p10": None,
            "horizon_p50": None,
            "horizon_p90": None,
            "model_version": probe.get("model_version"),
            "calibration_applied": False,
        }

    lean_direction_raw, lean_strength_pct = compute_chronos_lean(probe)
    lean_direction = "neutral" if lean_direction_raw == "flat" else lean_direction_raw

    dir_acc = compute_dir_acc_30f(backtest) if backtest else None

    ibja_forecast: list[dict] = probe.get("ibja_forecast", [])
    horizon_p10: list[float] | None = [d["p10"] for d in ibja_forecast] if ibja_forecast else None
    horizon_p50: list[float] | None = [d["p50"] for d in ibja_forecast] if ibja_forecast else None
    horizon_p90: list[float] | None = [d["p90"] for d in ibja_forecast] if ibja_forecast else None

    cal_applied = False
    if calibration.get("valid") and horizon_p50 is not None:
        slope = calibration.get("slope")
        intercept = calibration.get("intercept")
        if slope is not None and intercept is not None:
            cal = lambda v: round(slope * v + intercept, 2)  # noqa: E731
            horizon_p10 = [cal(v) for v in horizon_p10] if horizon_p10 else None
            horizon_p50 = [cal(v) for v in horizon_p50]
            horizon_p90 = [cal(v) for v in horizon_p90] if horizon_p90 else None
            cal_applied = True

    return {
        "status": "success",
        "lean_direction": lean_direction,
        "lean_strength_pct": lean_strength_pct,
        "direction_acc_30f": round(dir_acc, 3) if dir_acc is not None else None,
        "horizon_p10": horizon_p10,
        "horizon_p50": horizon_p50,
        "horizon_p90": horizon_p90,
        "model_version": probe.get("model_version"),
        "calibration_applied": cal_applied,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # 1. Current price
    prices: list[dict] = _load_json(DATA_DIR / "prices.json") or []
    if not prices:
        raise RuntimeError("prices.json not found or empty")
    latest = sorted(prices, key=lambda x: x["timestamp"])[-1]
    current_22k = int(latest["22k"])
    scraped_at: str = latest["timestamp"]
    real_readings_count = len(prices)
    logger.info(
        "Current 22K: Rs.%d  scraped=%s  readings=%d",
        current_22k, scraped_at, real_readings_count,
    )

    # 2. Conformal PI from backtest naive errors
    backtest: dict = _load_json(DATA_DIR / "backtest.json") or {}
    conformal_pi_half, naive_mae_recent_30 = _compute_conformal_pi(backtest)
    logger.info("Conformal PI half=Rs.%.1f  naive_mae_recent_30=%.1f", conformal_pi_half, naive_mae_recent_30)

    # 3. Headline: naive flat-hold
    predicted_22k = current_22k
    lower = round(current_22k - conformal_pi_half)
    upper = round(current_22k + conformal_pi_half)
    headline: dict = {
        "method": "naive_flat_hold",
        "predicted_22k": predicted_22k,
        "lower": lower,
        "upper": upper,
        "conformal_pi_half": conformal_pi_half,
        "naive_mae_recent_30": naive_mae_recent_30,
    }

    # 4. Chronos companion (read from probe; never call Chronos directly)
    probe: dict = _load_json(DATA_DIR / "chronos_probe.json") or {}
    calibration: dict = _load_json(DATA_DIR / "calibration.json") or {}
    chronos_companion = _build_chronos_companion(probe, backtest, calibration)
    model_fallback = probe.get("status") != "success"

    # 5. Timestamps
    predicted_at = datetime.now(UTC)
    target_time = (predicted_at + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # 6. Write forecast.json — new nested schema + top-level aliases for PWA compat
    result: dict = {
        "predicted_at": predicted_at.isoformat(),
        "target_window": "5d",
        "headline": headline,
        "chronos_companion": chronos_companion,
        "real_readings_count": real_readings_count,
        "current_22k": current_22k,
        "scraped_at": scraped_at,
        "model_fallback": model_fallback,
        # Top-level aliases — read by app.js, drift.py, metrics.py, notifications.py.
        # Removed in a follow-up PWA-update PR after the new schema is rendered in the UI.
        "predicted_22k": predicted_22k,
        "lower": lower,
        "upper": upper,
        "target_time": target_time.isoformat(),
        "model_status": "naive_headline",
        "model_version": "naive_flat_hold",
        "warmup": False,
    }

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "forecast.json").write_text(json.dumps(result, indent=2) + "\n")
    logger.info(
        "Forecast written: Rs.%d [%d-%d]  lean=%s  dir_acc=%s",
        predicted_22k, lower, upper,
        chronos_companion.get("lean_direction"),
        chronos_companion.get("direction_acc_30f"),
    )


if __name__ == "__main__":
    main()
