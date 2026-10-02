"""ml.known_at -- the ONE place that says when each source's value became KNOWN (ADR 061).

Every function returns a tz-aware UTC pd.Timestamp: the instant the value was published or
captured, never its value date. ml.leak_guard compares these against a prediction moment.

Timestamp conventions (VERIFIED = measured, ASSUMED = stated convention, not measured):

  IBJA AM / PM       value date D (IST). ASSUMED published 12:00 / 17:00 Asia/Kolkata on D
                     (06:30 / 11:30 UTC) -- the repo-wide convention (ml/sources/ibja.py stamps
                     PM observed_at 11:30 UTC; ml/markup.py uses 12:00/17:00). Publish times are
                     not independently verified (ADR 058). When the row's `fetched_at` (when this
                     repo actually fetched it) is LATER, pass it: known_at = max(publish,
                     fetched_at). Omit it only when replaying PUBLIC availability for history the
                     repo backfilled long after publication -- and say so at the call site.
  Retailers          (Tanishq in data/prices.json `timestamp`, GRT / Malabar / Kalyan / IBJA
                     rows in data/fusion_snapshots.parquet `capture_utc`): the capture instant.
                     A retailer's own `observed_at` is earlier or equal and is not what the
                     repo knew; the capture is.
  COMEX GC=F daily   bar dated D = the 13:30 America/New_York settlement on D (17:30 UTC summer,
                     18:30 UTC winter). VERIFIED against 5-minute bars in ADR 058 (#2051).
  USD/INR INR=X      the daily bar is a single snapshot with no exact clock (ADR 058: closest to
    daily            00:00-02:00 UTC of D, not pinned down). Treated as known only at 23:59 UTC
                     of D -- the conservative end, so the guard can over-block, never under-block.
  Hourly bars        (any Yahoo intraday series) labelled by bar START: known at start + 1 h.
  Other macro daily  (ml/macro.py TICKER_MAP) bar dated D = that exchange's close on D. ASSUMED
                     (published exchange hours, not measured here): ^TNX 15:00 America/Chicago,
                     DX-Y.NYB 17:00 America/New_York, ^VIX 16:15 America/New_York, CL=F 14:30
                     America/New_York (NYMEX settlement), TIP 16:00 America/New_York, ^BSESN and
                     ^INDIAVIX 15:30 Asia/Kolkata. Vendor publication lag after the close is
                     ignored (minutes); a later close would only make the guard stricter.
  Feature-store row  data/feature_store/snapshots.parquet. `live_pit` rows: a macro value is the
                     intraday quote seen at `capture_utc`, so known_at = capture_utc; an IBJA
                     value is a discrete publication, so known_at = max(capture_utc, publish
                     clock of its `_asof_date`) -- 38 rows repaired by #621 carry an IBJA PM
                     published after their capture_utc (ADR 061 finding F3). Backfill rows
                     (`backfill_yfinance`) were reconstructed later: their capture_utc is the
                     backfill run, so each field uses its source clock on its `_asof_date`.

ADR 058's ml/timing_alignment.py (#2051, unmerged at the time of writing) defines the same Clock
shape and the same COMEX / IBJA / INR=X clocks. `Clock` and `at_utc` here match its signatures
so that module can replace them by import once it lands (tests/test_known_at.py asserts the
shared clocks agree whenever ml.timing_alignment is importable).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pandas as pd

from ml.leak_guard import NaiveTimestampError, to_utc

UTC = ZoneInfo("UTC")
IST = "Asia/Kolkata"


@dataclass(frozen=True)
class Clock:
    """A daily series' publication time: wall-clock `at` in time zone `tz` on the label date."""

    name: str
    tz: str
    at: time


IBJA_AM = Clock("ibja_am", IST, time(12, 0))
IBJA_PM = Clock("ibja_pm", IST, time(17, 0))
COMEX_SETTLE = Clock("comex_settle", "America/New_York", time(13, 30))
USDINR_SNAPSHOT_CONSERVATIVE = Clock("usdinr_yahoo_conservative", "UTC", time(23, 59))
HOURLY_BAR = pd.Timedelta(hours=1)

# Keyed by ml/macro.py TICKER_MAP column names. tests/test_known_at.py fails if a macro column
# is added there without a clock here -- a new series cannot enter the store with no known_at.
MACRO_DAILY_CLOCKS: dict[str, Clock] = {
    "gold_usd": COMEX_SETTLE,
    "usd_inr": USDINR_SNAPSHOT_CONSERVATIVE,
    "us_10y_yield": Clock("cboe_tnx_close", "America/Chicago", time(15, 0)),
    "dxy": Clock("ice_dxy_close", "America/New_York", time(17, 0)),
    "sensex": Clock("bse_close", IST, time(15, 30)),
    "vix": Clock("cboe_vix_close", "America/New_York", time(16, 15)),
    "crude_wti": Clock("nymex_wti_settle", "America/New_York", time(14, 30)),
    "tips": Clock("nyse_arca_close", "America/New_York", time(16, 0)),
    "india_vix": Clock("nse_close", IST, time(15, 30)),
}
IBJA_FIELDS: dict[str, Clock] = {"ibja_am_916": IBJA_AM, "ibja_pm_916": IBJA_PM}
LIVE_SNAPSHOT_SOURCE = "live_pit"


