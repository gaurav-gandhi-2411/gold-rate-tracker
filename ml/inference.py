"""CPU-only LightGBM inference.

Loads lgbm.txt from models/production/ and produces a forecast written to
data/forecast.json. TFT and N-BEATS have been retired (PR B); the synthetic
seed corpus has been archived to archive/ and is loaded for calibration
continuity until PR H replaces the legacy path entirely.

Usage:
    python -m ml.inference
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROD_DIR = ROOT / "models" / "production"
DATA_DIR = ROOT / "data"

MIN_REAL_READINGS_FOR_WARMUP_CLEAR = 100


# ------------------------------------------------------------------
# LightGBM runner
# ------------------------------------------------------------------


def _run_lgbm(x_pred: np.ndarray) -> tuple[float, float, float]:
    import lightgbm as lgb

    b_mean = lgb.Booster(model_file=str(PROD_DIR / "lgbm.txt"))
    b_p10 = lgb.Booster(model_file=str(PROD_DIR / "lgbm-p10.txt"))
    b_p90 = lgb.Booster(model_file=str(PROD_DIR / "lgbm-p90.txt"))
    delta_mean = float(b_mean.predict(x_pred)[0])
    delta_p10 = float(b_p10.predict(x_pred)[0])
    delta_p90 = float(b_p90.predict(x_pred)[0])
    return delta_mean, delta_p10, delta_p90


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------


def main() -> None:
    from ml.features import (
        FEATURE_COLS,
        MINIMAL_FEATURE_COLS,
        build_feature_matrix,
        get_predict_row,
        get_train_Xy,
    )
    from ml.forecast import (
        ARCHIVE_SEED_PATH,
        _calibrate_seed,
        _load_json,
        _make_lgb,
        load_combined_history,
    )
    from ml.forecast import (
        DATA_DIR as FC_DATA_DIR,
    )
    from ml.macro import load_macro_features
    from ml.regime import add_regime_to_macro

    # --- 1. Load data and capture seed calibration scale ---
    # Deprecated: loading synthetic seed from archive/ — legacy path removed in PR H
    seed_entries = _load_json(ARCHIVE_SEED_PATH)
    live_entries = _load_json(FC_DATA_DIR / "prices.json")
    real_readings_count = len(live_entries)

    seed_scale: float = 1.0
    if seed_entries and live_entries:
        live_df_raw = pd.DataFrame(live_entries)
        live_df_raw["ts_parsed"] = pd.to_datetime(live_df_raw["timestamp"], utc=True)
        live_df_raw["utc_date"] = live_df_raw["ts_parsed"].dt.date
        live_daily_raw = (
            live_df_raw.sort_values("ts_parsed")
            .drop_duplicates("utc_date", keep="last")
            .to_dict("records")
        )
        _, seed_scale = _calibrate_seed(seed_entries, live_daily_raw)

    history = load_combined_history()
    current_22k = float(history.iloc[-1]["22k"])

    # --- 2. Macro features ---
    macro_df = None
    try:
        macro_df = load_macro_features()
        if macro_df is not None:
            today_utc = pd.Timestamp.now(tz="UTC").normalize()
            if macro_df.index[-1] < today_utc:
                extended_idx = pd.date_range(macro_df.index[0], today_utc, freq="D", tz="UTC")
                macro_df = macro_df.reindex(extended_idx, method="ffill")
            macro_df = add_regime_to_macro(macro_df)
            print(f"Macro features: {len(macro_df)} rows (extended to {macro_df.index[-1].date()})")
        else:
            print("Macro cache absent — using base features only")
    except Exception as exc:
        print(f"Macro features unavailable ({exc})")

    # --- 3. Feature matrix + active feature set (minimal_v2) ---
    feat_df = build_feature_matrix(history, macro_df=macro_df)

    # minimal_v2: 8 features — only keep those present (macro cols absent if macro unavailable)
    feature_cols = [c for c in MINIMAL_FEATURE_COLS if c in feat_df.columns]
    if len(feature_cols) < 4:
        feature_cols = list(FEATURE_COLS)
        print(f"minimal_v2 has <4 cols — falling back to FEATURE_COLS ({len(feature_cols)})")
    print(f"Feature set: minimal_v2 => {len(feature_cols)} features available")

    # --- 4. Train/calibration split for conformal PI and blend weights ---
    X, y = get_train_Xy(feat_df, feature_cols=feature_cols)
    n_total = len(X)
    if n_total < 15:
        raise RuntimeError(f"Too few training rows ({n_total}); need ≥15")

    calib_n = max(10, n_total // 5)  # ~20% calibration, minimum 10 rows
    X_tr, y_tr = X.iloc[:-calib_n], y.iloc[:-calib_n]
    X_cal, y_cal = X.iloc[-calib_n:], y.iloc[-calib_n:]

    m_cal = _make_lgb("regression")
    m_cal.fit(X_tr.values, y_tr.values)
    cal_preds = m_cal.predict(X_cal.values)
    residuals = np.abs(cal_preds - y_cal.values)

    conformal_pi_half = float(np.percentile(residuals, 80))
    val_mae = float(np.mean(residuals))
    naive_mae = float(np.mean(np.abs(y_cal.values)))  # naive delta=0 baseline

    # Empirical coverage on calibration set (~80% by construction from percentile choice)
    pi_coverage_80_empirical = round(
        float(np.sum(residuals <= conformal_pi_half) / len(residuals)), 3
    )

    # --- 5. Final model: retrain on all data ---
    m_final = _make_lgb("regression")
    m_final.fit(X.values, y.values)

    x_pred, _ = get_predict_row(feat_df, feature_cols=feature_cols)
    if x_pred is None:
        row = feat_df.iloc[-2][feature_cols]
        if not row.isna().any():
            x_pred = row.values.reshape(1, -1)
            print("Using t-1 row (most recent has incomplete features)")
    if x_pred is None:
        raise RuntimeError("Prediction row has NaN features — cannot produce forecast")

    lgbm_delta = float(m_final.predict(x_pred)[0])
    print(f"LightGBM delta: Rs.{lgbm_delta:+.1f}")

    # --- 6. Naive blend (inverse-MAE weights, eps=1.0, clamp [0.1, 0.9]) ---
    _EPS = 1.0
    w_lgbm_raw = 1.0 / (val_mae + _EPS)
    w_naive_raw = 1.0 / (naive_mae + _EPS)
    w_lgbm = w_lgbm_raw / (w_lgbm_raw + w_naive_raw)
    w_lgbm = max(0.1, min(0.9, w_lgbm))  # clamp [0.1, 0.9]
    w_naive = 1.0 - w_lgbm
    blended_delta = w_lgbm * lgbm_delta  # naive_delta = 0 (last-value forecast)

    # --- 7. Final forecast values with conformal PI ---
    predicted_22k = round(current_22k + blended_delta)
    lower = round(current_22k + blended_delta - conformal_pi_half)
    upper = round(current_22k + blended_delta + conformal_pi_half)
    print(
        f"Blended forecast: 22K=Rs.{predicted_22k} [{lower}-{upper}]"
        f"  w_lgbm={w_lgbm:.2f}  conf_pi=+/-{conformal_pi_half:.0f}"
    )

    # --- 8. Model status ---
    if val_mae < naive_mae * 0.99:
        model_status = "beating_naive"
    elif val_mae <= naive_mae * 1.01:
        model_status = "matching_naive"
    else:
        model_status = "trailing_naive"

    # --- 9. Persist model files for reference / Phase 6 ensemble ---
    PROD_DIR.mkdir(parents=True, exist_ok=True)
    m_final.booster_.save_model(str(PROD_DIR / "lgbm.txt"))
    lgbm_meta = {
        "n_train": n_total,
        "n_val": calib_n,
        "feature_cols": feature_cols,
        "feature_set": "minimal_v2",
        "val_mae": round(val_mae, 2),
        "naive_mae": round(naive_mae, 2),
        "conformal_pi_half": round(conformal_pi_half, 2),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    (PROD_DIR / "lgbm-meta.json").write_text(json.dumps(lgbm_meta, indent=2) + "\n")

    # --- 10. Write forecast.json ---
    predicted_at = datetime.now(UTC)
    target_time = (predicted_at + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    assert target_time > predicted_at, "target_time must be in the future"

    result: dict = {
        "predicted_at": predicted_at.isoformat(),
        "target_time": target_time.isoformat(),
        "predicted_22k": predicted_22k,
        "lower": lower,
        "upper": upper,
        "model_version": "lgbm-minimal_v2",
        "training_rows": n_total,
        "feature_count": len(feature_cols),
        "feature_set": "minimal_v2",
        "macro_features_used": macro_df is not None,
        "real_readings_count": real_readings_count,
        "warmup": real_readings_count < MIN_REAL_READINGS_FOR_WARMUP_CLEAR,
        "val_mae": round(val_mae, 1),
        "naive_mae": round(naive_mae, 1),
        "model_status": model_status,
        # Phase 2: naive blend
        "lgbm_pred_raw": round(lgbm_delta, 1),
        "naive_pred_raw": 0.0,
        "blend_weight_lgbm": round(w_lgbm, 3),
        "blend_weight_naive": round(w_naive, 3),
        # Phase 2: conformal PI
        "conformal_pi_half": round(conformal_pi_half, 1),
        "pi_coverage_80_empirical": pi_coverage_80_empirical,
        "pi_coverage_80_calibrated": 0.80,
        # Phase 2: seed calibration
        "seed_calibration_scale": round(seed_scale, 4),
        "ensemble": {
            "method": "naive_blend",
            "n_models": 2,
            "lgbm_weight": round(w_lgbm, 3),
            "naive_weight": round(w_naive, 3),
            "min_readings_for_warmup_clear": MIN_REAL_READINGS_FOR_WARMUP_CLEAR,
        },
        "models": {
            "lgbm": {
                "delta": round(lgbm_delta, 1),
                "weight": round(w_lgbm, 3),
            },
            "naive": {
                "delta": 0.0,
                "weight": round(w_naive, 3),
            },
        },
    }

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "forecast.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"Forecast written: 22K=Rs.{predicted_22k} [{lower}-{upper}] (minimal_v2 + naive-blend)")


if __name__ == "__main__":
    main()
