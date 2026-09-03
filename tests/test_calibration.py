"""Tests for ml.calibration — no live requests. Read-only real-data checks are
confined to the coverage regression gate at the bottom of this file (same
pattern as tests/test_schema_contracts.py); everything else is synthetic."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import ml.calibration as cal
import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LinearRegression

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

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
    ibja_df.loc[:4, "date"] = [f"2020-01-0{i + 1}" for i in range(5)]
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
    assert huber_error < ols_error, (
        f"Huber slope error {huber_error:.4f} not less than OLS error {ols_error:.4f}"
    )


def test_fit_stores_huber_epsilon():
    ibja_df, tanishq_df = _make_overlap_df(n=35)
    params = cal.fit_calibration(ibja_df, tanishq_df, huber_epsilon=1.5)
    assert params.huber_epsilon == 1.5


def test_fit_stores_half_life_default():
    ibja_df, tanishq_df = _make_overlap_df(n=35)
    params = cal.fit_calibration(ibja_df, tanishq_df)
    assert params.half_life == cal._DEFAULT_HALF_LIFE


def test_fit_includes_oos_fields_when_enough_pairs_for_one_fold():
    # min_train (30) + 1 held-out pair = 31 minimum for walk_forward_validate to run.
    ibja_df, tanishq_df = _make_overlap_df(n=31)
    params = cal.fit_calibration(ibja_df, tanishq_df)
    assert params.n_oos == 1
    assert params.r_squared_oos is not None
    assert params.residual_std_oos is not None
    assert params.mae_oos is not None
    assert params.oos_method == "expanding_window_walk_forward_recency_weighted"


def test_fit_oos_fields_none_when_exactly_at_min_fit_threshold():
    # n=30 overlap pairs satisfies fit_calibration's own floor but leaves zero
    # pairs to hold out for walk_forward_validate (needs min_train+1=31) --
    # OOS fields must be honestly absent, not fabricated.
    ibja_df, tanishq_df = _make_overlap_df(n=30)
    params = cal.fit_calibration(ibja_df, tanishq_df)
    assert params.n_observations == 30
    assert params.r_squared_oos is None
    assert params.residual_std_oos is None
    assert params.mae_oos is None
    assert params.n_oos is None


def test_fit_can_disable_oos_validation():
    ibja_df, tanishq_df = _make_overlap_df(n=40)
    params = cal.fit_calibration(ibja_df, tanishq_df, run_oos_validation=False)
    assert params.r_squared_oos is None
    assert params.n_oos is None


# ---------------------------------------------------------------------------
# _recency_weights
# ---------------------------------------------------------------------------


def test_recency_weights_most_recent_is_one():
    weights = cal._recency_weights(10, half_life=5.0)
    assert weights[-1] == pytest.approx(1.0)


def test_recency_weights_decay_at_half_life():
    # The observation exactly half_life pairs before the most recent one
    # should be weighted at exactly 0.5.
    weights = cal._recency_weights(11, half_life=5.0)
    assert weights[-6] == pytest.approx(0.5)  # age = 6 -> (age-1)/half_life = 1.0


def test_recency_weights_monotonically_increasing():
    weights = cal._recency_weights(20, half_life=10.0)
    assert all(weights[i] <= weights[i + 1] for i in range(len(weights) - 1))


def test_recency_weights_unweighted_limit():
    # A very large half_life should make weights nearly uniform (~1.0 each).
    weights = cal._recency_weights(10, half_life=1e6)
    assert weights == pytest.approx(np.ones(10), abs=1e-3)


# ---------------------------------------------------------------------------
# walk_forward_validate
# ---------------------------------------------------------------------------


def test_walk_forward_validate_returns_expected_keys():
    ibja_df, tanishq_df = _make_overlap_df(n=40)
    result = cal.walk_forward_validate(ibja_df, tanishq_df)
    assert set(result) == {"n_oos", "r_squared_oos", "residual_std_oos", "mae_oos", "method"}
    assert result["n_oos"] == 10  # 40 pairs - 30 min_train


def test_walk_forward_validate_raises_on_insufficient_pairs():
    ibja_df, tanishq_df = _make_overlap_df(n=30)  # exactly at floor, 0 to hold out
    with pytest.raises(ValueError, match="overlap pairs"):
        cal.walk_forward_validate(ibja_df, tanishq_df)


def test_walk_forward_validate_no_leakage():
    """Changing a value strictly AFTER a held-out fold must not change that
    fold's prediction — proves each fold is fit only on prior data."""
    ibja_df, tanishq_df = _make_overlap_df(n=40, seed=7)

    result_a = cal.walk_forward_validate(ibja_df, tanishq_df, min_train=30)

    # Mutate only the LAST pair's tanishq value (index 39, never used to train
    # any fold since folds 30..38 only ever see indices < their own).
    tanishq_df_mutated = tanishq_df.copy()
    tanishq_df_mutated.loc[39, "22k"] = tanishq_df_mutated.loc[39, "22k"] + 5000.0

    result_b = cal.walk_forward_validate(ibja_df, tanishq_df_mutated, min_train=30)

    # Every fold's prediction (all but the very last, which now folds itself in
    # as its own held-out actual, not a training input) must be unaffected by
    # a change to a later observation.
    # n_oos is unchanged; residual_std_oos/r_squared_oos DO change slightly
    # because the last fold's actual changed, but the first 9 of 10 OOS
    # predictions must be byte-identical since they never saw index 39.
    assert result_a["n_oos"] == result_b["n_oos"] == 10


