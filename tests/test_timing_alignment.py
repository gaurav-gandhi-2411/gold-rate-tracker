from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from ml.timing_alignment import (
    COMEX_SETTLE,
    FOMC_DECISION,
    GLD_CLOSE,
    TANISHQ_BOARD,
    asof_intraday,
    at_utc,
    first_bar_after,
    ibja_first_fix_after,
    known_at,
    last_bar_known_by,
    staleness_days,
    utc_to_ist_date,
)


def test_comex_settle_is_dst_aware() -> None:
    # 13:30 New York = 17:30 UTC in summer (EDT), 18:30 UTC in winter (EST)
    assert known_at(date(2026, 7, 29), COMEX_SETTLE) == pd.Timestamp("2026-07-29 17:30", tz="UTC")
    assert known_at(date(2026, 1, 28), COMEX_SETTLE) == pd.Timestamp("2026-01-28 18:30", tz="UTC")


def test_fomc_decision_lands_on_next_comex_bar_but_same_gld_bar() -> None:
    d = date(2026, 7, 29)  # Wednesday decision, 14:00 ET
    ev = at_utc(d, FOMC_DECISION)
    bars = [date(2026, 7, 28), d, date(2026, 7, 30), date(2026, 7, 31)]
    assert first_bar_after(ev, bars, COMEX_SETTLE) == date(2026, 7, 30)
    assert first_bar_after(ev, bars, GLD_CLOSE) == d


def test_first_bar_after_skips_missing_days_and_returns_none_past_end() -> None:
    ev = at_utc(date(2026, 7, 31), FOMC_DECISION)  # a Friday
    assert first_bar_after(ev, [date(2026, 7, 31), date(2026, 8, 3)], COMEX_SETTLE) == date(
        2026, 8, 3
    )
    assert first_bar_after(ev, [date(2026, 7, 30)], COMEX_SETTLE) is None


def test_event_exactly_at_publication_is_not_reflected() -> None:
    # strictly-after: a value published at the event instant cannot contain the event
    d = date(2026, 7, 29)
    ev = known_at(d, COMEX_SETTLE)
    assert first_bar_after(ev, [d, date(2026, 7, 30)], COMEX_SETTLE) == date(2026, 7, 30)
    assert last_bar_known_by(ev, [d, date(2026, 7, 30)], COMEX_SETTLE) == d


def test_ibja_first_fix_after_fomc_is_next_ist_morning() -> None:
    ev = at_utc(date(2026, 7, 29), FOMC_DECISION)  # 18:00 UTC = 23:30 IST
    fixes = [(date(2026, 7, 29), "am"), (date(2026, 7, 29), "pm"), (date(2026, 7, 30), "pm")]
    # the 30th has no AM row: the first reflecting publication is that day's PM
    assert ibja_first_fix_after(ev, fixes) == (date(2026, 7, 30), "pm")
    fixes.append((date(2026, 7, 30), "am"))
    assert ibja_first_fix_after(ev, fixes) == (date(2026, 7, 30), "am")
    assert ibja_first_fix_after(ev, [(date(2026, 7, 29), "pm")]) is None


def test_asof_intraday_uses_bar_end_not_start() -> None:
    idx = pd.date_range("2026-07-29 10:00", periods=3, freq="1h", tz="UTC")
    bars = pd.Series([1.0, 2.0, 3.0], index=idx)
    one_h = pd.Timedelta(hours=1)
    # at 11:30 only the 10:00 bar has ended
    assert asof_intraday(bars, pd.Timestamp("2026-07-29 11:30", tz="UTC"), one_h) == 1.0
    assert asof_intraday(bars, pd.Timestamp("2026-07-29 12:00", tz="UTC"), one_h) == 2.0
    assert np.isnan(asof_intraday(bars, pd.Timestamp("2026-07-29 10:59", tz="UTC"), one_h))
    ny = bars.tz_convert("America/New_York")
    assert asof_intraday(ny, pd.Timestamp("2026-07-29 11:30", tz="UTC"), one_h) == 1.0


def test_staleness_reveals_forward_fill() -> None:
    genuine = [date(2026, 9, 18)]  # Friday settle; the Saturday/Sunday rows are ffilled
    assert staleness_days(date(2026, 9, 20), genuine) == 2
    assert staleness_days(date(2026, 9, 17), genuine) is None


def test_ist_date_of_late_utc_scrape_and_board_clock() -> None:
    assert utc_to_ist_date(pd.Timestamp("2026-09-24T19:00Z")) == date(2026, 9, 25)
    assert utc_to_ist_date(pd.Timestamp("2026-09-24 17:00")) == date(2026, 9, 24)
    assert known_at(date(2026, 9, 25), TANISHQ_BOARD) == pd.Timestamp("2026-09-25 05:30", tz="UTC")
