"""CPU-only ensemble inference.

Loads tft.onnx, nbeats.onnx, and lgbm.txt from models/production/ and produces
a uniform-weighted ensemble forecast written to data/forecast.json.

No torch dependency — ONNX models run via onnxruntime, LightGBM via its native API.

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
    tomorrow_idx = pd.DatetimeIndex(
        [past_idx[-1] + pd.Timedelta("1D")], tz=past_idx.tz
    )

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
# Main entry point
# ------------------------------------------------------------------


def main() -> None:
    from ml.features import ALL_FEATURE_COLS, FEATURE_COLS, build_feature_matrix, get_predict_row
    from ml.forecast import load_combined_history
    from ml.macro import load_macro_features
    from ml.regime import REGIME_FEATURE_COLS, add_regime_to_macro

    history = load_combined_history()

    macro_df = None
    try:
        macro_df = load_macro_features()
        if macro_df is not None:
            # Forward-fill macro through weekends/holidays so that price rows
            # from Sat/Sun (beyond the Friday cache) still get macro values.
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

    mean, std = _load_normalizer()

    # Build daily price series
    df = history.copy()
    df["ts"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("ts")
    full_idx = pd.date_range(
        df["ts"].min().normalize(),
        df["ts"].max().normalize(),
        freq="D",
        tz="UTC",
    )
    prices_daily = (
        df.set_index("ts")["22k"].astype(float).reindex(full_idx, method="ffill")
    )
    current_22k = float(prices_daily.iloc[-1])

    # --- TFT inference (requires macro for meaningful predictions) ---
    tft_delta: float | None = None
    if (PROD_DIR / "tft.onnx").exists() and macro_df is not None:
        try:
            past_in, fut_in = _build_tft_inputs(prices_daily, macro_df, mean, std)
            tft_norm = _run_onnx_tft(past_in, fut_in)
            tft_price = float(tft_norm * std + mean)
            tft_delta = tft_price - current_22k
            print(f"TFT delta: Rs.{tft_delta:+.1f}")
        except Exception as exc:
            print(f"TFT inference failed ({exc})")
    elif not (PROD_DIR / "tft.onnx").exists():
        print("TFT ONNX not found — skipping")
    else:
        print("TFT skipped — macro unavailable")

    # --- N-BEATS inference ---
    nbeats_delta: float | None = None
    if (PROD_DIR / "nbeats.onnx").exists():
        try:
            past_in_nb = _build_nbeats_input(prices_daily, mean, std)
            nb_norm = _run_onnx_nbeats(past_in_nb)
            nb_price = float(nb_norm * std + mean)
            nbeats_delta = nb_price - current_22k
            print(f"N-BEATS delta: Rs.{nbeats_delta:+.1f}")
        except Exception as exc:
            print(f"N-BEATS inference failed ({exc})")
    else:
        print("N-BEATS ONNX not found — skipping")

    # --- LightGBM inference ---
    lgbm_delta: float | None = None
    lgbm_p10: float | None = None
    lgbm_p90: float | None = None
    feature_cols_used: list[str] = []
    if (PROD_DIR / "lgbm.txt").exists():
        try:
            feat_df = build_feature_matrix(history, macro_df=macro_df)
            if macro_df is not None:
                feature_cols_used = list(ALL_FEATURE_COLS)
                if all(c in feat_df.columns for c in REGIME_FEATURE_COLS):
                    feature_cols_used = feature_cols_used + list(REGIME_FEATURE_COLS)
            else:
                feature_cols_used = list(FEATURE_COLS)

            x_pred, _ = get_predict_row(feat_df, feature_cols=feature_cols_used)
            if x_pred is None:
                # Most recent row may have NaN macro features (cache 1 day behind).
                # Fall back to second-to-last row which is more likely complete.
                row = feat_df.iloc[-2][feature_cols_used]
                if not row.isna().any():
                    x_pred = row.values.reshape(1, -1)
                    print("LightGBM: using t-1 row (most recent has incomplete macro)")
            if x_pred is not None:
                lgbm_delta, lgbm_p10, lgbm_p90 = _run_lgbm(x_pred)
                lgbm_p10 = min(lgbm_p10, lgbm_delta)
                lgbm_p90 = max(lgbm_p90, lgbm_delta)
                print(f"LightGBM delta: Rs.{lgbm_delta:+.1f}")
            else:
                print("LightGBM: prediction row has NaN features — skipping")
        except Exception as exc:
            print(f"LightGBM inference failed ({exc})")
    else:
        print("LightGBM model not found — skipping")

    # --- Uniform-weighted ensemble ---
    available_deltas = [d for d in [lgbm_delta, tft_delta, nbeats_delta] if d is not None]
    if not available_deltas:
        raise RuntimeError("All models failed — cannot produce ensemble forecast")

    ensemble_delta = float(np.mean(available_deltas))
    n_models = len(available_deltas)

    # Ensemble CI: span the range of available model predictions so the
    # interval always contains the ensemble point estimate.
    ensemble_lower = min(available_deltas)
    ensemble_upper = max(available_deltas)

    predicted_22k = round(current_22k + ensemble_delta)
    lower = round(current_22k + ensemble_lower)
    upper = round(current_22k + ensemble_upper)

    predicted_at = datetime.now(UTC)
    target_time = (predicted_at + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    assert target_time > predicted_at, "target_time must be in the future"

    prices_path = DATA_DIR / "prices.json"
    real_readings_count = (
        len(json.loads(prices_path.read_text()))
        if prices_path.exists()
        else 0
    )

    result: dict = {
        "predicted_at": predicted_at.isoformat(),
        "target_time": target_time.isoformat(),
        "predicted_22k": predicted_22k,
        "lower": lower,
        "upper": upper,
        "model_version": "ensemble-3m",
        "training_rows": 0,
        "feature_count": len(feature_cols_used),
        "macro_features_used": macro_df is not None,
        "nbeats_available": nbeats_delta is not None,
        "nbeats_delta": round(nbeats_delta, 1) if nbeats_delta is not None else None,
        "real_readings_count": real_readings_count,
        "warmup": real_readings_count < 56,
        "ensemble": {"method": "uniform", "n_models": n_models},
        "models": {
            "lgbm": (
                {
                    "delta": round(lgbm_delta, 1),
                    "lower": round(lgbm_p10, 1),
                    "upper": round(lgbm_p90, 1),
                }
                if lgbm_delta is not None
                else None
            ),
            "tft": (
                {"delta": round(tft_delta, 1)} if tft_delta is not None else None
            ),
            "nbeats": (
                {"delta": round(nbeats_delta, 1)} if nbeats_delta is not None else None
            ),
        },
    }

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "forecast.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        f"Ensemble forecast: 22K=Rs.{predicted_22k} [{lower}-{upper}] "
        f"({n_models} models, uniform weight)"
    )


if __name__ == "__main__":
    main()