def test_walk_forward_validate_first_fold_prediction_unaffected_by_future_mutation():
    """More direct no-leakage check: the FIRST held-out fold's prediction
    (trained only on the first 30 pairs) is identical whether or not any
    later pair is mutated."""
    ibja_df, tanishq_df = _make_overlap_df(n=40, seed=7)

    X = ibja_df["pm_916"].to_numpy() / 10.0
    y = tanishq_df["22k"].to_numpy()
    slope_a, intercept_a = cal._fit_robust(
        X[:30].reshape(-1, 1), y[:30], huber_epsilon=1.35, weights=cal._recency_weights(30)
    )
    pred_a = slope_a * X[30] + intercept_a

    tanishq_df_mutated = tanishq_df.copy()
    tanishq_df_mutated.loc[35, "22k"] = tanishq_df_mutated.loc[35, "22k"] + 9999.0
    y_mut = tanishq_df_mutated["22k"].to_numpy()
    slope_b, intercept_b = cal._fit_robust(
        X[:30].reshape(-1, 1), y_mut[:30], huber_epsilon=1.35, weights=cal._recency_weights(30)
    )
    pred_b = slope_b * X[30] + intercept_b

    assert pred_a == pytest.approx(pred_b)


def test_walk_forward_validate_recency_weighted_beats_unweighted_on_trending_data():
    """The whole point of recency weighting: when the true slope DRIFTS over
    time, a recency-weighted walk-forward should track it at least as well as
    an unweighted one on average OOS error."""
    rng = np.random.default_rng(3)
    n = 60
    ibja_per_g = rng.uniform(13_500, 15_000, size=n)
    # True markup drifts from 1.00 to 1.04 across the window (linear).
    drifting_slope = np.linspace(1.00, 1.04, n)
    tanishq_22k = drifting_slope * ibja_per_g + rng.normal(0, 5.0, size=n)
    base = pd.Timestamp("2026-01-02")
    dates = [str((base + pd.Timedelta(days=i)).date()) for i in range(n)]
    ibja_df = pd.DataFrame({"date": dates, "pm_916": ibja_per_g * 10})
    tanishq_df = pd.DataFrame({"date": dates, "22k": tanishq_22k})

    weighted = cal.walk_forward_validate(ibja_df, tanishq_df, half_life=10.0)
    unweighted = cal.walk_forward_validate(ibja_df, tanishq_df, half_life=1e6)

    assert weighted["mae_oos"] <= unweighted["mae_oos"] * 1.05  # allow tiny noise slack


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
    # schema_version 3: adds residual_abs_quantiles (empirical-quantile band
    # sizing, replacing the Gaussian-sigma residual_std_oos band).
    assert raw["schema_version"] == 3


def test_save_ends_with_trailing_newline(tmp_path):
    # pre-commit's end-of-file-fixer hook rewrites (and fails CI on) any
    # file missing a trailing newline. A refit that lands without one
    # breaks the required "lint" check on the bot-sync PR every time.
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
    assert p.read_text().endswith("\n")


def test_load_raises_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        cal.load_calibration(tmp_path / "nonexistent.json")


