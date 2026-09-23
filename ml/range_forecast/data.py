"""ml.range_forecast.data — trading-day series loading and driver features.

Two price histories, loaded separately per the task brief (proxy history has
much more depth but ADR 032 shows it is only ~44-54% direction-reliable below
~50 Rs/gram moves; real IBJA is short but product-relevant):

  load_proxy_log_returns  -- ml.inr_proxy_labels.LABEL_OUTPUT_PATH, 2013-2026
  load_ibja_log_returns   -- data/ibja_rates.parquet pm_916, ~2022-2026

Both drop carried-forward (non-publication) rows first: a row equal to the
previous row is not a new trading day, it is the last known value repeated
onto a calendar day the source never actually published for (weekends for
the label series; a fetch gap for IBJA).

build_driver_features assembles the M1 driver set for the quantile model
(ml.direction.config_sweep.M1_DRIVER_COLS is the direction model's list; this
module additionally pulls realized-vol and usd_inr-change features from
ml.macro) under the SAME T-1 lag discipline as that module: every driver
value attached to trading day t is the value known at or before the close of
day t-1 (the prior trading day), never day t's own close.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from ml.inr_proxy_labels import LABEL_OUTPUT_PATH

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
IBJA_PARQUET_PATH = DATA_DIR / "ibja_rates.parquet"


def _drop_carried_forward(series: pd.Series) -> pd.Series:
    """Keep the first row and every row that differs from the row before it.

    A row equal to its predecessor is a carried-forward (non-publication)
    day, not a genuine new trading-day observation -- see module docstring.
    """
    series = series.dropna()
    if series.empty:
        return series
    keep = series.ne(series.shift(1))
    keep.iloc[0] = True
    return series[keep]


def load_proxy_price_series(label_path: Path = LABEL_OUTPUT_PATH) -> pd.Series:
    """Real-trading-day 22K INR/10g proxy label series, date-indexed."""
    df = pd.read_parquet(label_path)
    series = df["label_22k_per_10g"]
    series.index = pd.to_datetime(series.index, utc=True).tz_localize(None)
    return _drop_carried_forward(series).sort_index()


def load_ibja_price_series(ibja_path: Path = IBJA_PARQUET_PATH) -> pd.Series:
    """Real-trading-day IBJA 22K (pm_916, PM fix) price series, INR/10g."""
    df = pd.read_parquet(ibja_path)
    df = df.dropna(subset=["pm_916"]).sort_values("date")
    series = pd.Series(df["pm_916"].to_numpy(), index=pd.to_datetime(df["date"]))
    return _drop_carried_forward(series).sort_index()


def log_returns(price: pd.Series) -> pd.Series:
    """Daily log return series (index shrinks by 1 vs `price`)."""
    return np.log(price / price.shift(1)).dropna()


def forward_log_return(price: pd.Series, horizon: int) -> pd.Series:
    """log(P[t+h]) - log(P[t]) indexed by t (the as-of day, position-based)."""
    log_p = np.log(price)
    fwd = log_p.shift(-horizon) - log_p
    return fwd.dropna()


def _t_minus_1_lag(daily_calendar_series: pd.Series, as_of_dates: pd.DatetimeIndex) -> pd.Series:
    """Reindex a forward-filled CALENDAR-daily series onto `as_of_dates`
    using the value as of the PRIOR calendar day (T-1 lag discipline)."""
    shifted_index = as_of_dates - pd.Timedelta(days=1)
    aligned = daily_calendar_series.reindex(shifted_index)
    aligned.index = as_of_dates
    return aligned


def realized_vol_features(returns: pd.Series, windows: tuple[int, ...] = (5, 20, 60)) -> pd.DataFrame:
    """Rolling std of daily log returns, ending at and including day t (no
    leakage: only returns known by the close of day t)."""
    out = pd.DataFrame(index=returns.index)
    for w in windows:
        out[f"rv_{w}d"] = returns.rolling(w, min_periods=max(2, w // 2)).std()
    return out


def calendar_features(dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Vectorized wrapper around ml.calendar_events.get_demand_calendar_features."""
    from ml.calendar_events import get_demand_calendar_features

    rows = [get_demand_calendar_features(d.date()) for d in dates]
    out = pd.DataFrame(rows, index=dates)
    for col in ("is_wedding_season", "is_budget_window", "is_duty_event_recent"):
        out[col] = out[col].astype(bool)
    out["days_since_duty_event"] = out["days_since_duty_event"].astype(float)
    return out


