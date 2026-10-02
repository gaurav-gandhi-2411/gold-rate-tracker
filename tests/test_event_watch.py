"""Tests for ml.event_watch (F4 "Event watch", ADR 050) -- all synthetic,
no network calls."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from ml.event_watch import (
    BONFERRONI_THRESHOLD,
    block_bootstrap_diff_test,
    bonferroni_significant,
    bootstrap_median_ci,
    build_sentence,
    event_day_log_return,
    events_of_type,
    exclusion_window_dates,
    load_events_calendar,
    normal_day_log_returns,
    passes_success_gate,
)


def _daily_series(start: str, n: int, values: list[float]) -> pd.Series:
    idx = pd.date_range(start, periods=n, freq="D")
    return pd.Series(values, index=idx)


# ---------------------------------------------------------------------------
# events_of_type / exclusion_window_dates
# ---------------------------------------------------------------------------


class TestCalendarHelpers:
    def test_events_of_type_filters_and_sorts(self) -> None:
        rows = [
            {"type": "fomc_decision", "date": "2020-06-10"},
            {"type": "us_cpi", "date": "2020-01-01"},
            {"type": "fomc_decision", "date": "2020-01-29"},
        ]
        assert events_of_type(rows, "fomc_decision") == ["2020-01-29", "2020-06-10"]

    def test_exclusion_window_covers_plus_minus_one_day(self) -> None:
        rows = [{"type": "fomc_decision", "date": "2020-06-10"}]
        excluded = exclusion_window_dates(rows, window_days=1)
        assert excluded == {"2020-06-09", "2020-06-10", "2020-06-11"}

    def test_exclusion_window_is_union_across_all_types(self) -> None:
        rows = [
            {"type": "fomc_decision", "date": "2020-06-10"},
            {"type": "us_cpi", "date": "2020-09-01"},
        ]
        excluded = exclusion_window_dates(rows, window_days=1)
        assert "2020-08-31" in excluded
        assert "2020-06-11" in excluded

    def test_load_events_calendar_only_returns_verified_rows(self, tmp_path) -> None:
        import json

        p = tmp_path / "cal.json"
        p.write_text(
            json.dumps(
                [
                    {"type": "fomc_decision", "date": "2020-01-01", "verified": True},
                    {"type": "fomc_decision", "date": "2020-02-01", "verified": False},
                ]
            ),
            encoding="utf-8",
        )
        rows = load_events_calendar(p)
        assert len(rows) == 1
        assert rows[0]["date"] == "2020-01-01"


# ---------------------------------------------------------------------------
# event_day_log_return / normal_day_log_returns
# ---------------------------------------------------------------------------


class TestEventDayLogReturn:
    def test_basic_move_computed_against_prior_genuine_day(self) -> None:
        series = _daily_series("2020-01-01", 5, [100.0, 100.0, 110.0, 110.0, 110.0])
        is_genuine = pd.Series([True, False, True, False, False], index=series.index)
        result = event_day_log_return("2020-01-03", series, is_genuine)
        assert result is not None
        move, event_day, prior_day = result
        assert event_day == "2020-01-03"
        assert prior_day == "2020-01-01"
        assert move == pytest.approx(abs(np.log(110.0 / 100.0)))

    def test_rolls_forward_when_event_date_not_genuine(self) -> None:
        """A weekend/holiday event date advances to the next genuine day --
        ADR 050 'the first trading-day close that could reflect the
        release'."""
        series = _daily_series("2020-01-01", 5, [100.0, 105.0, 108.0, 108.0, 112.0])
        is_genuine = pd.Series([True, False, True, False, True], index=series.index)
        # 2020-01-02 is not genuine; should roll forward to 2020-01-03.
        result = event_day_log_return("2020-01-02", series, is_genuine)
        assert result is not None
        _move, event_day, prior_day = result
        assert event_day == "2020-01-03"
        assert prior_day == "2020-01-01"

    def test_returns_none_when_no_prior_genuine_day_exists(self) -> None:
        series = _daily_series("2020-01-01", 3, [100.0, 105.0, 108.0])
        is_genuine = pd.Series([True, True, True], index=series.index)
        result = event_day_log_return("2020-01-01", series, is_genuine)
        assert result is None

    def test_returns_none_when_event_date_beyond_series_end(self) -> None:
        series = _daily_series("2020-01-01", 3, [100.0, 105.0, 108.0])
        is_genuine = pd.Series([True, True, True], index=series.index)
        result = event_day_log_return("2020-06-01", series, is_genuine)
        assert result is None


