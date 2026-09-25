"""Tests for ml.fhs_ranges (brief item 5b, ADR 056). Synthetic data only."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from ml.fhs_ranges import (
    EWMA_LAMBDA_GRID,
    VolCache,
    VolFit,
    fhs_base_range,
    fhs_next_day_range_pct_series,
    fhs_walk_forward,
    fit_vol_model,
    forecast_variance_path,
    h_day_forecast_stdev,
    select_ewma_lambda,
    standardized_quantile_path,
)
from ml.range_forecast.baselines import ewma_variance_path
from ml.range_forecast.garch import MIN_TRAIN_SIZE, garch_sigma2_path


def _proxy_ibja(
    n_proxy: int = 900, n_ibja: int = 200, seed: int = 42
) -> tuple[pd.Series, pd.Series]:
    rng = np.random.default_rng(seed)
    pdates = pd.bdate_range("2020-01-02", periods=n_proxy)
    proxy = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n_proxy))), index=pdates)
    idates = pdates[-n_ibja:]
    ibja = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.008, n_ibja))), index=idates)
    return proxy, ibja


# ---------------------------------------------------------------------------
# EWMA / GARCH recursion reuse and alignment
# ---------------------------------------------------------------------------


def test_ewma_forecast_variance_path_is_the_pre_return_shift_of_ewma_variance_path() -> None:
    """forecast_variance_path must not duplicate ml.range_forecast.baselines.ewma_variance_
    path's recursion -- it only re-indexes that module's own output by one position, to
    convert its post-return convention to the pre-return one GARCH already uses."""
    r = np.array([0.01, -0.02, 0.03, -0.01, 0.02, -0.03, 0.01, 0.02])
    fit = VolFit(method="ewma", n_train=len(r), lam=0.9)
    fc = forecast_variance_path(r, fit)
    post = ewma_variance_path(r, lam=0.9)
    assert fc[0] == pytest.approx(post[0])  # no prior return to condition on: same seed
    assert np.allclose(fc[1:], post[:-1])  # every later point shifted back by one


def test_garch_forecast_variance_path_is_garch_sigma2_path_reused_verbatim() -> None:
    """forecast_variance_path("garch") must call ml.range_forecast.garch.garch_sigma2_path
    directly, with no transformation -- that function already returns the pre-return
    Var(r_i | F_{i-1}) convention FHS needs."""
    r = np.array([0.01, -0.02, 0.03, -0.01, 0.02, -0.03, 0.01, 0.02])
    params = {"omega": 1e-5, "alpha": 0.08, "beta": 0.85}
    fit = VolFit(method="garch", n_train=len(r), params=params)
    got = forecast_variance_path(r, fit)
    want = garch_sigma2_path(r, params["omega"], params["alpha"], params["beta"])
    assert np.array_equal(got, want)


def test_fit_vol_model_returns_none_below_min_train_size() -> None:
    r = np.zeros(MIN_TRAIN_SIZE - 1)
    assert fit_vol_model(r, "ewma") is None
    assert fit_vol_model(r, "garch") is None


def test_fit_vol_model_rejects_unknown_method() -> None:
    with pytest.raises(ValueError, match="unknown vol method"):
        fit_vol_model(np.ones(MIN_TRAIN_SIZE), "not_a_method")


# ---------------------------------------------------------------------------
# EWMA lambda selection -- "chosen on training folds only"
# ---------------------------------------------------------------------------


def test_select_ewma_lambda_adapts_to_a_late_volatility_regime_switch() -> None:
    """A regime switch inside the held-back tail (calm first 700 returns, then a 3x-louder
    last 300) should favor the FASTEST-adapting (lowest) lambda in the grid: a slow-decaying
    lambda is still mostly reacting to the calm regime by the time the tail is scored."""
    rng = np.random.default_rng(42)
    r_train = np.concatenate([rng.normal(0, 0.005, 700), rng.normal(0, 0.03, 300)])
    lam = select_ewma_lambda(r_train)
    assert lam == min(EWMA_LAMBDA_GRID)


def test_select_ewma_lambda_never_uses_data_past_its_own_input() -> None:
    """Appending more returns AFTER the array passed to select_ewma_lambda must not change
    its choice -- it only ever sees what is passed in."""
    rng = np.random.default_rng(7)
    r_train = rng.normal(0, 0.01, 500)
    lam_before = select_ewma_lambda(r_train)
    extended = np.concatenate([r_train, rng.normal(0, 0.05, 200)])  # future, louder regime
    lam_after_on_same_prefix = select_ewma_lambda(extended[:500])
    assert lam_before == lam_after_on_same_prefix


def test_select_ewma_lambda_falls_back_when_too_little_data_to_hold_back() -> None:
    tiny = np.ones(10) * 0.01
    assert select_ewma_lambda(tiny) == float(np.median(EWMA_LAMBDA_GRID))


# ---------------------------------------------------------------------------
# h-day-ahead volatility forecast
# ---------------------------------------------------------------------------


def test_h_day_forecast_stdev_grows_with_horizon() -> None:
    r = np.random.default_rng(1).normal(0, 0.01, 400)
    fit = VolFit(method="ewma", n_train=len(r), lam=0.94)
    s1 = h_day_forecast_stdev(r, fit, 1)
    s5 = h_day_forecast_stdev(r, fit, 5)
    assert s1 < s5
    assert s5 == pytest.approx(s1 * np.sqrt(5.0))  # EWMA: flat sqrt-time scaling, no reversion


def test_h_day_forecast_stdev_garch_matches_garch_h_day_variance_reused_directly() -> None:
    """h_day_forecast_stdev("garch") must be sqrt of ml.range_forecast.garch.garch_h_day_
    variance's own output (mean-reverting toward the long-run level), not a flat sqrt(h)
    rescale of the 1-day forecast -- the two disagree whenever sigma2_next != long_run_var,
    which is the normal case, not a special one."""
    r = np.random.default_rng(1).normal(0, 0.01, 400)
    params = {"omega": 1e-5, "alpha": 0.05, "beta": 0.80}  # persistence 0.85 < 1: mean-reverting
    fit = VolFit(method="garch", n_train=len(r), params=params)
    s1 = h_day_forecast_stdev(r, fit, 1)
    s5 = h_day_forecast_stdev(r, fit, 5)
    assert s1 < s5
    # Not flat sqrt-time scaling (that's the EWMA convention, tested above) -- GARCH's h-day
    # variance is the sum of a geometrically-reverting path, not h copies of the 1-day one.
    assert s5**2 != pytest.approx(5.0 * s1**2)

    from ml.range_forecast.garch import garch_h_day_variance, garch_sigma2_path

    sigma2_path = garch_sigma2_path(r, params["omega"], params["alpha"], params["beta"])
    persistence = params["alpha"] + params["beta"]
    long_run_var = params["omega"] / (1.0 - persistence)
    sigma2_next = params["omega"] + params["alpha"] * r[-1] ** 2 + params["beta"] * sigma2_path[-1]
    want_h_var = garch_h_day_variance(sigma2_next, long_run_var, persistence, 5)
    assert s5 == pytest.approx(np.sqrt(want_h_var))


# ---------------------------------------------------------------------------
# Quantile rescaling
# ---------------------------------------------------------------------------


def test_standardized_quantile_path_k1_matches_plain_empirical_quantiles() -> None:
    """At k=1 the rolling-path construction degenerates to the ordinary tail quantiles of z
    itself (a 1-day path has only one step, so its min and max are both that one return)."""
    rng = np.random.default_rng(3)
    z = rng.normal(0, 1.0, 600)
    got = standardized_quantile_path(z, 1, hs_window=500, min_sample=60, level=0.80)
    assert got is not None
    lo_q, hi_q = got
    z_tail = z[-501:]
    assert lo_q == pytest.approx(float(np.quantile(z_tail, 0.10)))
    assert hi_q == pytest.approx(float(np.quantile(z_tail, 0.90)))


def test_standardized_quantile_path_returns_none_below_min_sample() -> None:
    z = np.random.default_rng(3).normal(0, 1.0, 30)
    assert standardized_quantile_path(z, 5, hs_window=500, min_sample=60) is None


def test_standardized_quantile_path_k5_matches_hand_rolled_rolling_paths() -> None:
    rng = np.random.default_rng(9)
    z = rng.normal(0, 1.0, 300)
    k, hs_window, min_sample = 5, 200, 60
    got = standardized_quantile_path(z, k, hs_window=hs_window, min_sample=min_sample)
    assert got is not None

    z_tail = z[-(hs_window + k) :]
    cum = np.concatenate(([0.0], np.cumsum(z_tail)))
    n = len(cum) - k
    steps = np.stack([cum[s : s + n] for s in range(1, k + 1)], axis=1) - cum[:n, None]
    want_lo = float(np.quantile(steps.min(axis=1), 0.10))
    want_hi = float(np.quantile(steps.max(axis=1), 0.90))
    assert got == (pytest.approx(want_lo), pytest.approx(want_hi))


def test_fhs_base_range_rescales_by_todays_forecast_not_the_historical_average() -> None:
    """Two histories share the exact same standardized-residual SHAPE (same z-quantiles) but
    end on different current volatility levels -- a calm run vs. a just-spiked one. FHS must
    return a wider range for the just-spiked history even though both trained on the same
    average volatility, because rescaling uses TODAY's forecast, not the trailing average."""
    rng = np.random.default_rng(11)
    r_calm = rng.normal(0, 0.01, 400)
    # Same draws, but the tail is scaled up 5x just before the as-of day -- a recent vol spike.
    r_spiked = r_calm.copy()
    r_spiked[-5:] *= 5.0

    calm_log = np.concatenate(([0.0], np.cumsum(r_calm)))
    spiked_log = np.concatenate(([0.0], np.cumsum(r_spiked)))

    cache_calm, cache_spiked = VolCache("ewma"), VolCache("ewma")
    calm = fhs_base_range(calm_log, cache_calm, 1)
    spiked = fhs_base_range(spiked_log, cache_spiked, 1)
    assert calm is not None
    assert spiked is not None
    assert (spiked[1] - spiked[0]) > (calm[1] - calm[0])


