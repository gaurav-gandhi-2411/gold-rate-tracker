"""ml.timing_alignment -- when was each daily value actually KNOWN? (ADR 058)

Every series in this repo carries a calendar-date label, but the moment its value becomes public
differs by up to a day between sources. Mixing them on the label date alone attributes a move to
the wrong day or uses a value before it existed. These helpers turn a (series, label date) into
the UTC instant the value became known, and answer the two questions an event study or a join
needs: "which is the first observation that could reflect an event at time T?" and "what is the
latest observation known by time T?".

Clock conventions (evidence in docs/adr/058-timing-audit.md):
  COMEX_SETTLE   Yahoo GC=F daily Close = the 13:30 America/New_York settlement (VERIFIED against
                 5-minute bars: median gap 1.2 bp at 13:30 vs 42.6 bp to the last trade of the day).
  GLD_CLOSE      Yahoo GLD daily Close = the 16:00 America/New_York NYSE close (VERIFIED, 0.7 bp).
  IBJA_AM/PM     IBJA's two publications, ~12:00 and ~17:00 Asia/Kolkata (repo convention:
                 ml/sources/ibja.py uses 11:30 UTC for PM; ml/markup.py uses the same boundaries).
  TANISHQ_BOARD  Tanishq's board changes are first seen 11:00-13:00 IST (measured on prices.json);
                 a board labelled with IST date D is taken as known from 11:00 IST on D.
  INR=X          Yahoo's INR=X daily bar is a single snapshot (Close == Open on 91% of 2022-2026
                 days); the 1-hour comparison weakly places it near 00:00-02:00 UTC of the bar date.
                 No exact clock is claimed; USDINR_SNAPSHOT_CONSERVATIVE treats it as known only at
                 23:59 UTC of the bar date.

Pure functions, no I/O. Nothing in the live pipeline imports this module.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

UTC = ZoneInfo("UTC")


@dataclass(frozen=True)
class Clock:
    """A daily series' publication time: wall-clock `at` in time zone `tz` on the label date."""

    name: str
    tz: str
    at: time


COMEX_SETTLE = Clock("comex_settle", "America/New_York", time(13, 30))
GLD_CLOSE = Clock("gld_close", "America/New_York", time(16, 0))
IBJA_AM = Clock("ibja_am", "Asia/Kolkata", time(12, 0))
IBJA_PM = Clock("ibja_pm", "Asia/Kolkata", time(17, 0))
TANISHQ_BOARD = Clock("tanishq_board", "Asia/Kolkata", time(11, 0))
USDINR_SNAPSHOT_CONSERVATIVE = Clock("usdinr_yahoo_conservative", "UTC", time(23, 59))
FOMC_DECISION = Clock("fomc_decision", "America/New_York", time(14, 0))


def at_utc(d: date, clock: Clock) -> pd.Timestamp:
    """The UTC instant `clock` fires on local calendar date `d` (DST-aware)."""
    local = datetime.combine(d, clock.at, tzinfo=ZoneInfo(clock.tz))
    return pd.Timestamp(local.astimezone(UTC))


def known_at(label_date: date, clock: Clock) -> pd.Timestamp:
    """When a daily value labelled `label_date` became public (alias of at_utc, for intent)."""
    return at_utc(label_date, clock)


def first_bar_after(
    event_utc: pd.Timestamp, bar_dates: Iterable[date], clock: Clock
) -> date | None:
    """The first bar whose value was published STRICTLY after `event_utc` -- the first
    observation that could reflect the event. None if no such bar exists."""
    ev = pd.Timestamp(event_utc)
    for d in sorted(bar_dates):
        if known_at(d, clock) > ev:
            return d
    return None


def last_bar_known_by(t_utc: pd.Timestamp, bar_dates: Iterable[date], clock: Clock) -> date | None:
    """The latest bar whose value was published at or before `t_utc`. None if none was."""
    t = pd.Timestamp(t_utc)
    best: date | None = None
    for d in sorted(bar_dates):
        if known_at(d, clock) <= t:
            best = d
        else:
            break
    return best


def ibja_first_fix_after(
    event_utc: pd.Timestamp, fixes: Iterable[tuple[date, str]]
) -> tuple[date, str] | None:
    """First IBJA publication (date, "am"|"pm") strictly after the event. `fixes` lists the
    publications that exist (a missing AM or PM is simply absent)."""
    ev = pd.Timestamp(event_utc)
    clocks = {"am": IBJA_AM, "pm": IBJA_PM}
    ordered = sorted(fixes, key=lambda f: known_at(f[0], clocks[f[1]]))
    for d, fix in ordered:
        if known_at(d, clocks[fix]) > ev:
            return d, fix
    return None


def asof_intraday(bars: pd.Series, t_utc: pd.Timestamp, bar_length: pd.Timedelta) -> float:
    """Price known at `t_utc` from intraday bars labelled by their START time (Yahoo convention):
    the Close of the last bar that ENDED at or before t. NaN if none did."""
    if bars.empty:
        return float("nan")
    idx = pd.DatetimeIndex(bars.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    ends = idx + bar_length
    t = pd.Timestamp(t_utc)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    pos = int(ends.searchsorted(t, side="right")) - 1
    if pos < 0:
        return float("nan")
    return float(bars.to_numpy()[pos])


def staleness_days(asof_label: date, genuine_dates: Iterable[date]) -> int | None:
    """Days between an as-of label and the last GENUINE observation on or before it -- what a
    forward-filled frame hides. None if no genuine observation precedes the label."""
    prior = [d for d in genuine_dates if d <= asof_label]
    if not prior:
        return None
    return (asof_label - max(prior)).days


def utc_to_ist_date(ts: pd.Timestamp) -> date:
    """IST calendar date of a UTC instant (a 19:00Z scrape is the next IST day)."""
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    return t.tz_convert("Asia/Kolkata").date()
