"""Tests for ml.markup -- F1 "store markup meter" model/data layer.

Fixtures are synthetic (no dependence on the repo's real data files), except for one small
integration test against the real committed parquet/json files' shape.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from ml.markup import (
    CATEGORY_HIGHER,
    CATEGORY_LOWER,
    CATEGORY_USUAL,
    MIN_TRAILING_DAYS_FOR_CATEGORY,
    DailyMarkupRow,
    _percentile_rank,
    _quantile,
    _wanted_fix,
    build_daily_markup_series,
    compute_markup_today,
    compute_rolling_positions,
    compute_today_position,
    load_fusion_readings,
    load_tanishq_readings,
    markup_pct,
    resolve_ibja_fix,
    write_markup_today,
)

IST = ZoneInfo("Asia/Kolkata")


def _ibja_df(rows: list[dict]) -> pd.DataFrame:
    """rows: [{"date": "2026-09-01", "am_916": 100000.0, "pm_916": 100500.0}, ...]."""
    return pd.DataFrame(rows)


def _ist_dt(y: int, m: int, d: int, h: int, minute: int = 0) -> datetime:
    return datetime(y, m, d, h, minute, tzinfo=IST)


# ---------------------------------------------------------------------------
# markup_pct
# ---------------------------------------------------------------------------


def test_markup_pct_hand_computed():
    # 14180 vs 13924.7 -> ~1.833%
    assert markup_pct(14180.0, 13924.7) == pytest.approx(1.8334, abs=1e-3)


def test_markup_pct_zero_retailer_over_zero_ibja_is_zero():
    assert markup_pct(1000.0, 1000.0) == pytest.approx(0.0)


def test_markup_pct_rejects_nonpositive_ibja_rate():
    with pytest.raises(ValueError):
        markup_pct(1000.0, 0.0)


# ---------------------------------------------------------------------------
# _wanted_fix -- the timing rule, at each boundary
# ---------------------------------------------------------------------------


def test_wanted_fix_before_noon_ist_is_previous_day_pm():
    assert _wanted_fix(_ist_dt(2026, 9, 10, 11, 59)) == (date(2026, 9, 9), "pm")


def test_wanted_fix_exactly_noon_ist_is_same_day_am():
    assert _wanted_fix(_ist_dt(2026, 9, 10, 12, 0)) == (date(2026, 9, 10), "am")


def test_wanted_fix_between_noon_and_five_is_same_day_am():
    assert _wanted_fix(_ist_dt(2026, 9, 10, 16, 59)) == (date(2026, 9, 10), "am")


def test_wanted_fix_exactly_five_pm_ist_is_same_day_pm():
    assert _wanted_fix(_ist_dt(2026, 9, 10, 17, 0)) == (date(2026, 9, 10), "pm")


def test_wanted_fix_after_five_pm_is_same_day_pm():
    assert _wanted_fix(_ist_dt(2026, 9, 10, 23, 0)) == (date(2026, 9, 10), "pm")


def test_wanted_fix_accepts_naive_datetime_as_utc():
    # 2026-09-10 06:00 UTC == 11:30 IST -> before-noon rule -> previous day's PM.
    naive = datetime(2026, 9, 10, 6, 0)
    assert _wanted_fix(naive) == (date(2026, 9, 9), "pm")


# ---------------------------------------------------------------------------
# resolve_ibja_fix
# ---------------------------------------------------------------------------


def test_resolve_exact_match_am():
    df = _ibja_df([{"date": "2026-09-10", "am_916": 100000.0, "pm_916": 100500.0}])
    fix = resolve_ibja_fix(_ist_dt(2026, 9, 10, 13, 0), df)
    assert (fix.fix_date, fix.fix_type, fix.stale) == (date(2026, 9, 10), "am", False)
    assert fix.rate_per_gram == pytest.approx(10000.0)


def test_resolve_exact_match_pm():
    df = _ibja_df([{"date": "2026-09-10", "am_916": 100000.0, "pm_916": 100500.0}])
    fix = resolve_ibja_fix(_ist_dt(2026, 9, 10, 18, 0), df)
    assert (fix.fix_date, fix.fix_type, fix.stale) == (date(2026, 9, 10), "pm", False)
    assert fix.rate_per_gram == pytest.approx(10050.0)


def test_resolve_before_noon_uses_previous_pm_not_stale():
    """Previous day's PM before 12:00 IST is the CORRECT in-force fix, not a fallback."""
    df = _ibja_df(
        [
            {"date": "2026-09-09", "am_916": 99000.0, "pm_916": 99500.0},
            {"date": "2026-09-10", "am_916": 100000.0, "pm_916": 100500.0},
        ]
    )
    fix = resolve_ibja_fix(_ist_dt(2026, 9, 10, 9, 0), df)
    assert (fix.fix_date, fix.fix_type, fix.stale) == (date(2026, 9, 9), "pm", False)