def test_fhs_base_range_returns_none_below_min_train_size() -> None:
    proxy_log = np.cumsum(np.ones(MIN_TRAIN_SIZE - 5) * 0.001)
    assert fhs_base_range(proxy_log, VolCache("ewma"), 1) is None


# ---------------------------------------------------------------------------
# No-lookahead
# ---------------------------------------------------------------------------


def test_fhs_walk_forward_never_calibrates_on_an_unfinished_window() -> None:
    proxy, ibja = _proxy_ibja()
    df = fhs_walk_forward(proxy, ibja, "week", "ewma")
    ends = dict(zip(df["as_of"], df["end"], strict=True))
    for _, row in df.iterrows():
        finished = sum(1 for a, e in ends.items() if a < row["as_of"] and e < row["as_of"])
        assert row["n_cal"] <= finished
    assert df["scale"].notna().any()


def test_fhs_next_day_range_no_look_ahead() -> None:
    """Altering data dated >= d, in either series, must not change d's FHS range."""
    proxy, ibja = _proxy_ibja()
    d = ibja.index[-20]
    want = fhs_next_day_range_pct_series(proxy, ibja, [d], "ewma")[d]
    assert want is not None

    spiked_proxy = proxy.copy()
    spiked_proxy[spiked_proxy.index >= d] *= 5.0
    got_proxy = fhs_next_day_range_pct_series(spiked_proxy, ibja, [d], "ewma")[d]
    assert got_proxy == want

    spiked_ibja = ibja.copy()
    spiked_ibja[spiked_ibja.index >= d] *= 5.0
    got_ibja = fhs_next_day_range_pct_series(proxy, spiked_ibja, [d], "ewma")[d]
    assert got_ibja == want

    extra_dates = pd.bdate_range(ibja.index[-1] + pd.Timedelta(days=1), periods=10)
    extended_ibja = pd.concat([ibja, pd.Series([999.0] * len(extra_dates), index=extra_dates)])
    got_extended = fhs_next_day_range_pct_series(proxy, extended_ibja, [d], "ewma")[d]
    assert got_extended == want