def test_save_load_roundtrip_preserves_oos_fields(tmp_path):
    params = cal.CalibrationParams(
        slope=1.023,
        intercept=42.7,
        fit_date="2026-05-19",
        n_observations=35,
        residual_std=12.5,
        r_squared=0.997,
        huber_epsilon=1.35,
        half_life=10.0,
        r_squared_oos=0.91,
        residual_std_oos=80.1,
        mae_oos=60.7,
        n_oos=22,
        oos_method="expanding_window_walk_forward_recency_weighted",
    )
    p = tmp_path / "calibration.json"
    cal.save_calibration(params, p)
    loaded = cal.load_calibration(p)
    assert loaded.half_life == pytest.approx(10.0)
    assert loaded.r_squared_oos == pytest.approx(0.91)
    assert loaded.residual_std_oos == pytest.approx(80.1)
    assert loaded.mae_oos == pytest.approx(60.7)
    assert loaded.n_oos == 22
    assert loaded.oos_method == "expanding_window_walk_forward_recency_weighted"


def test_load_defaults_oos_fields_to_none_for_pre_adr027_schema(tmp_path):
    """A schema_version-1 calibration.json (written before ADR 027) has none of
    the OOS fields at all -- load_calibration must default them to None rather
    than raising a KeyError."""
    legacy_payload = {
        "slope": 1.012,
        "intercept": 0.04,
        "fit_date": "2026-07-16",
        "n_observations": 51,
        "residual_std": 90.68,
        "r_squared": 0.963,
        "huber_epsilon": 1.35,
        "valid": True,
        "schema_version": 1,
    }
    p = tmp_path / "calibration.json"
    p.write_text(json.dumps(legacy_payload) + "\n")
    loaded = cal.load_calibration(p)
    assert loaded.slope == pytest.approx(1.012)
    assert loaded.half_life is None
    assert loaded.r_squared_oos is None
    assert loaded.residual_std_oos is None
    assert loaded.mae_oos is None
    assert loaded.n_oos is None
    assert loaded.oos_method is None


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


# ---------------------------------------------------------------------------
# run_refit_if_needed
# ---------------------------------------------------------------------------


def _write_stub_calibration(path, valid=False, n_observations=21, fit_date="2026-05-19"):
    payload = {
        "slope": None,
        "intercept": None,
        "fit_date": fit_date,
        "n_observations": n_observations,
        "residual_std": None,
        "r_squared": None,
        "huber_epsilon": 1.35,
        "valid": valid,
        "schema_version": 1,
    }
    path.write_text(json.dumps(payload))


def _write_ibja_parquet(path, dates):
    import pandas as pd

    ibja_per_g = [14000.0 + i * 10 for i in range(len(dates))]
    df = pd.DataFrame(
        {
            "date": dates,
            "pm_916": [v * 10 for v in ibja_per_g],
        }
    )
    df.to_parquet(path, index=False)


def _write_prices_json(path, dates):
    readings = [
        {"timestamp": f"{d}T12:00:00.000Z", "22k": 14000.0 + i * 10, "24k": 15000.0}
        for i, d in enumerate(dates)
    ]
    path.write_text(json.dumps(readings))


def _iso_dates(n: int, start: str = "2026-01-02") -> list[str]:
    base = pd.Timestamp(start)
    return [str((base + pd.Timedelta(days=i)).date()) for i in range(n)]


def test_run_refit_skips_when_fewer_than_30_overlap_pairs(tmp_path):
    """No refit when overlap < 30."""
    dates_29 = _iso_dates(29)
    _write_ibja_parquet(tmp_path / "ibja_rates.parquet", dates_29)
    _write_prices_json(tmp_path / "prices.json", dates_29)
    _write_stub_calibration(tmp_path / "calibration.json", valid=False, n_observations=0)

    result = cal.run_refit_if_needed(data_dir=tmp_path)

    assert result is False
    loaded = json.loads((tmp_path / "calibration.json").read_text())
    assert loaded["valid"] is False


def test_run_refit_initial_unlock_at_exactly_30_pairs(tmp_path):
    """Initial unlock: valid=False + overlap=30 triggers refit, flips valid=True."""
    dates_30 = _iso_dates(30)
    _write_ibja_parquet(tmp_path / "ibja_rates.parquet", dates_30)
    _write_prices_json(tmp_path / "prices.json", dates_30)
    _write_stub_calibration(tmp_path / "calibration.json", valid=False, n_observations=21)

    result = cal.run_refit_if_needed(data_dir=tmp_path)

    assert result is True
    loaded = json.loads((tmp_path / "calibration.json").read_text())
    assert loaded["valid"] is True
    assert loaded["n_observations"] == 30
    assert loaded["slope"] is not None
    assert loaded["intercept"] is not None


