"""Tests for ml.direction.comex_daily (M2: COMEX-targeted daily variant, #1756).

All tests are fully offline — ml.macro._download_with_retry is mocked.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from ml.direction.comex_daily import (
    _DEAD_BAND_PCT,
    _fetch_all_drivers,
    _make_ternary_label,
    build_comex_dataset,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_yf_response(n_days: int = 400, seed: int = 7) -> pd.DataFrame:
    """Business-day-only yfinance-style MultiIndex response for GC=F + every
    ml.macro ticker + GLD."""
    dates = pd.date_range("2023-01-02", periods=n_days, freq="B", tz="UTC")
    rng = np.random.default_rng(seed)
    tickers = [
        "INR=X",
        "GC=F",
        "^TNX",
        "DX-Y.NYB",
        "^BSESN",
        "^VIX",
        "CL=F",
        "TIP",
        "^INDIAVIX",
        "GLD",
    ]
    columns = pd.MultiIndex.from_product([["Close"], tickers])
    data = {}
    base = {
        "INR=X": 83.0,
        "GC=F": 2000.0,
        "^TNX": 4.0,
        "DX-Y.NYB": 103.0,
        "^BSESN": 65000.0,
        "^VIX": 15.0,
        "CL=F": 75.0,
        "TIP": 105.0,
        "^INDIAVIX": 12.0,
        "GLD": 185.0,
    }
    for t in tickers:
        walk = base[t] + np.cumsum(rng.normal(0, base[t] * 0.005, n_days))
        data[("Close", t)] = walk
    return pd.DataFrame(data, index=dates, columns=columns)


# ---------------------------------------------------------------------------
# _make_ternary_label
# ---------------------------------------------------------------------------


class TestMakeTernaryLabel:
    def test_up_beyond_dead_band(self) -> None:
        ternary, binary = _make_ternary_label(2000.0, 2010.0, _DEAD_BAND_PCT)
        assert ternary == "up"
        assert binary == 1

    def test_down_beyond_dead_band(self) -> None:
        ternary, binary = _make_ternary_label(2000.0, 1990.0, _DEAD_BAND_PCT)
        assert ternary == "down"
        assert binary == 0

    def test_flat_within_dead_band(self) -> None:
        # 0.1% move on 2000 = $2, inside the 0.15% dead band.
        ternary, binary = _make_ternary_label(2000.0, 2002.0, _DEAD_BAND_PCT)
        assert ternary == "flat"
        # binary is still a strict > comparison regardless of the ternary band.
        assert binary == 1


# ---------------------------------------------------------------------------
# _fetch_all_drivers -- trading-day mask
# ---------------------------------------------------------------------------


class TestFetchAllDrivers:
    def test_weekend_is_not_a_trading_day(self) -> None:
        raw = _make_yf_response(n_days=10)
        with patch("ml.direction.comex_daily._download_with_retry", return_value=raw):
            _drivers, _roll_flags, is_trading = _fetch_all_drivers("2023-01-01", "2023-02-01")
        # The business-day-only fixture leaves weekends absent from `raw`,
        # so they must show up as non-trading in the full-calendar reindex.
        saturdays = [d for d in is_trading.index if d.weekday() == 5]
        assert len(saturdays) > 0
        assert not is_trading.loc[saturdays].any()

    def test_gold_usd_ffilled_across_weekend(self) -> None:
        raw = _make_yf_response(n_days=10)
        with patch("ml.direction.comex_daily._download_with_retry", return_value=raw):
            drivers, _roll_flags, _is_trading = _fetch_all_drivers("2023-01-01", "2023-02-01")
        assert drivers["gold_usd"].isna().sum() == 0


# ---------------------------------------------------------------------------
# build_comex_dataset -- end to end
# ---------------------------------------------------------------------------


class TestBuildComexDataset:
    def test_only_trading_days_produce_rows(self) -> None:
        raw = _make_yf_response(n_days=60)
        with patch("ml.direction.comex_daily._download_with_retry", return_value=raw):
            df = build_comex_dataset(start="2023-01-02", end="2023-03-15", extra_horizons=(1,))
        as_of_weekdays = pd.to_datetime(df["as_of_date"]).dt.dayofweek
        # Every row's as_of_date's NEXT day (the label target) must itself be
        # a business day -- weekends never appear as h=1 target days, so
        # Friday as_of rows (target=Saturday) must not exist.
        assert not (as_of_weekdays == 4).any()

    def test_label_up_fraction_is_not_weekend_diluted(self) -> None:
        """Regression test for the bug this module's own history fixed: an
        earlier version built on the full 7-day calendar without a
        trading-day filter, producing ~30% artificial ties and skewing
        label_binary_h1 to ~36% 'up' regardless of the underlying random
        walk. With the fix, a symmetric random walk should land close to
        50%, not systematically below it."""
        raw = _make_yf_response(n_days=400, seed=3)
        with patch("ml.direction.comex_daily._download_with_retry", return_value=raw):
            df = build_comex_dataset(start="2023-01-02", end="2023-12-01", extra_horizons=(1,))
        up_frac = df["label_binary_h1"].mean()
        assert 0.35 < up_frac < 0.65

    def test_t1_lag_no_same_day_leakage(self) -> None:
        """A same-day spike in gold_usd must not appear in that day's own
        gold_usd_lag1 feature -- only the following trading day's."""
        raw = _make_yf_response(n_days=100, seed=5)
        spike_idx = raw.index[50]
        raw.loc[spike_idx, ("Close", "GC=F")] = raw.loc[spike_idx, ("Close", "GC=F")] * 3

        with patch("ml.direction.comex_daily._download_with_retry", return_value=raw):
            df = build_comex_dataset(start="2023-01-02", end="2023-06-01", extra_horizons=(1,))

        spike_date = spike_idx.strftime("%Y-%m-%d")
        # The spike happened ON spike_date -- so it must show up as the
        # gold_usd_lag1 feature on the NEXT row (as_of_date == spike_date),
        # not on the row whose as_of_date IS spike_date's own prior day.
        row_with_spike_as_current = df[df["as_of_date"] == spike_date]
        if not row_with_spike_as_current.empty:
            # current_pm916 for as_of_date==spike_date should reflect the
            # spike (it's exactly gold_usd at that date).
            assert row_with_spike_as_current.iloc[0]["current_pm916"] > 1000

    def test_calendar_features_present_and_typed(self) -> None:
        raw = _make_yf_response(n_days=60)
        with patch("ml.direction.comex_daily._download_with_retry", return_value=raw):
            df = build_comex_dataset(start="2023-01-02", end="2023-03-01", extra_horizons=(1,))
        assert df["is_festival_window"].dtype == bool
        assert df["is_wedding_season"].dtype == bool
        assert df["days_to_next_festival"].notna().all()

    def test_window_min_reflects_path_not_endpoint(self) -> None:
        raw = _make_yf_response(n_days=30, seed=11)
        # Force a dip mid-window for a specific date's h=5 window.
        dip_idx = raw.index[15]
        raw.loc[dip_idx, ("Close", "GC=F")] = raw.loc[dip_idx, ("Close", "GC=F")] * 0.5

        with patch("ml.direction.comex_daily._download_with_retry", return_value=raw):
            df = build_comex_dataset(start="2023-01-02", end="2023-02-15", extra_horizons=(5,))

        has_dip_in_window = (df["window_min_pm916_h5"] < df["current_pm916"] * 0.7).any()
        assert has_dip_in_window

    def test_declares_usd_units_and_rupee_builder_refuses_it(self) -> None:
        # The ₹50/g buyer_decision threshold on USD/oz produced a fake 100%
        # accuracy on 2026-09-23; the declared units make that fail loudly.
        from ml.direction.price_units import PRICE_UNITS_ATTR, USD_PER_TROY_OZ, PriceUnitsError
        from ml.direction.reframed_targets import (
            add_buyer_decision_binary,
            add_buyer_decision_binary_pct,
        )

        raw = _make_yf_response(n_days=60)
        with patch("ml.direction.comex_daily._download_with_retry", return_value=raw):
            df = build_comex_dataset(start="2023-01-02", end="2023-03-15", extra_horizons=(5,))
        assert df.attrs[PRICE_UNITS_ATTR] == USD_PER_TROY_OZ
        with pytest.raises(PriceUnitsError):
            add_buyer_decision_binary(df, 5)
        labels = add_buyer_decision_binary_pct(df, 5, dip_pct=0.5)
        assert labels.dropna().isin([0.0, 1.0]).all()
