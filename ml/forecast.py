"""
forecast.py — Train LightGBM and write data/forecast.json.

Usage (from repo root):
    python ml/forecast.py

Reads:  archive/history_seed_synthetic.json (deprecated synthetic seed) + data/prices.json
Writes: data/forecast.json

The model predicts the *delta* of the next 22K reading (differenced target is
more stationary than the level). Confidence interval comes from two extra
LightGBM quantile-regression models (α=0.10, α=0.90).
"""

from __future__ import annotations

import hashlib
import json
import sys
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

# LightGBM prediction with a numpy array triggers a cosmetic sklearn warning
# about feature names; suppress it since we track feature order in FEATURE_COLS.
warnings.filterwarnings("ignore", message="X does not have valid feature names")

# Allow `python ml/forecast.py` from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.features import (
    ALL_FEATURE_COLS,
    FEATURE_COLS,
    build_feature_matrix,
    get_predict_row,
    get_train_Xy,
)
from ml.regime import REGIME_FEATURE_COLS

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
# Deprecated synthetic seed path — removed in PR H when legacy inference path retires.
ARCHIVE_SEED_PATH = ROOT / "archive" / "history_seed_synthetic.json"
MODEL_VERSION = "lgbm-v1"

_LGB_BASE = dict(
    n_estimators=200,
    max_depth=4,
    learning_rate=0.05,
    num_leaves=15,
    min_child_samples=5,
    subsample=0.8,
    colsample_bytree=0.8,
    random_state=42,
    verbose=-1,
)

# bd602a6 regularized params for small-data regime (num_leaves=16, lr=0.02,
# min_data_in_leaf=40, lambda_l2=1.0). n_estimators=500 approximates the
# effective budget of 2000 iters + early_stop=100 without a validation split.
_LGB_TUNED = dict(
    n_estimators=500,
    max_depth=4,
    learning_rate=0.02,
    num_leaves=16,
    min_child_samples=40,
    colsample_bytree=0.6,
    subsample=0.7,
    subsample_freq=1,
    reg_lambda=1.0,
    random_state=42,
    verbose=-1,
)


def _load_json(path: Path) -> list:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _calibrate_seed(
    seed_entries: list[dict], live_daily_entries: list[dict]
) -> tuple[list[dict], float]:
    """
    Scale seed 22k/24k/18k so the tail of the seed matches the head of live data.

    scale_factor = mean(first min(3) real readings)
                 / mean(last min(3) seed readings on or before the first real date)

    Returns (calibrated_list, scale_factor). scale_factor=1.0 when no calibration
    is applied (no live data, or insufficient overlap).
    """
    if not seed_entries or not live_daily_entries:
        return list(seed_entries) if seed_entries else [], 1.0

    seed_df = pd.DataFrame(seed_entries).copy()
    live_df = pd.DataFrame(live_daily_entries).copy()

    seed_df["ts_parsed"] = pd.to_datetime(seed_df["timestamp"], utc=True)
    live_df["ts_parsed"] = pd.to_datetime(live_df["timestamp"], utc=True)

    live_sorted = live_df.sort_values("ts_parsed")
    first_real_date = live_sorted["ts_parsed"].iloc[0].date()

    seed_before = seed_df[seed_df["ts_parsed"].dt.date <= first_real_date].tail(3)
    if seed_before.empty or "22k" not in seed_before.columns:
        return seed_df.drop(columns=["ts_parsed"]).to_dict("records"), 1.0

    n = min(3, len(live_sorted))
    seed_mean = float(seed_before["22k"].mean())
    real_mean = float(live_sorted.head(n)["22k"].mean())

    if seed_mean <= 0:
        return seed_df.drop(columns=["ts_parsed"]).to_dict("records"), 1.0

    scale_factor = real_mean / seed_mean
    print(
        f"Calibrated seed: scale_factor={scale_factor:.4f}, applied to {len(seed_df)} seed rows "
        f"(seed tail mean: Rs.{seed_mean:.0f}, live head mean: Rs.{real_mean:.0f})"
    )

    seed_df = seed_df.drop(columns=["ts_parsed"])
    for col in ("22k", "24k", "18k"):
        if col in seed_df.columns:
            seed_df[col] = (seed_df[col] * scale_factor).round().astype(int)

    return seed_df.to_dict("records"), scale_factor


def load_combined_history() -> pd.DataFrame:
    """
    Merge seed + scraped data, resampled to one reading per UTC day.

    prices.json is resampled to daily (last reading per UTC day) before
    concatenation with seed. On overlapping dates, prices.json wins.
    Seed values are calibrated to match the live data at the boundary.
    """
    seed_entries = _load_json(ARCHIVE_SEED_PATH)
    live_entries = _load_json(DATA_DIR / "prices.json")

    if not seed_entries and not live_entries:
        raise RuntimeError("No data found in history_seed.json or prices.json")

    # Resample live to daily: keep last reading per UTC day
    live_daily: list[dict] = []
    if live_entries:
        ldf = pd.DataFrame(live_entries)
        ldf["ts_parsed"] = pd.to_datetime(ldf["timestamp"], utc=True)
        ldf["utc_date"] = ldf["ts_parsed"].dt.date
        ldf = ldf.sort_values("ts_parsed").drop_duplicates(subset=["utc_date"], keep="last")
        live_daily = ldf.drop(columns=["ts_parsed", "utc_date"]).to_dict("records")

    # Calibrate seed to live boundary, then concat (live wins on overlap)
    calibrated_seed, _scale = _calibrate_seed(seed_entries or [], live_daily)
    combined = calibrated_seed + live_daily
    df = pd.DataFrame(combined)
    df["ts_parsed"] = pd.to_datetime(df["timestamp"], utc=True)
    df["utc_date"] = df["ts_parsed"].dt.date
    df = (
        df.sort_values("ts_parsed")
        .drop_duplicates(subset=["utc_date"], keep="last")
        .drop(columns=["utc_date"])
        .reset_index(drop=True)
    )
    return df