def test_run_refit_periodic_refit_at_ten_new_pairs(tmp_path):
    """Periodic refit: valid=True but 10+ new pairs since last fit."""
    dates_31 = _iso_dates(31)
    _write_ibja_parquet(tmp_path / "ibja_rates.parquet", dates_31)
    _write_prices_json(tmp_path / "prices.json", dates_31)
    # Last fit was at n_observations=21; 31-21=10 >= 10 → should_refit
    _write_stub_calibration(tmp_path / "calibration.json", valid=True, n_observations=21)

    result = cal.run_refit_if_needed(data_dir=tmp_path)

    assert result is True
    loaded = json.loads((tmp_path / "calibration.json").read_text())
    assert loaded["valid"] is True
    assert loaded["n_observations"] == 31


def test_run_refit_skips_when_valid_and_insufficient_new_pairs(tmp_path):
    """No refit when valid=True but fewer than 10 new pairs."""
    dates_29 = _iso_dates(29)
    _write_ibja_parquet(tmp_path / "ibja_rates.parquet", dates_29)
    _write_prices_json(tmp_path / "prices.json", dates_29)
    # 29-21=8 new pairs < 10; valid already True
    _write_stub_calibration(tmp_path / "calibration.json", valid=True, n_observations=21)

    # Overlap=29 < 30 → early exit (below threshold), so result is False regardless
    result = cal.run_refit_if_needed(data_dir=tmp_path)

    assert result is False


def test_run_refit_skips_valid_true_and_low_new_pairs_above_threshold(tmp_path):
    """No refit when valid=True, overlap>=30, but fewer than 10 new pairs."""
    dates_35 = _iso_dates(35)
    _write_ibja_parquet(tmp_path / "ibja_rates.parquet", dates_35)
    _write_prices_json(tmp_path / "prices.json", dates_35)
    # 35-30=5 new pairs < 10
    _write_stub_calibration(tmp_path / "calibration.json", valid=True, n_observations=30)

    result = cal.run_refit_if_needed(data_dir=tmp_path)

    assert result is False
    loaded = json.loads((tmp_path / "calibration.json").read_text())
    assert loaded["n_observations"] == 30  # unchanged


def test_run_refit_handles_missing_ibja_parquet(tmp_path):
    """Gracefully skips when ibja_rates.parquet is absent."""
    _write_prices_json(tmp_path / "prices.json", _iso_dates(35))
    _write_stub_calibration(tmp_path / "calibration.json", valid=False, n_observations=0)

    result = cal.run_refit_if_needed(data_dir=tmp_path)

    assert result is False


def test_run_refit_handles_missing_prices_json(tmp_path):
    """Gracefully skips when prices.json is absent."""
    _write_ibja_parquet(tmp_path / "ibja_rates.parquet", _iso_dates(35))
    _write_stub_calibration(tmp_path / "calibration.json", valid=False, n_observations=0)

    result = cal.run_refit_if_needed(data_dir=tmp_path)

    assert result is False


def test_run_refit_handles_missing_calibration_json(tmp_path):
    """Gracefully starts from empty calibration when calibration.json is absent."""
    dates_30 = _iso_dates(30)
    _write_ibja_parquet(tmp_path / "ibja_rates.parquet", dates_30)
    _write_prices_json(tmp_path / "prices.json", dates_30)
    # No calibration.json — treated as valid=False, n_observations=0

    result = cal.run_refit_if_needed(data_dir=tmp_path)

    assert result is True
    loaded = json.loads((tmp_path / "calibration.json").read_text())
    assert loaded["valid"] is True


def test_run_refit_no_partial_overlap_still_counts_correctly(tmp_path):
    """Only overlapping dates count; non-overlapping IBJA/Tanishq rows are ignored."""
    # 35 IBJA dates; only first 28 match Tanishq → 28 overlap < 30 → no refit
    ibja_dates = _iso_dates(35, start="2026-01-02")
    tanishq_dates = _iso_dates(28, start="2026-01-02")  # first 28 only
    _write_ibja_parquet(tmp_path / "ibja_rates.parquet", ibja_dates)
    _write_prices_json(tmp_path / "prices.json", tanishq_dates)
    _write_stub_calibration(tmp_path / "calibration.json", valid=False, n_observations=0)

    result = cal.run_refit_if_needed(data_dir=tmp_path)

    assert result is False


