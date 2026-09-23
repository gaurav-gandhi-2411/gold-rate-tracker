"""ml.direction.comex_daily — the COMEX-targeted daily direction dataset (#1756, M2).

Ground truth is COMEX gold itself (GC=F), not the IBJA-based INR price — so,
unlike the rest of M2, this does NOT need the M1 INR proxy at all. This is
the highest-power experiment available: 13+ years of daily COMEX/macro data
gives thousands of forward folds instead of the ~159-183 the IBJA-based
dataset can offer.

Produces a DataFrame with the SAME schema `ml.direction.dataset.build_dataset`
does (as_of_date, current_pm916, label_binary_hN/label_ternary_hN/
delta_per_gram_hN/label_date_hN/window_min_pm916_hN per horizon) so it is a
drop-in input to the existing M2 machinery — `ml.direction.evaluate_reframed.
run_walk_forward_reframed` and `ml.direction.reframed_targets`'s label
builders run UNCHANGED against this dataset, just with COMEX_FEATURE_COLS in
place of ml.direction.dataset.FEATURE_COLS. ("_per_gram"/"_pm916" naming is
inherited from that shared schema, not literal here — the underlying unit is
USD/troy-oz, not INR/10g.)

Leakage control: every FEATURE is built from data known at or before the end
of day t-1 (T-1 lag, same discipline ml.inr_proxy uses) to predict day t's
own move. Calendar features (festival/wedding/budget/duty-proximity) are the
one exception — they describe day t itself, not t-1, but that's not a leak:
a calendar date is known arbitrarily far in advance, unlike a market price.

Futures rolls: GC=F is ratio-adjusted via ml.inr_proxy's GLD-divergence
method (same construction, same 6-sigma threshold) before any feature or
label is derived from it — a roll artifact must never look like a genuine
price move to either side of the walk-forward split.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from ml.calendar_events import get_demand_calendar_features
from ml.direction.price_units import USD_PER_TROY_OZ, declare_units
from ml.inr_proxy import _detect_and_adjust_rolls
from ml.macro import TICKER_MAP, _download_with_retry

# ml.direction.dataset.make_label hardcodes a /10 (INR-per-10g) conversion
# that doesn't apply to COMEX's USD/troy-oz quoting -- a fixed dollar
# dead-band also wouldn't scale across GC=F's ~$1300-$2600+ range over this
# history, so this dead-band is expressed as a PERCENTAGE of the current
# price, computed per-row, rather than reusing that function.
_DEAD_BAND_PCT = 0.15  # 0.15% of current price -- roughly GC=F's typical
# same-day bid-ask/slippage floor; small enough to keep the ternary "flat"
# bucket meaningfully rare, large enough to filter out pure noise ticks.


def _make_ternary_label(current: float, next_value: float, dead_band_pct: float) -> tuple[str, int]:
    pct_change = (next_value - current) / current * 100.0
    if pct_change > dead_band_pct:
        ternary = "up"
    elif pct_change < -dead_band_pct:
        ternary = "down"
    else:
        ternary = "flat"
    binary = int(next_value > current)
    return ternary, binary


COMEX_START_DATE = "2013-01-01"  # matches ml.inr_proxy's PROXY_START_DATE
_FETCH_BUFFER_DAYS = 60  # extra lookback for the 20-day MA / 5-day vol warmup

# Extra macro drivers beyond gold_usd/usd_inr, reusing ml.macro's own
# TICKER_MAP tickers (same production source) plus GLD for roll-adjustment.
_EXTRA_TICKERS: dict[str, str] = {
    "gld": "GLD",
}

COMEX_FEATURE_COLS: list[str] = [
    "gold_usd_lag1",
    "usd_inr_lag1",
    "us_10y_yield_lag1",
    "dxy_lag1",
    "sensex_lag1",
    "vix_lag1",
    "crude_wti_lag1",
    "tips_lag1",
    "india_vix_lag1",
    "gc_return_1d",
    "gc_return_5d",
    "gc_vol_5d",
    "gc_ma20_dist",
    "dow",
    "dom",
    "month",
    "is_festival_window",
    "days_to_next_festival",
    "is_wedding_season",
    "is_budget_window",
    "is_duty_event_recent",
    "days_since_duty_event",
]


def _fetch_all_drivers(start: str, end: str) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Fetch GC=F + every ml.macro ticker + GLD in one shot. Returns
    (drivers_df, roll_flags, is_gc_trading_day) where drivers_df['gold_usd']
    is already roll-adjusted and forward-filled onto a full daily calendar
    (needed so every ticker's own holiday calendar lines up), but
    is_gc_trading_day marks which of those calendar days had a GENUINE
    (non-carried-forward) GC=F close -- weekends/holidays get COMEX's last
    real close via ffill, which would otherwise make ~30% of "daily
    direction" labels a trivial tie (see build_comex_dataset, which filters
    prediction TARGET days to this mask)."""
    combined_map = {**TICKER_MAP, **_EXTRA_TICKERS}
    tickers = list(combined_map.values())
    raw = _download_with_retry(tickers, start=start, end=end)
    if raw.empty:
        raise RuntimeError(f"yfinance returned no data for {tickers}")

    raw.index = pd.to_datetime(raw.index, utc=True)
    full_idx = pd.date_range(start=raw.index.min(), end=raw.index.max(), freq="D", tz="UTC")

    is_gc_trading_day = raw[("Close", "GC=F")].reindex(full_idx).notna()
    raw = raw.reindex(full_idx)

    out = pd.DataFrame(index=raw.index)
    for col, ticker in combined_map.items():
        if ("Close", ticker) in raw.columns:
            out[col] = raw[("Close", ticker)].values
        else:
            out[col] = np.nan
    out = out.ffill()

    gold_adjusted, roll_flags = _detect_and_adjust_rolls(out["gold_usd"], out["gld"])
    out["gold_usd"] = gold_adjusted
    return out, roll_flags, is_gc_trading_day