def build_driver_features(
    as_of_dates: pd.DatetimeIndex,
    recent_returns: pd.Series,
    macro_start: str | None = None,
    macro_end: str | None = None,
) -> pd.DataFrame:
    """M1-style driver features for the quantile model, T-1 lag discipline
    throughout (india_vix and usd_inr_change_1d are the PRIOR trading day's
    values -- the same discipline ADR 038 amendment A2 requires for the
    proxy arm, applied here uniformly rather than only where a past leak was
    found).

    Columns: india_vix_prior, usd_inr_change_1d_prior, is_wedding_season,
    is_budget_window, is_duty_event_recent, days_since_duty_event,
    rv_5d, rv_20d, rv_60d, ret_1d, ret_5d_mean.
    """
    from ml.macro import fetch_macro_features

    start = macro_start or (as_of_dates.min() - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
    end = macro_end or (as_of_dates.max() + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    macro = fetch_macro_features(start, end)
    macro.index = pd.to_datetime(macro.index, utc=True).tz_localize(None)

    dates_utc_naive = pd.DatetimeIndex(as_of_dates)
    india_vix_prior = _t_minus_1_lag(macro["india_vix"], dates_utc_naive)
    usd_inr_change_prior = _t_minus_1_lag(macro["usd_inr_change_1d"], dates_utc_naive)

    cal = calendar_features(dates_utc_naive)

    rv = realized_vol_features(recent_returns).reindex(dates_utc_naive)
    ret_1d = recent_returns.reindex(dates_utc_naive)
    ret_5d_mean = recent_returns.rolling(5, min_periods=2).mean().reindex(dates_utc_naive)

    out = pd.DataFrame(
        {
            "india_vix_prior": india_vix_prior.to_numpy(),
            "usd_inr_change_1d_prior": usd_inr_change_prior.to_numpy(),
            "is_wedding_season": cal["is_wedding_season"].to_numpy(),
            "is_budget_window": cal["is_budget_window"].to_numpy(),
            "is_duty_event_recent": cal["is_duty_event_recent"].to_numpy(),
            "days_since_duty_event": cal["days_since_duty_event"].to_numpy(),
            "rv_5d": rv["rv_5d"].to_numpy(),
            "rv_20d": rv["rv_20d"].to_numpy(),
            "rv_60d": rv["rv_60d"].to_numpy(),
            "ret_1d": ret_1d.to_numpy(),
            "ret_5d_mean": ret_5d_mean.to_numpy(),
        },
        index=dates_utc_naive,
    )
    return out


M1_DRIVER_FEATURE_COLS: list[str] = [
    "india_vix_prior",
    "usd_inr_change_1d_prior",
    "is_wedding_season",
    "is_budget_window",
    "is_duty_event_recent",
    "days_since_duty_event",
    "rv_5d",
    "rv_20d",
    "rv_60d",
    "ret_1d",
    "ret_5d_mean",
]


def sub_period_mask(as_of_dates: np.ndarray, start: str, end_exclusive: str) -> np.ndarray:
    """Boolean mask for `start <= as_of_date < end_exclusive` (string dates)."""
    return (as_of_dates >= start) & (as_of_dates < end_exclusive)


SUB_PERIODS: list[tuple[str, str, str]] = [
    ("2015-2017", "2015-01-01", "2018-01-01"),
    ("2018-2021", "2018-01-01", "2022-01-01"),
    ("2022-2026", "2022-01-01", "2027-01-01"),
]


def today_iso() -> str:
    return date.today().isoformat()