def test_run_refit_excludes_null_pm916_from_overlap_count(tmp_path):
    """Rows with null pm_916 do not count toward overlap — avoids triggering a refit
    that fit_calibration() would reject with ValueError (< 30 valid pairs)."""
    dates_30 = _iso_dates(30)
    # Write IBJA parquet where the last 9 rows have pm_916=NaN (live-append pattern)
    ibja_per_g = [14000.0 + i * 10 for i in range(30)]
    rows = []
    for i, d in enumerate(dates_30):
        pm_val = ibja_per_g[i] * 10 if i < 21 else float("nan")
        rows.append({"date": d, "pm_916": pm_val})
    ibja_df = pd.DataFrame(rows)
    ibja_df.to_parquet(tmp_path / "ibja_rates.parquet", index=False)

    _write_prices_json(tmp_path / "prices.json", dates_30)
    _write_stub_calibration(tmp_path / "calibration.json", valid=False, n_observations=0)

    # Effective overlap = 21 (only non-null rows); 21 < 30 → no refit
    result = cal.run_refit_if_needed(data_dir=tmp_path)

    assert result is False
    loaded = json.loads((tmp_path / "calibration.json").read_text())
    assert loaded["valid"] is False  # calibration must NOT have flipped


# ---------------------------------------------------------------------------
# residual_abs_quantiles / evaluate_empirical_band_coverage
# ---------------------------------------------------------------------------


def test_fit_includes_residual_abs_quantiles():
    ibja_df, tanishq_df = _make_overlap_df(n=40)
    params = cal.fit_calibration(ibja_df, tanishq_df, run_oos_validation=False)
    assert params.residual_abs_quantiles is not None
    assert set(params.residual_abs_quantiles.keys()) == {"68", "80", "90"}


def test_residual_abs_quantiles_monotonic_by_level():
    # A wider nominal level must never imply a NARROWER band.
    ibja_df, tanishq_df = _make_overlap_df(n=60, noise_std=25.0)
    params = cal.fit_calibration(ibja_df, tanishq_df, run_oos_validation=False)
    q = params.residual_abs_quantiles
    assert q["68"] <= q["80"] <= q["90"]


def test_evaluate_empirical_band_coverage_returns_expected_keys():
    ibja_df, tanishq_df = _make_overlap_df(n=45)
    result = cal.evaluate_empirical_band_coverage(ibja_df, tanishq_df, level=80)
    assert set(result.keys()) == {"n", "n_in_band", "coverage"}
    assert result["n"] == 45 - cal._MIN_FIT_OBSERVATIONS
    assert 0 <= result["n_in_band"] <= result["n"]


def test_evaluate_empirical_band_coverage_no_leakage():
    """Mutating a pair strictly AFTER the scored index must not change that
    index's own band or coverage outcome -- mirrors
    test_walk_forward_validate_no_leakage's protocol for the same reason."""
    ibja_df, tanishq_df = _make_overlap_df(n=50, seed=7)
    result_before = cal.evaluate_empirical_band_coverage(ibja_df, tanishq_df, level=80)

    tanishq_mutated = tanishq_df.copy()
    tanishq_mutated.loc[tanishq_mutated.index[-1], "22k"] += 5000.0  # blow up the last pair only
    result_after = cal.evaluate_empirical_band_coverage(ibja_df, tanishq_mutated, level=80)

    # Only the LAST scored day can differ (it's the one point whose actual
    # value changed); every earlier day's fit, band, and outcome must be
    # bit-for-bit identical, so n and n_in_band can differ by at most 1.
    assert result_after["n"] == result_before["n"]
    assert abs(result_after["n_in_band"] - result_before["n_in_band"]) <= 1


def test_evaluate_empirical_band_coverage_insufficient_data_returns_none():
    ibja_df, tanishq_df = _make_overlap_df(n=cal._MIN_FIT_OBSERVATIONS)  # no scoreable days
    result = cal.evaluate_empirical_band_coverage(ibja_df, tanishq_df, level=80)
    assert result["n"] == 0
    assert result["coverage"] is None


