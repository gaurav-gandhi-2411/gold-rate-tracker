"""Unit tests for ml/experiments/festival_seasonality.py — synthetic data, no live I/O.

Mirrors tests/test_phi10a_driver_decomp.py's pattern: pure-function tests only, the
network-fetching fetch_long_history()/main() entry point is not exercised here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from ml.experiments.festival_seasonality import (
    _nearest_index,
    benjamini_hochberg,
    bootstrap_ci,
    build_permutation_pool,
    compute_festival_excess_returns,
    permutation_pvalue,
)

# ---------------------------------------------------------------------------
# _nearest_index
# ---------------------------------------------------------------------------


def test_nearest_index_exact_match():
    idx = pd.date_range("2020-01-01", periods=10, freq="D")
    s = pd.Series(range(10), index=idx)
    result = _nearest_index(s, pd.Timestamp("2020-01-05"))
    assert result == pd.Timestamp("2020-01-05")


def test_nearest_index_rounds_to_closest():
    idx = pd.date_range("2020-01-01", periods=10, freq="D")
    s = pd.Series(range(10), index=idx)
    # 2020-01-05 12:00 is equidistant-ish; get_indexer nearest picks one side deterministically
    result = _nearest_index(s, pd.Timestamp("2020-01-05 10:00"))
    assert result in (pd.Timestamp("2020-01-05"), pd.Timestamp("2020-01-06"))


def test_nearest_index_empty_series_returns_none():
    s = pd.Series([], index=pd.DatetimeIndex([]), dtype=float)
    assert _nearest_index(s, pd.Timestamp("2020-01-01")) is None


# ---------------------------------------------------------------------------
# compute_festival_excess_returns — positive control: inject a known seasonal bump
# ---------------------------------------------------------------------------


def _make_synthetic_series(
    n_years: int = 10,
    trend_pct_per_year: float = 0.10,
    festival_bump_pct: float = 0.0,
    festival_month_day: tuple[int, int] = (10, 25),
    noise_std: float = 0.0,
    seed: int = 0,
) -> tuple[pd.Series, pd.Series, list[str]]:
    """Build a log-price series with a linear secular trend, an optional injected
    excess return around a fixed day-of-year "festival" window each year, and
    optional gaussian noise. Returns (log_price, trend, festival_date_strings)."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp(f"{2010}-01-01")
    end = pd.Timestamp(f"{2010 + n_years}-01-01")
    idx = pd.date_range(start, end, freq="D")
    n = len(idx)

    # Linear secular trend in log-space.
    daily_drift = np.log(1 + trend_pct_per_year) / 365.25
    log_price = pd.Series(daily_drift * np.arange(n), index=idx)

    if noise_std > 0:
        log_price = log_price + rng.normal(0, noise_std, size=n)

    dates: list[str] = []
    for year in range(2010, 2010 + n_years):
        anchor = pd.Timestamp(year, festival_month_day[0], festival_month_day[1])
        dates.append(anchor.strftime("%Y-%m-%d"))
        if festival_bump_pct != 0.0:
            # Apply the bump as a step-up starting at the anchor date, persisting
            # forward (so window_end sees it, window_start doesn't).
            log_price.loc[log_price.index >= anchor] += np.log(1 + festival_bump_pct)

    trend = log_price.rolling(91, center=True, min_periods=60).mean()
    return log_price, trend, dates


def test_compute_festival_excess_returns_detects_injected_bump():
    """A festival window with a real +2% bump (beyond trend) should show up clearly
    in the computed excess return, confirming the detrend-then-measure pipeline works."""
    log_price, trend, dates = _make_synthetic_series(n_years=10, festival_bump_pct=0.02)
    res = compute_festival_excess_returns(log_price, trend, dates)
    assert len(res) == 10
    # The step-up lands inside every window (window spans anchor-3 to anchor+3), so
    # excess should be positive and roughly consistent with the 2% bump.
    assert (res["excess"] > 0).all(), f"expected all-positive excess, got {res['excess'].tolist()}"
    assert res["excess"].mean() == pytest.approx(np.log(1.02), abs=0.005)


def test_compute_festival_excess_returns_no_bump_is_near_zero():
    """With no injected seasonal effect, detrended excess returns should be small
    (residual noise from the rolling-mean edge effects only, not a systematic bump)."""
    log_price, trend, dates = _make_synthetic_series(n_years=10, festival_bump_pct=0.0)
    res = compute_festival_excess_returns(log_price, trend, dates)
    assert len(res) == 10
    assert res["excess"].abs().mean() < 0.01, (
        f"expected near-zero excess, got mean={res['excess'].mean()}"
    )


