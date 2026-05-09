"""
features.py — Pure feature engineering for the gold rate forecaster.

Input: pandas DataFrame with columns ['timestamp', '22k', '24k', '18k']
Output: feature matrix and target vector (next-reading delta on 22k)

No I/O in this module — all functions are pure transforms.

Macro integration
-----------------
Pass a macro DataFrame (from ml.macro.load_macro_features) to
build_feature_matrix() to add 24 additional features.  The base 19
FEATURE_COLS are unchanged so existing code and tests keep working.
Use FEATURE_COLS + MACRO_FEATURE_COLS (or ALL_FEATURE_COLS) when macro
data is available.
"""

from __future__ import annotations

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

FEATURE_COLS: list[str] = [
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

# Macro-economic features added when a macro DataFrame is provided.
# 6 raw spot levels (forward-filled daily) + 4 derived + 14 lags = 24 features.
MACRO_FEATURE_COLS: list[str] = [
    # Spot levels
    "usd_inr",
    "gold_usd",
    "us_10y_yield",
    "dxy",
    "sensex",
    "vix_level",
    # Derived rates-of-change / volatility
    "usd_inr_change_1d",
    "gold_usd_change_1d",
    "gold_usd_5d_vol",
    "sensex_5d_return",
    # USD/INR 1–7 day lags
    "usd_inr_lag_1",
    "usd_inr_lag_2",
    "usd_inr_lag_3",
    "usd_inr_lag_4",
    "usd_inr_lag_5",
    "usd_inr_lag_6",
    "usd_inr_lag_7",
    # Gold-USD 1–7 day lags
    "gold_usd_lag_1",
    "gold_usd_lag_2",
    "gold_usd_lag_3",
    "gold_usd_lag_4",
    "gold_usd_lag_5",
    "gold_usd_lag_6",
    "gold_usd_lag_7",
]

# Convenience alias: full feature set when macro data is available.
ALL_FEATURE_COLS: list[str] = FEATURE_COLS + MACRO_FEATURE_COLS


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


def add_macro_features(feat_df: pd.DataFrame, macro_df: pd.DataFrame) -> pd.DataFrame:
    """
    Join macro features onto a feature matrix by UTC calendar date.

    Computes 1–7 day lags of usd_inr and gold_usd directly from the
    (already forward-filled) daily macro DataFrame before joining, so every
    gold-price row — including intraday 6-hourly readings — inherits the
    macro snapshot for that calendar day.

    Parameters
    ----------
    feat_df : pd.DataFrame
        Output of build_feature_matrix (must have a 'ts' column).
    macro_df : pd.DataFrame
        Output of ml.macro.load_macro_features — UTC DatetimeIndex, daily.

    Returns a new DataFrame with MACRO_FEATURE_COLS appended.
    """
    # Compute lag columns on the daily macro series (before joining)
    macro = macro_df.copy()
    for lag in range(1, 8):
        macro[f"usd_inr_lag_{lag}"] = macro["usd_inr"].shift(lag)
        macro[f"gold_usd_lag_{lag}"] = macro["gold_usd"].shift(lag)

    # Normalise macro index to date-only (strip time, keep UTC)
    macro_dates = macro.index.normalize()
    macro = macro.copy()
    macro.index = macro_dates.tz_localize(None)  # drop tz for merge key

    # Extract date from feature matrix timestamps for the join key
    feat = feat_df.copy()
    feat["_join_date"] = pd.to_datetime(feat["ts"], utc=True).dt.normalize().dt.tz_localize(None)

    # Reset macro index to a column, then merge on the date key
    macro_reset = macro.reset_index().rename(columns={"index": "_join_date"})
    merged = feat.merge(macro_reset, on="_join_date", how="left")
    merged = merged.drop(columns=["_join_date"])
    return merged


def build_feature_matrix(
    df: pd.DataFrame,
    macro_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Compute features for every row in df.

    Returns a DataFrame with FEATURE_COLS columns + 'target' (NaN on the last
    row, where no next reading is known yet). When macro_df is provided, also
    adds MACRO_FEATURE_COLS via a left-join on UTC calendar date.
    Does NOT drop NaN rows — callers decide whether to keep or drop.

    Parameters
    ----------
    df : pd.DataFrame
        Price history with columns ['timestamp', '22k', '24k', '18k'].
    macro_df : pd.DataFrame, optional
        Output of ml.macro.load_macro_features().  When provided, MACRO_FEATURE_COLS
        are appended (otherwise those columns are absent from the result).
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
    df["dow"] = df["ts"].dt.dayofweek  # 0=Mon … 6=Sun
    df["hour"] = df["ts"].dt.hour
    df["dom"] = df["ts"].dt.day
    df["month"] = df["ts"].dt.month

    # --- Festival proximity flags (±3 days around Akshaya Tritiya / Dhanteras) ---
    dates = df["ts"].dt.date
    df["akshaya_tritiya"] = dates.apply(
        lambda d: int(_is_festival_window(d, _AKSHAYA_TRITIYA))
    ).astype(int)
    df["dhanteras"] = dates.apply(lambda d: int(_is_festival_window(d, _DHANTERAS))).astype(int)

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

    # --- Optional macro join ---
    if macro_df is not None:
        df = add_macro_features(df, macro_df)

    return df


def get_train_Xy(
    features_df: pd.DataFrame,
    feature_cols: list[str] | None = None,
):
    """
    Return (X, y) for supervised training: rows where all feature columns AND
    the target are non-NaN.

    Parameters
    ----------
    features_df : pd.DataFrame
        Output of build_feature_matrix().
    feature_cols : list[str], optional
        Which columns to use as features.  Defaults to FEATURE_COLS (19 base
        features).  Pass FEATURE_COLS + MACRO_FEATURE_COLS (or ALL_FEATURE_COLS)
        when macro data was provided to build_feature_matrix().
    """
    cols = feature_cols if feature_cols is not None else FEATURE_COLS
    mask = features_df[cols].notna().all(axis=1) & features_df["target"].notna()
    return (
        features_df.loc[mask, cols].copy(),
        features_df.loc[mask, "target"].copy(),
    )


def get_predict_row(
    features_df: pd.DataFrame,
    feature_cols: list[str] | None = None,
):
    """
    Return the last row's feature vector for inference.

    Returns (array shape (1, n_features), Series) or (None, None) if any
    required feature is missing (not enough history to build all lags).

    Parameters
    ----------
    feature_cols : list[str], optional
        Defaults to FEATURE_COLS.  Pass ALL_FEATURE_COLS when macro data
        was provided to build_feature_matrix().
    """
    cols = feature_cols if feature_cols is not None else FEATURE_COLS
    row = features_df.iloc[-1][cols]
    if row.isna().any():
        return None, None
    return row.values.reshape(1, -1), row
