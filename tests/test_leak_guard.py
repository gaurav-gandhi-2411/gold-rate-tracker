"""Unit tests for ml.leak_guard and ml.known_at (ADR 061)."""

from __future__ import annotations

import importlib.util
from datetime import datetime
from typing import ClassVar

import pandas as pd
import pytest
from ml import known_at as ka
from ml.leak_guard import (
    KnownInput,
    LeakGuard,
    NaiveTimestampError,
    TimingLeakError,
    assert_known_before,
    filter_known_before,
    find_leaks,
    to_utc,
)

T = pd.Timestamp("2026-07-21T08:22:07Z")


def _inp(known_at: object, name: str = "x") -> KnownInput:
    return KnownInput(name, "test", known_at)  # type: ignore[arg-type]


class TestStrictBoundary:
    def test_known_exactly_at_the_moment_is_blocked(self) -> None:
        with pytest.raises(TimingLeakError) as exc:
            assert_known_before(T, [_inp(T)])
        assert exc.value.violations[0].late_by_s == 0.0

    def test_one_second_earlier_is_allowed(self) -> None:
        assert_known_before(T, [_inp(T - pd.Timedelta(seconds=1))])

    def test_filter_keeps_only_strictly_earlier_inputs_in_order(self) -> None:
        early_a = _inp(T - pd.Timedelta(hours=2), "a")
        late = _inp(T + pd.Timedelta(hours=1), "late")
        equal = _inp(T, "equal")
        early_b = _inp(T - pd.Timedelta(seconds=1), "b")
        kept = filter_known_before(T, [early_a, late, equal, early_b])
        assert [x.name for x in kept] == ["a", "b"]
        assert_known_before(T, kept)

    def test_error_names_every_leaky_input(self) -> None:
        with pytest.raises(TimingLeakError, match=r"2 input\(s\).*grt.*malabar"):
            assert_known_before(
                T,
                [_inp("2026-07-21T19:48:27Z", "grt"), _inp("2026-07-21T19:48:27Z", "malabar")],
                context="kalman 2026-07-21",
            )

    def test_equivalent_instants_in_other_zones_compare_as_utc(self) -> None:
        ist = pd.Timestamp("2026-07-21 13:52:07", tz="Asia/Kolkata")  # == T
        assert find_leaks(T, [_inp(ist)])
        assert not find_leaks(T, [_inp(ist - pd.Timedelta(seconds=1))])


class TestFailClosed:
    @pytest.mark.parametrize(
        "bad",
        [
            "2026-07-21T08:22:07",
            pd.Timestamp("2026-07-21 08:22:07"),
            datetime(2026, 7, 21, 8, 22, 7),
            None,
            pd.NaT,
            float("nan"),
            "not a time",
            20260721,
        ],
    )
    def test_naive_or_missing_known_at_is_rejected(self, bad: object) -> None:
        with pytest.raises(NaiveTimestampError):
            _inp(bad)

    def test_naive_prediction_moment_is_rejected(self) -> None:
        with pytest.raises(NaiveTimestampError):
            assert_known_before(pd.Timestamp("2026-07-21 08:22:07"), [])
        with pytest.raises(NaiveTimestampError):
            filter_known_before("2026-07-21T08:22:07", [])

    def test_to_utc_normalises_offsets(self) -> None:
        assert to_utc("2026-07-21T13:52:07+05:30") == T

    def test_naive_is_rejected_even_in_report_mode(self) -> None:
        guard = LeakGuard("g", mode="report")
        with pytest.raises(NaiveTimestampError):
            guard.check(pd.Timestamp("2026-07-21"), [])


class TestLeakGuardModes:
    def test_raise_mode_refuses_the_first_leaky_prediction(self) -> None:
        guard = LeakGuard("g", mode="raise")
        guard.check(T, [_inp(T - pd.Timedelta(minutes=1))])
        with pytest.raises(TimingLeakError, match=r"^g fold 2"):
            guard.check(T, [_inp(T)], context="fold 2")

    def test_report_mode_records_without_raising(self) -> None:
        guard = LeakGuard("g", mode="report")
        guard.check(T, [_inp(T), _inp(T + pd.Timedelta(hours=1), "y")], context="d1")
        guard.check(T, [_inp(T - pd.Timedelta(hours=1))], context="d2")
        s = guard.summary()
        assert (s["n_checks"], s["n_violations"], s["n_checks_with_violation"]) == (2, 2, 1)
        assert s["violations_by_source"] == {"test": 2}
        assert s["max_late_by_s"] == 3600.0
        assert s["examples"][0]["context"] == "d1"

    def test_unknown_mode_is_an_error(self) -> None:
        with pytest.raises(ValueError, match="mode"):
            LeakGuard("g", mode="warn")  # type: ignore[arg-type]


