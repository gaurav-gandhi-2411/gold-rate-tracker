"""Tests for ml.wait_or_buy and scripts/run_wait_or_buy_shadow.py (ADR 049,
F2 "buy now or wait?"). Synthetic data only."""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from ml.wait_or_buy import (
    N_VALUES,
    build_sentence,
    outcome_windows,
    prob_lower_stats,
    realized_vol_today,
    rolling_realized_vol,
    rs_bounds,
    trading_day_windows,
    vol_category,
    vol_percentile,
    week_windows,
)
from ml.weekly_range import (
    MIN_CAL,
    base_range,
    base_range_endpoint,
    conformal_scale,
    endpoint_windows,
    walk_forward_endpoint,
)

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run_wait_or_buy_shadow.py"
_spec = importlib.util.spec_from_file_location("run_wait_or_buy_shadow", _SCRIPT)
assert _spec is not None and _spec.loader is not None
shadow_mod = importlib.util.module_from_spec(_spec)
sys.modules["run_wait_or_buy_shadow"] = shadow_mod
_spec.loader.exec_module(shadow_mod)


def _series(dates: list[str], vals: list[float]) -> pd.Series:
    return pd.Series(vals, index=pd.to_datetime(dates), dtype=float)


def _synthetic(
    seed: int = 42, n_proxy: int = 700, n_ibja: int = 150
) -> tuple[pd.Series, pd.Series]:
    rng = np.random.default_rng(seed)
    pdates = pd.bdate_range("2023-01-02", periods=n_proxy)
    proxy = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n_proxy))), index=pdates)
    idates = pdates[-n_ibja:]
    ibja = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.008, n_ibja))), index=idates)
    return proxy, ibja


# ---------------------------------------------------------------------------
# (a) trading-day / calendar-week outcome windows
# ---------------------------------------------------------------------------


def test_trading_day_windows_skips_a_hole() -> None:
    dates = ["2025-01-06", "2025-01-07", "2025-01-27", "2025-01-28"]
    ibja = _series(dates, [100.0, 101.0, 90.0, 91.0])
    windows = trading_day_windows(ibja, 1)
    assert [w.as_of for w in windows] == [pd.Timestamp("2025-01-06"), pd.Timestamp("2025-01-27")]


def test_trading_day_windows_allows_one_holiday_step() -> None:
    # Good Friday 2025-04-18 missing: Thu 04-17 -> Mon 04-21 is one holiday, not a hole.
    dates = ["2025-04-14", "2025-04-15", "2025-04-16", "2025-04-17", "2025-04-21", "2025-04-22"]
    ibja = _series(dates, [100.0, 101.0, 99.0, 102.0, 98.0, 100.0])
    windows = trading_day_windows(ibja, 2)
    # 04-16 -> (04-17, 04-21): both steps consecutive (one is the holiday step).
    assert pd.Timestamp("2025-04-16") in [w.as_of for w in windows]


def test_outcome_tie_counts_as_not_lower() -> None:
    dates = ["2025-01-06", "2025-01-07"]
    ibja = _series(dates, [100.0, 100.0])
    w = trading_day_windows(ibja, 1)[0]
    assert w.lower == 0


def test_week_windows_reuses_weekly_range_week_rule() -> None:
    days = pd.bdate_range("2025-01-06", "2025-01-15")
    ibja = _series([d.strftime("%Y-%m-%d") for d in days], [100.0 + i for i in range(len(days))])
    w = week_windows(ibja)[0]
    assert w.as_of == pd.Timestamp("2025-01-06")
    assert w.target == pd.Timestamp("2025-01-13")  # last publication day inside (t, t+7]


def test_outcome_windows_dispatches_by_n() -> None:
    days = pd.bdate_range("2025-01-06", "2025-01-15")
    ibja = _series([d.strftime("%Y-%m-%d") for d in days], [100.0 + i for i in range(len(days))])
    assert outcome_windows(ibja, 7) == week_windows(ibja)
    assert outcome_windows(ibja, 1) == trading_day_windows(ibja, 1)


# ---------------------------------------------------------------------------
# (a) prob_lower_stats: HAC test, effective-n interval, copy rule
# ---------------------------------------------------------------------------