def test_resolve_weekend_falls_back_to_last_published_fix_flagged_stale():
    """Saturday reading, no IBJA row at all for Sat/Sun -- falls back to Friday's PM."""
    df = _ibja_df(
        [
            {"date": "2026-09-11", "am_916": 100000.0, "pm_916": 100500.0},  # Friday
            {"date": "2026-09-14", "am_916": 101000.0, "pm_916": 101500.0},  # Monday
        ]
    )
    saturday_evening = _ist_dt(2026, 9, 12, 18, 0)
    fix = resolve_ibja_fix(saturday_evening, df)
    assert (fix.fix_date, fix.fix_type, fix.stale) == (date(2026, 9, 11), "pm", True)


def test_resolve_never_uses_a_same_day_pm_for_an_am_window_reading():
    """No-look-ahead: an AM-window reading must never resolve to that SAME day's PM fix, even
    though the PM row exists in the dataframe (it hasn't been published yet at that time)."""
    df = _ibja_df([{"date": "2026-09-10", "am_916": 100000.0, "pm_916": 100500.0}])
    fix = resolve_ibja_fix(_ist_dt(2026, 9, 10, 13, 0), df)
    assert fix.fix_type == "am"


def test_resolve_am_missing_falls_back_to_previous_pm_flagged_stale():
    """AM-window reading, but today's am_916 is null (not yet published) -- falls back to the
    most recent prior published fix (previous day's PM), not today's not-yet-existing AM."""
    df = _ibja_df(
        [
            {"date": "2026-09-09", "am_916": 99000.0, "pm_916": 99500.0},
            {"date": "2026-09-10", "am_916": None, "pm_916": None},
        ]
    )
    fix = resolve_ibja_fix(_ist_dt(2026, 9, 10, 13, 0), df)
    assert (fix.fix_date, fix.fix_type, fix.stale) == (date(2026, 9, 9), "pm", True)


def test_resolve_raises_when_no_ibja_data_at_or_before_wanted_slot():
    df = _ibja_df([{"date": "2026-09-10", "am_916": 100000.0, "pm_916": 100500.0}])
    with pytest.raises(ValueError):
        resolve_ibja_fix(_ist_dt(2026, 9, 1, 13, 0), df)


def test_resolve_raises_on_empty_ibja_df():
    df = _ibja_df([])
    with pytest.raises(ValueError):
        resolve_ibja_fix(_ist_dt(2026, 9, 10, 13, 0), df)


# ---------------------------------------------------------------------------
# build_daily_markup_series
# ---------------------------------------------------------------------------


def test_daily_series_keeps_only_last_reading_per_ist_date():
    df = _ibja_df([{"date": "2026-09-10", "am_916": 100000.0, "pm_916": 100500.0}])
    readings = [
        (_ist_dt(2026, 9, 10, 12, 30), 10100.0),  # earlier
        (_ist_dt(2026, 9, 10, 18, 0), 10200.0),  # last of the day -- this one wins
    ]
    daily = build_daily_markup_series(readings, df)
    assert len(daily) == 1
    assert daily[0].retailer_rate_per_gram == 10200.0
    assert daily[0].ibja_fix_type == "pm"  # 18:00 IST -> PM window


