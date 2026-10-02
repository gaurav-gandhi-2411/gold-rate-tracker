"""ml.range_forecast.har -- HAR-RV (Corsi 2009) by ordinary least squares.

No `arch`/`statsmodels` dependency: fit via `numpy.linalg.lstsq` on an
expanding window, refit every 21 trading days.

Realized-variance proxy: RV_t = r_t^2 (the squared DAILY log return). This
is a real simplification versus the HAR-RV literature, which builds RV from
INTRADAY returns (5-minute bars, typically) -- daily OHLC data has no
intraday granularity here, so RV_t is a noisy (but unbiased) one-observation
estimator of the day's variance rather than the low-noise sum-of-squares
estimator the original method assumes. Flagged as a methodological choice
to review; it is the best available proxy given this repo's data (daily
closes only).

One-step model: RV_t = c + b_d*RV_d(t-1) + b_w*RV_w(t-1) + b_m*RV_m(t-1),
where RV_d/RV_w/RV_m are the last 1/5/22-day averages of RV known through
day t-1 (i.e. predicting tomorrow's RV from today's rolling components).
Multi-step (h-day) forecasts are built by recursive substitution: forecast
RV_{t}, then roll it into the d/w/m averages to forecast RV_{t+1}, etc.
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

REFIT_EVERY = 21
MIN_TRAIN_SIZE = 250
_RV_FLOOR = 1e-12  # OLS is unconstrained and can predict a non-positive RV; floor it


def realized_variance(r: np.ndarray) -> np.ndarray:
    return r**2


def har_components(rv: np.ndarray, t: int) -> tuple[float, float, float]:
    """RV_d/RV_w/RV_m as of day t (price-index units), using rv[0:t] (the t
    RV observations known through day t: rv[k] is realized at price day
    k+1, so rv[:t] covers price days 1..t)."""
    rv_d = float(rv[t - 1])
    rv_w = float(np.mean(rv[max(0, t - 5) : t]))
    rv_m = float(np.mean(rv[max(0, t - 22) : t]))
    return rv_d, rv_w, rv_m


def fit_har(rv: np.ndarray, t: int, min_origin: int = 22) -> np.ndarray:
    """OLS fit of RV_t' = c + b_d*RV_d + b_w*RV_w + b_m*RV_m over origins
    t' in [min_origin, t) -- all pairs whose target rv[t'] is already known
    given rv[:t] is the data available at day t. Returns [c, b_d, b_w, b_m]."""
    origins = np.arange(min_origin, t)
    X = np.empty((len(origins), 4))
    y = np.empty(len(origins))
    for i, t_prime in enumerate(origins):
        rv_d, rv_w, rv_m = har_components(rv, t_prime)
        X[i] = [1.0, rv_d, rv_w, rv_m]
        y[i] = rv[t_prime]
    coefs, *_ = np.linalg.lstsq(X, y, rcond=None)
    return coefs


def har_h_day_variance(rv_known: np.ndarray, coefs: np.ndarray, t: int, horizon: int) -> float:
    """Recursive multi-step HAR forecast: forecast RV[t], append it to the
    history, forecast RV[t+1], ... sum the h one-step forecasts."""
    history = list(rv_known[:t])
    total = 0.0
    for _ in range(horizon):
        rv_d = history[-1]
        rv_w = float(np.mean(history[-5:]))
        rv_m = float(np.mean(history[-22:]))
        pred = coefs[0] + coefs[1] * rv_d + coefs[2] * rv_w + coefs[3] * rv_m
        pred = max(pred, _RV_FLOOR)
        total += pred
        history.append(pred)
    return total


def har_forecast_set(
    price: pd.Series,
    dataset: str,
    horizon: int,
    levels: tuple[float, ...] = (0.8, 0.9),
    refit_every: int = REFIT_EVERY,
    min_train_size: int = MIN_TRAIN_SIZE,
) -> dict:
    log_price, r = log_price_and_returns(price)
    positions, date_strs = price_positions_and_dates(price)
    n_price = len(log_price)
    rv = realized_variance(r)

    dates, poss, cur_px, actual, scale = [], [], [], [], []
    level_lo: dict[float, list[float]] = {lv: [] for lv in levels}
    level_hi: dict[float, list[float]] = {lv: [] for lv in levels}

    coefs: np.ndarray | None = None
    last_refit_t = -(10**9)
    fit_count = 0

    for t in range(min_train_size, n_price - horizon):
        if coefs is None or (t - last_refit_t) >= refit_every:
            coefs = fit_har(rv, t)
            last_refit_t = t
            fit_count += 1

        h_var = har_h_day_variance(rv, coefs, t, horizon)
        scale_h = float(np.sqrt(max(h_var, _RV_FLOOR)))
        act = float(log_price[t + horizon] - log_price[t])

        dates.append(date_strs[t])
        poss.append(positions[t])
        cur_px.append(float(price.iloc[t]))
        actual.append(act)
        scale.append(scale_h)
        for lv in levels:
            lo, hi = normal_quantile_bounds(np.array([scale_h]), lv)
            level_lo[lv].append(float(lo[0]))
            level_hi[lv].append(float(hi[0]))

    fs = assemble_forecast_set(
        "har",
        dataset,
        horizon,
        dates,
        poss,
        cur_px,
        actual,
        scale,
        {lv: (level_lo[lv], level_hi[lv]) for lv in levels},
    )
    fs["n_refits"] = fit_count
    return fs
