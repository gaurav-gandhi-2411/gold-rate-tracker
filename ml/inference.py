"""CPU-only LightGBM inference.

Loads lgbm.txt from models/production/ and produces a forecast written to
data/forecast.json. TFT and N-BEATS are gated behind real-readings thresholds
and skipped until the corpus is large enough. ONNX helpers are kept for
future reintroduction (Phase 6).

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

# Real-data corpus thresholds for deep model reintroduction (Phase 6)
MIN_REAL_READINGS_FOR_NBEATS = 1000
MIN_REAL_READINGS_FOR_TFT = 2000
MIN_REAL_READINGS_FOR_WARMUP_CLEAR = 100

# Must match TFTForecaster training: PAST_COV_COLS and FUTURE_COV_COLS order
_PAST_COV_COLS = [
    "usd_inr",
    "gold_usd",
    "us_10y_yield",
    "dxy",
    "sensex",
    "vix_level",
    "usd_inr_change_1d",
    "gold_usd_change_1d",
    "gold_usd_5d_vol",
    "sensex_5d_return",
]
_FUTURE_COV_COLS = ["dow", "dom", "month", "akshaya_tritiya", "dhanteras", "regime"]
_ICL = 30  # input_chunk_length (must match tft.yaml / nbeats.yaml)


# ------------------------------------------------------------------
# Normalizer
# ------------------------------------------------------------------


def _load_normalizer() -> tuple[float, float]:
    """Load z-score params from models/production/normalizer.json.

    Falls back to scanning local checkpoints if production copy is missing.
    """
    path = PROD_DIR / "normalizer.json"
    if not path.exists():
        for model in ("nbeats", "tft"):
            for d in sorted((ROOT / "models" / "local" / model).glob("v*"), reverse=True):
                p = d / "normalizer.json"
                if p.exists():
                    path = p
                    break
            if path.exists():
                break
    data = json.loads(path.read_text())
    return float(data["mean"]), float(data["std"])


# ------------------------------------------------------------------
# Calendar helpers
# ------------------------------------------------------------------


def _calendar_array(timestamps: pd.DatetimeIndex, regime_series: pd.Series | None) -> np.ndarray:
    """Build FUTURE_COV_COLS array for the given timestamps."""
    from ml.features import _AKSHAYA_TRITIYA, _DHANTERAS, _is_festival_window

    n = len(timestamps)
    arr = np.zeros((n, len(_FUTURE_COV_COLS)), dtype=np.float32)
    for i, ts in enumerate(timestamps):
        d = ts.date()
        arr[i, 0] = float(ts.dayofweek)
        arr[i, 1] = float(ts.day)
        arr[i, 2] = float(ts.month)
        arr[i, 3] = float(_is_festival_window(d, _AKSHAYA_TRITIYA))
        arr[i, 4] = float(_is_festival_window(d, _DHANTERAS))

    if regime_series is not None:
        rs = regime_series.copy()
        if rs.index.tz is not None:
            rs.index = rs.index.normalize().tz_localize(None)
        else:
            rs.index = rs.index.normalize()
        ts_naive = timestamps.normalize().tz_localize(None)
        arr[:, 5] = rs.reindex(ts_naive, method="ffill").fillna(0.0).values.astype(np.float32)

    return arr


# ------------------------------------------------------------------
# TFT input construction
# ------------------------------------------------------------------


def _build_tft_inputs(
    prices_daily: pd.Series,
    macro_df: pd.DataFrame | None,
    mean: float,
    std: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Build past_input (1, ICL, 17) and future_input (1, 1, 6) for TFT ONNX.

    past_input channel layout: [normalized_price | past_cov×10 | calendar×6]
    This must match the concatenation order in darts' TFT internal model.
    """
    past_idx = prices_daily.index[-_ICL:]
    tomorrow_idx = pd.DatetimeIndex([past_idx[-1] + pd.Timedelta("1D")], tz=past_idx.tz)

    # Column 0: normalized prices
    prices = prices_daily.values[-_ICL:].astype(np.float32)
    norm_prices = ((prices - mean) / std).reshape(_ICL, 1)

    # Columns 1-10: macro past covariates (forward-filled to past_idx dates)
    if macro_df is not None:
        m = macro_df.copy()
        if m.index.tz is not None:
            m.index = m.index.normalize().tz_localize(None)
        else:
            m.index = m.index.normalize()
        idx_naive = past_idx.normalize().tz_localize(None)
        past_macro = np.zeros((_ICL, len(_PAST_COV_COLS)), dtype=np.float32)
        for j, col in enumerate(_PAST_COV_COLS):
            if col in m.columns:
                past_macro[:, j] = m[col].reindex(idx_naive, method="ffill").fillna(0.0).values
    else:
        past_macro = np.zeros((_ICL, len(_PAST_COV_COLS)), dtype=np.float32)

    # Columns 11-16: historic future covariates (calendar over past window)
    regime_series = (
        macro_df["regime"].dropna()
        if macro_df is not None and "regime" in macro_df.columns
        else None
    )
    cal_hist = _calendar_array(past_idx, regime_series)  # (ICL, 6)
    cal_tomorrow = _calendar_array(tomorrow_idx, regime_series)  # (1, 6)

    past_input = np.concatenate([norm_prices, past_macro, cal_hist], axis=1)  # (ICL, 17)
    past_input = past_input.reshape(1, _ICL, 17).astype(np.float32)  # (1, 30, 17)
    future_input = cal_tomorrow.reshape(1, 1, 6).astype(np.float32)  # (1, 1, 6)

    return past_input, future_input


