"""ml.range_forecast.baselines -- historical volatility, historical
simulation, and RiskMetrics EWMA.

All three assume ZERO drift (central interval centered on the day-t price,
not on a fitted mean h-day return) -- the standard short-horizon VaR/interval
convention, and a deliberate simplification worth reviewing: a model with a
persistent historical drift (e.g. gold's long-run upward trend) would be
slightly better centered by including a fitted mean. Flagged as a
methodological choice in the PR/report.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.range_forecast.metrics import normal_quantile_bounds
from ml.range_forecast.walkforward import (
    assemble_forecast_set,
    log_price_and_returns,
    price_positions_and_dates,
)

VOL_WINDOW = 250
HS_WINDOW = 500
HS_MIN_SAMPLE = 60
EWMA_LAMBDA = 0.94
EWMA_SEED_WINDOW = 30


def historical_vol_forecast_set(
    price: pd.Series,
    dataset: str,
    horizon: int,
    levels: tuple[float, ...] = (0.8, 0.9),
    vol_window: int = VOL_WINDOW,
    min_train_size: int = VOL_WINDOW,
) -> dict:
    """Rolling `vol_window`-day std of daily log returns, scaled by
    sqrt(horizon), normal quantiles. Baseline #1 (historical volatility)."""
    log_price, r = log_price_and_returns(price)
    positions, date_strs = price_positions_and_dates(price)
    n_price = len(log_price)
    start_t = max(min_train_size, vol_window)

    dates, poss, cur_px, actual, scale = [], [], [], [], []
    level_lo: dict[float, list[float]] = {lv: [] for lv in levels}
    level_hi: dict[float, list[float]] = {lv: [] for lv in levels}

    for t in range(start_t, n_price - horizon):
        window_returns = r[t - vol_window : t]
        sigma_daily = float(window_returns.std(ddof=1))
        sigma_h = sigma_daily * np.sqrt(horizon)
        act = float(log_price[t + horizon] - log_price[t])

        dates.append(date_strs[t])
        poss.append(positions[t])
        cur_px.append(float(price.iloc[t]))
        actual.append(act)
        scale.append(sigma_h)
        for lv in levels:
            lo, hi = normal_quantile_bounds(np.array([sigma_h]), lv)
            level_lo[lv].append(float(lo[0]))
            level_hi[lv].append(float(hi[0]))

    return assemble_forecast_set(
        "historical_vol",
        dataset,
        horizon,
        dates,
        poss,
        cur_px,
        actual,
        scale,
        {lv: (level_lo[lv], level_hi[lv]) for lv in levels},
    )


def historical_simulation_forecast_set(
    price: pd.Series,
    dataset: str,
    horizon: int,
    levels: tuple[float, ...] = (0.8, 0.9),
    hs_window: int = HS_WINDOW,
    min_train_size: int = HS_MIN_SAMPLE,
) -> dict:
    """Empirical quantiles of trailing OVERLAPPING h-day log returns (a
    non-parametric baseline: no distributional assumption). Baseline #2."""
    log_price, _ = log_price_and_returns(price)
    positions, date_strs = price_positions_and_dates(price)
    n_price = len(log_price)

    # h-day return realized ending at price index k (k >= horizon): known once
    # price[k] is known, i.e. usable in a sample as of any as-of day t >= k.
    fwd_h = np.full(n_price, np.nan)
    fwd_h[horizon:] = log_price[horizon:] - log_price[:-horizon]

    start_t = max(min_train_size, horizon + HS_MIN_SAMPLE)

    dates, poss, cur_px, actual, scale = [], [], [], [], []
    level_lo: dict[float, list[float]] = {lv: [] for lv in levels}
    level_hi: dict[float, list[float]] = {lv: [] for lv in levels}

    for t in range(start_t, n_price - horizon):
        lo_idx = max(horizon, t - hs_window + 1)
        sample = fwd_h[lo_idx : t + 1]
        sample = sample[~np.isnan(sample)]
        if len(sample) < HS_MIN_SAMPLE:
            continue
        act = float(log_price[t + horizon] - log_price[t])

        dates.append(date_strs[t])
        poss.append(positions[t])
        cur_px.append(float(price.iloc[t]))
        actual.append(act)
        scale.append(float(sample.std(ddof=1)))
        for lv in levels:
            tail = (1.0 - lv) / 2.0
            lo_v = float(np.quantile(sample, tail))
            hi_v = float(np.quantile(sample, 1.0 - tail))
            level_lo[lv].append(lo_v)
            level_hi[lv].append(hi_v)

    return assemble_forecast_set(
        "historical_simulation",
        dataset,
        horizon,
        dates,
        poss,
        cur_px,
        actual,
        scale,
        {lv: (level_lo[lv], level_hi[lv]) for lv in levels},
    )


def ewma_variance_path(
    returns: np.ndarray, lam: float = EWMA_LAMBDA, seed_window: int = EWMA_SEED_WINDOW
) -> np.ndarray:
    """RiskMetrics EWMA conditional variance, one value per return in
    `returns` (sigma2[i] is the variance ESTIMATE incorporating return[i],
    i.e. known as of the close of the day return[i] was realized).
    Seeded with the sample variance of the first `seed_window` returns."""
    n = len(returns)
    sigma2 = np.empty(n)
    if n == 0:
        return sigma2
    seed_n = min(seed_window, n)
    prev = float(np.var(returns[:seed_n], ddof=1)) if seed_n > 1 else float(returns[0] ** 2)
    for i in range(n):
        prev = lam * prev + (1.0 - lam) * float(returns[i]) ** 2
        sigma2[i] = prev
    return sigma2


def ewma_forecast_set(
    price: pd.Series,
    dataset: str,
    horizon: int,
    levels: tuple[float, ...] = (0.8, 0.9),
    lam: float = EWMA_LAMBDA,
    min_train_size: int = EWMA_SEED_WINDOW * 2,
) -> dict:
    """RiskMetrics lambda=0.94 EWMA volatility, sqrt(horizon)-scaled
    (no mean reversion in the EWMA recursion, so the standard iid
    sqrt-time extrapolation applies), normal quantiles. Baseline #3."""
    log_price, r = log_price_and_returns(price)
    positions, date_strs = price_positions_and_dates(price)
    n_price = len(log_price)
    sigma2_path = ewma_variance_path(r, lam=lam)  # index i -> return realized at price index i+1

    dates, poss, cur_px, actual, scale = [], [], [], [], []
    level_lo: dict[float, list[float]] = {lv: [] for lv in levels}
    level_hi: dict[float, list[float]] = {lv: [] for lv in levels}

    for t in range(min_train_size, n_price - horizon):
        sigma2_t = sigma2_path[t - 1]  # last known variance estimate, as of close of day t
        sigma_h = float(np.sqrt(sigma2_t * horizon))
        act = float(log_price[t + horizon] - log_price[t])

        dates.append(date_strs[t])
        poss.append(positions[t])
        cur_px.append(float(price.iloc[t]))
        actual.append(act)
        scale.append(sigma_h)
        for lv in levels:
            lo, hi = normal_quantile_bounds(np.array([sigma_h]), lv)
            level_lo[lv].append(float(lo[0]))
            level_hi[lv].append(float(hi[0]))

    return assemble_forecast_set(
        "ewma",
        dataset,
        horizon,
        dates,
        poss,
        cur_px,
        actual,
        scale,
        {lv: (level_lo[lv], level_hi[lv]) for lv in levels},
    )
