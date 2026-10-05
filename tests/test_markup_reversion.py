"""Tests for ml/markup_reversion.py (ADR 057) -- synthetic data only, no repo data files."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from ml.markup_reversion import (
    ar1_stats,
    build_pair_frame,
    evaluate_cell,
    forward_frame,
    hac_lag,
    hac_mean,
    hac_ols,
    ibja_business_days,
    lag1_pairs,
    resolve_outcomes,
    robust_trailing_z,
    select_variant,
    shadow_entry_for,
)


def _ibja(days: list[date], pm: list[float]) -> pd.DataFrame:
    """IBJA frame in the parquet's native Rs/10g unit."""
    return pd.DataFrame(
        {
            "date": [d.isoformat() for d in days],
            "am_916": [p * 10 for p in pm],
            "pm_916": [p * 10 for p in pm],
        }
    )


def _evening(d: date) -> datetime:
    # 21:00 IST == 15:30 UTC -> paired with that day's PM fix
    return datetime(d.year, d.month, d.day, 15, 30, tzinfo=UTC)


def test_same_day_pair_is_fresh_and_weekend_pair_is_stale() -> None:
    fri, sat = date(2026, 9, 18), date(2026, 9, 19)
    ibja = _ibja([fri], [13000.0])
    frame = build_pair_frame([(_evening(fri), 13200.0), (_evening(sat), 13250.0)], ibja)
    by = frame.set_index("date")
    assert not by.loc[fri, "stale_ibja"]
    assert by.loc[sat, "stale_ibja"]  # Saturday carries Friday's PM forward
    assert by.loc[fri, "markup_rs"] == pytest.approx(200.0)
    assert by.loc[sat, "weekend"]
    assert len(select_variant(frame, "b_same_day_fresh")) == 1


def test_tanishq_repeat_and_backfill_flags() -> None:
    d0, d1 = date(2026, 9, 21), date(2026, 9, 22)
    ibja = _ibja([d0, d1], [13000.0, 13010.0])
    ts0 = datetime(2026, 9, 21, 6, 30, tzinfo=UTC)  # synthetic backfill stamp
    frame = build_pair_frame(
        [(ts0, 13200.0), (_evening(d1), 13200.0)],
        ibja,
        backfill_ts={"2026-09-21T06:30:00.000Z"},
    )
    by = frame.set_index("date")
    assert by.loc[d0, "backfill"] and not by.loc[d1, "backfill"]
    assert by.loc[d1, "tanishq_repeat"] and not by.loc[d0, "tanishq_repeat"]
    assert select_variant(frame, "c_no_carry_forward").empty


def test_robust_trailing_z_uses_only_strictly_earlier_values() -> None:
    x = np.array([1.0, 2.0, 3.0, 4.0, 100.0])
    z = robust_trailing_z(x, window=10, min_obs=4)
    assert np.isnan(z[:4]).all()
    # prev = [1,2,3,4]: median 2.5, MAD 1.0 -> sd 1.4826
    assert z[4] == pytest.approx((100 - 2.5) / 1.4826)
    # Changing the current value never changes an earlier z (no look-ahead).
    x2 = x.copy()
    x2[4] = -50.0
    assert np.allclose(robust_trailing_z(x2, 10, 4)[:4], z[:4], equal_nan=True)


def test_robust_trailing_z_zero_mad_is_nan() -> None:
    z = robust_trailing_z(np.array([1.0, 1.0, 1.0, 5.0]), window=10, min_obs=3)
    assert np.isnan(z[3])


def test_ar1_stats_recovers_known_persistence() -> None:
    rng = np.random.default_rng(42)
    x = np.zeros(3000)
    for i in range(1, len(x)):
        x[i] = 0.7 * x[i - 1] + rng.normal()
    res = ar1_stats(x[:-1], x[1:])
    assert res["ar1"] == pytest.approx(0.7, abs=0.05)
    lo, hi = res["fisher_ci95"]
    assert lo < 0.7 < hi
    assert res["half_life_steps"] == pytest.approx(np.log(0.5) / np.log(res["ar1"]))


def test_ar1_stats_too_few_pairs() -> None:
    assert ar1_stats(np.arange(5.0), np.arange(5.0))["ar1"] is None


def test_lag1_pairs_gap_filter() -> None:
    df = pd.DataFrame(
        {"date": [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 5)], "v": [1.0, 2.0, 3.0]}
    )
    _a, _b, gaps = lag1_pairs(df, "v")
    assert gaps == [1, 3]
    a1, _, _ = lag1_pairs(df, "v", max_gap_days=1)
    assert list(a1) == [1.0]


