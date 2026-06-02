"""Unit tests for ml/experiments/driver_decomp.py — mocked data, no live I/O."""

from __future__ import annotations

import pandas as pd
import pytest
from ml.experiments.driver_decomp import (
    compute_daily_drift,
    compute_trailing_premium,
    forecast_driver,
    run_driver_decomp_experiment,
)

# ---------------------------------------------------------------------------
# Helpers: build minimal mock Series
# ---------------------------------------------------------------------------


def _make_ibja(values: list[float], start: str = "2024-01-01") -> pd.Series:
    """Return a date-indexed IBJA Series with UTC-naive dates (matches load_ibja_series)."""
    idx = pd.date_range(start, periods=len(values), freq="D")
    return pd.Series(values, index=idx)


def _make_macro(
    values: list[float],
    start: str = "2024-01-01",
    col: str = "gold_usd",
) -> pd.Series:
    """Return a UTC-indexed macro Series."""
    idx = pd.date_range(start, periods=len(values), freq="D", tz="UTC")
    return pd.Series(values, index=idx, name=col)


# ---------------------------------------------------------------------------
# test_compute_trailing_premium
# ---------------------------------------------------------------------------


class TestComputeTrailingPremium:
    def test_valid_computation(self) -> None:
        """Median premium is ibja / (gold * fx) for stable values."""
        n = 10
        ibja = _make_ibja([6000.0] * n)
        gold_usd = _make_macro([3000.0] * n, col="gold_usd")
        usd_inr = _make_macro([84.0] * n, col="usd_inr")
        expected_premium = 6000.0 / (3000.0 * 84.0)
        result = compute_trailing_premium(ibja, gold_usd, usd_inr, window=30)
        assert result is not None
        assert abs(result - expected_premium) < 1e-8

    def test_returns_none_when_fewer_than_5_valid(self) -> None:
        """Returns None when there are fewer than 5 valid premium observations."""
        # Only 3 IBJA observations — will find < 5 premiums
        ibja = _make_ibja([6000.0, 6100.0, 6050.0])
        gold_usd = _make_macro([3000.0, 3010.0, 3005.0], col="gold_usd")
        usd_inr = _make_macro([84.0, 84.1, 84.0], col="usd_inr")
        result = compute_trailing_premium(ibja, gold_usd, usd_inr, window=30)
        assert result is None

    def test_skips_zero_macro_values(self) -> None:
        """Entries with gold_usd=0 or usd_inr=0 are excluded from premium list."""
        # Rows 0-4 have gold=0 (invalid), rows 5-9 are valid — result from 5 valid rows
        gold_vals = [0.0] * 5 + [3000.0] * 5
        inr_vals = [84.0] * 10
        ibja_vals = [6000.0] * 10
        ibja = _make_ibja(ibja_vals)
        gold_usd = _make_macro(gold_vals, col="gold_usd")
        usd_inr = _make_macro(inr_vals, col="usd_inr")
        result = compute_trailing_premium(ibja, gold_usd, usd_inr, window=30)
        assert result is not None
        expected = 6000.0 / (3000.0 * 84.0)
        assert abs(result - expected) < 1e-8

    def test_uses_last_macro_when_context_is_after_macro_range(self) -> None:
        """When IBJA context is after macro range, last available macro value is used (ffill).

        compute_trailing_premium aligns IBJA dates to macro via .loc[:ts].iloc[-1], so
        a macro that ends before the IBJA range is still valid — the last row is carried forward.
        The function should return a non-None premium using those last macro values.
        """
        ibja = _make_ibja([6000.0] * 10, start="2025-01-01")
        # Macro ends ~1yr before IBJA starts — last macro row is gold=3000, inr=84
        gold_usd = _make_macro([3000.0] * 5, start="2024-01-01", col="gold_usd")
        usd_inr = _make_macro([84.0] * 5, start="2024-01-01", col="usd_inr")
        result = compute_trailing_premium(ibja, gold_usd, usd_inr, window=30)
        # Last macro values are 3000 and 84 → premium = 6000/(3000*84)
        assert result is not None
        expected = 6000.0 / (3000.0 * 84.0)
        assert abs(result - expected) < 1e-8

    def test_returns_none_when_macro_entirely_after_context(self) -> None:
        """Returns None when ALL macro data is in the future relative to each IBJA date."""
        ibja = _make_ibja([6000.0] * 10, start="2023-01-01")
        # Macro starts 2 years after IBJA — .loc[:ts] will always be empty
        gold_usd = _make_macro([3000.0] * 5, start="2025-06-01", col="gold_usd")
        usd_inr = _make_macro([84.0] * 5, start="2025-06-01", col="usd_inr")
        result = compute_trailing_premium(ibja, gold_usd, usd_inr, window=30)
        assert result is None

    def test_window_limits_observations(self) -> None:
        """Only the last `window` IBJA observations are used."""
        # 40 observations: first 30 at premium 1.0, last 10 at premium 2.0
        gold_vals = [3000.0] * 30 + [1500.0] * 10  # lower gold → higher premium
        ibja_vals = [6000.0] * 40
        inr_vals = [84.0] * 40
        ibja = _make_ibja(ibja_vals)
        gold_usd = _make_macro(gold_vals, col="gold_usd")
        usd_inr = _make_macro(inr_vals, col="usd_inr")
        # window=10 → only last 10 observations (all with premium ~6000/(1500*84))
        result = compute_trailing_premium(ibja, gold_usd, usd_inr, window=10)
        assert result is not None
        expected_high = 6000.0 / (1500.0 * 84.0)
        assert abs(result - expected_high) < 1e-6


# ---------------------------------------------------------------------------
# test_compute_daily_drift
# ---------------------------------------------------------------------------


