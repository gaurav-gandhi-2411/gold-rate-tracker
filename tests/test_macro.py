"""
Unit tests for ml/macro.py and the macro integration in ml/features.py.

All tests are fully offline — yfinance.download is mocked via unittest.mock.
Network calls must never happen in the test suite.

Test groups:
  1. TestFetchMacroFeatures — schema, forward-fill, derived features, parquet cache
  2. TestLoadMacroFeatures  — load hit/miss, timezone normalisation
  3. TestForwardFill        — weekend / holiday gaps are filled correctly
  4. TestDerivedFeatures    — pct-change and volatility features are in range
  5. TestMacroIntegration   — build_feature_matrix(df, macro_df) adds expected columns
  6. TestGetTrainXyWithMacro — get_train_Xy with ALL_FEATURE_COLS works end-to-end
"""

from __future__ import annotations

from unittest.mock import patch

import ml.macro as macro_mod
import numpy as np
import pandas as pd
import pytest
from ml.features import (
    ALL_FEATURE_COLS,
    FEATURE_COLS,
    MACRO_FEATURE_COLS,
    build_feature_matrix,
    get_predict_row,
    get_train_Xy,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_START = "2026-03-01"
_MOCK_END = "2026-05-09"


def _make_yf_response(start: str = _MOCK_START, end: str = _MOCK_END) -> pd.DataFrame:
    """
    Build a minimal yfinance-style DataFrame matching the MultiIndex column
    format returned by yf.download(multiple tickers, auto_adjust=True).
    """
    dates = pd.date_range(start, end, freq="B", tz="UTC")  # business days only
    rng = np.random.default_rng(42)

    tickers = ["INR=X", "GC=F", "^TNX", "DX-Y.NYB", "^BSESN", "^VIX", "^INDIAVIX"]
    columns = pd.MultiIndex.from_product([["Close"], tickers])

    data = {
        ("Close", "INR=X"): rng.uniform(83.0, 85.0, len(dates)),
        ("Close", "GC=F"): rng.uniform(2300.0, 2400.0, len(dates)),
        ("Close", "^TNX"): rng.uniform(4.0, 5.0, len(dates)),
        ("Close", "DX-Y.NYB"): rng.uniform(100.0, 106.0, len(dates)),
        ("Close", "^BSESN"): rng.uniform(73000.0, 77000.0, len(dates)),
        ("Close", "^VIX"): rng.uniform(12.0, 25.0, len(dates)),
        ("Close", "^INDIAVIX"): rng.uniform(10.0, 30.0, len(dates)),
    }
    return pd.DataFrame(data, index=dates, columns=columns)


def _make_gold_df(n: int = 60, start: str = "2026-03-01") -> pd.DataFrame:
    """Small synthetic gold price DataFrame for feature integration tests."""
    rng = np.random.default_rng(99)
    t0 = pd.Timestamp(start, tz="UTC")
    timestamps = [t0 + pd.Timedelta(days=i) for i in range(n)]
    prices = 14000 + rng.integers(-300, 301, size=n).cumsum()
    prices = np.clip(prices, 9000, 20000)
    return pd.DataFrame(
        {
            "timestamp": [t.isoformat() for t in timestamps],
            "22k": prices.tolist(),
            "24k": (prices * 24 / 22).round().astype(int).tolist(),
            "18k": (prices * 18 / 22).round().astype(int).tolist(),
        }
    )


# ---------------------------------------------------------------------------
# 1. TestFetchMacroFeatures
# ---------------------------------------------------------------------------


class TestFetchMacroFeatures:
    @patch("ml.macro.yf.download")
    def test_returns_dataframe(self, mock_dl, tmp_path):
        mock_dl.return_value = _make_yf_response()
        df = macro_mod.fetch_macro_features(
            _MOCK_START, _MOCK_END, cache_path=tmp_path / "c.parquet"
        )
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0

    @patch("ml.macro.yf.download")
    def test_schema_has_all_required_columns(self, mock_dl, tmp_path):
        """Every MACRO_FEATURE_COLS base column (excluding lags) must be present."""
        mock_dl.return_value = _make_yf_response()
        df = macro_mod.fetch_macro_features(
            _MOCK_START, _MOCK_END, cache_path=tmp_path / "c.parquet"
        )
        expected = [
            "usd_inr",
            "gold_usd",
            "us_10y_yield",
            "dxy",
            "sensex",
            "vix_level",
            "india_vix_level",
            "usd_inr_change_1d",
            "gold_usd_change_1d",
            "gold_usd_5d_vol",
            "sensex_5d_return",
        ]
        missing = [c for c in expected if c not in df.columns]
        assert not missing, f"Missing columns: {missing}"

    @patch("ml.macro.yf.download")
    def test_caches_to_parquet(self, mock_dl, tmp_path):
        mock_dl.return_value = _make_yf_response()
        cache = tmp_path / "macro.parquet"
        macro_mod.fetch_macro_features(_MOCK_START, _MOCK_END, cache_path=cache)
        assert cache.exists(), "Parquet cache file was not written"

    @patch("ml.macro.yf.download")
    def test_index_is_utc_datetime(self, mock_dl, tmp_path):
        mock_dl.return_value = _make_yf_response()
        df = macro_mod.fetch_macro_features(
            _MOCK_START, _MOCK_END, cache_path=tmp_path / "c.parquet"
        )
        assert isinstance(df.index, pd.DatetimeIndex)
        assert df.index.tz is not None, "Index must be timezone-aware (UTC)"

    @patch("ml.macro.yf.download")
    def test_new_data_wins_over_cache_on_overlap(self, mock_dl, tmp_path):
        """When fetch overlaps with existing cache, the new value should win."""
        cache = tmp_path / "macro.parquet"
        # First fetch — writes cache
        mock_dl.return_value = _make_yf_response()
        macro_mod.fetch_macro_features(_MOCK_START, _MOCK_END, cache_path=cache)

        # Second fetch with different values on the same dates
        raw2 = _make_yf_response()
        raw2[("Close", "INR=X")] = 99.0  # obviously different value
        mock_dl.return_value = raw2
        df2 = macro_mod.fetch_macro_features(_MOCK_START, _MOCK_END, cache_path=cache)

        # Last business day should have the new value, not the cached one
        usd_inr_vals = df2["usd_inr"].dropna()
        assert (usd_inr_vals == 99.0).any(), "New data did not overwrite cache on overlap"

    @patch("ml.macro.yf.download")
    def test_empty_response_raises(self, mock_dl, tmp_path):
        mock_dl.return_value = pd.DataFrame()
        with pytest.raises(RuntimeError, match="empty DataFrame"):
            macro_mod.fetch_macro_features(
                _MOCK_START, _MOCK_END, cache_path=tmp_path / "c.parquet"
            )


# ---------------------------------------------------------------------------
# 2. TestLoadMacroFeatures
# ---------------------------------------------------------------------------


class TestLoadMacroFeatures:
    @patch("ml.macro.yf.download")
    def test_load_returns_dataframe(self, mock_dl, tmp_path):
        mock_dl.return_value = _make_yf_response()
        cache = tmp_path / "macro.parquet"
        macro_mod.fetch_macro_features(_MOCK_START, _MOCK_END, cache_path=cache)

        loaded = macro_mod.load_macro_features(cache_path=cache)
        assert isinstance(loaded, pd.DataFrame)
        assert len(loaded) > 0

    def test_load_returns_none_if_missing(self, tmp_path):
        result = macro_mod.load_macro_features(cache_path=tmp_path / "nonexistent.parquet")
        assert result is None

    @patch("ml.macro.yf.download")
    def test_loaded_index_is_utc(self, mock_dl, tmp_path):
        mock_dl.return_value = _make_yf_response()
        cache = tmp_path / "macro.parquet"
        macro_mod.fetch_macro_features(_MOCK_START, _MOCK_END, cache_path=cache)
        loaded = macro_mod.load_macro_features(cache_path=cache)
        assert loaded.index.tz is not None, "Loaded index should be UTC-aware"


# ---------------------------------------------------------------------------
# 3. TestForwardFill — weekend gaps must be filled
# ---------------------------------------------------------------------------


class TestForwardFill:
    def _make_sparse_response(self) -> pd.DataFrame:
        """Response with only Monday/Wednesday/Friday rows (simulates gaps)."""
        # Use a full calendar index but set Tue/Thu/Sat/Sun to NaN
        dates = pd.date_range(_MOCK_START, _MOCK_END, freq="D", tz="UTC")
        tickers = ["INR=X", "GC=F", "^TNX", "DX-Y.NYB", "^BSESN", "^VIX"]
        columns = pd.MultiIndex.from_product([["Close"], tickers])
        rng = np.random.default_rng(7)
        arr = np.where(
            np.isin(dates.dayofweek, [0, 2, 4]),  # Mon, Wed, Fri
            rng.uniform(83, 85, len(dates)),
            np.nan,
        )
        data = {col: arr for col in columns}
        return pd.DataFrame(data, index=dates, columns=columns)

    @patch("ml.macro.yf.download")
    def test_no_nan_in_usd_inr_after_ffill(self, mock_dl, tmp_path):
        """After forward-fill, usd_inr should have no NaN (sparse input → filled)."""
        mock_dl.return_value = self._make_sparse_response()
        df = macro_mod.fetch_macro_features(
            _MOCK_START, _MOCK_END, cache_path=tmp_path / "c.parquet"
        )
        nan_count = df["usd_inr"].isna().sum()
        # First row may be NaN if the series started on a non-trading day; accept ≤1
        assert nan_count <= 1, f"Too many NaN after ffill: {nan_count}"

    @patch("ml.macro.yf.download")
    def test_ffill_propagates_to_weekend(self, mock_dl, tmp_path):
        """A Saturday row should carry the Friday close value (no NaN)."""
        mock_dl.return_value = self._make_sparse_response()
        df = macro_mod.fetch_macro_features(
            _MOCK_START, _MOCK_END, cache_path=tmp_path / "c.parquet"
        )
        saturdays = df[df.index.dayofweek == 5]
        if len(saturdays) > 0:
            assert saturdays["usd_inr"].notna().all(), "Saturday rows should be forward-filled"


# ---------------------------------------------------------------------------
# 4. TestDerivedFeatures — sanity check on derived columns
# ---------------------------------------------------------------------------


class TestDerivedFeatures:
    @patch("ml.macro.yf.download")
    def test_daily_usd_inr_change_small(self, mock_dl, tmp_path):
        """Daily % change in USD/INR should be < 5% in normal conditions."""
        mock_dl.return_value = _make_yf_response()
        df = macro_mod.fetch_macro_features(
            _MOCK_START, _MOCK_END, cache_path=tmp_path / "c.parquet"
        )
        changes = df["usd_inr_change_1d"].dropna().abs()
        assert (changes < 0.05).all(), f"Unexpectedly large USD/INR daily move: {changes.max():.4f}"

    @patch("ml.macro.yf.download")
    def test_gold_5d_vol_non_negative(self, mock_dl, tmp_path):
        mock_dl.return_value = _make_yf_response()
        df = macro_mod.fetch_macro_features(
            _MOCK_START, _MOCK_END, cache_path=tmp_path / "c.parquet"
        )
        vol = df["gold_usd_5d_vol"].dropna()
        assert (vol >= 0).all(), "Volatility must be non-negative"

    @patch("ml.macro.yf.download")
    def test_vix_level_same_as_vix(self, mock_dl, tmp_path):
        mock_dl.return_value = _make_yf_response()
        df = macro_mod.fetch_macro_features(
            _MOCK_START, _MOCK_END, cache_path=tmp_path / "c.parquet"
        )
        pd.testing.assert_series_equal(
            df["vix"], df["vix_level"], check_names=False, obj="vix_level must equal vix"
        )


# ---------------------------------------------------------------------------
# 5. TestMacroIntegration — build_feature_matrix(df, macro_df=macro_df)
# ---------------------------------------------------------------------------


class TestMacroIntegration:
    @patch("ml.macro.yf.download")
    def test_macro_columns_present_in_feature_matrix(self, mock_dl, tmp_path):
        """When macro_df is passed, MACRO_FEATURE_COLS appear in the feature matrix."""
        mock_dl.return_value = _make_yf_response()
        macro_df = macro_mod.fetch_macro_features(
            _MOCK_START, _MOCK_END, cache_path=tmp_path / "c.parquet"
        )

        gold_df = _make_gold_df()
        feat = build_feature_matrix(gold_df, macro_df=macro_df)

        # Check a representative set of macro columns
        for col in ["usd_inr", "gold_usd", "vix_level", "usd_inr_lag_3", "gold_usd_lag_7"]:
            assert col in feat.columns, f"Missing macro column after join: {col}"

    @patch("ml.macro.yf.download")
    def test_base_feature_matrix_unchanged_without_macro(self, mock_dl, tmp_path):
        """build_feature_matrix without macro_df must NOT add any macro columns."""
        gold_df = _make_gold_df()
        feat_base = build_feature_matrix(gold_df)
        for col in MACRO_FEATURE_COLS:
            assert col not in feat_base.columns, f"Unexpected macro column in base matrix: {col}"

    @patch("ml.macro.yf.download")
    def test_usd_inr_lag1_equals_yesterday(self, mock_dl, tmp_path):
        """
        usd_inr_lag_1 for a given date should equal the usd_inr value from
        the previous calendar day.
        """
        mock_dl.return_value = _make_yf_response()
        macro_df = macro_mod.fetch_macro_features(
            _MOCK_START, _MOCK_END, cache_path=tmp_path / "c.parquet"
        )

        gold_df = _make_gold_df(n=50, start="2026-03-10")
        feat = build_feature_matrix(gold_df, macro_df=macro_df)

        # For each row that has both usd_inr and usd_inr_lag_1 populated,
        # lag_1 should equal macro_df["usd_inr"] from one day before.
        for _, row in feat.dropna(subset=["usd_inr", "usd_inr_lag_1"]).iterrows():
            row_date = pd.to_datetime(row["ts"], utc=True).normalize()
            prev_date = row_date - pd.Timedelta(days=1)
            # Find closest macro row at or before prev_date
            macro_at_prev = macro_df[macro_df.index.normalize() <= prev_date]
            if macro_at_prev.empty:
                continue
            expected_lag1 = float(macro_at_prev["usd_inr"].iloc[-1])
            actual_lag1 = float(row["usd_inr_lag_1"])
            assert (
                abs(actual_lag1 - expected_lag1) < 1e-6
            ), f"usd_inr_lag_1 mismatch: got {actual_lag1}, expected {expected_lag1}"
            break  # one verified row is sufficient for this test

    @patch("ml.macro.yf.download")
    def test_all_feature_cols_present_in_feature_matrix(self, mock_dl, tmp_path):
        """ALL_FEATURE_COLS must all be present when macro_df is provided."""
        mock_dl.return_value = _make_yf_response()
        macro_df = macro_mod.fetch_macro_features(
            _MOCK_START, _MOCK_END, cache_path=tmp_path / "c.parquet"
        )

        gold_df = _make_gold_df(n=70, start="2026-03-05")
        feat = build_feature_matrix(gold_df, macro_df=macro_df)

        missing = [c for c in ALL_FEATURE_COLS if c not in feat.columns]
        assert not missing, f"Columns missing from feature matrix: {missing}"


# ---------------------------------------------------------------------------
# 6. TestGetTrainXyWithMacro — end-to-end training with macro features
# ---------------------------------------------------------------------------


class TestGetTrainXyWithMacro:
    @patch("ml.macro.yf.download")
    def test_training_rows_returned_with_macro(self, mock_dl, tmp_path):
        """get_train_Xy with ALL_FEATURE_COLS should return non-empty training set."""
        mock_dl.return_value = _make_yf_response()
        macro_df = macro_mod.fetch_macro_features(
            _MOCK_START, _MOCK_END, cache_path=tmp_path / "c.parquet"
        )

        gold_df = _make_gold_df(n=70, start="2026-03-10")
        feat = build_feature_matrix(gold_df, macro_df=macro_df)
        X, y = get_train_Xy(feat, feature_cols=ALL_FEATURE_COLS)

        assert len(X) > 0, "No training rows returned with macro features"
        assert X.shape[1] == len(ALL_FEATURE_COLS)
        assert not X.isnull().any().any(), "NaN in training features with macro"

    @patch("ml.macro.yf.download")
    def test_predict_row_shape_with_macro(self, mock_dl, tmp_path):
        """get_predict_row with ALL_FEATURE_COLS returns correct shape."""
        mock_dl.return_value = _make_yf_response()
        macro_df = macro_mod.fetch_macro_features(
            _MOCK_START, _MOCK_END, cache_path=tmp_path / "c.parquet"
        )

        gold_df = _make_gold_df(n=70, start="2026-03-10")
        feat = build_feature_matrix(gold_df, macro_df=macro_df)
        x_arr, _ = get_predict_row(feat, feature_cols=ALL_FEATURE_COLS)

        if x_arr is not None:
            assert x_arr.shape == (1, len(ALL_FEATURE_COLS))

    def test_base_feature_cols_still_work_unchanged(self):
        """Backward compat: get_train_Xy() with default args must still work."""
        gold_df = _make_gold_df(n=60, start="2026-03-01")
        feat = build_feature_matrix(gold_df)  # no macro
        X, y = get_train_Xy(feat)  # no feature_cols arg
        assert X.shape[1] == len(FEATURE_COLS), "Base FEATURE_COLS count changed"
        assert not X.isnull().any().any()