class TestSourceConventions:
    def test_ibja_pm_used_at_1659_ist_is_blocked(self) -> None:
        pm = KnownInput("pm", "ibja_pm", ka.ibja_known_at("2026-07-21", "pm"))
        at_1659 = pd.Timestamp("2026-07-21 16:59", tz="Asia/Kolkata")
        with pytest.raises(TimingLeakError):
            assert_known_before(at_1659, [pm])
        assert_known_before(at_1659 + pd.Timedelta(minutes=1, seconds=1), [pm])

    def test_ibja_am_is_known_at_1200_ist(self) -> None:
        assert ka.ibja_known_at("2026-07-21", "am") == pd.Timestamp("2026-07-21T06:30Z")

    def test_ibja_uses_fetched_at_only_when_later(self) -> None:
        later = "2026-07-21T13:40:14.666998+00:00"
        assert ka.ibja_known_at("2026-07-21", "pm", later) == pd.Timestamp(later)
        earlier = "2026-07-21T11:00:00+00:00"
        assert ka.ibja_known_at("2026-07-21", "pm", earlier) == pd.Timestamp("2026-07-21T11:30Z")
        with pytest.raises(NaiveTimestampError):
            ka.ibja_known_at("2026-07-21", "pm", "2026-07-21T13:40:14")

    def test_gc_daily_bar_used_before_1330_et_is_blocked(self) -> None:
        bar = KnownInput("gc", "comex", ka.comex_daily_known_at("2026-07-20"))
        before = pd.Timestamp("2026-07-20 13:29:59", tz="America/New_York")
        with pytest.raises(TimingLeakError):
            assert_known_before(before, [bar])
        assert_known_before(before + pd.Timedelta(seconds=2), [bar])

    def test_gc_settlement_follows_us_daylight_saving(self) -> None:
        assert ka.comex_daily_known_at("2026-07-20") == pd.Timestamp("2026-07-20T17:30Z")
        assert ka.comex_daily_known_at("2026-01-20") == pd.Timestamp("2026-01-20T18:30Z")

    def test_usdinr_daily_is_conservatively_end_of_utc_day(self) -> None:
        assert ka.usdinr_daily_known_at("2026-07-20") == pd.Timestamp("2026-07-20T23:59Z")

    def test_hourly_bar_is_known_one_hour_after_its_start(self) -> None:
        assert ka.hourly_bar_known_at("2026-07-20T13:00Z") == pd.Timestamp("2026-07-20T14:00Z")
        with pytest.raises(NaiveTimestampError):
            ka.hourly_bar_known_at("2026-07-20T13:00")

    def test_india_vix_close_is_1530_ist(self) -> None:
        assert ka.macro_daily_known_at("india_vix", "2026-09-23") == pd.Timestamp(
            "2026-09-23T10:00Z"
        )

    def test_every_macro_series_has_a_clock(self) -> None:
        """A new ml/macro.py series must get a known_at clock before it can be used."""
        from ml.macro import TICKER_MAP

        assert set(TICKER_MAP) <= set(ka.MACRO_DAILY_CLOCKS)
        with pytest.raises(KeyError, match="no known_at clock"):
            ka.macro_daily_known_at("new_series", "2026-09-23")

    def test_ist_day_bounds(self) -> None:
        assert ka.ist_day_start("2026-09-25") == pd.Timestamp("2026-09-24T18:30Z")
        assert ka.ist_day_end("2026-09-25") == pd.Timestamp("2026-09-25T18:30Z")

    @pytest.mark.skipif(
        importlib.util.find_spec("ml.timing_alignment") is None,
        reason="ADR 058's ml/timing_alignment.py (#2051) is not on this branch",
    )
    def test_clocks_agree_with_adr058_timing_alignment(self) -> None:
        ta = importlib.import_module("ml.timing_alignment")
        for mine, theirs in (
            (ka.COMEX_SETTLE, ta.COMEX_SETTLE),
            (ka.IBJA_AM, ta.IBJA_AM),
            (ka.IBJA_PM, ta.IBJA_PM),
            (ka.USDINR_SNAPSHOT_CONSERVATIVE, ta.USDINR_SNAPSHOT_CONSERVATIVE),
        ):
            assert (mine.tz, mine.at) == (theirs.tz, theirs.at)


class TestSnapshotFields:
    LIVE: ClassVar[dict[str, object]] = {
        "source": "live_pit",
        "capture_utc": "2026-06-08T20:11:30Z",
        "gold_usd": 4000.0,
        "gold_usd_asof_date": "2026-06-08",
        "ibja_pm_916": 139707.0,
        "ibja_pm_916_asof_date": "2026-06-09",
        "tanishq_22k": 14000.0,
    }

    def test_live_macro_is_the_intraday_quote_seen_at_capture(self) -> None:
        assert ka.snapshot_field_known_at(self.LIVE, "gold_usd") == pd.Timestamp(
            "2026-06-08T20:11:30Z"
        )

    def test_live_ibja_is_never_earlier_than_its_publication(self) -> None:
        """#621-repaired rows: the stored PM was published ~15 h after capture_utc."""
        assert ka.snapshot_field_known_at(self.LIVE, "ibja_pm_916") == pd.Timestamp(
            "2026-06-09T11:30Z"
        )

    def test_backfill_macro_uses_the_bar_close_not_the_run_time(self) -> None:
        row = {**self.LIVE, "source": "backfill_yfinance", "capture_utc": "2026-06-07T22:38:07Z"}
        assert ka.snapshot_field_known_at(row, "gold_usd") == pd.Timestamp("2026-06-08T17:30Z")
        with pytest.raises(KeyError):
            ka.snapshot_field_known_at(row, "tanishq_22k")

    def test_null_value_has_no_known_at_and_unknown_column_is_an_error(self) -> None:
        assert ka.snapshot_field_known_at({**self.LIVE, "gold_usd": None}, "gold_usd") is None
        with pytest.raises(KeyError, match="no known_at rule"):
            ka.snapshot_field_known_at(self.LIVE, "dow")

    def test_live_row_without_capture_time_fails_closed(self) -> None:
        with pytest.raises(NaiveTimestampError):
            ka.snapshot_field_known_at({**self.LIVE, "capture_utc": None}, "gold_usd")