def test_daily_series_one_row_per_distinct_ist_date():
    df = _ibja_df(
        [
            {"date": "2026-09-10", "am_916": 100000.0, "pm_916": 100500.0},
            {"date": "2026-09-11", "am_916": 101000.0, "pm_916": 101500.0},
        ]
    )
    readings = [
        (_ist_dt(2026, 9, 10, 13, 0), 10100.0),
        (_ist_dt(2026, 9, 11, 13, 0), 10200.0),
    ]
    daily = build_daily_markup_series(readings, df)
    assert [r.ist_date for r in daily] == [date(2026, 9, 10), date(2026, 9, 11)]


def test_daily_series_sorted_chronologically_regardless_of_input_order():
    df = _ibja_df(
        [
            {"date": "2026-09-10", "am_916": 100000.0, "pm_916": 100500.0},
            {"date": "2026-09-11", "am_916": 101000.0, "pm_916": 101500.0},
        ]
    )
    readings = [
        (_ist_dt(2026, 9, 11, 13, 0), 10200.0),
        (_ist_dt(2026, 9, 10, 13, 0), 10100.0),
    ]
    daily = build_daily_markup_series(readings, df)
    assert [r.ist_date for r in daily] == [date(2026, 9, 10), date(2026, 9, 11)]


# ---------------------------------------------------------------------------
# percentile / category, no-look-ahead
# ---------------------------------------------------------------------------


def _synthetic_daily(markups: list[float], start: date = date(2026, 1, 1)) -> list[DailyMarkupRow]:
    """Build DailyMarkupRow objects directly from a list of markup_pct values, one per
    consecutive calendar day, with a fixed dummy IBJA rate of 10000.0/gram."""
    from datetime import timedelta

    rows = []
    for i, m in enumerate(markups):
        d = start + timedelta(days=i)
        retailer_rate = 10000.0 * (1 + m / 100.0)
        rows.append(
            DailyMarkupRow(
                ist_date=d,
                reading_at_utc=datetime(d.year, d.month, d.day, 12, tzinfo=UTC),
                retailer_rate_per_gram=retailer_rate,
                ibja_fix_date=d,
                ibja_fix_type="pm",
                ibja_rate_per_gram=10000.0,
                stale_ibja=False,
                markup_pct=m,
            )
        )
    return rows


def test_percentile_rank_hand_computed():
    assert _percentile_rank(0.0, [1.0, 2.0, 3.0, 4.0]) == pytest.approx(0.0)
    assert _percentile_rank(2.0, [1.0, 2.0, 3.0, 4.0]) == pytest.approx(50.0)
    assert _percentile_rank(5.0, [1.0, 2.0, 3.0, 4.0]) == pytest.approx(100.0)


def test_percentile_rank_raises_on_empty():
    with pytest.raises(ValueError):
        _percentile_rank(1.0, [])


def test_quantile_matches_pandas_linear_interpolation():
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    s = pd.Series(values)
    assert _quantile(values, 0.25) == pytest.approx(s.quantile(0.25))
    assert _quantile(values, 0.75) == pytest.approx(s.quantile(0.75))


def test_today_position_no_history_has_no_percentile_or_category():
    daily = _synthetic_daily([1.5])
    pos = compute_today_position(daily)
    assert pos.percentile_90d is None
    assert pos.percentile_30d is None
    assert pos.category is None
    assert pos.n_history_days == 0


def test_today_position_below_min_trailing_days_has_percentile_but_no_category():
    n_earlier = MIN_TRAILING_DAYS_FOR_CATEGORY - 1
    daily = _synthetic_daily([1.0] * n_earlier + [5.0])
    pos = compute_today_position(daily)
    assert pos.percentile_90d is not None
    assert pos.category is None


def test_today_position_category_lower_usual_higher():
    # 20 earlier days uniformly 1..20 -> p25=5.75, p75=15.25 (pandas linear interpolation).
    earlier = [float(i) for i in range(1, 21)]
    lower_daily = _synthetic_daily([*earlier, 1.0])
    usual_daily = _synthetic_daily([*earlier, 10.0])
    higher_daily = _synthetic_daily([*earlier, 30.0])

    assert compute_today_position(lower_daily).category == CATEGORY_LOWER
    assert compute_today_position(usual_daily).category == CATEGORY_USUAL
    assert compute_today_position(higher_daily).category == CATEGORY_HIGHER


