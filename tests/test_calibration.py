"""Tests for ml.calibration — no live requests, no filesystem side effects."""

from __future__ import annotations

import json
from datetime import date

import ml.calibration as cal
import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LinearRegression

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_overlap_df(
    n: int = 40,
    true_slope: float = 1.02,
    true_intercept: float = 50.0,
    noise_std: float = 10.0,
    seed: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Synthetic aligned ibja + tanishq DataFrames (n trading days)."""
    rng = np.random.default_rng(seed)
    ibja_per_g = rng.uniform(13_500, 15_000, size=n)
    tanishq_22k = true_slope * ibja_per_g + true_intercept + rng.normal(0, noise_std, size=n)

    base = pd.Timestamp("2026-01-02")
    dates = [str((base + pd.Timedelta(days=i)).date()) for i in range(n)]

    ibja_df = pd.DataFrame({"date": dates, "pm_916": ibja_per_g * 10})
    tanishq_df = pd.DataFrame({"date": dates, "22k": tanishq_22k})
    return ibja_df, tanishq_df


# ---------------------------------------------------------------------------
# fit_calibration
# ---------------------------------------------------------------------------


def test_fit_recovers_slope_and_intercept():
    ibja_df, tanishq_df = _make_overlap_df(
        n=40, true_slope=1.02, true_intercept=50.0, noise_std=5.0
    )
    params = cal.fit_calibration(ibja_df, tanishq_df)
    assert abs(params.slope - 1.02) < 0.05
    assert abs(params.intercept - 50.0) < 50.0


def test_fit_returns_correct_n_observations():
    ibja_df, tanishq_df = _make_overlap_df(n=35)
    params = cal.fit_calibration(ibja_df, tanishq_df)
    assert params.n_observations == 35


def test_fit_sets_fit_date_to_today():
    ibja_df, tanishq_df = _make_overlap_df(n=35)
    params = cal.fit_calibration(ibja_df, tanishq_df)
    assert params.fit_date == date.today().isoformat()


def test_fit_r_squared_high_on_clean_data():
    ibja_df, tanishq_df = _make_overlap_df(n=50, noise_std=5.0)
    params = cal.fit_calibration(ibja_df, tanishq_df)
    assert params.r_squared > 0.99


def test_fit_raises_on_fewer_than_30_overlap():
    ibja_df, tanishq_df = _make_overlap_df(n=29)
    with pytest.raises(ValueError, match="30 overlap days"):
        cal.fit_calibration(ibja_df, tanishq_df)


def test_fit_raises_on_exactly_29_overlap():
    ibja_df, tanishq_df = _make_overlap_df(n=29)
    with pytest.raises(ValueError):
        cal.fit_calibration(ibja_df, tanishq_df)


def test_fit_succeeds_on_exactly_30_overlap():
    ibja_df, tanishq_df = _make_overlap_df(n=30)
    params = cal.fit_calibration(ibja_df, tanishq_df)
    assert params.n_observations == 30


def test_fit_partial_date_overlap():
    """fit_calibration uses only matched dates — non-overlapping rows are ignored."""
    ibja_df, tanishq_df = _make_overlap_df(n=40)
    # Shift first 5 ibja rows to dates that don't exist in tanishq
    ibja_df.loc[:4, "date"] = [f"2020-01-0{i+1}" for i in range(5)]
    # 35 overlap days remain — should still succeed
    params = cal.fit_calibration(ibja_df, tanishq_df)
    assert params.n_observations == 35


def test_fit_huber_less_sensitive_to_outliers_than_ols():
    """HuberRegressor slope should be closer to true_slope than OLS when outliers present."""
    rng = np.random.default_rng(0)
    n = 50
    true_slope = 1.02
    true_intercept = 50.0
    ibja_per_g = rng.uniform(13_500, 15_000, size=n)
    tanishq_22k = true_slope * ibja_per_g + true_intercept + rng.normal(0, 5.0, size=n)

    # Inject 5 large outliers
    for i in range(5):
        tanishq_22k[i] += 500.0

    base = pd.Timestamp("2026-01-02")
    dates = [str((base + pd.Timedelta(days=j)).date()) for j in range(n)]
    ibja_df = pd.DataFrame({"date": dates, "pm_916": ibja_per_g * 10})
    tanishq_df = pd.DataFrame({"date": dates, "22k": tanishq_22k})

    huber_params = cal.fit_calibration(ibja_df, tanishq_df)

    # OLS reference
    X = ibja_per_g.reshape(-1, 1)
    y = tanishq_22k
    ols = LinearRegression().fit(X, y)
    ols_slope = float(ols.coef_[0])

    huber_error = abs(huber_params.slope - true_slope)
    ols_error = abs(ols_slope - true_slope)
    assert (
        huber_error < ols_error
    ), f"Huber slope error {huber_error:.4f} not less than OLS error {ols_error:.4f}"


def test_fit_stores_huber_epsilon():
    ibja_df, tanishq_df = _make_overlap_df(n=35)
    params = cal.fit_calibration(ibja_df, tanishq_df, huber_epsilon=1.5)
    assert params.huber_epsilon == 1.5


# ---------------------------------------------------------------------------
# apply_calibration
# ---------------------------------------------------------------------------


def test_apply_calibration_scalar():
    params = cal.CalibrationParams(
        slope=1.02,
        intercept=50.0,
        fit_date="2026-05-19",
        n_observations=35,
        residual_std=10.0,
        r_squared=0.998,
        huber_epsilon=1.35,
    )
    result = cal.apply_calibration(14_000.0, params)
    assert abs(result - (1.02 * 14_000.0 + 50.0)) < 1e-6


def test_apply_calibration_series():
    params = cal.CalibrationParams(
        slope=1.02,
        intercept=50.0,
        fit_date="2026-05-19",
        n_observations=35,
        residual_std=10.0,
        r_squared=0.998,
        huber_epsilon=1.35,
    )
    ibja_series = pd.Series([13_500.0, 14_000.0, 14_500.0])
    result = cal.apply_calibration(ibja_series, params)
    expected = pd.Series([1.02 * v + 50.0 for v in [13_500.0, 14_000.0, 14_500.0]])
    pd.testing.assert_series_equal(result, expected)


def test_apply_calibration_vectorized_length():
    params = cal.CalibrationParams(
        slope=1.02,
        intercept=50.0,
        fit_date="2026-05-19",
        n_observations=35,
        residual_std=10.0,
        r_squared=0.998,
        huber_epsilon=1.35,
    )
    forecast = pd.Series(range(10), dtype=float)
    result = cal.apply_calibration(forecast, params)
    assert len(result) == 10


# ---------------------------------------------------------------------------
# save / load round-trip
# ---------------------------------------------------------------------------


def test_save_load_roundtrip(tmp_path):
    params = cal.CalibrationParams(
        slope=1.023,
        intercept=42.7,
        fit_date="2026-05-19",
        n_observations=35,
        residual_std=12.5,
        r_squared=0.997,
        huber_epsilon=1.35,
    )
    p = tmp_path / "calibration.json"
    cal.save_calibration(params, p)
    loaded = cal.load_calibration(p)
    assert loaded.slope == pytest.approx(1.023)
    assert loaded.intercept == pytest.approx(42.7)
    assert loaded.fit_date == "2026-05-19"
    assert loaded.n_observations == 35
    assert loaded.residual_std == pytest.approx(12.5)
    assert loaded.r_squared == pytest.approx(0.997)
    assert loaded.huber_epsilon == pytest.approx(1.35)


def test_save_includes_valid_true(tmp_path):
    params = cal.CalibrationParams(
        slope=1.02,
        intercept=50.0,
        fit_date="2026-05-19",
        n_observations=35,
        residual_std=10.0,
        r_squared=0.998,
        huber_epsilon=1.35,
    )
    p = tmp_path / "calibration.json"
    cal.save_calibration(params, p)
    raw = json.loads(p.read_text())
    assert raw["valid"] is True
    assert raw["schema_version"] == 1


def test_load_raises_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        cal.load_calibration(tmp_path / "nonexistent.json")


# ---------------------------------------------------------------------------
# should_refit
# ---------------------------------------------------------------------------


def test_should_refit_false_zero_new_pairs():
    assert cal.should_refit(date(2026, 5, 1), 30, 30) is False


def test_should_refit_false_nine_new_pairs():
    assert cal.should_refit(date(2026, 5, 1), 39, 30) is False


def test_should_refit_true_ten_new_pairs():
    assert cal.should_refit(date(2026, 5, 1), 40, 30) is True


def test_should_refit_true_more_than_ten():
    assert cal.should_refit(date(2026, 5, 1), 50, 30) is True


def test_should_refit_boundary_exactly_ten():
    assert cal.should_refit(date(2026, 4, 1), 40, 30) is True
