"""
inference.py — Naive flat-hold headline forecast + Chronos directional companion.

Production design per ADR 012, ADR 014, and ADR 022:
  - Headline forecast: naive flat-hold (predicted = current 22K price).
  - Confidence interval: 80th-percentile of the last 30 folds' naive h=1 (next
    trading day) errors — matches the horizon ml.metrics.compute_band_coverage
    actually measures against (see ADR 022). A separate h=5 reference (same
    percentile, horizon_idx=4) floors ml.volatility's dynamic 5-day estimate —
    that estimate is a genuinely different, still-5-day quantity.
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

from ml.drivers import compute_driver_attribution
from ml.notifications import NotificationState
from ml.volatility import compute_vol_context

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

logger = logging.getLogger(__name__)

# 80th percentile — matches the conformal PI percentile used in the legacy LightGBM path.
_CONFORMAL_PCT: int = 80
# Number of recent backtest folds used for conformal PI and naive_mae_recent computation.
_CONFORMAL_FOLDS: int = 30
# Minimum valid fold errors required for a reliable 80th-percentile estimate.
# Below this threshold the PI estimate has too much variance to be useful; the
# caller writes model_status="insufficient_backtest_history" instead of a fake band.
_MIN_CONFORMAL_FOLDS: int = 30
# Staleness threshold shared with app.js banner (hours). Change in ONE place only.
# Per ADR 025: this now gates Tanishq *enrichment* — how fresh a successful scrape
# must be to override the IBJA-primary display with a confirmed retail reading.
# Tanishq being older than this (now the expected steady state under its sustained
# Cloudflare block, not an error) simply means no enrichment this cycle.
_STALE_THRESHOLD_H: int = 8
# Generous backstop (calendar days) on how old the last IBJA reading may be before
# the primary display gives up entirely and falls through to the last-confirmed-
# Tanishq-price state. Per ADR 025, IBJA is now the PRIMARY source (not an
# occasional fallback), so this is deliberately loose — it should essentially
# never bind in practice (IBJA publishes ~5x/week) and exists only as a defensive
# ceiling against showing an absurdly old number as current. Weekend/holiday
# carry-forward (a few days old) is expected and handled by business-day-aware
# alerting (ml.ibja.compute_ibja_gap_business_days), not by this constant.
_IBJA_DISPLAY_MAX_AGE_DAYS: int = 14
# IBJA publishes pm_916 ~17:00 IST = 11:30 UTC on each trading day.
_IBJA_PUBLISH_UTC: tuple[int, int] = (11, 30)


def _load_json(path: Path) -> dict | list | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _compute_conformal_pi(backtest: dict, horizon_idx: int = 4) -> tuple[float, float] | None:
    """80th-percentile conformal PI from the last 30 folds' naive absolute errors at
    a given horizon step.

    horizon_idx indexes into each fold's actuals/naive arrays (0 = next trading day
    (h=1), 4 = 5th day out (h=5)). Defaults to 4 (h=5) for backward compatibility with
    the vol-context floor reference (see main()); the displayed band (headline.lower/
    upper) is calibrated at horizon_idx=0 (h=1) — the horizon actually measured by
    ml.metrics.compute_band_coverage, so the band and its coverage claim agree.

    Returns (conformal_pi_half, naive_mae_recent_30), or None when fewer than
    _MIN_CONFORMAL_FOLDS valid fold errors are available.  None signals the caller
    to write model_status='insufficient_backtest_history' rather than a fabricated PI.
    """
    folds: list[dict] = backtest.get("folds", [])
    recent = folds[-_CONFORMAL_FOLDS:]

    errors: list[float] = []
    for fold in recent:
        actuals = fold.get("actuals", [])
        naive = fold.get("naive", [])
        if len(actuals) >= 5 and len(naive) >= 5:
            errors.append(abs(actuals[horizon_idx] - naive[horizon_idx]))

    if len(errors) < _MIN_CONFORMAL_FOLDS:
        return None

    arr = np.array(errors)
    return round(float(np.percentile(arr, _CONFORMAL_PCT)), 1), round(float(np.mean(arr)), 1)


def _build_chronos_companion(
    probe: dict,
    backtest: dict,
    calibration: dict,
    notification_state: NotificationState | None = None,
) -> dict:
    """Build the chronos_companion block from probe + backtest + calibration.

    Applies IBJA->Tanishq calibration to horizon arrays when calibration.valid is True
    and the probe succeeded. Calibration is refitted automatically by ml/calibration.py
    (run after IBJA append each CI cycle); valid flips to True once 30 overlap pairs
    accumulate, after which horizon arrays are expressed in Tanishq retail units.

    notification_state: optional NotificationState used to compute calibration_just_unlocked.
        True when calibration is newly valid and T6 has never fired (last_t6_fired_date_ist=="").
    """
    from ml.notifications import compute_chronos_lean, compute_dir_acc_30f

    if probe.get("status") != "success":
        return {
            "status": probe.get("status", "failed"),
            "lean_direction": "neutral",
            "lean_strength_pct": 0.0,
            "direction_acc_30f": None,
            "direction_prob_basis": "base_rate_fallback",
            "horizon_p10": None,
            "horizon_p50": None,
            "horizon_p90": None,
            "model_version": probe.get("model_version"),
            "calibration_applied": False,
            "calibration_just_unlocked": False,
            "majority_direction": "neutral",
            "direction_consensus": 0.0,
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

    calibration_just_unlocked = (
        bool(calibration.get("valid"))
        and notification_state is not None
        and notification_state.last_t6_fired_date_ist == ""
    )

    return {
        "status": "success",
        "lean_direction": lean_direction,
        "lean_strength_pct": lean_strength_pct,
        "direction_acc_30f": round(dir_acc, 3) if dir_acc is not None else None,
        "direction_prob_basis": "base_rate_fallback",
        "horizon_p10": horizon_p10,
        "horizon_p50": horizon_p50,
        "horizon_p90": horizon_p90,
        "model_version": probe.get("model_version"),
        "calibration_applied": cal_applied,
        "calibration_just_unlocked": calibration_just_unlocked,
        "majority_direction": probe.get("majority_direction", "neutral"),
        "direction_consensus": float(probe.get("direction_consensus") or 0.0),
    }


def _select_price_source(
    current_22k: int,
    scraped_at: str,
    calibration: dict,
    data_dir: Path,
    now: datetime,
) -> tuple[int, str, int | None, int | None, str | None]:
    """Select the displayed current price per ADR 025's source hierarchy.

    IBJA-calibrated is now the PRIMARY source; a fresh Tanishq scrape is an
    ENRICHMENT that overrides it with a confirmed retail reading when available.
    Tanishq being stale is the expected steady state (sustained Cloudflare block,
    not an error) — it silently yields to the IBJA-calibrated estimate rather
    than being treated as a failure.

    Returns (current_22k, price_source, est_low, est_high, ibja_asof).
    Falls back to (current_22k, "tanishq_scrape", None, None, None) — using the
    last-confirmed Tanishq reading — when any gate fails (no valid calibration,
    no IBJA data, or IBJA itself is beyond the defensive staleness ceiling).

    Gates (all must pass for the IBJA-primary path):
      - calibration.valid == True AND slope/intercept/residual_std present
      - Tanishq scrape age > _STALE_THRESHOLD_H (i.e. no fresher enrichment to show)
      - IBJA pm_916 row exists AND its age <= _IBJA_DISPLAY_MAX_AGE_DAYS
    Does NOT touch scraped_at (ADR 021).
    Does NOT modify the Chronos-horizon calibration block in _build_chronos_companion.
    """
    _noop: tuple[int, str, int | None, int | None, str | None] = (
        current_22k,
        "tanishq_scrape",
        None,
        None,
        None,
    )

    if not calibration.get("valid"):
        return _noop

    slope = calibration.get("slope")
    intercept = calibration.get("intercept")
    residual_std = calibration.get("residual_std")
    if slope is None or intercept is None or residual_std is None:
        return _noop

    # ADR 027: prefer the genuinely out-of-sample residual_std_oos (expanding-
    # window walk-forward, no leakage) for the displayed band when available --
    # residual_std alone is in-sample (fit and evaluated on the same data) and
    # per ADR 023's caution must not be treated as a generalization estimate.
    # calibration.json files predating ADR 027 (schema_version 1) have no
    # residual_std_oos key at all; falling back to the in-sample residual_std
    # keeps those working exactly as before.
    residual_std_oos = calibration.get("residual_std_oos")
    band_half_width = residual_std_oos if residual_std_oos is not None else residual_std

    # Scrape staleness check
    try:
        scraped_dt = datetime.fromisoformat(scraped_at.replace("Z", "+00:00"))
        scrape_age_h = (now - scraped_dt).total_seconds() / 3600
    except Exception:
        logger.warning("_select_price_source: could not parse scraped_at %r", scraped_at)
        return _noop

    if scrape_age_h <= _STALE_THRESHOLD_H:
        return _noop  # scrape is fresh — no override needed

    # Resilient IBJA parquet read
    try:
        import pandas as pd  # local import — pandas not needed on the cold path

        parquet_path = data_dir / "ibja_rates.parquet"
        ibja_df = pd.read_parquet(parquet_path)
        valid_rows = ibja_df[ibja_df["pm_916"].notna()].sort_values("date")
        if valid_rows.empty:
            logger.warning("_select_price_source: no non-null pm_916 rows — skipping")
            return _noop
        latest_ibja = valid_rows.iloc[-1]
        ibja_date_str: str = str(latest_ibja["date"])[:10]  # "YYYY-MM-DD"
        pm_916 = float(latest_ibja["pm_916"])
    except FileNotFoundError:
        logger.info("_select_price_source: ibja_rates.parquet not found — skipping")
        return _noop
    except Exception as exc:
        logger.warning("_select_price_source: parquet read failed: %s — skipping", exc)
        return _noop

    # IBJA publication datetime: ~17:00 IST = 11:30 UTC on the row's date
    try:
        y, m, d = int(ibja_date_str[:4]), int(ibja_date_str[5:7]), int(ibja_date_str[8:10])
        ibja_asof_dt = datetime(y, m, d, _IBJA_PUBLISH_UTC[0], _IBJA_PUBLISH_UTC[1], tzinfo=UTC)
    except Exception as exc:
        logger.warning("_select_price_source: could not parse ibja date %r: %s", ibja_date_str, exc)
        return _noop

    ibja_age_days = (now - ibja_asof_dt).total_seconds() / 86400
    if ibja_age_days >= _IBJA_DISPLAY_MAX_AGE_DAYS:
        logger.info(
            "_select_price_source: IBJA %s is %.1fd old (>= %dd) — genuinely stale",
            ibja_date_str,
            ibja_age_days,
            _IBJA_DISPLAY_MAX_AGE_DAYS,
        )
        return _noop

    # All gates passed — compute calibrated estimate
    ibja_per_g = pm_916 / 10.0
    ibja_calibrated_22k = round(slope * ibja_per_g + intercept)
    est_low = round(ibja_calibrated_22k - band_half_width)
    est_high = round(ibja_calibrated_22k + band_half_width)
    ibja_asof_iso = ibja_asof_dt.isoformat()

    logger.info(
        "_select_price_source: ibja_per_g=%.2f -> Rs.%d [Rs.%d-Rs.%d]  ibja_date=%s",
        ibja_per_g,
        ibja_calibrated_22k,
        est_low,
        est_high,
        ibja_date_str,
    )
    return ibja_calibrated_22k, "ibja_calibrated", est_low, est_high, ibja_asof_iso


def main(now: datetime | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if now is None:
        now = datetime.now(UTC)

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
        current_22k,
        scraped_at,
        real_readings_count,
    )

    # 2. Conformal PI from backtest naive errors — h=1 (next trading day), matching
    # the horizon ml.metrics.compute_band_coverage actually tests (ADR 022).
    backtest: dict = _load_json(DATA_DIR / "backtest.json") or {}
    pi_result = _compute_conformal_pi(backtest, horizon_idx=0)
    if pi_result is None:
        fold_count = len(backtest.get("folds", []))
        logger.warning(
            "Insufficient backtest fold data (%d valid folds, need %d); "
            "writing model_status=insufficient_backtest_history",
            fold_count,
            _MIN_CONFORMAL_FOLDS,
        )
        predicted_at = now
        target_time = (predicted_at + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        result: dict = {
            "predicted_at": predicted_at.isoformat(),
            "target_window": "5d",
            "real_readings_count": real_readings_count,
            "current_22k": current_22k,
            "scraped_at": scraped_at,
            "price_source": "tanishq_scrape",
            "est_low": None,
            "est_high": None,
            "ibja_asof": None,
            "model_status": "insufficient_backtest_history",
            "model_version": "naive_flat_hold",
            "model_fallback": False,
            "warmup": False,
            "predicted_22k": current_22k,
            "lower": None,
            "upper": None,
            "target_time": target_time.isoformat(),
        }
        DATA_DIR.mkdir(exist_ok=True)
        (DATA_DIR / "forecast.json").write_text(json.dumps(result, indent=2) + "\n")
        return
    conformal_pi_half, naive_mae_recent_30 = pi_result

    # Separate h=5 reference purely to floor ml.volatility's dynamic 5-day estimate —
    # that estimate is a genuinely different (realized-vol-scaled) 5-day quantity and
    # must not silently inherit the h=1 band's magnitude. Same fold-count gate as the
    # h=1 call above, so this cannot be None when pi_result above succeeded.
    pi_result_5d = _compute_conformal_pi(backtest, horizon_idx=4)
    conformal_pi_half_5d = pi_result_5d[0] if pi_result_5d is not None else conformal_pi_half

    logger.info(
        "Conformal PI half=Rs.%.1f (h1)  naive_mae_recent_30=%.1f  vol-floor ref=Rs.%.1f (h5)",
        conformal_pi_half,
        naive_mae_recent_30,
        conformal_pi_half_5d,
    )

    calibration: dict = _load_json(DATA_DIR / "calibration.json") or {}
    current_22k, price_source, est_low, est_high, ibja_asof = _select_price_source(
        current_22k, scraped_at, calibration, DATA_DIR, now
    )

    # 3. Headline: naive flat-hold
    predicted_22k = current_22k
    lower = round(current_22k - conformal_pi_half)
    upper = round(current_22k + conformal_pi_half)

    # 3a. Dynamic vol context — magnitude-of-movement estimate, NOT a forecast (ADR 005).
    # Floored against the h=5 reference (conformal_pi_half_5d), not the h=1 displayed
    # band — this is a genuinely-5-day-scaled realized-vol estimate (ml/volatility.py).
    vol_ctx = compute_vol_context(prices, conformal_pi_half_5d)
    logger.info(
        "Vol context: method=%s  half_width=Rs.%d  regime=%s  is_degraded=%s",
        vol_ctx["method"],
        vol_ctx["half_width"],
        vol_ctx["regime"],
        vol_ctx["is_degraded"],
    )

    headline: dict = {
        "method": "naive_flat_hold",
        "predicted_22k": predicted_22k,
        "lower": lower,
        "upper": upper,
        "conformal_pi_half": conformal_pi_half,
        "naive_mae_recent_30": naive_mae_recent_30,
        "vol_context": dict(vol_ctx),
    }

    # 4. Chronos companion (read from probe; never call Chronos directly)
    probe: dict = _load_json(DATA_DIR / "chronos_probe.json") or {}
    from ml.notifications import STATE_PATH, load_state

    notification_state = load_state(STATE_PATH)
    chronos_companion = _build_chronos_companion(probe, backtest, calibration, notification_state)
    model_fallback = probe.get("status") != "success"

    # 5. Driver-context attribution (log decomposition — DESCRIPTIVE, not a forecast).
    # Wrapped in try/except so a drivers.py failure never blocks inference (norm #8).
    try:
        driver_context = compute_driver_attribution(data_dir=DATA_DIR)
    except Exception as exc:
        logger.warning("drivers: compute_driver_attribution failed: %s", exc)
        driver_context = None

    # 6. Timestamps
    predicted_at = now
    target_time = (predicted_at + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # 7. Write forecast.json — new nested schema + top-level aliases for PWA compat
    result: dict = {
        "predicted_at": predicted_at.isoformat(),
        "target_window": "5d",
        "headline": headline,
        "chronos_companion": chronos_companion,
        "driver_context": driver_context,
        "real_readings_count": real_readings_count,
        "current_22k": current_22k,
        "scraped_at": scraped_at,
        "price_source": price_source,
        "est_low": est_low,
        "est_high": est_high,
        "ibja_asof": ibja_asof,
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
        predicted_22k,
        lower,
        upper,
        chronos_companion.get("lean_direction"),
        chronos_companion.get("direction_acc_30f"),
    )


if __name__ == "__main__":
    main()
