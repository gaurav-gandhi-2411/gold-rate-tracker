"""
features.py — Pure feature engineering for the gold rate forecaster.

Input: pandas DataFrame with columns ['timestamp', '22k', '24k', '18k']
Output: feature matrix and target vector (next-reading delta on 22k)

No I/O in this module — all functions are pure transforms.
"""

from datetime import date

import numpy as np
import pandas as pd

# Festival dates with high Indian gold demand (Wikipedia-sourced, approximate)
_AKSHAYA_TRITIYA = [
    date(2022, 5, 3),
    date(2023, 4, 22),
    date(2024, 5, 10),
    date(2025, 4, 30),
    date(2026, 5, 19),
]

_DHANTERAS = [
    date(2022, 10, 22),
    date(2023, 11, 10),
    date(2024, 10, 29),
    date(2025, 10, 20),
    date(2026, 11, 7),
]

FEATURE_COLS = [
    "lag_1",
    "lag_2",
    "lag_3",
    "lag_4",
    "lag_7d",
    "lag_30d",
    "roll_7d_mean",
    "roll_7d_std",
    "roll_7d_min",
    "roll_7d_max",
    "dow",
    "hour",
    "dom",
    "month",
    "akshaya_tritiya",
    "dhanteras",
    "since_last_drop",
    "hours_since_prev",
    "prev_delta",
]


def _is_festival_window(d: date, festival_dates: list, window: int = 3) -> bool:
    return any(abs((d - fd).days) <= window for fd in festival_dates)


def _time_based_lag(ts_arr: np.ndarray, price_arr: np.ndarray, days: int) -> np.ndarray:
    """
    For each ts[i], return the price at the most recent reading <= ts[i] - `days`.
    Uses searchsorted for O(n log n) vectorised lookup — no future info can leak
    because the target timestamp is always strictly before ts[i].
    """
    delta = np.timedelta64(days, "D")
    target_ns = ts_arr - delta
    # searchsorted side='right': insertion point AFTER any equal value, so -1 gives
    # the last index whose ts <= target, i.e. the most recent reading before the lag.
    indices = np.searchsorted(ts_arr, target_ns, side="right") - 1
    result = np.where(indices >= 0, price_arr[np.maximum(indices, 0)], np.nan)
    return result.astype(float)


def build_feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute features for every row in df.

    Returns a DataFrame with FEATURE_COLS + 'target' (NaN on the last row,
    where no next reading is known yet). Does NOT drop NaN rows — callers
    decide whether to keep or drop incomplete rows.
    """
    df = df.copy()
    df["ts"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("ts").reset_index(drop=True)
    price = df["22k"].astype(float)

    # --- Index-based lags (previous N readings) ---
    for lag in [1, 2, 3, 4]:
        df[f"lag_{lag}"] = price.shift(lag)

    # Change from the immediately preceding reading
    df["prev_delta"] = price.diff()

    # Hours elapsed since the previous reading (helps model distinguish 6-hourly
    # scraped data from daily seed data)
    df["hours_since_prev"] = df["ts"].diff().dt.total_seconds() / 3600

    # --- Time-based lags (nearest reading ~7d / ~30d ago) ---
    ts_ns = df["ts"].values.astype("datetime64[ns]")
    df["lag_7d"] = _time_based_lag(ts_ns, price.values, 7)
    df["lag_30d"] = _time_based_lag(ts_ns, price.values, 30)

    # --- Rolling stats over the previous 7 calendar days ---
    # Use DatetimeIndex for the rolling call so the window is time-based.
    df_idx = df.set_index("ts")
    r7 = df_idx["22k"].astype(float).rolling("7D", min_periods=1)
    df["roll_7d_mean"] = r7.mean().values
    # std is NaN for single-element windows; fill with 0 (observed volatility = 0)
    df["roll_7d_std"] = r7.std(ddof=1).fillna(0.0).values
    df["roll_7d_min"] = r7.min().values
    df["roll_7d_max"] = r7.max().values

    # --- Calendar features ---
    df["dow"] = df["ts"].dt.dayofweek    # 0=Mon … 6=Sun
    df["hour"] = df["ts"].dt.hour
    df["dom"] = df["ts"].dt.day
    df["month"] = df["ts"].dt.month

    # --- Festival proximity flags (±3 days around Akshaya Tritiya / Dhanteras) ---
    dates = df["ts"].dt.date
    df["akshaya_tritiya"] = dates.apply(
        lambda d: int(_is_festival_window(d, _AKSHAYA_TRITIYA))
    ).astype(int)
    df["dhanteras"] = dates.apply(
        lambda d: int(_is_festival_window(d, _DHANTERAS))
    ).astype(int)

    # --- Readings since last drop of ≥₹100 ---
    drop_flag = (df["prev_delta"] <= -100).fillna(False)
    counter = 0
    since_drop = []
    for flag in drop_flag:
        if flag:
            counter = 0
        else:
            counter += 1
        since_drop.append(counter)
    df["since_last_drop"] = since_drop

    # --- Target: next-reading delta (NaN on the final row) ---
    df["target"] = price.shift(-1) - price

    return df


def get_train_Xy(features_df: pd.DataFrame):
    """
    Return (X, y) for supervised training: rows where all features AND
    the target are non-NaN.
    """
    mask = features_df[FEATURE_COLS].notna().all(axis=1) & features_df["target"].notna()
    return (
        features_df.loc[mask, FEATURE_COLS].copy(),
        features_df.loc[mask, "target"].copy(),
    )


def get_predict_row(features_df: pd.DataFrame):
    """
    Return the last row's feature vector for inference.
    Returns (array shape (1, n_features), Series) or (None, None) if any
    feature is missing (not enough history to build all lags).
    """
    row = features_df.iloc[-1][FEATURE_COLS]
    if row.isna().any():
        return None, None
    return row.values.reshape(1, -1), row
