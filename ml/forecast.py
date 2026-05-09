"""
forecast.py — Train LightGBM and write data/forecast.json.

Usage (from repo root):
    python ml/forecast.py

Reads:  data/history_seed.json (if present) + data/prices.json
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# LightGBM prediction with a numpy array triggers a cosmetic sklearn warning
# about feature names; suppress it since we track feature order in FEATURE_COLS.
warnings.filterwarnings("ignore", message="X does not have valid feature names")

# Allow `python ml/forecast.py` from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.features import FEATURE_COLS, build_feature_matrix, get_predict_row, get_train_Xy

DATA_DIR = Path(__file__).parent.parent / "data"
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


def _load_json(path: Path) -> list:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _calibrate_seed(seed_entries: list[dict], live_daily_entries: list[dict]) -> list[dict]:
    """
    Scale seed 22k/24k/18k so the tail of the seed matches the head of live data.

    scale_factor = mean(first min(3) real readings)
                 / mean(last min(3) seed readings on or before the first real date)

    Returns a new list of dicts — does not modify inputs.
    """
    if not seed_entries or not live_daily_entries:
        return list(seed_entries) if seed_entries else []

    seed_df = pd.DataFrame(seed_entries).copy()
    live_df = pd.DataFrame(live_daily_entries).copy()

    seed_df["ts_parsed"] = pd.to_datetime(seed_df["timestamp"], utc=True)
    live_df["ts_parsed"] = pd.to_datetime(live_df["timestamp"], utc=True)

    live_sorted = live_df.sort_values("ts_parsed")
    first_real_date = live_sorted["ts_parsed"].iloc[0].date()

    seed_before = seed_df[seed_df["ts_parsed"].dt.date <= first_real_date].tail(3)
    if seed_before.empty or "22k" not in seed_before.columns:
        return seed_df.drop(columns=["ts_parsed"]).to_dict("records")

    n = min(3, len(live_sorted))
    seed_mean = float(seed_before["22k"].mean())
    real_mean = float(live_sorted.head(n)["22k"].mean())

    if seed_mean <= 0:
        return seed_df.drop(columns=["ts_parsed"]).to_dict("records")

    scale_factor = real_mean / seed_mean
    print(
        f"Calibrated seed: scale_factor={scale_factor:.4f}, applied to {len(seed_df)} seed rows "
        f"(seed tail mean: Rs.{seed_mean:.0f}, live head mean: Rs.{real_mean:.0f})"
    )

    seed_df = seed_df.drop(columns=["ts_parsed"])
    for col in ("22k", "24k", "18k"):
        if col in seed_df.columns:
            seed_df[col] = (seed_df[col] * scale_factor).round().astype(int)

    return seed_df.to_dict("records")


def load_combined_history() -> pd.DataFrame:
    """
    Merge seed + scraped data, resampled to one reading per UTC day.

    prices.json is resampled to daily (last reading per UTC day) before
    concatenation with seed. On overlapping dates, prices.json wins.
    Seed values are calibrated to match the live data at the boundary.
    """
    seed_entries = _load_json(DATA_DIR / "history_seed.json")
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
    calibrated_seed = _calibrate_seed(seed_entries or [], live_daily)
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
    entries = _load_json(DATA_DIR / "history_seed.json") + _load_json(DATA_DIR / "prices.json")
    if not entries:
        raise RuntimeError("No data found in history_seed.json or prices.json")
    df = pd.DataFrame(entries)
    df["ts_parsed"] = pd.to_datetime(df["timestamp"], utc=True)
    df = (
        df.sort_values("ts_parsed")
        .drop_duplicates(subset=["ts_parsed"])
        .reset_index(drop=True)
    )
    return df


def _make_lgb(objective: str, alpha: float | None = None):
    """Return a fitted-ready LGBMRegressor with the right objective."""
    import lightgbm as lgb  # lazy import so import error surfaces clearly

    params = dict(**_LGB_BASE, objective=objective)
    if alpha is not None:
        params["alpha"] = alpha
    return lgb.LGBMRegressor(**params)


def train_predict(df: pd.DataFrame):
    """
    Train mean + quantile models on all available data, then return predictions
    for the next reading.

    Returns (predicted_delta_mean, predicted_delta_p10, predicted_delta_p90,
             current_22k, features_df)
    """
    feat_df = build_feature_matrix(df)
    X_train, y_train = get_train_Xy(feat_df)

    if len(X_train) < 10:
        raise RuntimeError(f"Too few training rows ({len(X_train)}); need ≥10")

    # Mean model
    m_mean = _make_lgb("regression")
    m_mean.fit(X_train, y_train)

    # Quantile models for the confidence interval
    m_p10 = _make_lgb("quantile", alpha=0.10)
    m_p10.fit(X_train, y_train)

    m_p90 = _make_lgb("quantile", alpha=0.90)
    m_p90.fit(X_train, y_train)

    x_pred, _ = get_predict_row(feat_df)
    if x_pred is None:
        raise RuntimeError("Cannot build prediction row — not enough history in the data")

    delta_mean = float(m_mean.predict(x_pred)[0])
    delta_p10 = float(m_p10.predict(x_pred)[0])
    delta_p90 = float(m_p90.predict(x_pred)[0])

    # Ensure p10 <= mean <= p90 (quantile models can sometimes invert)
    delta_p10 = min(delta_p10, delta_mean)
    delta_p90 = max(delta_p90, delta_mean)

    current_22k = float(df.iloc[-1]["22k"])
    return delta_mean, delta_p10, delta_p90, current_22k, feat_df


def _target_time(now: datetime | None = None) -> datetime:
    """Return tomorrow midnight UTC — guaranteed to be strictly in the future."""
    if now is None:
        now = datetime.now(timezone.utc)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _model_hash(X: pd.DataFrame, y: pd.Series) -> str:
    payload = f"{len(X)}-{float(y.mean()):.4f}-{float(X.iloc[-1].sum()):.4f}"
    return hashlib.md5(payload.encode()).hexdigest()[:8]


def main():
    df = load_combined_history()
    delta_mean, delta_p10, delta_p90, current_22k, feat_df = train_predict(df)

    predicted_22k = round(current_22k + delta_mean)
    lower = round(current_22k + delta_p10)
    upper = round(current_22k + delta_p90)

    X_train, y_train = get_train_Xy(feat_df)
    version = f"{MODEL_VERSION}-{_model_hash(X_train, y_train)}"

    predicted_at = datetime.now(timezone.utc)
    target_time = _target_time(predicted_at)
    assert target_time > predicted_at, "target_time must be in the future"

    real_readings_count = len(_load_json(DATA_DIR / "prices.json"))

    result = {
        "predicted_at": predicted_at.isoformat(),
        "target_time": target_time.isoformat(),
        "predicted_22k": predicted_22k,
        "lower": lower,
        "upper": upper,
        "model_version": version,
        "training_rows": len(X_train),
        "real_readings_count": real_readings_count,
        "warmup": real_readings_count < 56,
    }

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "forecast.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"Forecast written: 22K=Rs.{predicted_22k} [Rs.{lower}-Rs.{upper}] (trained on {len(X_train)} rows)")


if __name__ == "__main__":
    main()