def test_fhs_next_day_range_is_deterministic_for_a_fixed_batch() -> None:
    """Same inputs, same batch, called twice: identical output. (Batching several decision
    days together intentionally reuses one VolCache sequentially across them, for refit
    efficiency -- so the shape-at-d cache can be at a DIFFERENT refit staleness than a
    single-day call would produce; that is a documented performance trade-off, not
    nondeterminism, and is not asserted equal to the single-day case here.)"""
    proxy, ibja = _proxy_ibja()
    dates = list(ibja.index[-30:])
    a = fhs_next_day_range_pct_series(proxy, ibja, dates, "garch")
    b = fhs_next_day_range_pct_series(proxy, ibja, dates, "garch")
    assert a == b


def test_fhs_next_day_range_first_date_in_batch_matches_scoring_it_alone() -> None:
    """The EARLIEST date in a batch is always the first thing the shape-at-d cache fits, in
    both a single-day call and a batch call -- so, unlike a later date in the batch, it must
    match regardless of what (if anything) is batched after it."""
    proxy, ibja = _proxy_ibja()
    dates = list(ibja.index[-30:])
    d0 = dates[0]
    alone = fhs_next_day_range_pct_series(proxy, ibja, [d0], "garch")[d0]
    batch = fhs_next_day_range_pct_series(proxy, ibja, dates, "garch")[d0]
    assert alone == batch


# ---------------------------------------------------------------------------
# VolCache refit cadence
# ---------------------------------------------------------------------------


def test_volcache_refits_only_every_refit_every_returns() -> None:
    cache = VolCache("ewma", refit_every=25)
    rng = np.random.default_rng(5)
    seen_n_train: list[int] = []
    r = rng.normal(0, 0.01, MIN_TRAIN_SIZE)
    for extra in range(0, 80, 5):
        r_train = np.concatenate([r, rng.normal(0, 0.01, extra)])
        fit = cache.get(r_train)
        assert fit is not None
        seen_n_train.append(fit.n_train)
    # n_train only changes when the cache actually refits -- consecutive calls within one
    # refit_every window must return the SAME cached n_train, not one that tracks r_train.
    assert len(set(seen_n_train)) < len(seen_n_train)
    changes = [
        seen_n_train[i]
        for i in range(1, len(seen_n_train))
        if seen_n_train[i] != seen_n_train[i - 1]
    ]
    for i in range(1, len(changes)):
        assert changes[i] - changes[i - 1] >= 25


def test_volcache_returns_none_below_min_train_size() -> None:
    cache = VolCache("garch")
    assert cache.get(np.zeros(MIN_TRAIN_SIZE - 1)) is None