def load_all_data() -> pd.DataFrame:
    """Merge seed + scraped data, sort, deduplicate by timestamp."""
    entries = _load_json(ARCHIVE_SEED_PATH) + _load_json(DATA_DIR / "prices.json")
    if not entries:
        raise RuntimeError("No data found in history_seed.json or prices.json")
    df = pd.DataFrame(entries)
    df["ts_parsed"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("ts_parsed").drop_duplicates(subset=["ts_parsed"]).reset_index(drop=True)
    return df


def _make_lgb(objective: str, alpha: float | None = None):
    """Return a fitted-ready LGBMRegressor with the right objective."""
    import lightgbm as lgb  # lazy import so import error surfaces clearly

    params = dict(**_LGB_BASE, objective=objective)
    if alpha is not None:
        params["alpha"] = alpha
    return lgb.LGBMRegressor(**params)


def _make_lgb_tuned(objective: str, alpha: float | None = None):
    """LGBMRegressor with bd602a6 regularization params (for backtest comparison)."""
    import lightgbm as lgb

    params = dict(**_LGB_TUNED, objective=objective)
    if alpha is not None:
        params["alpha"] = alpha
    return lgb.LGBMRegressor(**params)


def train_predict(df: pd.DataFrame, macro_df=None):
    """
    Train mean + quantile LightGBM models on all available data and return
    predictions for the next reading.

    When macro_df is supplied, ALL_FEATURE_COLS (43 features) are used;
    otherwise falls back to the base FEATURE_COLS (19 features).

    Returns (predicted_delta_mean, predicted_delta_p10, predicted_delta_p90,
             current_22k, features_df, feature_cols_used)
    """
    feat_df = build_feature_matrix(df, macro_df=macro_df)

    # Choose feature set: extended when macro data is available.
    # Regime is included dynamically when add_regime_to_macro ran successfully.
    if macro_df is not None:
        feature_cols = list(ALL_FEATURE_COLS)
        if all(c in feat_df.columns for c in REGIME_FEATURE_COLS):
            feature_cols = feature_cols + REGIME_FEATURE_COLS
    else:
        feature_cols = FEATURE_COLS

    X_train, y_train = get_train_Xy(feat_df, feature_cols=feature_cols)

    # If macro features caused too many rows to be dropped, fall back to base
    if len(X_train) < 10 and macro_df is not None:
        print("  Macro features reduced training rows too much — falling back to base features")
        feature_cols = FEATURE_COLS
        X_train, y_train = get_train_Xy(feat_df, feature_cols=feature_cols)

    if len(X_train) < 10:
        raise RuntimeError(f"Too few training rows ({len(X_train)}); need ≥10")

    # Mean model
    m_mean = _make_lgb("regression")
    m_mean.fit(X_train, y_train)

    # Quantile models for the 80% confidence interval
    m_p10 = _make_lgb("quantile", alpha=0.10)
    m_p10.fit(X_train, y_train)

    m_p90 = _make_lgb("quantile", alpha=0.90)
    m_p90.fit(X_train, y_train)

    x_pred, _ = get_predict_row(feat_df, feature_cols=feature_cols)
    if x_pred is None:
        # Prediction row has NaN macro/regime features — fall back to base features
        if macro_df is not None and feature_cols != FEATURE_COLS:
            print("  Prediction row missing macro/regime features — falling back to base features")
            feature_cols = FEATURE_COLS
            X_train, y_train = get_train_Xy(feat_df, feature_cols=feature_cols)
            m_mean = _make_lgb("regression")
            m_mean.fit(X_train, y_train)
            m_p10 = _make_lgb("quantile", alpha=0.10)
            m_p10.fit(X_train, y_train)
            m_p90 = _make_lgb("quantile", alpha=0.90)
            m_p90.fit(X_train, y_train)
            x_pred, _ = get_predict_row(feat_df, feature_cols=feature_cols)
        if x_pred is None:
            raise RuntimeError("Cannot build prediction row — not enough history in the data")

    delta_mean = float(m_mean.predict(x_pred)[0])
    delta_p10 = float(m_p10.predict(x_pred)[0])
    delta_p90 = float(m_p90.predict(x_pred)[0])

    # Ensure p10 <= mean <= p90 (quantile models can sometimes cross)
    delta_p10 = min(delta_p10, delta_mean)
    delta_p90 = max(delta_p90, delta_mean)

    current_22k = float(df.iloc[-1]["22k"])
    return delta_mean, delta_p10, delta_p90, current_22k, feat_df, feature_cols


def _target_time(now: datetime | None = None) -> datetime:
    """Return tomorrow midnight UTC — guaranteed to be strictly in the future."""
    if now is None:
        now = datetime.now(UTC)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _model_hash(X: pd.DataFrame, y: pd.Series) -> str:
    payload = f"{len(X)}-{float(y.mean()):.4f}-{float(X.iloc[-1].sum()):.4f}"
    return hashlib.md5(payload.encode()).hexdigest()[:8]


def main():
    from ml.inference import main as _inference_main

    _inference_main()


if __name__ == "__main__":
    main()