def _as_date(d: Any) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    ts = pd.Timestamp(d)
    if pd.isna(ts):
        raise NaiveTimestampError(f"value date {d!r} is missing")
    return ts.date()


def at_utc(d: Any, clock: Clock) -> pd.Timestamp:
    """The UTC instant `clock` fires on local calendar date `d` (DST-aware)."""
    local = datetime.combine(_as_date(d), clock.at, tzinfo=ZoneInfo(clock.tz))
    return pd.Timestamp(local.astimezone(UTC))


def ibja_known_at(
    value_date: Any, fix: Literal["am", "pm"], fetched_at: Any = None
) -> pd.Timestamp:
    """IBJA AM/PM for value date D: the assumed publish instant, or `fetched_at` if later."""
    clock = {"am": IBJA_AM, "pm": IBJA_PM}[fix]
    published = at_utc(value_date, clock)
    if fetched_at is None:
        return published
    return max(published, to_utc(fetched_at, what=f"ibja {fix} fetched_at"))


def comex_daily_known_at(bar_date: Any) -> pd.Timestamp:
    """GC=F daily bar dated D: the 13:30 America/New_York settlement on D."""
    return at_utc(bar_date, COMEX_SETTLE)


def usdinr_daily_known_at(bar_date: Any) -> pd.Timestamp:
    """INR=X daily bar dated D: 23:59 UTC on D (conservative, see module docstring)."""
    return at_utc(bar_date, USDINR_SNAPSHOT_CONSERVATIVE)


def macro_daily_known_at(column: str, bar_date: Any) -> pd.Timestamp:
    """A daily bar of a ml/macro.py series (by column name) dated D: its close on D."""
    if column not in MACRO_DAILY_CLOCKS:
        raise KeyError(f"no known_at clock for macro column {column!r}; add one to known_at.py")
    return at_utc(bar_date, MACRO_DAILY_CLOCKS[column])


def hourly_bar_known_at(bar_start: Any) -> pd.Timestamp:
    """An intraday 1-hour bar labelled by its start: known when it ends (start + 1 h)."""
    return to_utc(bar_start, what="hourly bar start") + HOURLY_BAR


def capture_known_at(capture_ts: Any) -> pd.Timestamp:
    """A retailer / fusion / feature-store reading: the instant this repo captured it."""
    return to_utc(capture_ts, what="capture timestamp")


def ist_day_start(d: Any) -> pd.Timestamp:
    """00:00 Asia/Kolkata on IST date D, in UTC (D-1 18:30 UTC)."""
    return pd.Timestamp(datetime.combine(_as_date(d), time(0, 0), tzinfo=ZoneInfo(IST))).tz_convert(
        "UTC"
    )


def ist_day_end(d: Any) -> pd.Timestamp:
    """The first instant AFTER IST date D (00:00 IST on D+1). known_at < ist_day_end(D) means
    'known during or before IST day D'."""
    return ist_day_start(_as_date(d)) + pd.Timedelta(days=1)


def snapshot_field_known_at(row: Mapping[str, Any] | pd.Series, column: str) -> pd.Timestamp | None:
    """When a feature-store row's value in `column` was known; None if the value is null.

    Macro and IBJA columns carry `<column>_asof_date` (ml/macro.py, ml/feature_store.py). See the
    module docstring for the live vs backfill rule. Raises KeyError for a column with no rule."""
    if column not in IBJA_FIELDS and column not in MACRO_DAILY_CLOCKS and column != "tanishq_22k":
        raise KeyError(f"no known_at rule for feature-store column {column!r}")
    value = row.get(column)
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    live = row.get("source") == LIVE_SNAPSHOT_SOURCE
    asof = row.get(f"{column}_asof_date")
    if column in IBJA_FIELDS:
        published = at_utc(asof, IBJA_FIELDS[column])
        return max(published, capture_known_at(row.get("capture_utc"))) if live else published
    if column in MACRO_DAILY_CLOCKS:
        if live:
            return capture_known_at(row.get("capture_utc"))
        return macro_daily_known_at(column, asof)
    if live:  # tanishq_22k
        return capture_known_at(row.get("capture_utc"))
    raise KeyError("tanishq_22k on a non-live row has no capture instant")
