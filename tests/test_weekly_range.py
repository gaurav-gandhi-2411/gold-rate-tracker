"""Tests for ml.weekly_range (brief item 5). Synthetic data only."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from ml.weekly_range import (
    MIN_CAL,
    base_range,
    complete_windows,
    conformal_scale,
    score,
    times_out_of_ten,
    walk_forward,
)


def _series(dates: list[str], vals: list[float]) -> pd.Series:
    return pd.Series(vals, index=pd.to_datetime(dates), dtype=float)


def test_week_window_is_seven_calendar_days_of_publication_days() -> None:
    # Mon 2025-01-06 .. Wed 2025-01-15, every weekday published.
    days = pd.bdate_range("2025-01-06", "2025-01-15")
    ibja = _series([d.strftime("%Y-%m-%d") for d in days], [100.0 + i for i in range(len(days))])
    w = complete_windows(ibja, "week")[0]
    assert w.as_of == pd.Timestamp("2025-01-06")
    assert w.end == pd.Timestamp("2025-01-13")
    # Tue..Fri and next Mon: 5 publication days inside (Mon, Mon + 7].
    assert [d.strftime("%m-%d") for d in w.days] == ["01-07", "01-08", "01-09", "01-10", "01-13"]
    assert w.path_max == pytest.approx(math.log(105.0 / 100.0))


def test_holiday_week_has_four_days_and_still_counts() -> None:
    # Good Friday 2025-04-18 missing: Thu 04-17 -> Mon 04-21 is one holiday, not a hole.
    dates = ["2025-04-14", "2025-04-15", "2025-04-16", "2025-04-17", "2025-04-21", "2025-04-22"]
    ibja = _series(dates, [100.0, 101.0, 99.0, 102.0, 98.0, 100.0])
    w = complete_windows(ibja, "week")[0]
    assert len(w.days) == 4
    assert w.path_min == pytest.approx(math.log(98.0 / 100.0))


def test_window_across_a_hole_is_not_scored() -> None:
    dates = ["2025-01-06", "2025-01-07", "2025-01-27", "2025-01-28"]
    ibja = _series(dates, [100.0, 101.0, 90.0, 91.0])
    assert [w.as_of for w in complete_windows(ibja, "1d")] == [
        pd.Timestamp("2025-01-06"),
        pd.Timestamp("2025-01-27"),
    ]
    # No week window: from 01-06 the record jumps past the window end without a chain.
    assert all(w.as_of >= pd.Timestamp("2025-01-27") for w in complete_windows(ibja, "week"))


def test_score_is_the_scale_that_just_contains_the_path() -> None:
    days = pd.bdate_range("2025-01-06", "2025-01-15")
    ibja = _series([d.strftime("%Y-%m-%d") for d in days], [100.0, 98.0, 103.0] + [100.0] * 5)
    w = complete_windows(ibja, "week")[0]
    lo, hi = -0.04, 0.02
    s = score(w, lo, hi)
    assert s == pytest.approx(max(math.log(0.98) / lo, math.log(1.03) / hi))
    assert w.path_min >= s * lo - 1e-12 and w.path_max <= s * hi + 1e-12


def test_base_range_uses_only_proxy_known_at_as_of() -> None:
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2020-01-01", periods=700)
    proxy = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 700))), index=dates)
    as_of = dates[600]
    spiked = proxy.copy()
    spiked.iloc[601:] *= 3.0  # a huge move AFTER as_of must not change the range
    assert base_range(proxy, as_of, 5) == base_range(spiked, as_of, 5)
    lo, hi = base_range(proxy, as_of, 5)  # type: ignore[misc]
    assert lo < 0 < hi


def test_conformal_scale_needs_min_cal_and_hits_the_level() -> None:
    assert conformal_scale([1.0] * (MIN_CAL - 1)) is None
    scores = list(np.linspace(0.01, 1.0, 100))
    s = conformal_scale(scores)
    assert s is not None
    assert np.mean(np.asarray(scores) <= s) == pytest.approx(0.81, abs=0.011)


def test_walk_forward_never_calibrates_on_an_unfinished_window() -> None:
    rng = np.random.default_rng(42)
    pdates = pd.bdate_range("2023-01-02", periods=700)
    proxy = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 700))), index=pdates)
    idates = pdates[-150:]
    ibja = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.008, 150))), index=idates)
    df = walk_forward(proxy, ibja, "week")
    ends = dict(zip(df["as_of"], df["end"], strict=True))
    for _, row in df.iterrows():
        # every window counted in n_cal ended strictly before this as_of
        finished = sum(1 for a, e in ends.items() if a < row["as_of"] and e < row["as_of"])
        assert row["n_cal"] <= finished
    assert df["scale"].notna().any()


@pytest.mark.parametrize(("cov", "want"), [(0.851, 8), (0.8, 8), (0.799, 7), (0.773, 7)])
def test_times_out_of_ten_rounds_down(cov: float, want: int) -> None:
    assert times_out_of_ten(cov) == want