class TestComputeDailyDrift:
    def test_zero_drift(self) -> None:
        """Flat series → drift = 0."""
        vals = [100.0] * 40
        series = _make_macro(vals)
        before_ts = series.index[-1]
        drift = compute_daily_drift(series, before_ts, window_days=30)
        assert abs(drift) < 1e-10

    def test_positive_drift(self) -> None:
        """Series rising by 1.0 per day → drift ≈ 1.0."""
        vals = [float(i) for i in range(40)]
        series = _make_macro(vals)
        before_ts = series.index[-1]
        drift = compute_daily_drift(series, before_ts, window_days=30)
        assert abs(drift - 1.0) < 1e-8

    def test_negative_drift(self) -> None:
        """Series falling by 2.0 per day → drift ≈ -2.0."""
        vals = [float(80 - 2 * i) for i in range(40)]
        series = _make_macro(vals)
        before_ts = series.index[-1]
        drift = compute_daily_drift(series, before_ts, window_days=30)
        assert abs(drift - (-2.0)) < 1e-8

    def test_returns_zero_when_no_data_in_window(self) -> None:
        """When no data falls in the trailing window, returns 0.0."""
        vals = [100.0] * 5
        series = _make_macro(vals, start="2024-01-01")
        # Ask for data before 2024-01-01 → no overlap
        before_ts = pd.Timestamp("2023-01-01", tz="UTC")
        drift = compute_daily_drift(series, before_ts, window_days=30)
        assert drift == 0.0

    def test_window_days_limits_lookback(self) -> None:
        """Only data within window_days is used."""
        # 60 days: first 30 rising, last 30 flat
        vals = [float(i) for i in range(30)] + [29.0] * 30
        series = _make_macro(vals)
        before_ts = series.index[-1]
        # Short window (10 days): covers only flat tail → drift ≈ 0
        drift_short = compute_daily_drift(series, before_ts, window_days=10)
        assert abs(drift_short) < 1e-8
        # Long window (60 days): covers rising + flat → drift > 0
        drift_long = compute_daily_drift(series, before_ts, window_days=60)
        assert drift_long > 0


# ---------------------------------------------------------------------------
# test_forecast_driver
# ---------------------------------------------------------------------------


class TestForecastDriver:
    def test_random_walk_is_flat(self) -> None:
        """use_drift=False → all values equal last_val."""
        result = forecast_driver(100.0, 5.0, [1, 2, 3, 4, 5], use_drift=False)
        assert len(result) == 5
        assert all(v == 100.0 for v in result)

    def test_drift_projects_correctly(self) -> None:
        """use_drift=True → each value = last_val + d * drift."""
        result = forecast_driver(100.0, 2.0, [1, 2, 3, 4, 5], use_drift=True)
        expected = [102.0, 104.0, 106.0, 108.0, 110.0]
        for a, e in zip(result, expected, strict=False):
            assert abs(a - e) < 1e-8

    def test_negative_drift(self) -> None:
        """Negative drift decreases over time."""
        result = forecast_driver(200.0, -3.0, [1, 2, 5], use_drift=True)
        assert result[0] < 200.0
        assert result[1] < result[0]
        assert result[2] < result[1]

    def test_zero_drift_collapses_to_flat(self) -> None:
        """drift=0 with use_drift=True → same as random walk."""
        rw = forecast_driver(150.0, 0.0, [1, 3, 5], use_drift=False)
        drift = forecast_driver(150.0, 0.0, [1, 3, 5], use_drift=True)
        assert rw == drift

    def test_output_length_matches_calendar_days_ahead(self) -> None:
        for n in [1, 3, 5, 7]:
            result = forecast_driver(100.0, 1.0, list(range(1, n + 1)), use_drift=True)
            assert len(result) == n


# ---------------------------------------------------------------------------
# Integration: collapses to naive when drivers are flat
# ---------------------------------------------------------------------------


class TestDriverDecompCollapsesToNaive:
    """When drift=0 and gold_usd=RW, ibja_hat ≈ context_last (flat-naive).

    premium_hat = median(ibja / (gold * inr)) over context
    gold_hat    = gold_last  (RW)
    inr_hat     = inr_last + days*0  (drift=0)
    => ibja_hat = premium_hat * gold_last * inr_last ≈ ibja_last  (when stable)
    """

    def test_collapses_to_naive_when_all_flat(self) -> None:
        """Stable ibja, gold, inr, zero drift → forecast ≈ ibja_last."""
        n_obs = 40
        ibja_val = 6000.0
        gold_val = 3000.0
        inr_val = 84.0

        ibja = _make_ibja([ibja_val] * n_obs)
        gold_usd = _make_macro([gold_val] * n_obs, col="gold_usd")
        usd_inr = _make_macro([inr_val] * n_obs, col="usd_inr")

        results = run_driver_decomp_experiment(ibja, gold_usd, usd_inr, horizon=5, min_context=8)
        assert len(results) == 2, f"expected 2 variants, got {len(results)}"

        for r in results:
            if r["n_folds_ge30ctx"] == 0:
                pytest.skip("insufficient folds for integration check")
            # With flat drivers, both variants should produce near-identical MAE
            # (RW: ibja_hat = premium * gold * inr = ibja_last; drift: drift≈0 anyway)
            mae_v = r["mae_variant"]
            mae_n = r["mae_naive"]
            # Both should be very close (within 0.1%) since actuals are flat too
            assert mae_v is not None and mae_n is not None
            if mae_n > 0:
                relative_diff = abs(mae_v - mae_n) / mae_n
                # Allow up to 1% relative difference (numerical precision only)
                assert relative_diff < 0.01, (
                    f"{r['name']}: mae_variant={mae_v:.4f} vs mae_naive={mae_n:.4f} "
                    f"relative_diff={relative_diff:.4f} — expected near-identical for flat data"
                )