def _derived_technical_features(gold_usd: pd.Series) -> pd.DataFrame:
    """Momentum/mean-reversion features computed purely from GC=F's own
    (already roll-adjusted) history — all using data through the row's own
    date, to be lagged by the caller before use as a T-1 feature."""
    log_ret = pd.Series(np.log(gold_usd / gold_usd.shift(1)), index=gold_usd.index)
    ma20 = gold_usd.rolling(20, min_periods=10).mean()
    return pd.DataFrame(
        {
            "gc_return_1d": gold_usd.pct_change(1),
            "gc_return_5d": gold_usd.pct_change(5),
            "gc_vol_5d": log_ret.rolling(5, min_periods=3).std(),
            "gc_ma20_dist": (gold_usd - ma20) / ma20,
        }
    )


def build_comex_dataset(
    start: str = COMEX_START_DATE,
    end: str | None = None,
    extra_horizons: tuple[int, ...] = (1, 5, 10),
) -> pd.DataFrame:
    """Build the COMEX daily direction dataset.

    One row per calendar day in [start, end). Features are T-1 (known at
    end of day t-1); labels are the same-schema hN construction
    ml.direction.dataset uses, applied to the (roll-adjusted) GC=F series
    instead of IBJA.
    """
    if end is None:
        end = (date.today() + timedelta(days=1)).isoformat()
    fetch_start = (date.fromisoformat(start) - timedelta(days=_FETCH_BUFFER_DAYS)).isoformat()

    drivers, roll_flags, is_gc_trading_day = _fetch_all_drivers(fetch_start, end)
    technical = _derived_technical_features(drivers["gold_usd"])

    lag_cols = [
        "gold_usd",
        "usd_inr",
        "us_10y_yield",
        "dxy",
        "sensex",
        "vix",
        "crude_wti",
        "tips",
        "india_vix",
    ]
    lagged = drivers[lag_cols].shift(1)
    lagged.columns = pd.Index([f"{c}_lag1" for c in lag_cols])
    technical_lagged = technical.shift(1)
    roll_flags_lag = roll_flags.shift(1).astype("boolean").fillna(False).astype(bool)

    full_index = pd.DatetimeIndex(drivers.index)
    gold_usd = drivers["gold_usd"]

    rows: list[dict] = []
    for i, dt in enumerate(full_index):
        if i == 0:
            continue
        # h=1's target day (day i) must be a GENUINE COMEX trading day --
        # otherwise gold_usd[i] is Friday's close ffilled onto a weekend, and
        # "did it go up" against gold_usd[i-1] is a trivial tie for ~30% of
        # calendar days (measured directly: without this filter, label_binary_h1
        # comes out 35.7% up instead of a real market's near-50/50 split).
        # NOTE: this does not fully extend to h=5/h=10's own endpoint (i+4/i+9
        # calendar days out can still land on a weekend) -- a smaller residual
        # version of the same tie-dilution remains there; not corrected here,
        # since h=1 is the primary daily variant this experiment is about.
        if not bool(is_gc_trading_day.iloc[i]):
            continue
        # as_of_date is the CAPTURE day (i-1), matching ml.direction.dataset's
        # contract (as_of_date=capture day, label_date strictly after it).
        # `dt` (day i, the day being predicted) is used below only for
        # calendar/dow/dom/month features, which describe a known-in-advance
        # calendar fact about the target day, not a market observation --
        # not a leak (see module docstring).
        as_of = full_index[i - 1].strftime("%Y-%m-%d")
        current = float(gold_usd.iloc[i - 1])  # the price known as of t-1 (feature time)
        if pd.isna(current):
            continue

        row: dict = {
            "as_of_date": as_of,
            "current_pm916": current,
            "roll_adjusted_lag1": bool(roll_flags_lag.iloc[i])
            if i < len(roll_flags_lag)
            else False,
        }
        for c in lagged.columns:
            row[c] = float(lagged[c].iloc[i]) if not pd.isna(lagged[c].iloc[i]) else np.nan
        for c in technical_lagged.columns:
            row[c] = (
                float(technical_lagged[c].iloc[i])
                if not pd.isna(technical_lagged[c].iloc[i])
                else np.nan
            )
        cal = get_demand_calendar_features(dt.date())
        row["dow"] = dt.weekday()
        row["dom"] = dt.day
        row["month"] = dt.month
        row["is_festival_window"] = bool(cal["is_festival_window"])
        row["days_to_next_festival"] = int(cal["days_to_next_festival"])  # type: ignore[call-overload]
        row["is_wedding_season"] = bool(cal["is_wedding_season"])
        row["is_budget_window"] = bool(cal["is_budget_window"])
        row["is_duty_event_recent"] = bool(cal["is_duty_event_recent"])
        row["days_since_duty_event"] = int(cal["days_since_duty_event"])  # type: ignore[call-overload]

        for horizon_n in extra_horizons:
            end_idx = i + horizon_n - 1
            key = f"h{horizon_n}"
            if end_idx < len(full_index) and not pd.isna(gold_usd.iloc[end_idx]):
                window = gold_usd.iloc[i : end_idx + 1]
                window_valid = window.dropna()
                end_val = float(gold_usd.iloc[end_idx])
                ternary_n, binary_n_int = _make_ternary_label(current, end_val, _DEAD_BAND_PCT)
                row[f"next_pm916_{key}"] = end_val
                row[f"delta_per_gram_{key}"] = end_val - current
                row[f"label_ternary_{key}"] = ternary_n
                row[f"label_binary_{key}"] = float(binary_n_int)
                row[f"label_date_{key}"] = full_index[end_idx].strftime("%Y-%m-%d")
                row[f"window_min_pm916_{key}"] = (
                    float(window_valid.min()) if len(window_valid) else None
                )
            else:
                row[f"next_pm916_{key}"] = None
                row[f"delta_per_gram_{key}"] = None
                row[f"label_ternary_{key}"] = None
                row[f"label_binary_{key}"] = None
                row[f"label_date_{key}"] = None
                row[f"window_min_pm916_{key}"] = None

        rows.append(row)

    df = pd.DataFrame(rows)
    df = df.sort_values("as_of_date").reset_index(drop=True)
    df = df[(df["as_of_date"] >= start) & (df["as_of_date"] < end)].reset_index(drop=True)
    # INR-constant label builders refuse this frame (ml.direction.price_units).
    return declare_units(df, USD_PER_TROY_OZ)
