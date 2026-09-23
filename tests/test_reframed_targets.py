"""Tests for ml.direction.reframed_targets (M2 reframed label constructions)."""

from __future__ import annotations

import pandas as pd
from ml.direction.price_units import INR_PER_10G, declare_units
from ml.direction.reframed_targets import (
    add_buyer_decision_binary,
    add_deadzone_binary,
    add_detrended_binary,
)

# ---------------------------------------------------------------------------
# add_deadzone_binary
# ---------------------------------------------------------------------------


class TestDeadzoneBinary:
    def test_up_down_flat_mapping(self) -> None:
        df = pd.DataFrame({"label_ternary_h1": ["up", "down", "flat", "up"]})
        result = add_deadzone_binary(df, 1)
        assert result.iloc[0] == 1.0
        assert result.iloc[1] == 0.0
        assert pd.isna(result.iloc[2])
        assert result.iloc[3] == 1.0


# ---------------------------------------------------------------------------
# add_detrended_binary
# ---------------------------------------------------------------------------


class TestDetrendedBinary:
    def test_excess_return_above_trend_is_up(self) -> None:
        # Rows 0-4: flat delta=0 trend-setters, matured well before row 5.
        as_of = [f"2025-01-{d:02d}" for d in range(1, 7)]
        label_date = [f"2025-01-{d:02d}" for d in range(2, 8)]
        deltas = [0.0, 0.0, 0.0, 0.0, 0.0, 500.0]  # row 5 spikes well above trend
        df = pd.DataFrame(
            {"as_of_date": as_of, "label_date_h1": label_date, "delta_per_gram_h1": deltas}
        )
        result = add_detrended_binary(df, 1, trend_window=5, min_trend_obs=3)
        assert result.iloc[5] == 1.0  # 500 - trend(~0) > 0

    def test_nan_before_enough_matured_observations(self) -> None:
        as_of = ["2025-01-01", "2025-01-02"]
        label_date = ["2025-01-02", "2025-01-03"]
        deltas = [10.0, 20.0]
        df = pd.DataFrame(
            {"as_of_date": as_of, "label_date_h1": label_date, "delta_per_gram_h1": deltas}
        )
        result = add_detrended_binary(df, 1, trend_window=5, min_trend_obs=3)
        assert pd.isna(result.iloc[0])
        assert pd.isna(result.iloc[1])  # only 1 matured prior obs, needs >= 3

    def test_unmatured_prior_row_excluded_from_trend(self) -> None:
        """A prior row whose label hasn't matured by this row's as_of_date
        must NOT contribute to the trend -- this is the leakage control."""
        # Row 0: as_of=01-01, label matures 01-10 (still not matured by row 4's date).
        # Rows 1-3: as_of 01-02..01-04, labels mature 01-03..01-04, all strictly
        # before row 4's as_of_date (01-05) -> all 3 are matured/eligible.
        as_of = ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04", "2025-01-05"]
        label_date = ["2025-01-10", "2025-01-03", "2025-01-04", "2025-01-04", "2025-01-06"]
        deltas = [10000.0, 0.0, 0.0, 0.0, 5.0]  # row 0's huge delta must not pollute the trend
        df = pd.DataFrame(
            {"as_of_date": as_of, "label_date_h1": label_date, "delta_per_gram_h1": deltas}
        )
        result = add_detrended_binary(df, 1, trend_window=5, min_trend_obs=3)
        # If row 0 leaked in, trend would be huge and row 4 (delta=5) would read as "down"
        # (5 - huge_trend < 0). With row 0 correctly excluded, trend ~= 0 and 5 > 0 -> up.
        assert result.iloc[4] == 1.0


# ---------------------------------------------------------------------------
# add_buyer_decision_binary
# ---------------------------------------------------------------------------


class TestBuyerDecisionBinary:
    def test_dip_beyond_dead_band_is_one(self) -> None:
        df = declare_units(
            pd.DataFrame({"current_pm916": [70000.0], "window_min_pm916_h5": [68000.0]}),
            INR_PER_10G,
        )
        # dip = (70000-68000)/10 = 200 Rs/gram > 50 default dead band
        result = add_buyer_decision_binary(df, 5, dead_band_per_gram=50.0)
        assert result.iloc[0] == 1.0

    def test_small_dip_within_dead_band_is_zero(self) -> None:
        df = declare_units(
            pd.DataFrame({"current_pm916": [70000.0], "window_min_pm916_h5": [69800.0]}),
            INR_PER_10G,
        )
        # dip = (70000-69800)/10 = 20 Rs/gram < 50
        result = add_buyer_decision_binary(df, 5, dead_band_per_gram=50.0)
        assert result.iloc[0] == 0.0

    def test_price_only_rose_is_zero(self) -> None:
        df = declare_units(
            pd.DataFrame({"current_pm916": [70000.0], "window_min_pm916_h5": [70500.0]}),
            INR_PER_10G,
        )
        result = add_buyer_decision_binary(df, 5, dead_band_per_gram=50.0)
        assert result.iloc[0] == 0.0

    def test_nan_window_min_propagates_as_nan(self) -> None:
        df = declare_units(
            pd.DataFrame({"current_pm916": [70000.0], "window_min_pm916_h5": [None]}), INR_PER_10G
        )
        result = add_buyer_decision_binary(df, 5, dead_band_per_gram=50.0)
        assert pd.isna(result.iloc[0])