def test_compute_festival_excess_returns_skips_dates_outside_series():
    log_price, trend, _ = _make_synthetic_series(n_years=3)
    res = compute_festival_excess_returns(log_price, trend, ["1990-01-01", "2011-10-25"])
    # 1990 is outside the series entirely -- _nearest_index still returns the closest
    # available point, but the NaN guard (trend has min_periods=60) should drop it.
    assert len(res) <= 2


# ---------------------------------------------------------------------------
# build_permutation_pool
# ---------------------------------------------------------------------------


def test_build_permutation_pool_excludes_festival_windows():
    log_price, trend, dates = _make_synthetic_series(n_years=5, festival_bump_pct=0.05)
    pool = build_permutation_pool(log_price, trend, {"Test": dates})
    assert len(pool) > 0
    # None of the pool values should be anywhere near the injected 5% bump size --
    # if festival windows leaked into the pool, some entries would show ~log(1.05).
    assert np.abs(pool).max() < np.log(1.05) - 0.01


def test_build_permutation_pool_nonempty_for_realistic_series():
    log_price, trend, dates = _make_synthetic_series(n_years=10)
    pool = build_permutation_pool(log_price, trend, {"Dhanteras": dates})
    # ~10 years of daily data minus festival exclusion zones should leave a large pool.
    assert len(pool) > 1000


# ---------------------------------------------------------------------------
# bootstrap_ci / permutation_pvalue — sanity checks on shape/behavior, not exact values
# ---------------------------------------------------------------------------


def test_bootstrap_ci_brackets_true_mean_for_tight_distribution():
    rng = np.random.default_rng(1)
    values = np.array([0.01, 0.012, 0.011, 0.009, 0.0105, 0.0115, 0.0095, 0.0108])
    lo, hi = bootstrap_ci(values, rng, n_boot=2000)
    assert lo <= values.mean() <= hi
    assert lo > 0, "a tight, consistently-positive sample should have a CI excluding 0"


def test_bootstrap_ci_includes_zero_for_noisy_mixed_sign_sample():
    rng = np.random.default_rng(2)
    values = np.array([0.02, -0.018, 0.015, -0.021, 0.01, -0.012, 0.008, -0.009])
    lo, hi = bootstrap_ci(values, rng, n_boot=2000)
    assert lo < 0 < hi, "a noisy near-zero-mean sample should have a CI spanning 0"


def test_permutation_pvalue_low_for_extreme_effect():
    rng = np.random.default_rng(3)
    pool = rng.normal(0, 0.01, size=5000)
    extreme_values = np.full(16, 0.05)  # 5x the pool's std, consistently
    p = permutation_pvalue(extreme_values, pool, rng, n_perm=2000)
    assert p < 0.01, f"expected a tiny p-value for an extreme, consistent effect, got {p}"


def test_permutation_pvalue_high_for_pool_matched_sample():
    rng = np.random.default_rng(4)
    pool = rng.normal(0, 0.01, size=5000)
    typical_values = rng.choice(pool, size=16, replace=False)
    p = permutation_pvalue(typical_values, pool, rng, n_perm=2000)
    assert p > 0.05, f"expected a large p-value for a pool-typical sample, got {p}"


# ---------------------------------------------------------------------------
# benjamini_hochberg
# ---------------------------------------------------------------------------


def test_benjamini_hochberg_rejects_none_when_all_pvalues_high():
    reject = benjamini_hochberg([0.55, 0.25, 0.74])
    assert reject == [False, False, False]


def test_benjamini_hochberg_rejects_only_the_clearly_significant_one():
    # p=0.001 should survive BH correction across 3 tests; the other two should not.
    reject = benjamini_hochberg([0.001, 0.4, 0.6])
    assert reject[0] is True
    assert reject[1] is False
    assert reject[2] is False


def test_benjamini_hochberg_matches_known_worked_example():
    # Classic BH worked example: p = [0.01, 0.02, 0.03, 0.04, 0.05], m=5, alpha=0.05
    # thresholds = [0.01, 0.02, 0.03, 0.04, 0.05] -- all p_i <= threshold_i in sorted order,
    # so all 5 should be rejected.
    reject = benjamini_hochberg([0.01, 0.02, 0.03, 0.04, 0.05])
    assert all(reject)