def test_prob_lower_stats_empty() -> None:
    df = pd.DataFrame(columns=["as_of", "target", "price_t", "price_target", "lower"])
    assert prob_lower_stats(df, 1) == {"n": 0}


def test_prob_lower_stats_effective_n_below_n_when_autocorrelated() -> None:
    # Strong positive autocorrelation (long runs of the same indicator value)
    # inflates the long-run variance, so effective_n must shrink below n. Needs
    # n=7 (lag=6 in the HAC correction, ADR 049 (a)) -- at n=1 the correction's
    # lag is 0 by construction (no overlap at a 1-day horizon), so effective_n
    # always equals n there regardless of the data's own autocorrelation.
    rng = np.random.default_rng(0)
    n = 200
    indicator = np.repeat(rng.integers(0, 2, n // 10), 10).astype(float)[:n]
    df = pd.DataFrame(
        {
            "as_of": pd.bdate_range("2023-01-02", periods=n),
            "target": pd.bdate_range("2023-01-03", periods=n),
            "price_t": 100.0,
            "price_target": 100.0,
            "lower": indicator,
        }
    )
    stats = prob_lower_stats(df, 7)
    assert stats["effective_n"] < stats["n"]


def test_prob_lower_stats_about_equally_likely_when_ci_contains_half() -> None:
    rng = np.random.default_rng(1)
    n = 300
    indicator = rng.integers(0, 2, n).astype(float)  # coin flips: true P=0.5
    df = pd.DataFrame(
        {
            "as_of": pd.bdate_range("2023-01-02", periods=n),
            "target": pd.bdate_range("2023-01-03", periods=n),
            "price_t": 100.0,
            "price_target": 100.0,
            "lower": indicator,
        }
    )
    stats = prob_lower_stats(df, 1)
    assert stats["about_equally_likely"] is True


def test_prob_lower_stats_not_about_equally_likely_when_one_sided() -> None:
    n = 300
    indicator = np.ones(n)  # always "lower" -> P=1.0, nowhere near 0.5
    df = pd.DataFrame(
        {
            "as_of": pd.bdate_range("2023-01-02", periods=n),
            "target": pd.bdate_range("2023-01-03", periods=n),
            "price_t": 100.0,
            "price_target": 100.0,
            "lower": indicator,
        }
    )
    stats = prob_lower_stats(df, 1)
    assert stats["about_equally_likely"] is False
    assert stats["times_out_of_10"] == 10


# ---------------------------------------------------------------------------
# (b) endpoint range: base_range_endpoint differs from base_range's path shape
# ---------------------------------------------------------------------------


def test_base_range_endpoint_uses_the_kth_step_not_the_path_min_max() -> None:
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2020-01-01", periods=700)
    proxy = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 700))), index=dates)
    as_of = dates[600]
    path_lo, path_hi = base_range(proxy, as_of, 5)  # type: ignore[misc]
    end_lo, end_hi = base_range_endpoint(proxy, as_of, 5)  # type: ignore[misc]
    # The k-day PATH min/max spans at least as wide as the single k-day ENDPOINT
    # return distribution (the path event is a superset of the endpoint event).
    assert path_lo <= end_lo
    assert path_hi >= end_hi


def test_base_range_endpoint_uses_only_proxy_known_at_as_of() -> None:
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2020-01-01", periods=700)
    proxy = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 700))), index=dates)
    as_of = dates[600]
    spiked = proxy.copy()
    spiked.iloc[601:] *= 3.0  # a huge move AFTER as_of must not change the range
    assert base_range_endpoint(proxy, as_of, 2) == base_range_endpoint(spiked, as_of, 2)


def test_endpoint_windows_days_is_the_single_target_day() -> None:
    dates = pd.bdate_range("2025-01-06", periods=10)
    ibja = _series([d.strftime("%Y-%m-%d") for d in dates], [100.0 + i for i in range(10)])
    w = endpoint_windows(ibja, 2)[0]
    assert w.days == (dates[2],)
    assert w.path_min == w.path_max == pytest.approx(math.log(102.0 / 100.0))