class TestNormalDayLogReturns:
    def test_excludes_dates_in_the_exclusion_set(self) -> None:
        series = _daily_series("2020-01-01", 6, [100.0, 101.0, 150.0, 103.0, 104.0, 105.0])
        is_genuine = pd.Series(True, index=series.index)
        excluded = {"2020-01-03"}  # the day with the outlier 150.0 move
        normal = normal_day_log_returns(series, is_genuine, excluded)
        assert "2020-01-03" not in {d.date().isoformat() for d in normal.index}

    def test_only_genuine_days_included(self) -> None:
        series = _daily_series("2020-01-01", 4, [100.0, 101.0, 101.0, 103.0])
        is_genuine = pd.Series([True, True, False, True], index=series.index)
        normal = normal_day_log_returns(series, is_genuine, excluded_dates=set())
        # day 3 (index 2) is not genuine and must not appear
        assert len(normal) == 2  # day 2 (vs day1) and day 4 (vs day2)


# ---------------------------------------------------------------------------
# block_bootstrap_diff_test / bonferroni_significant / passes_success_gate
# ---------------------------------------------------------------------------


class TestBlockBootstrapDiffTest:
    def test_clearly_larger_event_moves_are_significant(self) -> None:
        rng = np.random.default_rng(42)
        event_moves = rng.normal(loc=0.03, scale=0.005, size=40).clip(min=0.001)
        normal_moves = rng.normal(loc=0.01, scale=0.003, size=500).clip(min=0.0001)
        result = block_bootstrap_diff_test(event_moves, normal_moves)
        assert result["p_one_sided"] < 0.01
        assert result["ratio_of_means"] > 1.2
        assert bonferroni_significant(result["p_one_sided"])
        assert passes_success_gate(result)

    def test_identical_distributions_are_not_significant(self) -> None:
        rng = np.random.default_rng(7)
        event_moves = rng.normal(loc=0.01, scale=0.003, size=40).clip(min=0.0001)
        normal_moves = rng.normal(loc=0.01, scale=0.003, size=500).clip(min=0.0001)
        result = block_bootstrap_diff_test(event_moves, normal_moves)
        assert result["p_one_sided"] > BONFERRONI_THRESHOLD
        assert not passes_success_gate(result)

    def test_empty_event_moves_returns_none_fields(self) -> None:
        result = block_bootstrap_diff_test(np.array([]), np.array([1.0, 2.0]))
        assert result["p_one_sided"] is None
        assert not passes_success_gate(result)

    def test_ratio_below_threshold_fails_gate_even_if_significant(self) -> None:
        """Statistically significant but a small ratio must still fail the
        gate -- ADR 050's success rule is AND, not OR."""
        rng = np.random.default_rng(3)
        event_moves = rng.normal(loc=0.0105, scale=0.0005, size=2000).clip(min=0.0001)
        normal_moves = rng.normal(loc=0.01, scale=0.003, size=2000).clip(min=0.0001)
        result = block_bootstrap_diff_test(event_moves, normal_moves)
        assert result["ratio_of_means"] < 1.2
        # even if this particular draw happens to be significant, the gate must still fail
        assert not passes_success_gate(result)


class TestBonferroniSignificant:
    def test_none_pvalue_is_not_significant(self) -> None:
        assert not bonferroni_significant(None)

    def test_threshold_is_alpha_over_m(self) -> None:
        assert pytest.approx(0.0125) == BONFERRONI_THRESHOLD
        assert bonferroni_significant(0.0125)
        assert not bonferroni_significant(0.0126)


# ---------------------------------------------------------------------------
# bootstrap_median_ci
# ---------------------------------------------------------------------------


class TestBootstrapMedianCi:
    def test_ci_brackets_the_true_median_on_tight_distribution(self) -> None:
        rng = np.random.default_rng(1)
        values = rng.normal(loc=0.02, scale=0.001, size=200)
        median, lo, hi = bootstrap_median_ci(values)
        assert lo <= median <= hi
        assert lo == pytest.approx(0.02, abs=0.01)

    def test_empty_input_returns_nan(self) -> None:
        median, lo, hi = bootstrap_median_ci(np.array([]))
        assert np.isnan(median)
        assert np.isnan(lo)
        assert np.isnan(hi)


# ---------------------------------------------------------------------------
# build_sentence
# ---------------------------------------------------------------------------


class TestBuildSentence:
    def test_sentence_contains_rounded_figure_and_type_label(self) -> None:
        sentence = build_sentence("india_budget", "2026-02-01", 42.7)
        assert "Rs.43/g" in sentence
        assert "Budget day" in sentence

    def test_sentence_never_hardcodes_a_direction_word(self) -> None:
        import re

        sentence = build_sentence("fomc_decision", "2026-01-28", 55.0)
        for banned in ("rise", "fall", "rally", "drop", "surge", "plunge"):
            assert re.search(rf"\b{banned}\b", sentence.lower()) is None
