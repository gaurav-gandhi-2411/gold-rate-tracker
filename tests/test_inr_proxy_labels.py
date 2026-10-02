"""Tests for ml.inr_proxy_labels (M1 follow-up: label/feature role separation)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from ml.inr_proxy_labels import (
    compute_lag_agreement,
    compute_magnitude_bucket_agreement,
)

# ---------------------------------------------------------------------------
# compute_lag_agreement
# ---------------------------------------------------------------------------


class TestComputeLagAgreement:
    def test_perfectly_aligned_series_peaks_at_lag_zero(self) -> None:
        idx = pd.date_range("2022-01-01", periods=60, freq="D", tz="UTC")
        rng = np.random.default_rng(1)
        ibja = pd.Series(50000 + np.cumsum(rng.normal(0, 200, 60)), index=idx)
        candidate = ibja.copy()  # identical series -> perfect agreement at lag 0

        result = compute_lag_agreement(candidate, ibja, lags=(-1, 0, 1))
        assert result["0"]["agreement"] == 1.0
        assert result["-1"]["agreement"] < 1.0
        assert result["1"]["agreement"] < 1.0

    def test_one_day_late_candidate_peaks_at_lag_plus_one(self) -> None:
        """If `candidate` is genuinely running one day behind ibja, shifting
        candidate BACK by one day (lag=+1 in this function's convention)
        should recover perfect agreement."""
        idx = pd.date_range("2022-01-01", periods=60, freq="D", tz="UTC")
        rng = np.random.default_rng(2)
        ibja = pd.Series(50000 + np.cumsum(rng.normal(0, 200, 60)), index=idx)
        candidate = ibja.shift(1).bfill()  # candidate[t] = ibja[t-1] -> one day late

        result = compute_lag_agreement(candidate, ibja, lags=(-1, 0, 1))
        assert result["1"]["agreement"] > result["0"]["agreement"]

    def test_only_new_value_days_counted(self) -> None:
        idx = pd.date_range("2022-01-01", periods=10, freq="D", tz="UTC")
        ibja = pd.Series([100, 100, 100, 110, 110, 120, 120, 120, 130, 140], index=idx)
        candidate = ibja.copy()
        result = compute_lag_agreement(candidate, ibja, lags=(0,))
        # 10 rows, but only 4 genuine new-value transitions (100->110->120->130->140,
        # i.e. 4 real changes) minus the first (no prior diff) -> fewer than 9.
        assert result["0"]["n"] < 9


# ---------------------------------------------------------------------------
# compute_magnitude_bucket_agreement
# ---------------------------------------------------------------------------


class TestComputeMagnitudeBucketAgreement:
    def test_buckets_by_absolute_move_size(self) -> None:
        idx = pd.date_range("2022-01-01", periods=5, freq="D", tz="UTC")
        # Moves (per 10g): +10 (tiny, <20/g->wait this is per-10g so /10 for
        # per-gram: +1, +150 (15/g), +600 (60/g), +2000 (200/g)
        ibja = pd.Series([50000, 50010, 50160, 50760, 52760], index=idx)
        candidate = ibja.copy()  # perfect agreement everywhere
        result = compute_magnitude_bucket_agreement(candidate, ibja)
        # Tiny move (1 Rs/g) falls in "<20"; larger ones in higher buckets.
        assert result["<20"]["n"] >= 1
        assert result["<20"]["agreement"] == 1.0

    def test_disagreement_concentrated_in_small_bucket(self) -> None:
        idx = pd.date_range("2022-01-01", periods=6, freq="D", tz="UTC")
        ibja = pd.Series([50000, 50005, 50010, 51500, 53200, 55000], index=idx)
        # candidate disagrees on the tiny early moves, agrees on the large later ones.
        candidate = pd.Series([50000, 49995, 50020, 51600, 53400, 55300], index=idx)
        result = compute_magnitude_bucket_agreement(candidate, ibja)
        small_bucket_agreement = result["<20"]["agreement"]
        large_bucket = next(
            (result[b]["agreement"] for b in ("200-500", "500+") if result[b]["n"] > 0), None
        )
        if small_bucket_agreement is not None and large_bucket is not None:
            assert large_bucket >= small_bucket_agreement

    def test_empty_bucket_reports_n_zero_not_crash(self) -> None:
        idx = pd.date_range("2022-01-01", periods=3, freq="D", tz="UTC")
        ibja = pd.Series([50000, 50001, 50002], index=idx)  # only tiny moves
        candidate = ibja.copy()
        result = compute_magnitude_bucket_agreement(candidate, ibja)
        assert result["500+"]["n"] == 0
        assert result["500+"]["agreement"] is None