def test_score_agrees_between_endpoint_and_path_for_a_single_day_window() -> None:
    # k=1: base_range and base_range_endpoint coincide (there is only one day),
    # so score() on an endpoint Window and a complete_windows Window must agree.
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2020-01-01", periods=700)
    proxy = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 700))), index=dates)
    as_of = dates[600]
    lo, hi = base_range_endpoint(proxy, as_of, 1)  # type: ignore[misc]
    lo2, hi2 = base_range(proxy, as_of, 1)  # type: ignore[misc]
    assert lo == pytest.approx(lo2)
    assert hi == pytest.approx(hi2)


def test_walk_forward_endpoint_never_calibrates_on_an_unfinished_window() -> None:
    proxy, ibja = _synthetic()
    df = walk_forward_endpoint(proxy, ibja, 2)
    ends = dict(zip(df["as_of"], df["end"], strict=True))
    for _, row in df.iterrows():
        finished = sum(1 for a, e in ends.items() if a < row["as_of"] and e < row["as_of"])
        assert row["n_cal"] <= finished
    assert df["scale"].notna().any()


def test_conformal_scale_still_needs_min_cal() -> None:
    assert conformal_scale([1.0] * (MIN_CAL - 1)) is None


def test_rs_bounds_x_is_the_larger_absolute_side() -> None:
    lo_rs, hi_rs, x = rs_bounds(price_t=100000.0, scale=1.0, lo=-0.05, hi=0.02)
    assert lo_rs < 0 < hi_rs
    assert x == pytest.approx(max(abs(lo_rs), abs(hi_rs)))
    assert x == pytest.approx(abs(lo_rs))  # the down side is bigger here


# ---------------------------------------------------------------------------
# (c) realised volatility vs its long-run distribution
# ---------------------------------------------------------------------------


def test_rolling_realized_vol_is_nan_across_a_gap() -> None:
    dates = [*pd.bdate_range("2025-01-01", periods=25).tolist(), pd.Timestamp("2025-03-01")]
    vals = [100.0 + i for i in range(25)] + [200.0]
    price = pd.Series(vals, index=pd.DatetimeIndex(dates))
    vol = rolling_realized_vol(price, window=20)
    assert pd.isna(vol.iloc[-1])  # the post-gap row can't have 20 consecutive returns yet
    assert not pd.isna(vol.iloc[24])  # the 25th pre-gap row does (returns 5..24)


def test_realized_vol_today_none_when_insufficient_history() -> None:
    dates = pd.bdate_range("2025-01-01", periods=5)
    price = pd.Series([100.0, 101, 99, 102, 98], index=dates)
    assert realized_vol_today(price, dates[-1]) is None


def test_vol_percentile_uses_only_values_strictly_before_as_of() -> None:
    # No-look-ahead test: a future spike in the reference series must not move
    # the percentile computed for an earlier as_of.
    dates = pd.bdate_range("2020-01-01", periods=100)
    ref = pd.Series(np.linspace(0.005, 0.02, 100), index=dates)
    as_of = dates[50]
    value = 0.01
    p_before = vol_percentile(ref, as_of, value)
    ref_spiked = ref.copy()
    ref_spiked.iloc[51:] = 999.0  # future values must have zero effect
    p_after_spike = vol_percentile(ref_spiked, as_of, value)
    assert p_before == pytest.approx(p_after_spike)


def test_vol_category_thresholds() -> None:
    assert vol_category(None) is None
    assert vol_category(0.1) == "calmer_than_usual"
    assert vol_category(0.5) == "about_as_usual"
    assert vol_category(0.9) == "moving_more_than_usual"
    # Boundary values are exclusive per ADR 049 (b) < 25th / (c) > 75th.
    assert vol_category(0.25) == "about_as_usual"
    assert vol_category(0.75) == "about_as_usual"


# ---------------------------------------------------------------------------
# Copy: the sentence is built only from computed values, and never states a
# expected saving/cost of waiting (ADR 049 (d)).
# ---------------------------------------------------------------------------

_equal_prob = {"about_equally_likely": True, "times_out_of_10": 5}
_measured_prob = {"about_equally_likely": False, "times_out_of_10": 3}


