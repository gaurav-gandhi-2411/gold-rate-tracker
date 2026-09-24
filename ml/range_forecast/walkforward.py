"""ml.range_forecast.walkforward -- the shared RangeForecastSet shape every
model in this package produces, plus small assembly helpers.

Every model walk-forward loop below (baselines.py, garch.py, har.py,
quantile_gbm.py, chronos_model.py) yields the SAME dict shape so
conformal.py and metrics.py, and the analysis script's aggregation, need
only one code path regardless of which model produced the numbers:

    {
      "model": str, "dataset": str, "horizon": int,
      "as_of_date": [str, ...], "as_of_position": [int, ...],
      "current_price": [float, ...], "actual_return": [float, ...],
      "scale": [float, ...],                     # predicted std of the h-day log return
      "levels": {"0.8": {"lo": [...], "hi": [...]}, "0.9": {"lo": [...], "hi": [...]}},
    }

`as_of_position` is the integer trading-day index into the price series used
to build this forecast set -- the embargo arithmetic in conformal.py is
defined purely in these units (position + horizon <= later position), so it
must be a genuine trading-day count, not a calendar-day offset.

Only forecasts whose outcome has already matured WITHIN the available price
history are emitted (t + horizon <= N - 1) -- this package evaluates a
backtest, not a live current-day forecast, so every row already has a
computable `actual_return`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

LEVELS: tuple[float, ...] = (0.8, 0.9)
HORIZONS: tuple[int, ...] = (1, 5, 10, 20)


def assemble_forecast_set(
    model: str,
    dataset: str,
    horizon: int,
    dates: list[str],
    positions: list[int],
    current_price: list[float],
    actual_return: list[float],
    scale: list[float],
    level_bounds: dict[float, tuple[list[float], list[float]]],
) -> dict:
    """Pack per-forecast arrays into the standard RangeForecastSet dict.
    `level_bounds`: {level: (lo_list, hi_list)}."""
    levels_out: dict[str, dict[str, list[float]]] = {}
    for level, (lo, hi) in level_bounds.items():
        levels_out[str(level)] = {"lo": [float(x) for x in lo], "hi": [float(x) for x in hi]}
    return {
        "model": model,
        "dataset": dataset,
        "horizon": horizon,
        "as_of_date": list(dates),
        "as_of_position": [int(p) for p in positions],
        "current_price": [float(x) for x in current_price],
        "actual_return": [float(x) for x in actual_return],
        "scale": [float(x) for x in scale],
        "levels": levels_out,
    }


def price_positions_and_dates(price: pd.Series) -> tuple[np.ndarray, list[str]]:
    """log-price array, integer positions, and ISO date strings for a
    trading-day-only price series."""
    positions = np.arange(len(price))
    date_strs = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in price.index]
    return positions, date_strs


def log_price_and_returns(price: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    """log_price[i] and returns[i] = log_price[i+1] - log_price[i] (returns
    has length N-1; returns[i] is the return REALIZED at price index i+1)."""
    log_price = np.log(price.to_numpy(dtype=float))
    returns = np.diff(log_price)
    return log_price, returns