def test_today_position_uses_only_earlier_days_not_the_window_endpoints_inclusive():
    """A day equal to today's own value must not count toward its own percentile (no
    self-inclusion / no look-ahead)."""
    daily = _synthetic_daily([2.0] * 15 + [2.0])
    pos = compute_today_position(daily)
    # All 15 earlier days == today's value -> percentile should be 100 (all <= today),
    # and n_history_days must be exactly 15 (today itself excluded).
    assert pos.n_history_days == 15
    assert pos.percentile_90d == pytest.approx(100.0)


def test_today_position_window_caps_at_90_and_30():
    daily = _synthetic_daily([float(i) for i in range(120)])
    pos = compute_today_position(daily)
    assert pos.n_history_days == 90
    # percentile_30d computed from only the last 30 earlier days (90..118), all < today (119).
    assert pos.percentile_30d == pytest.approx(100.0)


def test_compute_rolling_positions_is_no_look_ahead():
    """Changing a LATER day's markup value must not change an EARLIER day's computed position --
    the defining no-look-ahead property this module exists to guarantee."""
    base = [float(i % 7) for i in range(40)]
    mutated = list(base)
    mutated[-1] = 999.0  # only the very last day changes

    positions_base = compute_rolling_positions(_synthetic_daily(base))
    positions_mutated = compute_rolling_positions(_synthetic_daily(mutated))

    for i in range(len(base) - 1):
        assert positions_base[i].percentile_90d == positions_mutated[i].percentile_90d
        assert positions_base[i].category == positions_mutated[i].category


def test_compute_rolling_positions_length_matches_input():
    daily = _synthetic_daily([1.0, 2.0, 3.0])
    assert len(compute_rolling_positions(daily)) == 3


# ---------------------------------------------------------------------------
# compute_markup_today
# ---------------------------------------------------------------------------


def test_compute_markup_today_shape_and_omission():
    df = _ibja_df(
        [
            {"date": "2026-09-09", "am_916": 99000.0, "pm_916": 99500.0},
            {"date": "2026-09-10", "am_916": 100000.0, "pm_916": 100500.0},
        ]
    )
    retailer_readings = {
        "tanishq": [(_ist_dt(2026, 9, 10, 18, 0), 10200.0)],
        "grt": [],  # no readings at all -> omitted
        "malabar": [(_ist_dt(2026, 9, 9, 18, 0), 10000.0)],  # readings, but none on as_of_date
    }
    payload = compute_markup_today(retailer_readings, df, as_of_date=date(2026, 9, 10))
    assert payload["schema_version"] == 1
    assert payload["as_of"] == "2026-09-10"
    assert set(payload["retailers"]) == {"tanishq"}
    tanishq = payload["retailers"]["tanishq"]
    assert set(tanishq) == {
        "markup_pct",
        "percentile_90d",
        "category",
        "ibja_fix_used",
        "stale",
        "n_history_days",
    }
    assert tanishq["ibja_fix_used"] == {"date": "2026-09-10", "fix": "pm"}
    assert tanishq["stale"] is False


# ---------------------------------------------------------------------------
# write_markup_today -- I/O integration
# ---------------------------------------------------------------------------


def test_write_markup_today_writes_valid_json(tmp_path: Path):
    ibja_path = tmp_path / "ibja_rates.parquet"
    _ibja_df(
        [
            {"date": "2026-09-09", "am_916": 99000.0, "pm_916": 99500.0},
            {"date": "2026-09-10", "am_916": 100000.0, "pm_916": 100500.0},
        ]
    ).to_parquet(ibja_path)

    prices_path = tmp_path / "prices.json"
    prices_path.write_text(
        json.dumps([{"timestamp": "2026-09-10T12:30:00.000Z", "22k": 10200, "24k": 0, "18k": 0}]),
        encoding="utf-8",
    )

    fusion_path = tmp_path / "fusion_snapshots.parquet"
    pd.DataFrame(
        [
            {
                "capture_utc": "2026-09-10T12:30:00Z",
                "as_of_date": "2026-09-10",
                "schema_version": 1,
                "source": "grt",
                "city": None,
                "rate_22k": 10100.0,
                "observed_at": "2026-09-10T12:30:00+00:00",
                "attribution": "test",
            }
        ]
    ).to_parquet(fusion_path)

    out_path = tmp_path / "markup_today.json"
    payload = write_markup_today(
        out_path=out_path,
        prices_path=prices_path,
        fusion_path=fusion_path,
        ibja_path=ibja_path,
        as_of_date=date(2026, 9, 10),
    )

    assert out_path.exists()
    on_disk = json.loads(out_path.read_text(encoding="utf-8"))
    assert on_disk == payload
    assert "tanishq" in payload["retailers"]
    assert "grt" in payload["retailers"]