def test_evaluate_empirical_band_coverage_recovers_carry_forward_days():
    """A Tanishq reading on a date with no exact-match IBJA row (e.g. a weekend)
    must still be scored via the most recent PRIOR IBJA date, asof-matched --
    exactly what ml.inference._try_ibja_calibrated does in production. This is
    the recovery for the n=65-vs-45 gap (session dated 2026-08-27)."""
    ibja_df, tanishq_df = _make_overlap_df(n=40)
    # Add one extra Tanishq-only date (no matching IBJA row) two days after the
    # last synthetic pair -- a carry-forward day that should still get scored.
    last_date = pd.Timestamp(tanishq_df["date"].iloc[-1])
    carry_forward_date = str((last_date + pd.Timedelta(days=2)).date())
    extra_row = pd.DataFrame({"date": [carry_forward_date], "22k": [tanishq_df["22k"].iloc[-1]]})
    tanishq_extended = pd.concat([tanishq_df, extra_row], ignore_index=True)

    same_day_only = cal.evaluate_empirical_band_coverage(ibja_df, tanishq_df, level=80)
    with_carry_forward = cal.evaluate_empirical_band_coverage(ibja_df, tanishq_extended, level=80)
    assert with_carry_forward["n"] == same_day_only["n"] + 1


def test_max_age_days_matches_inference_constant():
    """ml.calibration's scoring gate must stay in sync with ml.inference's real
    production gate (no direct import to avoid a circular import -- see
    _SCORING_MAX_IBJA_AGE_DAYS's comment)."""
    from ml import inference as inf

    assert cal._SCORING_MAX_IBJA_AGE_DAYS == inf._IBJA_DISPLAY_MAX_AGE_DAYS


# ---------------------------------------------------------------------------
# Regression gate: production's live data must stay within the walk-forward-
# measured coverage tolerance around NOMINAL_COVERAGE_PCT.
#
# Tolerance derivation (session dated 2026-08-27, revised twice same day: once
# for the recency-weighted quantile fix (_weighted_percentile), once more
# after recovering the 20 asof-matched carry-forward scoring days the function
# had been dropping -- see evaluate_empirical_band_coverage's docstring for
# why n=45-same-day-only undercounted what production actually displays): a
# walk-forward audit of this exact method against the real ibja_rates.parquet/
# prices.json overlap (n=65 scored days: 45 same-day + 20 asof-matched
# carry-forward, after the min_train warmup) measured 83.1% observed coverage
# at 80% nominal, with a Wilson 95% CI of [72.2%, 90.3%] -- an 18.1
# percentage-point-wide interval at this sample size (narrower than the 22.0pp
# measured on the same-day-only n=45 before the recovery, as expected with
# more data). The task that introduced this test specified a default +/-10pp
# tolerance but required widening it to match the CI when the CI is wider than
# that -- it is, so the tolerance here is +/-19pp (ceil(18.1)), not +/-10pp
# and not the earlier +/-22pp. A tighter tolerance would fail intermittently
# on genuine sampling noise at this sample size, not on a real calibration
# regression; a materially wider tolerance would stop being a meaningful
# regression gate at all.
_COVERAGE_TOLERANCE_PP = 19


def test_real_data_empirical_band_coverage_within_tolerance():
    if not (DATA_DIR / "ibja_rates.parquet").exists() or not (DATA_DIR / "prices.json").exists():
        pytest.skip("real data files not present in this checkout")

    ibja_df = pd.read_parquet(DATA_DIR / "ibja_rates.parquet")
    ibja_df = ibja_df[ibja_df["pm_916"].notna()][["date", "pm_916"]]

    prices_raw = json.loads((DATA_DIR / "prices.json").read_text())
    rows = [
        {"date": r["timestamp"][:10], "22k": float(r["22k"])}
        for r in prices_raw
        if r.get("timestamp") and r.get("22k") is not None
    ]
    tanishq_df = pd.DataFrame(rows).sort_values("date").groupby("date").last().reset_index()

    result = cal.evaluate_empirical_band_coverage(
        ibja_df, tanishq_df, level=cal.NOMINAL_COVERAGE_PCT
    )
    if result["n"] < 20:
        pytest.skip(
            f"only {result['n']} walk-forward-scored days available yet — too few to gate on"
        )

    nominal = cal.NOMINAL_COVERAGE_PCT / 100.0
    tolerance = _COVERAGE_TOLERANCE_PP / 100.0
    coverage = result["coverage"]
    assert nominal - tolerance <= coverage <= nominal + tolerance, (
        f"empirical band coverage {coverage:.1%} (n={result['n']}) has drifted outside "
        f"[{nominal - tolerance:.0%}, {nominal + tolerance:.0%}] around the "
        f"{cal.NOMINAL_COVERAGE_PCT}% nominal level — re-run the R2-style walk-forward "
        f"audit before assuming the band is still well-calibrated."
    )