# ------------------------------------------------------------------
# N-BEATS input construction
# ------------------------------------------------------------------


def _build_nbeats_input(prices_daily: pd.Series, mean: float, std: float) -> np.ndarray:
    """Build past_input (1, ICL, 1) for N-BEATS ONNX."""
    prices = prices_daily.values[-_ICL:].astype(np.float32)
    norm_prices = (prices - mean) / std
    return norm_prices.reshape(1, _ICL, 1)


# ------------------------------------------------------------------
# ONNX runners
# ------------------------------------------------------------------


def _run_onnx_tft(past_input: np.ndarray, future_input: np.ndarray) -> float:
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    sess = ort.InferenceSession(
        str(PROD_DIR / "tft.onnx"), sess_options=opts, providers=["CPUExecutionProvider"]
    )
    result = sess.run(
        ["point_estimate"],
        {"past_input": past_input, "future_input": future_input},
    )
    return float(result[0][0, 0])  # normalized scalar


def _run_onnx_nbeats(past_input: np.ndarray) -> float:
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.log_severity_level = 3
    sess = ort.InferenceSession(
        str(PROD_DIR / "nbeats.onnx"), sess_options=opts, providers=["CPUExecutionProvider"]
    )
    result = sess.run(["point_estimate"], {"past_input": past_input})
    return float(result[0][0, 0])  # normalized scalar


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
# Model performance helpers
# ------------------------------------------------------------------


def _load_model_maes() -> dict[str, float]:
    """Load val_mae from each model's production meta JSON."""
    maes: dict[str, float] = {}
    for model, fname in [
        ("lgbm", "lgbm-meta.json"),
        ("tft", "tft-meta.json"),
        ("nbeats", "nbeats-meta.json"),
    ]:
        path = PROD_DIR / fname
        if path.exists():
            try:
                data = json.loads(path.read_text())
                maes[model] = float(data.get("val_mae", float("inf")))
            except Exception:
                pass
    return maes


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
        DATA_DIR as FC_DATA_DIR,
        _calibrate_seed,
        _load_json,
        _make_lgb,
        load_combined_history,
    )
    from ml.macro import load_macro_features
    from ml.regime import add_regime_to_macro

    # --- 1. Load data and capture seed calibration scale ---
    seed_entries = _load_json(FC_DATA_DIR / "history_seed.json")
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

    # TFT and N-BEATS remain gated until Phase 6 (real-data corpus threshold)
    print(f"TFT/N-BEATS gated — need {MIN_REAL_READINGS_FOR_TFT}/{MIN_REAL_READINGS_FOR_NBEATS}"
          f" real readings (have {real_readings_count})")

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
    pi_coverage_80_empirical = round(float(np.sum(residuals <= conformal_pi_half) / len(residuals)), 3)

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
        # Legacy / future Phase 6 fields kept for schema compatibility
        "nbeats_available": False,
        "nbeats_delta": None,
        "min_readings_for_model_improvement": 200,
        "ensemble": {
            "method": "naive_blend",
            "n_models": 2,
            "lgbm_weight": round(w_lgbm, 3),
            "naive_weight": round(w_naive, 3),
            "min_readings_for_nbeats": MIN_REAL_READINGS_FOR_NBEATS,
            "min_readings_for_tft": MIN_REAL_READINGS_FOR_TFT,
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