def test_write_markup_today_raises_on_missing_ibja_parquet(tmp_path: Path):
    with pytest.raises(ValueError):
        write_markup_today(
            out_path=tmp_path / "out.json",
            prices_path=tmp_path / "prices.json",
            fusion_path=tmp_path / "fusion.parquet",
            ibja_path=tmp_path / "does_not_exist.parquet",
            as_of_date=date(2026, 9, 10),
        )


# ---------------------------------------------------------------------------
# Loaders -- corrupt-data filtering (real bug found in fusion_snapshots.parquet)
# ---------------------------------------------------------------------------


def test_load_tanishq_readings_skips_unparseable_rows(tmp_path: Path):
    path = tmp_path / "prices.json"
    path.write_text(
        json.dumps(
            [
                {"timestamp": "2026-09-10T12:30:00.000Z", "22k": 10200, "24k": 0, "18k": 0},
                {"timestamp": "not-a-date", "22k": 10200, "24k": 0, "18k": 0},
                {"22k": 10200},  # missing timestamp entirely
            ]
        ),
        encoding="utf-8",
    )
    readings = load_tanishq_readings(path)
    assert len(readings) == 1


def test_load_tanishq_readings_filters_implausible_epoch_timestamps(tmp_path: Path):
    """Regression for the real corrupt-data shape found in data/fusion_snapshots.parquet
    (source=kalyan): a row whose timestamp parses successfully but lands at epoch (pre-2020)
    must be filtered, not treated as a genuine reading."""
    path = tmp_path / "prices.json"
    path.write_text(
        json.dumps(
            [
                {"timestamp": "1969-12-31T18:30:00.000Z", "22k": 10200, "24k": 0, "18k": 0},
                {"timestamp": "2026-09-10T12:30:00.000Z", "22k": 10300, "24k": 0, "18k": 0},
            ]
        ),
        encoding="utf-8",
    )
    readings = load_tanishq_readings(path)
    assert len(readings) == 1
    assert readings[0][1] == 10300


def test_load_fusion_readings_filters_national_vs_city(tmp_path: Path):
    path = tmp_path / "fusion_snapshots.parquet"
    pd.DataFrame(
        [
            {
                "capture_utc": "2026-09-10T12:30:00Z",
                "as_of_date": "2026-09-10",
                "schema_version": 1,
                "source": "grt",
                "city": None,
                "rate_22k": 10100.0,
                "observed_at": "2026-09-10T12:30:00+00:00",
                "attribution": "national",
            },
            {
                "capture_utc": "2026-09-10T12:30:00Z",
                "as_of_date": "2026-09-10",
                "schema_version": 1,
                "source": "kalyan",
                "city": "Bangalore",
                "rate_22k": 10250.0,
                "observed_at": "2026-09-10T12:30:00+00:00",
                "attribution": "city",
            },
        ]
    ).to_parquet(path)

    national = load_fusion_readings("grt", None, path=path)
    assert len(national) == 1
    assert national[0][1] == 10100.0

    city = load_fusion_readings("kalyan", "Bangalore", path=path)
    assert len(city) == 1
    assert city[0][1] == 10250.0

    missing_city = load_fusion_readings("kalyan", "Chennai", path=path)
    assert missing_city == []


def test_load_fusion_readings_missing_file_returns_empty(tmp_path: Path):
    assert load_fusion_readings("grt", None, path=tmp_path / "nope.parquet") == []
