"""
Unit tests for ml/features.py.

Three properties verified:
  1. No future leakage — lag_N at row i equals the price at row i-N
  2. Rolling stats are right-aligned — roll_7d_mean uses only data ≤ ts[i]
  3. Calendar flags fire on the correct dates (Akshaya Tritiya, Dhanteras)
     and stay silent on neutral dates
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from ml.features import FEATURE_COLS, build_feature_matrix


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def make_df(
    n: int = 60,
    start: str = "2024-01-01",
    freq_hours: int = 24,
    base_price: int = 6500,
    seed: int = 0,
) -> pd.DataFrame:
    """Synthetic price DataFrame with deterministic data."""
    rng = np.random.default_rng(seed)
    t0 = datetime.fromisoformat(start)
    timestamps = [t0 + timedelta(hours=i * freq_hours) for i in range(n)]
    prices = base_price + rng.integers(-200, 201, size=n).cumsum()
    prices = np.clip(prices, 4000, 10000).tolist()
    return pd.DataFrame(
        {
            "timestamp": [t.isoformat() for t in timestamps],
            "22k": prices,
            "24k": [int(p * 24 / 22) for p in prices],
            "18k": [int(p * 18 / 22) for p in prices],
        }
    )


# ---------------------------------------------------------------------------
# 1. No leakage: lag features must contain only past information
# ---------------------------------------------------------------------------

class TestLagNoLeakage:
    def test_lag1_equals_previous_price(self):
        df = make_df(n=40)
        feat = build_feature_matrix(df)
        # lag_1 at row i should equal df['22k'] at row i-1
        prices = df["22k"].tolist()
        for i in range(1, len(feat)):
            lag1 = feat.iloc[i]["lag_1"]
            if pd.notna(lag1):
                assert lag1 == prices[i - 1], (
                    f"Row {i}: lag_1={lag1} but price[{i-1}]={prices[i-1]}"
                )

    def test_lag4_equals_price_four_steps_back(self):
        df = make_df(n=40)
        feat = build_feature_matrix(df)
        prices = df["22k"].tolist()
        for i in range(4, len(feat)):
            lag4 = feat.iloc[i]["lag_4"]
            if pd.notna(lag4):
                assert lag4 == prices[i - 4], (
                    f"Row {i}: lag_4={lag4} but price[{i-4}]={prices[i-4]}"
                )

    def test_first_row_lags_are_nan(self):
        df = make_df(n=20)
        feat = build_feature_matrix(df)
        # Row 0 has no prior readings; all index-based lags must be NaN
        row0 = feat.iloc[0]
        for col in ["lag_1", "lag_2", "lag_3", "lag_4"]:
            assert pd.isna(row0[col]), f"Expected NaN for {col} on first row"

    def test_target_is_next_minus_current(self):
        df = make_df(n=30)
        feat = build_feature_matrix(df)
        prices = df["22k"].astype(float).tolist()
        for i in range(len(feat) - 1):
            expected_delta = prices[i + 1] - prices[i]
            actual_target = feat.iloc[i]["target"]
            assert abs(actual_target - expected_delta) < 1e-6, (
                f"Row {i}: target={actual_target} expected {expected_delta}"
            )

    def test_last_row_target_is_nan(self):
        df = make_df(n=20)
        feat = build_feature_matrix(df)
        assert pd.isna(feat.iloc[-1]["target"]), "Last row target must be NaN"


# ---------------------------------------------------------------------------
# 2. Rolling stats are right-aligned (no future information)
# ---------------------------------------------------------------------------

class TestRollingStats:
    def test_roll_7d_mean_right_aligned_daily(self):
        """
        For daily data, roll_7d_mean at row i == mean of all rows within 7 days
        of ts[i], using only rows 0..i (right-aligned).
        """
        df = make_df(n=30, freq_hours=24, seed=1)
        feat = build_feature_matrix(df)
        ts = pd.to_datetime(df["timestamp"])

        for i in range(7, len(df)):  # skip first few rows where window is partial
            # Pandas rolling("7D") uses a left-open window: (T-7D, T]
            expected_mean = df["22k"][
                (ts > ts.iloc[i] - pd.Timedelta(days=7)) & (ts <= ts.iloc[i])
            ].mean()
            actual_mean = feat.iloc[i]["roll_7d_mean"]
            assert abs(actual_mean - expected_mean) < 0.5, (
                f"Row {i}: roll_7d_mean={actual_mean:.2f} expected {expected_mean:.2f}"
            )

    def test_roll_7d_min_le_current_price(self):
        """7-day rolling min must be ≤ current price (since current is in the window)."""
        df = make_df(n=30)
        feat = build_feature_matrix(df)
        for i in range(len(feat)):
            rmin = feat.iloc[i]["roll_7d_min"]
            price = df.iloc[i]["22k"]
            if pd.notna(rmin):
                assert rmin <= price + 1e-6, f"Row {i}: min {rmin} > price {price}"

    def test_roll_7d_max_ge_current_price(self):
        """7-day rolling max must be ≥ current price."""
        df = make_df(n=30)
        feat = build_feature_matrix(df)
        for i in range(len(feat)):
            rmax = feat.iloc[i]["roll_7d_max"]
            price = df.iloc[i]["22k"]
            if pd.notna(rmax):
                assert rmax >= price - 1e-6, f"Row {i}: max {rmax} < price {price}"

    def test_roll_7d_std_non_negative(self):
        """Std must always be ≥ 0."""
        df = make_df(n=30)
        feat = build_feature_matrix(df)
        stds = feat["roll_7d_std"].dropna()
        assert (stds >= 0).all(), "Negative rolling std found"


# ---------------------------------------------------------------------------
# 3. Calendar features fire on the correct dates
# ---------------------------------------------------------------------------

class TestCalendarFeatures:
    def _make_window(self, center_date: str, n_days: int = 10) -> pd.DataFrame:
        t0 = datetime.fromisoformat(center_date)
        timestamps = [t0 + timedelta(days=i) for i in range(n_days)]
        price = 7000
        return pd.DataFrame(
            {
                "timestamp": [t.isoformat() for t in timestamps],
                "22k": [price] * n_days,
                "24k": [int(price * 24 / 22)] * n_days,
                "18k": [int(price * 18 / 22)] * n_days,
            }
        )

    def test_akshaya_tritiya_2024_fires(self):
        """Akshaya Tritiya 2024 is May 10. The ±3-day window is May 7–13."""
        # Start May 6 so window [May 7..May 13] is fully covered
        df = self._make_window("2024-05-06", n_days=10)
        feat = build_feature_matrix(df)
        flags = feat["akshaya_tritiya"].tolist()
        # Index 1 = May 7 (in window), index 7 = May 13 (in window)
        assert flags[1] == 1, "May 7 should be flagged (Akshaya Tritiya ±3 of May 10)"
        assert flags[7] == 1, "May 13 should be flagged (Akshaya Tritiya ±3 of May 10)"
        # Index 0 = May 6 (outside window)
        assert flags[0] == 0, "May 6 should NOT be flagged"

    def test_dhanteras_2024_fires(self):
        """Dhanteras 2024 is Oct 29. The ±3-day window is Oct 26–Nov 1."""
        df = self._make_window("2024-10-25", n_days=10)
        feat = build_feature_matrix(df)
        flags = feat["dhanteras"].tolist()
        # Index 1 = Oct 26 (in window), index 7 = Nov 1 (in window)
        assert flags[1] == 1, "Oct 26 should be flagged (Dhanteras ±3 of Oct 29)"
        assert flags[7] == 1, "Nov 1 should be flagged (Dhanteras ±3 of Oct 29)"
        assert flags[0] == 0, "Oct 25 should NOT be flagged"

    def test_no_festival_on_neutral_date(self):
        """A quiet period in March 2024 should have no festival flags."""
        df = self._make_window("2024-03-10", n_days=7)
        feat = build_feature_matrix(df)
        assert (feat["akshaya_tritiya"] == 0).all(), "Unexpected Akshaya Tritiya flag"
        assert (feat["dhanteras"] == 0).all(), "Unexpected Dhanteras flag"

    def test_dhanteras_2023_fires(self):
        """Dhanteras 2023 is Nov 10. Verify a different year fires correctly."""
        df = self._make_window("2023-11-08", n_days=6)
        feat = build_feature_matrix(df)
        flags = feat["dhanteras"].tolist()
        # Index 0 = Nov 8 (in window ±3 around Nov 10), index 4 = Nov 12 (still in window)
        assert flags[0] == 1, "Nov 8 2023 should be in Dhanteras window"


# ---------------------------------------------------------------------------
# 4. get_train_Xy and get_predict_row
# ---------------------------------------------------------------------------

class TestGetTrainXy:
    def test_no_nan_in_train_features(self):
        from ml.features import get_train_Xy
        df = make_df(n=50)
        feat = build_feature_matrix(df)
        X, y = get_train_Xy(feat)
        assert not X.isnull().any().any(), "NaN in training features"
        assert not y.isnull().any(), "NaN in training target"

    def test_train_rows_less_than_total(self):
        from ml.features import get_train_Xy
        df = make_df(n=50)
        feat = build_feature_matrix(df)
        X, y = get_train_Xy(feat)
        # First few rows and last row are dropped
        assert len(X) < len(feat)
        assert len(X) == len(y)

    def test_predict_row_returns_correct_shape(self):
        from ml.features import get_predict_row
        df = make_df(n=60)  # enough history for all lags
        feat = build_feature_matrix(df)
        x_arr, x_row = get_predict_row(feat)
        if x_arr is not None:
            assert x_arr.shape == (1, len(FEATURE_COLS))