def test_hac_lag_at_least_horizon_minus_one() -> None:
    for n in (1, 3, 5):
        assert hac_lag(n) >= n - 1


def test_hac_ols_matches_group_mean_difference() -> None:
    y = np.array([1.0, 2.0, 3.0, 10.0, 12.0])
    x = np.array([0, 0, 0, 1, 1], dtype=float)
    r = hac_ols(y, x, lag=0)
    assert r["b"] == pytest.approx(11.0 - 2.0)
    assert r["se_b"] > 0


def test_hac_mean_iid_effective_n_close_to_n() -> None:
    rng = np.random.default_rng(42)
    res = hac_mean(rng.normal(size=2000), lag=3)
    assert res["effective_n"] == pytest.approx(2000, rel=0.15)


def _series(n: int, markup_rs: list[float]) -> tuple[pd.DataFrame, list[date]]:
    start = date(2026, 1, 5)
    days = [start + timedelta(days=i) for i in range(n)]
    ibja = [13000.0] * n
    s = pd.DataFrame(
        {
            "date": days,
            "tanishq": [ibja[i] + markup_rs[i] for i in range(n)],
            "ibja": ibja,
            "markup_rs": markup_rs,
            "markup_pct": [m / 130.0 for m in markup_rs],
            "ibja_fix_date": days,
            "ibja_fix_type": ["pm"] * n,
        }
    )
    return s, days


def test_forward_frame_outcome_signs() -> None:
    s, days = _series(4, [100.0, 300.0, 150.0, 150.0])
    ff = forward_frame(s, days, 1)
    # day1 -> day2: markup falls 150 with the market flat -> y = -150, buyer saves 150
    assert ff.loc[1, "y"] == pytest.approx(-150.0)
    assert ff.loc[1, "gross_saving"] == pytest.approx(150.0)
    assert np.isnan(ff.loc[3, "y"])  # no target yet


def test_forward_frame_requires_target_in_series() -> None:
    s, days = _series(3, [100.0, 200.0, 300.0])
    s = s.drop(index=1)  # day 1 ineligible
    ff = forward_frame(s, days, 1)
    assert np.isnan(ff.set_index("date").loc[days[0], "y"])


def test_evaluate_cell_detects_planted_reversion() -> None:
    rng = np.random.default_rng(42)
    n = 400
    m = np.zeros(n)
    for i in range(1, n):
        m[i] = 0.3 * m[i - 1] + rng.normal(scale=100)
    s, days = _series(n, list(m + 150))
    s["z"] = robust_trailing_z(s["markup_pct"].to_numpy(), 60, 20)
    res = evaluate_cell(forward_frame(s, days, 1), 1.0, 1)
    assert res["signal_minus_nonsignal_y"]["b"] < 0
    assert res["signal_minus_nonsignal_y"]["p_one_sided"] < 0.01
    assert res["signal_days_saving_net_of_market"]["mean"] > 0


def test_evaluate_cell_no_signal_is_inconclusive() -> None:
    s, days = _series(30, [100.0] * 30)
    s["z"] = 0.0
    assert evaluate_cell(forward_frame(s, days, 1), 1.0, 1)["verdict"] == "INCONCLUSIVE"


def test_ibja_business_days_skips_empty_rows() -> None:
    df = pd.DataFrame(
        {"date": ["2026-01-01", "2026-01-02"], "am_916": [1.0, np.nan], "pm_916": [np.nan, np.nan]}
    )
    assert ibja_business_days(df) == [date(2026, 1, 1)]


def test_shadow_entry_and_outcome_resolution() -> None:
    s, days = _series(25, [100.0 + 10 * (i % 2) for i in range(24)] + [400.0])
    s["z"] = robust_trailing_z(s["markup_pct"].to_numpy(), 60, 20)
    e = shadow_entry_for(s, days[24])
    assert e is not None and e.signals["z_ge_1"]
    assert "tanishq" not in e.to_dict()  # derived numbers only
    assert shadow_entry_for(s, date(2030, 1, 1)) is None
    entries = resolve_outcomes([{"date": days[20].isoformat()}], s, days)
    # day 20 (100) -> day 21 (110): markup +10 with a flat market; buyer pays 10 more
    assert entries[0]["outcomes"]["n1"]["y_markup_change_rs"] == pytest.approx(10.0)
    assert entries[0]["outcomes"]["n3"]["gross_saving_rs"] == pytest.approx(-10.0)
