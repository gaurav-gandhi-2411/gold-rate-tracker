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

DATA CAVEAT + IBJA PROTOCOL (found via the 2026-09-23 Actions run,
35894266384/0d188fd2): data/ibja_rates.parquet is NOT daily before
2025-Q2 -- median gap 5-18 calendar days, one row in all of 2026-Q1.
Treating consecutive IBJA rows as consecutive trading days (the original
design) made a nominal "1-day" horizon silently span weeks there, inflating
coverage and shrinking width (e.g. historical_vol raw IBJA h1 80% measured
94.1% coverage at 6.27% width). `dense_segments` (below) matches
scripts/analysis_buyer_policy.py's fix for the same file (ADR 039/040):
split wherever the calendar gap between consecutive rows exceeds
MAX_GAP_DAYS. `score_against_ibja` then implements the corrected protocol
(product question: do proxy-fitted forecasts work on real IBJA prices?):
every model forecasts from the daily PROXY series only -- IBJA is never
walked forward independently -- and is SCORED against the real IBJA h-day
log return from t to t+h, only for as-of days t that are genuine IBJA rows
AND whose h-IBJA-ROW-ahead window (h counted in rows within one dense
segment, not calendar/trading days) never crosses a segment boundary.
"""

from __future__ import annotations

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


MAX_GAP_DAYS = 4  # weekend + a holiday; matches scripts/analysis_buyer_policy.py


def dense_segments(price: pd.Series, max_gap_days: int = MAX_GAP_DAYS) -> list[pd.Series]:
    """Split `price` into runs of consecutive rows whose calendar-day gap
    from the previous row is <= max_gap_days. See the module DATA CAVEAT
    docstring -- data/ibja_rates.parquet is not daily before 2025-Q2, so an
    unsplit series silently mixes daily and multi-week gaps into the same
    "1-row" horizon. Rows are never dropped, only partitioned; every input
    row belongs to exactly one output segment, in order."""
    if price.empty:
        return []
    gaps = price.index.to_series().diff().dt.days.fillna(0).to_numpy()
    seg_id = (gaps > max_gap_days).cumsum()
    return [price[seg_id == k] for k in sorted(set(seg_id))]


def score_against_ibja(
    raw: dict, ibja_price: pd.Series, horizon: int, max_gap_days: int = MAX_GAP_DAYS
) -> dict:
    """Rescore a proxy-forecast RangeForecastSet (ml.range_forecast.walkforward.
    assemble_forecast_set output, dataset="proxy") against real IBJA prices.

    Keeps only rows whose as_of_date is a genuine IBJA row AND whose h-IBJA-
    ROW-ahead window lies entirely inside one dense segment -- see the module
    DATA CAVEAT docstring. Interval bounds and `scale` are copied UNCHANGED
    from `raw`: the model forecasts from the proxy series regardless of which
    series scores it (the task brief's IBJA protocol); only the row subset,
    `actual_return` (now the real IBJA h-row log return), and `current_price`
    (now the real IBJA price, for honest Rs/gram width reporting) change.

    Returns the same RangeForecastSet dict shape as `raw`, dataset="ibja",
    plus `source_proxy_index`: for each kept row, its index into `raw`'s own
    arrays -- callers that need the matching proxy-arm forecast (e.g. the
    conformal fallback in ml.range_forecast.conformal.apply_ibja_fallback)
    look it up via this list rather than re-deriving it from dates.
    """
    segments = dense_segments(ibja_price, max_gap_days)
    log_ibja = np.log(ibja_price.to_numpy(dtype=float))
    global_pos_by_date: dict[str, int] = {
        ts.strftime("%Y-%m-%d"): i for i, ts in enumerate(ibja_price.index)
    }
    seg_and_local_pos_by_date: dict[str, tuple[int, int]] = {}
    seg_lengths: list[int] = [len(seg) for seg in segments]
    for seg_id, seg in enumerate(segments):
        for local_i, ts in enumerate(seg.index):
            seg_and_local_pos_by_date[ts.strftime("%Y-%m-%d")] = (seg_id, local_i)

    dates: list[str] = []
    positions: list[int] = []
    cur_px: list[float] = []
    actual: list[float] = []
    scale: list[float] = []
    source_proxy_index: list[int] = []
    level_keys = list(raw["levels"])
    level_lo: dict[str, list[float]] = {lv: [] for lv in level_keys}
    level_hi: dict[str, list[float]] = {lv: [] for lv in level_keys}

    for i, date_str in enumerate(raw["as_of_date"]):
        loc = seg_and_local_pos_by_date.get(date_str)
        if loc is None:
            continue  # as_of_date is not a genuine IBJA row
        seg_id, local_i = loc
        if local_i + horizon >= seg_lengths[seg_id]:
            continue  # the h-row window would cross this segment's boundary
        global_i = global_pos_by_date[date_str]
        global_target = global_i + horizon  # segments partition contiguously -> valid

        dates.append(date_str)
        positions.append(global_i)
        cur_px.append(float(ibja_price.iloc[global_i]))
        actual.append(float(log_ibja[global_target] - log_ibja[global_i]))
        scale.append(raw["scale"][i])
        source_proxy_index.append(i)
        for lv in level_keys:
            level_lo[lv].append(raw["levels"][lv]["lo"][i])
            level_hi[lv].append(raw["levels"][lv]["hi"][i])

    return {
        "model": raw["model"],
        "dataset": "ibja",
        "horizon": horizon,
        "as_of_date": dates,
        "as_of_position": positions,
        "current_price": cur_px,
        "actual_return": actual,
        "scale": scale,
        "levels": {lv: {"lo": level_lo[lv], "hi": level_hi[lv]} for lv in level_keys},
        "source_proxy_index": source_proxy_index,
        "ibja_dense_segments": [
            [str(seg.index[0].date()), str(seg.index[-1].date()), len(seg)] for seg in segments
        ],
    }


def log_returns(price: pd.Series) -> pd.Series:
    """Daily log return series (index shrinks by 1 vs `price`)."""
    # numpy's stubs type np.log(<Series>) as ndarray (it can't special-case pandas'
    # __array_ufunc__ override), even though at runtime it returns a Series here.
    return np.log(price / price.shift(1)).dropna()  # type: ignore[attr-defined]


def _t_minus_1_lag(daily_calendar_series: pd.Series, as_of_dates: pd.DatetimeIndex) -> pd.Series:
    """Reindex a forward-filled CALENDAR-daily series onto `as_of_dates`
    using the value as of the PRIOR calendar day (T-1 lag discipline)."""
    shifted_index = as_of_dates - pd.Timedelta(days=1)
    aligned = daily_calendar_series.reindex(shifted_index)
    aligned.index = as_of_dates
    return aligned


def realized_vol_features(
    returns: pd.Series, windows: tuple[int, ...] = (5, 20, 60)
) -> pd.DataFrame:
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


def build_driver_features_for_price(
    price: pd.Series, macro_start: str | None = None, macro_end: str | None = None
) -> pd.DataFrame:
    """Convenience wrapper: driver features aligned on `price.index`,
    computing `recent_returns` from `price` itself (one log_returns call)."""
    returns = log_returns(price)
    return build_driver_features(pd.DatetimeIndex(price.index), returns, macro_start, macro_end)


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