def test_build_sentence_equally_likely_clause() -> None:
    s = build_sentence(2, _equal_prob, 500.0, "about_as_usual", 13.0)
    assert "about equally likely" in s
    assert "Waiting 2 days" in s


def test_build_sentence_measured_clause_when_not_equally_likely() -> None:
    s = build_sentence(1, _measured_prob, 500.0, "calmer_than_usual", 13.0)
    assert "3 times out of 10" in s
    assert "Waiting 1 day:" in s  # singular "day"


def test_build_sentence_shows_x_only_for_moving_more_than_usual() -> None:
    s = build_sentence(7, _equal_prob, 1234.0, "moving_more_than_usual", 13.0)
    assert "₹1234" in s
    s_calm = build_sentence(7, _equal_prob, 1234.0, "calmer_than_usual", 13.0)
    assert "₹" not in s_calm


def test_build_sentence_no_volatility_clause_when_category_is_none() -> None:
    s = build_sentence(2, _equal_prob, 500.0, None, 13.0)
    assert s == "Waiting 2 days: prices are about equally likely to go up or down."


def test_build_sentence_never_mentions_a_saving_or_cost() -> None:
    for category in ("calmer_than_usual", "about_as_usual", "moving_more_than_usual", None):
        for prob in (_equal_prob, _measured_prob):
            s = build_sentence(2, prob, 500.0, category, 13.0)
            lowered = s.lower()
            assert "save" not in lowered
            assert "saving" not in lowered
            assert "cost" not in lowered
            assert "expected" not in lowered


# ---------------------------------------------------------------------------
# scripts/run_wait_or_buy_shadow.py: no look-ahead, append-only scoring
# ---------------------------------------------------------------------------


def test_issue_uses_only_data_known_on_the_day() -> None:
    proxy, ibja = _synthetic()
    ref = rolling_realized_vol(proxy)
    t = ibja.index[-20]
    later = ibja.copy()
    later.iloc[-19:] *= 2.0  # anything after t must not change the issued entry
    e1 = shadow_mod._issue(proxy, ibja, ref, 2, t)
    e2 = shadow_mod._issue(proxy, later, ref, 2, t)
    assert e1 is not None and e2 is not None
    assert e1["prob"] == e2["prob"]
    assert e1["range"] == e2["range"]


def test_issue_range_contains_current_price() -> None:
    proxy, ibja = _synthetic()
    t = ibja.index[-20]
    entry = shadow_mod._issue(proxy, ibja, rolling_realized_vol(proxy), 1, t)
    assert entry is not None and entry["range"] is not None
    assert entry["range"]["lo"] < entry["price_t"] < entry["range"]["hi"]


def test_score_waits_for_a_matured_window() -> None:
    proxy, ibja = _synthetic()
    t = ibja.index[-1]  # nothing after the last row can have matured yet
    entry = shadow_mod._issue(proxy, ibja, rolling_realized_vol(proxy), 2, t)
    assert entry is not None
    entry["as_of"] = t.strftime("%Y-%m-%d")
    entry["n"] = 2
    assert shadow_mod._score(entry, ibja) is None


def test_score_matches_once_matured() -> None:
    proxy, ibja = _synthetic()
    t = ibja.index[-5]
    entry = shadow_mod._issue(proxy, ibja, rolling_realized_vol(proxy), 1, t)
    assert entry is not None
    entry["as_of"] = t.strftime("%Y-%m-%d")
    entry["n"] = 1
    result = shadow_mod._score(entry, ibja)
    assert result is not None
    assert result["target_date"] == ibja.index[ibja.index.get_loc(t) + 1].strftime("%Y-%m-%d")


@pytest.mark.parametrize("n", N_VALUES)
def test_all_horizons_can_be_issued_on_enough_synthetic_history(n: int) -> None:
    proxy, ibja = _synthetic(n_proxy=800, n_ibja=200)
    t = ibja.index[-30]
    entry = shadow_mod._issue(proxy, ibja, rolling_realized_vol(proxy), n, t)
    assert entry is not None
    assert "sentence" in entry and "Waiting" in entry["sentence"]
