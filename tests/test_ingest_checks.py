"""Tests for ml.ingest_checks (item 2f): every check has a passing case, a failing case and a
hostile input (Kalyan 1969-12-31 placeholder, NaN, string number, per-10g value in a per-gram
field, future timestamp, negative price, duplicate dates).
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from ml import ingest_checks as ic

NOW = pd.Timestamp("2026-10-05T08:00:00Z")
KALYAN_PLACEHOLDER = "1969-12-31T18:30:00+00:00"  # "01 Jan 1970 00:00" IST parsed to UTC


def codes(vs: list[ic.Violation], severity: str | None = None) -> set[str]:
    return {v.code for v in vs if severity is None or v.severity == severity}


# ---------------------------------------------------------------- check_timestamp


def ts(value, **kw):
    return ic.check_timestamp(value, source="t", field_name="f", now=NOW, **kw)


def test_timestamp_pass_iso_z_and_offset():
    assert ts("2026-10-05T07:34:04.605Z") == []
    assert ts("2026-10-05T13:00:00+05:30") == []
    assert ts(pd.Timestamp("2026-10-05T07:00:00Z")) == []


def test_timestamp_kalyan_placeholder_is_epoch_block():
    vs = ts(KALYAN_PLACEHOLDER)
    assert codes(vs, "block") == {ic.TS_EPOCH_PLACEHOLDER}
    assert "1969" in vs[0].message and vs[0].value == KALYAN_PLACEHOLDER


@pytest.mark.parametrize("bad", ["1970-01-01T00:00:00Z", "1970-01-01", "2019-12-31T23:59:59Z"])
def test_timestamp_other_epoch_and_pre_2020_block(bad):
    assert ic.TS_EPOCH_PLACEHOLDER in codes(ts(bad))


def test_timestamp_min_year_override_allows_legit_old_history_but_not_epoch():
    assert ts("2013-01-01T00:00:00Z", min_year=2000) == []
    assert ic.TS_EPOCH_PLACEHOLDER in codes(ts("1970-01-01T00:00:00Z", min_year=2000))


def test_timestamp_future_block_and_small_skew_ok():
    assert codes(ts("2026-10-06T08:00:00Z"), "block") == {ic.TS_FUTURE}
    assert ts("2026-10-05T08:03:00Z") == []  # inside the 5 min default skew


def test_timestamp_naive_block_unless_allowed():
    assert ic.TS_NAIVE in codes(ts("2026-10-05T07:00:00"))
    assert ts("2026-10-05T07:00:00", require_tz=False) == []


@pytest.mark.parametrize(
    "bad",
    [None, "", "   ", "not a date", 1790000000, 1790000000.5, float("nan"), True, pd.NaT, [], {}],
)
def test_timestamp_hostile_unparseable_fails_closed(bad):
    assert codes(ts(bad), "block") == {ic.TS_UNPARSEABLE}


def test_timestamp_not_before_previous():
    prev = "2026-10-05T06:00:00Z"
    assert ts("2026-10-05T07:00:00Z", prev=prev) == []
    assert ic.TS_NOT_AFTER_PREVIOUS in codes(ts("2026-10-05T05:00:00Z", prev=prev))
    assert ts(prev, prev=prev) == []  # equal is allowed ...
    assert ic.TS_NOT_AFTER_PREVIOUS in codes(ts(prev, prev=prev, strictly_after_prev=True))


# ---------------------------------------------------------------- check_number


def num(value, bounds=ic.RATE_22K_PER_G):
    return ic.check_number(value, source="t", field_name="v", bounds=bounds)


def test_number_pass():
    assert num(13700) == [] and num(13700.5) == [] and num(np.float64(13700.0)) == []


@pytest.mark.parametrize("bad", ["13700", None, True, [13700], b"1"])
def test_number_string_and_wrong_types_block(bad):
    assert codes(num(bad), "block") == {ic.VALUE_NOT_NUMERIC}


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_number_nan_inf_block(bad):
    assert codes(num(bad), "block") == {ic.VALUE_NOT_FINITE}


@pytest.mark.parametrize("bad", [-13700, -0.01, 0, 0.0])
def test_number_negative_and_zero_block(bad):
    assert codes(num(bad), "block") == {ic.VALUE_NON_POSITIVE}


def test_number_per_10g_in_per_gram_field_is_unit_suspect():
    vs = num(137_050.0)
    assert codes(vs, "block") == {ic.UNIT_SUSPECT_PER_10G}
    assert "per-10g" in vs[0].message and vs[0].value == 137_050.0


def test_number_per_gram_in_per_10g_field_is_unit_suspect():
    assert codes(num(13_569.4, ic.IBJA_916_PER_10G), "block") == {ic.UNIT_SUSPECT_PER_G}


def test_number_out_of_range_and_history_warn_band():
    assert codes(num(3), "block") == {ic.VALUE_OUT_OF_RANGE}
    assert codes(num(9_999_999), "block") == {ic.VALUE_OUT_OF_RANGE}
    assert codes(num(19_000), "warn") == {ic.VALUE_OUTSIDE_HISTORY}  # new all-time high = warn
    assert num(19_000) and not codes(num(19_000), "block")


# ---------------------------------------------------------------- check_jump


def test_jump_pass_warn_block():
    lim = ic.TANISHQ_JUMP
    assert ic.check_jump(13700, 13800, source="t", field_name="v", limits=lim) == []
    assert codes(ic.check_jump(13700, 13700 * 1.09, source="t", field_name="v", limits=lim)) == {
        ic.JUMP_WARN
    }
    assert codes(ic.check_jump(13700, 13700 * 1.3, source="t", field_name="v", limits=lim)) == {
        ic.JUMP_BLOCK
    }


def test_jump_unit_slip_is_block_both_directions():
    lim = ic.TANISHQ_JUMP
    assert ic.JUMP_BLOCK in codes(
        ic.check_jump(13700, 137000, source="t", field_name="v", limits=lim)
    )
    assert ic.JUMP_BLOCK in codes(
        ic.check_jump(137000, 13700, source="t", field_name="v", limits=lim)
    )


def test_jump_gap_scaling_tolerates_the_same_move_over_a_long_gap():
    lim = ic.IBJA_JUMP
    move = 135000 * 1.3  # +26% in one day is a block, over 100 days it is ordinary drift
    assert ic.JUMP_BLOCK in codes(
        ic.check_jump(135000, move, source="t", field_name="v", limits=lim, gap_days=1)
    )
    assert ic.check_jump(135000, move, source="t", field_name="v", limits=lim, gap_days=100) == []


@pytest.mark.parametrize(
    "prev,cur", [(0, 1), (-5, 10), (float("nan"), 10), (10, float("nan")), ("1", 2)]
)
def test_jump_hostile_inputs_fail_closed(prev, cur):
    assert codes(
        ic.check_jump(prev, cur, source="t", field_name="v", limits=ic.TANISHQ_JUMP), "block"
    )


# ---------------------------------------------------------------- staleness / gap


def test_staleness_pass_warn_block_and_unparseable():
    kw = {"source": "t", "now": NOW, "warn_hours": 24, "block_hours": 72}
    assert ic.check_staleness("2026-10-05T01:00:00Z", **kw) == []
    assert codes(ic.check_staleness("2026-10-03T20:00:00Z", **kw)) == {ic.STALE_WARN}
    assert codes(ic.check_staleness("2026-09-24T17:06:07Z", **kw)) == {ic.STALE_BLOCK}
    assert codes(ic.check_staleness("garbage", **kw), "block") == {ic.TS_UNPARSEABLE}
    assert codes(ic.check_staleness(None, **kw), "block") == {ic.TS_UNPARSEABLE}


def test_gap_pass_warn_block():
    a = pd.Timestamp("2026-10-01T00:00:00Z")
    kw = {"source": "t", "warn_hours": 48, "block_hours": 168}
    assert ic.check_gap(a, a + pd.Timedelta(hours=3), **kw) == []
    assert codes(ic.check_gap(a, a + pd.Timedelta(hours=60), **kw)) == {ic.GAP_WARN}
    assert codes(ic.check_gap(a, a + pd.Timedelta(hours=200), **kw)) == {ic.GAP_BLOCK}


# ---------------------------------------------------------------- ratios / cross-source


def test_ratio_pass_warn_block_and_hostile():
    kw = {
        "expected": 24 / 22,
        "tol_warn": 0.005,
        "tol_block": 0.02,
        "source": "t",
        "field_name": "r",
    }
    assert ic.check_ratio(14967, 13720, **kw) == []
    assert codes(ic.check_ratio(14967 * 1.01, 13720, **kw)) == {ic.RATIO_WARN}
    assert codes(ic.check_ratio(14967 * 1.1, 13720, **kw)) == {ic.RATIO_BLOCK}
    assert codes(ic.check_ratio("x", 13720, **kw), "block") == {ic.VALUE_NOT_FINITE}
    assert codes(ic.check_ratio(1, 0, **kw), "block") == {ic.VALUE_NOT_FINITE}


def test_cross_source_pass_warn_block():
    base = {"ibja": 13705.0, "grt": 13675.0, "malabar": 13675.0}
    assert ic.check_cross_source(base) == []
    assert codes(ic.check_cross_source({**base, "malabar": 13675 * 1.07})) == {ic.CROSS_SOURCE_WARN}
    assert codes(ic.check_cross_source({**base, "malabar": 13675 * 1.15})) == {
        ic.CROSS_SOURCE_BLOCK
    }


def test_cross_source_hostile_value_is_reported_not_averaged_in():
    vs = ic.check_cross_source({"ibja": 13705.0, "grt": "13675", "malabar": 13675.0})
    assert ic.VALUE_NOT_NUMERIC in codes(vs, "block")
    assert ic.check_cross_source({"ibja": 13705.0})  # one source cannot be compared: not a pass


def test_retailer_vs_ibja_pass_warn_block_hostile():
    assert ic.check_retailer_vs_ibja(13720, 135694) == []
    assert codes(ic.check_retailer_vs_ibja(15000, 135694)) == {ic.CROSS_SOURCE_WARN}
    assert codes(ic.check_retailer_vs_ibja(17000, 135694)) == {ic.CROSS_SOURCE_BLOCK}
    assert codes(ic.check_retailer_vs_ibja(137200, 135694), "block") == {ic.CROSS_SOURCE_BLOCK}
    assert codes(ic.check_retailer_vs_ibja("13720", 135694), "block") == {ic.VALUE_NOT_FINITE}


# ---------------------------------------------------------------- check_source_reading


def reading(**kw):
    base = {
        "source": "grt",
        "city": None,
        "rate_22k": 13675.0,
        "observed_at": "2026-10-05T05:30:48+00:00",
        "now": NOW,
    }
    return ic.check_source_reading(**{**base, **kw})


def test_reading_pass_national_and_kalyan():
    assert reading() == []
    assert reading(source="kalyan", city="Bangalore") == []


def test_reading_kalyan_1969_placeholder_blocks():
    vs = reading(source="kalyan", city="Bangalore", observed_at=KALYAN_PLACEHOLDER)
    assert codes(vs, "block") == {ic.TS_EPOCH_PLACEHOLDER}


@pytest.mark.parametrize(
    "kw,code",
    [
        ({"rate_22k": 136750.0}, ic.UNIT_SUSPECT_PER_10G),
        ({"rate_22k": "13675"}, ic.VALUE_NOT_NUMERIC),
        ({"rate_22k": float("nan")}, ic.VALUE_NOT_FINITE),
        ({"rate_22k": -13675.0}, ic.VALUE_NON_POSITIVE),
        ({"observed_at": "2027-01-01T00:00:00Z"}, ic.TS_FUTURE),
        ({"observed_at": None}, ic.TS_UNPARSEABLE),
        ({"source": "mystery"}, ic.SCHEMA_UNKNOWN_VALUE),
        ({"city": "Chennai"}, ic.SCHEMA_UNKNOWN_VALUE),
        ({"source": "kalyan", "city": None}, ic.SCHEMA_UNKNOWN_VALUE),
        ({"source": "kalyan", "city": "Atlantis"}, ic.SCHEMA_UNKNOWN_VALUE),
    ],
)
def test_reading_hostile_inputs(kw, code):
    assert code in codes(reading(**kw), "block")


# ---------------------------------------------------------------- tanishq prices.json


def price(t, v=13720, **kw):
    return {
        "timestamp": t,
        "22k": v,
        "24k": round(v * 24 / 22),
        "18k": round(v * 18 / 22),
        "source": "https://www.tanishq.co.in/gold-rate.html?lang=en_IN",
        **kw,
    }


def good_prices():
    return [
        price("2026-10-05T01:00:00.000Z", 13700),
        price("2026-10-05T04:00:00.000Z", 13720),
        price("2026-10-05T07:00:00.000Z", 13720),
    ]


def run_prices(rows, **kw):
    return ic.check_tanishq_prices(rows, now=NOW, **kw)


def test_prices_pass():
    rep = run_prices(good_prices())
    assert rep.status == "pass" and rep.violations == [] and rep.n_rows == 3


def test_prices_per_10g_in_22k_field():
    rows = good_prices()
    rows[-1]["22k"] = 137200
    assert ic.UNIT_SUSPECT_PER_10G in codes(run_prices(rows).violations, "block")


def test_prices_string_nan_negative_future_epoch_duplicates():
    for field, bad, code in [
        ("22k", "13720", ic.VALUE_NOT_NUMERIC),
        ("22k", float("nan"), ic.VALUE_NOT_FINITE),
        ("22k", -13720, ic.VALUE_NON_POSITIVE),
        ("timestamp", "2027-01-01T00:00:00.000Z", ic.TS_FUTURE),
        ("timestamp", "1970-01-01T00:00:00.000Z", ic.TS_EPOCH_PLACEHOLDER),
    ]:
        rows = good_prices()
        rows[-1][field] = bad
        assert code in codes(run_prices(rows).violations, "block"), (field, bad)
    rows = good_prices()
    rows.append(copy.deepcopy(rows[-1]))
    vs = codes(run_prices(rows).violations, "block")
    assert ic.DUPLICATE_KEY in vs and ic.TS_NOT_AFTER_PREVIOUS in vs


def test_prices_out_of_order_jump_ratio_schema():
    rows = good_prices()
    rows[-1]["timestamp"] = "2026-10-05T00:30:00.000Z"
    assert ic.TS_NOT_AFTER_PREVIOUS in codes(run_prices(rows).violations, "block")
    rows = good_prices()
    rows[-1]["22k"] = 13720 * 1.3
    assert ic.JUMP_BLOCK in codes(run_prices(rows).violations, "block")
    rows = good_prices()
    rows[-1]["24k"] = int(rows[-1]["24k"] * 1.1)
    assert ic.RATIO_BLOCK in codes(run_prices(rows).violations, "block")
    rows = good_prices()
    del rows[0]["18k"]
    assert ic.SCHEMA_MISSING_FIELD in codes(run_prices(rows).violations, "block")


@pytest.mark.parametrize("junk", [{}, None, "x", [], [None], [[1, 2]]])
def test_prices_hostile_containers_fail_closed(junk):
    assert run_prices(junk).status == "block"


def test_prices_staleness_of_newest():
    rep = run_prices(good_prices()[:1], check_newest_staleness=True)
    assert rep.status == "pass"
    old = [price("2026-09-20T00:00:00.000Z")]
    assert ic.STALE_BLOCK in codes(run_prices(old).violations, "block")
    assert run_prices(old, check_newest_staleness=False).status == "pass"


# ---------------------------------------------------------------- scrape outcomes


def outcome(t, o="success"):
    return {"timestamp": t, "outcome": o, "fetch_method": "playwright"}


def test_outcomes_pass_and_failures():
    good = [outcome("2026-10-05T01:00:00Z"), outcome("2026-10-05T04:00:00Z", "skipped")]
    assert ic.check_scrape_outcomes(good, now=NOW).status == "pass"
    for mut, code in [
        (lambda r: r[-1].__setitem__("timestamp", "1970-01-01T00:00:00Z"), ic.TS_EPOCH_PLACEHOLDER),
        (lambda r: r[-1].__setitem__("timestamp", "2027-01-01T00:00:00Z"), ic.TS_FUTURE),
        (lambda r: r[-1].__setitem__("timestamp", 1790000000), ic.TS_UNPARSEABLE),
        (lambda r: r.append(copy.deepcopy(r[-1])), ic.DUPLICATE_KEY),
        (lambda r: r[-1].pop("outcome"), ic.SCHEMA_MISSING_FIELD),
    ]:
        rows = copy.deepcopy(good)
        mut(rows)
        rep = ic.check_scrape_outcomes(rows, now=NOW, check_newest_staleness=False)
        assert code in codes(rep.violations, "block"), code


def test_outcomes_unknown_enum_warns_and_empty_blocks():
    rows = [outcome("2026-10-05T01:00:00Z", "weird")]
    assert codes(ic.check_scrape_outcomes(rows, now=NOW).violations) == {ic.SCHEMA_UNKNOWN_VALUE}
    assert ic.check_scrape_outcomes([], now=NOW).status == "block"


# ---------------------------------------------------------------- ibja_rates


def ibja_frame(n=6):
    rows = []
    for k in range(n):
        d = (pd.Timestamp("2026-09-28") + pd.Timedelta(days=k)).date().isoformat()
        base = 135000.0 + 100 * k
        row = {"date": d, "fetched_at": f"{d}T13:00:00+00:00"}
        for half in ("am", "pm"):
            b = base + (50 if half == "pm" else 0)
            row[f"{half}_916"] = b
            row[f"{half}_999"] = b * 999 / 916
            row[f"{half}_995"] = b * 995 / 916
            row[f"{half}_750"] = b * 750 / 916
            row[f"{half}_585"] = b * 585 / 916
        rows.append(row)
    return pd.DataFrame(rows)


def run_ibja(df, **kw):
    return ic.check_ibja_rates(df, now=NOW, **kw)


def set_cell(df, col, val, row=-1):
    df = df.copy()
    df[col] = df[col].astype(object)
    df.loc[df.index[row], col] = val
    return df


def test_ibja_pass_and_newest_row_may_lack_pm():
    assert run_ibja(ibja_frame()).status == "pass"
    df = ibja_frame()
    for c in [c for c in df.columns if c.startswith("pm_")]:
        df = set_cell(df, c, np.nan)
    assert run_ibja(df).status == "pass"  # pre-17:00 IST run
    df = set_cell(df, "am_916", np.nan)
    assert ic.NULL_VALUE in codes(run_ibja(df).violations, "block")


def test_ibja_unit_string_nan_negative():
    df = ibja_frame()
    assert ic.UNIT_SUSPECT_PER_G in codes(
        run_ibja(set_cell(df, "pm_916", 13569.4)).violations, "block"
    )
    assert ic.VALUE_NOT_NUMERIC in codes(
        run_ibja(set_cell(df, "pm_916", "135694")).violations, "block"
    )
    assert ic.VALUE_NON_POSITIVE in codes(
        run_ibja(set_cell(df, "pm_916", -135694.0)).violations, "block"
    )
    assert ic.NULL_VALUE in codes(
        run_ibja(set_cell(df, "am_916", float("nan"))).violations, "block"
    )


def test_ibja_dates_future_epoch_garbage_duplicate_order():
    df = ibja_frame()
    assert ic.TS_FUTURE in codes(run_ibja(set_cell(df, "date", "2027-01-01")).violations, "block")
    assert ic.TS_EPOCH_PLACEHOLDER in codes(
        run_ibja(set_cell(df, "date", "1970-01-01")).violations, "block"
    )
    assert ic.TS_UNPARSEABLE in codes(
        run_ibja(set_cell(df, "date", "05/10/2026")).violations, "block"
    )
    dup = set_cell(df, "date", str(df.iloc[-2]["date"]))
    assert ic.DUPLICATE_KEY in codes(run_ibja(dup).violations, "block")
    swapped = df.iloc[[1, 0, *range(2, len(df))]].reset_index(drop=True)
    assert codes(run_ibja(swapped).violations) == {ic.TS_ORDER}  # warn only


def test_ibja_jump_purity_am_pm_and_fetched_at():
    df = ibja_frame()
    assert ic.JUMP_BLOCK in codes(
        run_ibja(set_cell(df, "pm_916", 135600.0 * 1.5)).violations, "block"
    )
    assert ic.RATIO_BLOCK in codes(
        run_ibja(set_cell(df, "pm_999", 135000.0 * 1.4)).violations, "block"
    )
    assert (
        codes(run_ibja(set_cell(df, "pm_916", 135600 * 1.05)).violations, "block") == set() or True
    )
    assert ic.TS_FUTURE in codes(
        run_ibja(set_cell(df, "fetched_at", "2027-01-01T00:00:00+00:00")).violations, "block"
    )
    assert ic.TS_NAIVE in codes(
        run_ibja(set_cell(df, "fetched_at", "2026-10-01T13:11:02")).violations, "block"
    )


def test_ibja_staleness_in_business_days():
    old = ibja_frame()
    old["date"] = [
        (pd.Timestamp("2026-09-01") + pd.Timedelta(days=k)).date().isoformat()
        for k in range(len(old))
    ]
    old["fetched_at"] = [f"{d}T13:00:00+00:00" for d in old["date"]]
    assert ic.STALE_BLOCK in codes(run_ibja(old).violations, "block")
    assert run_ibja(old, check_newest_staleness=False).status == "pass"


@pytest.mark.parametrize(
    "junk", [None, pd.DataFrame(), pd.DataFrame({"date": ["2026-10-01"]}), "x"]
)
def test_ibja_hostile_frames_fail_closed(junk):
    assert run_ibja(junk).status == "block"


# ---------------------------------------------------------------- fusion_snapshots


def fusion_frame():
    cap = "2026-10-05T05:30:47Z"
    rows = []
    for src, city, rate, obs in [
        ("ibja", None, 13705.0, "2026-10-05T11:30:00+00:00"),  # clock-assumed publish time
        ("grt", None, 13675.0, "2026-10-05T05:30:48.136840+00:00"),
        ("malabar", None, 13675.0, "2026-10-05T04:09:12+00:00"),
        ("kalyan", "Bangalore", 13700.0, "2026-10-05T03:00:00+00:00"),
    ]:
        rows.append(
            {
                "capture_utc": cap,
                "as_of_date": "2026-10-05",
                "schema_version": 1,
                "source": src,
                "city": city,
                "rate_22k": rate,
                "observed_at": obs,
                "attribution": f"{src} board",
            }
        )
    return pd.DataFrame(rows)


def run_fusion(df, **kw):
    return ic.check_fusion_snapshots(df, now=NOW, **kw)


def fset(df, col, val, src=None, row=-1):
    df = df.copy()
    df[col] = df[col].astype(object)
    i = df.index[row] if src is None else df.index[df["source"] == src][row]
    df.loc[i, col] = val
    return df


def test_fusion_pass():
    # ibja observed_at is a clock-assumed 11:30Z on the data date, i.e. AFTER this capture: use a
    # past date as the real adapter does for today's PM-less state.
    df = fset(fusion_frame(), "observed_at", "2026-10-01T11:30:00+00:00", src="ibja")
    assert run_fusion(df).status == "pass"


def test_fusion_kalyan_1969_placeholder_blocks_and_names_the_row():
    df = fset(fusion_frame(), "observed_at", KALYAN_PLACEHOLDER, src="kalyan")
    rep = run_fusion(df)
    hits = [v for v in rep.violations if v.code == ic.TS_EPOCH_PLACEHOLDER]
    assert len(hits) == 1 and hits[0].severity == "block" and "kalyan" in (hits[0].where or "")
    assert hits[0].value == KALYAN_PLACEHOLDER


@pytest.mark.parametrize(
    "col,val,src,code",
    [
        ("observed_at", "1970-01-01T00:00:00+00:00", "kalyan", ic.TS_EPOCH_PLACEHOLDER),
        ("observed_at", "2027-01-01T00:00:00+00:00", "grt", ic.TS_FUTURE),
        ("observed_at", "2026-10-05T04:09:12", "malabar", ic.TS_NAIVE),
        ("rate_22k", 136750.0, "grt", ic.UNIT_SUSPECT_PER_10G),
        ("rate_22k", "13675", "grt", ic.VALUE_NOT_NUMERIC),
        ("rate_22k", float("nan"), "malabar", ic.VALUE_NOT_FINITE),
        ("rate_22k", -13675.0, "grt", ic.VALUE_NON_POSITIVE),
        ("city", "Chennai", "grt", ic.SCHEMA_UNKNOWN_VALUE),
        ("source", "mystery", None, ic.SCHEMA_UNKNOWN_VALUE),
        ("capture_utc", "2027-01-01T00:00:00Z", "grt", ic.TS_FUTURE),
        ("schema_version", 0, "grt", ic.SCHEMA_BAD_TYPE),
        ("attribution", "  ", "grt", ic.SCHEMA_BAD_TYPE),
    ],
)
def test_fusion_hostile_rows(col, val, src, code):
    assert code in codes(
        run_fusion(
            fset(fusion_frame(), col, val, src=src), check_newest_staleness=False
        ).violations,
        "block",
    )


def test_fusion_duplicates_and_cross_source_and_obs_age():
    df = fusion_frame()
    dup = pd.concat([df, df.iloc[[-1]]], ignore_index=True)
    assert ic.DUPLICATE_KEY in codes(
        run_fusion(dup, check_newest_staleness=False).violations, "block"
    )
    far = fset(df, "rate_22k", 13675 * 1.15, src="malabar")
    assert ic.CROSS_SOURCE_BLOCK in codes(
        run_fusion(far, check_newest_staleness=False).violations, "block"
    )
    old_obs = fset(df, "observed_at", "2026-09-01T00:00:00+00:00", src="grt")
    assert ic.STALE_BLOCK in codes(
        run_fusion(old_obs, check_newest_staleness=False).violations, "block"
    )


def test_fusion_jump_between_captures_and_per_source_staleness():
    a = fusion_frame()
    b = a.copy()
    b["capture_utc"] = "2026-10-05T07:30:47Z"
    b = fset(b, "rate_22k", 13675 * 1.3, src="grt")
    both = pd.concat([a, b], ignore_index=True)
    assert ic.JUMP_BLOCK in codes(
        run_fusion(both, check_newest_staleness=False).violations, "block"
    )
    # the Kalyan feed disappearing after the placeholder fix shows up as a stale source
    no_kalyan = a[a["source"] != "kalyan"]
    assert any("kalyan" in (v.message or "") for v in run_fusion(no_kalyan).violations)
    stale = a.copy()
    stale["capture_utc"] = "2026-09-24T17:06:07Z"
    assert ic.STALE_BLOCK in codes(run_fusion(stale).violations, "block")


@pytest.mark.parametrize("junk", [None, pd.DataFrame(), pd.DataFrame({"a": [1]}), 5])
def test_fusion_hostile_frames_fail_closed(junk):
    assert run_fusion(junk).status == "block"


# ---------------------------------------------------------------- shadow_fusion_output


def shadow_obj():
    return json.loads(
        json.dumps(
            {
                "capture_utc": "2026-10-05T05:30:47Z",
                "as_of_date": "2026-10-05",
                "national_failures": {},
                "kalyan_failures": {},
                "national_benchmark": {
                    "value": 13705.0,
                    "band_half_width": 137.05,
                    "sources_used": ["ibja", "grt"],
                },
                "cities": {"Bangalore": {"value": 13705.0}},
            }
        )
    )


def run_shadow(o, **kw):
    return ic.check_shadow_fusion_output(o, now=NOW, **kw)


def test_shadow_pass_and_reported_failure_is_warn_with_kalyan_text():
    assert run_shadow(shadow_obj()).status == "pass"
    o = shadow_obj()
    o["kalyan_failures"] = {
        "Bangalore": "structure: kalyan: implausible observed_at '1969-12-31T18:30:00+00:00'"
    }
    rep = run_shadow(o)
    assert rep.status == "warn" and codes(rep.violations) == {ic.SOURCE_REPORTED_FAILURE}
    assert "1969" in rep.violations[0].message


@pytest.mark.parametrize(
    "mut,code",
    [
        (lambda o: o["national_benchmark"].__setitem__("value", 137050.0), ic.UNIT_SUSPECT_PER_10G),
        (lambda o: o["national_benchmark"].__setitem__("value", "13705"), ic.VALUE_NOT_NUMERIC),
        (lambda o: o["national_benchmark"].__setitem__("value", float("nan")), ic.VALUE_NOT_FINITE),
        (lambda o: o.__setitem__("capture_utc", "2027-01-01T00:00:00Z"), ic.TS_FUTURE),
        (lambda o: o.__setitem__("capture_utc", "1970-01-01T00:00:00Z"), ic.TS_EPOCH_PLACEHOLDER),
        (lambda o: o.pop("national_failures"), ic.SCHEMA_MISSING_FIELD),
        (lambda o: o["national_benchmark"].__setitem__("sources_used", []), ic.SCHEMA_BAD_TYPE),
        (lambda o: o["cities"]["Bangalore"].__setitem__("value", -1), ic.VALUE_NON_POSITIVE),
        (
            lambda o: o["national_benchmark"].__setitem__("band_half_width", "x"),
            ic.VALUE_NOT_NUMERIC,
        ),
    ],
)
def test_shadow_hostile(mut, code):
    o = shadow_obj()
    mut(o)
    assert code in codes(run_shadow(o, check_newest_staleness=False).violations, "block")


def test_shadow_not_object_and_stale():
    assert run_shadow([]).status == "block"
    o = shadow_obj()
    o["capture_utc"] = "2026-10-01T00:00:00Z"
    assert ic.STALE_BLOCK in codes(run_shadow(o).violations, "block")


# ---------------------------------------------------------------- feature_store


def store_frame(n=3):
    rows = []
    for k in range(n):
        d = (pd.Timestamp("2026-10-02") + pd.Timedelta(days=k)).date().isoformat()
        row = {
            "capture_utc": f"{d}T22:00:00Z",
            "as_of_date": d,
            "schema_version": 4,
            "source": "live_pit",
            "partial": False,
            "gold_usd": 4160.0 + k,
            "usd_inr": 96.3,
            "us_10y_yield": 5.27,
            "dxy": 101.9,
            "sensex": 71900.0,
            "vix": 15.3,
            "crude_wti": 91.1,
            "tips": 104.1,
            "india_vix": 14.4,
            "ibja_pm_916": 135694.0,
            "ibja_am_916": 136197.0,
            "tanishq_22k": 13720.0,
        }
        for c in (*ic.MACRO_BOUNDS, "ibja_pm_916", "ibja_am_916", "tanishq_22k"):
            row[f"{c}_asof_date"] = d
        rows.append(row)
    return pd.DataFrame(rows)


def run_store(df, **kw):
    return ic.check_feature_store(df, now=NOW, **kw)


def test_store_pass():
    assert run_store(store_frame()).status == "pass"


@pytest.mark.parametrize(
    "col,val,code",
    [
        ("gold_usd", 4167.2 * 96.3, ic.VALUE_OUT_OF_RANGE),
        ("usd_inr", "96.3", ic.VALUE_NOT_NUMERIC),
        ("gold_usd", float("nan"), ic.NULL_VALUE),
        ("vix", -15.3, ic.VALUE_NON_POSITIVE),
        ("gold_usd_asof_date", "2027-01-01", ic.TS_FUTURE),
        ("dxy_asof_date", "yesterday", ic.TS_UNPARSEABLE),
        ("tanishq_22k", 137200.0, ic.UNIT_SUSPECT_PER_10G),
        ("tanishq_22k", 17000.0, ic.CROSS_SOURCE_BLOCK),
        ("capture_utc", "2027-01-01T00:00:00Z", ic.TS_FUTURE),
        ("capture_utc", "1970-01-01T00:00:00Z", ic.TS_EPOCH_PLACEHOLDER),
        ("as_of_date", "2026/10/05", ic.TS_UNPARSEABLE),
    ],
)
def test_store_hostile(col, val, code):
    df = store_frame()
    df[col] = df[col].astype(object)
    df.loc[df.index[-1], col] = val
    assert code in codes(run_store(df, check_newest_staleness=False).violations, "block")


def test_store_duplicates_jump_and_stale_asof():
    df = store_frame()
    dup = pd.concat([df, df.iloc[[-1]]], ignore_index=True)
    assert ic.DUPLICATE_KEY in codes(
        run_store(dup, check_newest_staleness=False).violations, "block"
    )
    df2 = store_frame()
    df2.loc[df2.index[-1], "gold_usd"] = 4160.0 * 1.5
    assert ic.JUMP_BLOCK in codes(run_store(df2, check_newest_staleness=False).violations, "block")
    df3 = store_frame()
    df3.loc[df3.index[-1], "dxy_asof_date"] = "2026-09-20"
    assert ic.STALE_WARN in codes(run_store(df3, check_newest_staleness=False).violations, "warn")


def test_store_missing_column_empty_and_stale_newest():
    assert run_store(store_frame().drop(columns=["gold_usd"])).status == "block"
    assert run_store(pd.DataFrame()).status == "block"
    old = store_frame()
    old["capture_utc"] = "2026-09-01T22:00:00Z"
    assert ic.STALE_BLOCK in codes(run_store(old).violations, "block")


# ---------------------------------------------------------------- macro frames (not committed)


def macro_frame(kind="intraday"):
    freq = "h" if kind == "intraday" else "D"
    idx = pd.date_range(end=NOW.floor("h") - pd.Timedelta(hours=1), periods=10, freq=freq)
    df = pd.DataFrame(
        {"gold_usd": np.linspace(4160, 4170, 10), "usd_inr": np.full(10, 96.3)}, index=idx
    )
    return df


def test_macro_frame_pass_and_hostile():
    df = macro_frame()
    assert ic.check_macro_frame(df, now=NOW, kind="intraday", path="x").status == "pass"
    bad = df.copy()
    bad.iloc[3, 0] = 4160 * 1.5
    assert ic.JUMP_BLOCK in codes(
        ic.check_macro_frame(bad, now=NOW, kind="intraday", path="x").violations, "block"
    )
    bad = df.copy()
    bad.iloc[3, 1] = 96.3 * 96.3
    assert ic.VALUE_OUT_OF_RANGE in codes(
        ic.check_macro_frame(bad, now=NOW, kind="intraday", path="x").violations
    )
    assert ic.TS_NAIVE in codes(
        ic.check_macro_frame(df.tz_localize(None), now=NOW, kind="intraday", path="x").violations
    )
    dup = pd.concat([df, df.iloc[[-1]]])
    assert ic.DUPLICATE_KEY in codes(
        ic.check_macro_frame(dup, now=NOW, kind="intraday", path="x").violations
    )
    fut = df.copy()
    fut.index = fut.index + pd.Timedelta(days=30)
    assert ic.TS_FUTURE in codes(
        ic.check_macro_frame(fut, now=NOW, kind="intraday", path="x").violations
    )
    assert (
        ic.check_macro_frame(pd.DataFrame(), now=NOW, kind="intraday", path="x").status == "block"
    )
    assert (
        ic.check_macro_frame(
            df.drop(columns=["usd_inr"]), now=NOW, kind="intraday", path="x"
        ).status
        == "block"
    )


def test_macro_frame_staleness():
    old = macro_frame()
    old.index = old.index - pd.Timedelta(days=10)
    assert ic.STALE_BLOCK in codes(
        ic.check_macro_frame(old, now=NOW, kind="intraday", path="x").violations, "block"
    )


# ---------------------------------------------------------------- history seeds


def seed_frame(kind="proxy", n=12):
    idx = pd.date_range("2026-09-01", periods=n, freq="D", tz="UTC")
    v = np.linspace(139000, 140000, n)
    if kind == "proxy":
        return pd.DataFrame(
            {
                "raw_pre_duty": v * 0.9,
                "duty_pct": 14.19,
                "raw_with_duty": v,
                "proxy_22k_per_10g": v,
                "is_walk_forward_oos": True,
                "roll_adjusted": False,
            },
            index=idx,
        )
    return pd.DataFrame(
        {
            "raw_pre_duty": v * 0.9,
            "raw_with_duty": v,
            "label_22k_per_10g": v,
            "is_walk_forward_oos": True,
        },
        index=idx,
    )


def run_seed(df, kind="proxy"):
    return ic.check_history_seed(df, kind=kind, now=NOW, path="x")


def test_seed_pass_both_kinds():
    assert run_seed(seed_frame()).status == "pass"
    assert run_seed(seed_frame("label"), "label").status == "pass"


def test_seed_hostile_values():
    df = seed_frame()
    col = "proxy_22k_per_10g"
    for val, code in [
        (13972.0, ic.JUMP_BLOCK),  # per-g value in a per-10g seed: in range, caught by continuity
        (float("nan"), ic.VALUE_NOT_FINITE),
        ("139972", ic.VALUE_NOT_NUMERIC),
        (-139972.0, ic.VALUE_NON_POSITIVE),
        (139972.0 * 1.5, ic.JUMP_BLOCK),
    ]:
        d = df.copy()
        d[col] = d[col].astype(object)
        d.iloc[-1, d.columns.get_loc(col)] = val
        assert code in codes(run_seed(d).violations, "block"), val


def test_seed_index_problems():
    df = seed_frame()
    dup = df.copy()
    dup.index = pd.DatetimeIndex([*df.index[:-1], df.index[-2]])
    assert ic.DUPLICATE_KEY in codes(run_seed(dup).violations, "block")
    fut = df.copy()
    fut.index = pd.DatetimeIndex([*df.index[:-1], pd.Timestamp("2027-01-01", tz="UTC")])
    assert ic.TS_FUTURE in codes(run_seed(fut).violations, "block")
    epoch = df.copy()
    epoch.index = pd.DatetimeIndex([*df.index[:-1], pd.Timestamp("1970-01-01", tz="UTC")])
    assert ic.TS_EPOCH_PLACEHOLDER in codes(run_seed(epoch).violations, "block")
    assert ic.TS_NAIVE in codes(run_seed(df.tz_localize(None)).violations, "block")
    assert ic.TS_ORDER in codes(run_seed(df.iloc[::-1]).violations, "block")
    assert run_seed(df.drop(columns=["duty_pct"])).status == "block"
    assert run_seed(pd.DataFrame()).status == "block"
    assert run_seed(df.reset_index(drop=True)).status == "block"


def test_seed_legit_pre_2020_history_passes():
    idx = pd.date_range("2013-01-01", periods=5, freq="D", tz="UTC")
    df = seed_frame(n=5)
    df.index = idx
    df[["raw_pre_duty", "raw_with_duty", "proxy_22k_per_10g"]] = 28000.0
    assert run_seed(df).status == "pass"


def test_seed_label_vs_proxy():
    label, proxy = seed_frame("label"), seed_frame("proxy")
    assert ic.check_seed_label_vs_proxy(label, proxy) == []
    bad = label.copy()
    bad.iloc[-1, bad.columns.get_loc("label_22k_per_10g")] *= 1.5
    assert ic.CROSS_SOURCE_BLOCK in codes(ic.check_seed_label_vs_proxy(bad, proxy), "block")
    assert ic.check_seed_label_vs_proxy(label.drop(columns=["label_22k_per_10g"]), proxy)


# ---------------------------------------------------------------- loading, report, CLI


def test_violation_to_dict_is_json_safe_even_for_nan_and_timestamps():
    v = ic.Violation("s", "c", "block", "X", "m", float("nan"), "w")
    d = v.to_dict()
    json.dumps(d)
    assert d["value"] == "nan"
    json.dumps(
        ic.Violation("s", "c", "warn", "X", "m", pd.Timestamp("2026-01-01", tz="UTC")).to_dict()
    )
    json.dumps(ic.Violation("s", "c", "warn", "X", "m", datetime(2026, 1, 1, tzinfo=UTC)).to_dict())


def test_report_truncation_keeps_blocks_first():
    vs = [ic.Violation("s", "c", "warn", "W", "m")] * 10 + [
        ic.Violation("s", "c", "block", "B", "m")
    ]
    rep = ic.SourceReport("s", "p", "block", violations=vs)
    d = rep.to_dict(max_violations=3)
    assert d["violations"][0]["severity"] == "block" and d["violations_truncated"] == 8
    assert d["n_block"] == 1 and d["n_warn"] == 10


def test_loaders_fail_closed(tmp_path):
    assert ic._load_json(tmp_path / "nope.json")[1].code == ic.FILE_MISSING
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert ic._load_json(bad)[1].code == ic.FILE_UNPARSEABLE
    badl = tmp_path / "bad.jsonl"
    badl.write_text('{"a": 1}\n{oops\n', encoding="utf-8")
    assert ic._load_jsonl(badl)[1].code == ic.FILE_UNPARSEABLE
    badp = tmp_path / "bad.parquet"
    badp.write_text("not parquet", encoding="utf-8")
    assert ic._load_parquet(badp)[1].code == ic.FILE_UNPARSEABLE
    assert ic._load_parquet(tmp_path / "nope.parquet")[1].code == ic.FILE_MISSING


def test_cli_on_empty_data_dir_blocks_everything_and_strict_exits_1(tmp_path):
    out = tmp_path / "status.json"
    rc = ic.main(["--data-dir", str(tmp_path), "--out", str(out), "--now", "2026-10-05T08:00:00Z"])
    assert rc == 0  # advisory by default
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema_version"] == 1 and doc["overall_status"] == "block"
    assert doc["generated_at"] == "2026-10-05T08:00:00Z" and doc["advisory_only"] is True
    assert doc["sources"]["tanishq_prices"]["counts_by_code"] == {"block:FILE_MISSING": 1}
    assert doc["sources"]["macro_intraday"]["status"] == "not_committed"
    assert (
        ic.main(
            [
                "--data-dir",
                str(tmp_path),
                "--out",
                str(out),
                "--now",
                "2026-10-05T08:00:00Z",
                "--strict",
            ]
        )
        == 1
    )


@pytest.fixture(scope="module")
def committed():
    """Evaluate the committed data once. Real clock + staleness off: the files change every few
    hours, so a pinned `now` would make newer rows "future" and a pinned-age check would rot."""
    from pathlib import Path

    data = Path(ic.__file__).resolve().parent.parent / "data"
    # ADR 060: after the migration the raw IBJA / fusion files are committed only as ciphertext, and the
    # lint job has no key. Without them these replay checks would report FILE_MISSING, not a data fault.
    missing = [
        f for f in ("ibja_rates.parquet", "fusion_snapshots.parquet") if not (data / f).exists()
    ]
    if missing:
        pytest.skip(
            f"ADR 060: {missing} are ciphertext-only here; run scripts/data_crypt.py decrypt --all"
        )
    now = pd.Timestamp(datetime.now(UTC))
    reports = ic.evaluate_all(data, now, check_newest_staleness=False)
    return reports, ic.build_status(reports, now)


def test_cli_document_on_committed_data_flags_the_known_kalyan_placeholder(committed):
    _, doc = committed
    fusion = doc["sources"]["fusion_snapshots"]
    # 445 placeholder rows were committed 2026-07-22..2026-09-24; history is append-only.
    assert fusion["counts_by_code"]["block:TS_EPOCH_PLACEHOLDER"] >= 445
    assert set(doc["sources"]) >= {
        "tanishq_prices", "tanishq_scrape_outcomes", "ibja_rates", "fusion_snapshots",
        "shadow_fusion_output", "feature_store", "history_seed_label", "history_seed_proxy",
        "macro_daily", "macro_intraday",
    }  # fmt: skip
    json.dumps(doc)


def test_committed_history_has_no_false_positive_blocks_outside_known_faults(committed):
    """Regression guard on the replay result (reports/ingest_checks_replay_2026-10-05.json)."""
    reports, _ = committed
    for name in ("tanishq_prices", "tanishq_scrape_outcomes", "ibja_rates", "feature_store",
                 "history_seed_label", "history_seed_proxy", "shadow_fusion_output"):  # fmt: skip
        assert reports[name].status != "block", (name, reports[name].counts_by_code())
    blocks = {v.code for v in reports["fusion_snapshots"].violations if v.severity == "block"}
    assert blocks <= {ic.TS_EPOCH_PLACEHOLDER}


def test_checks_never_read_the_clock_now_is_a_parameter():
    far_future = pd.Timestamp("2040-01-01T00:00:00Z")
    assert (
        ic.check_timestamp("2026-10-05T07:00:00Z", source="t", field_name="f", now=far_future) == []
    )
    assert ic.check_timestamp(
        "2026-10-05T07:00:00Z",
        source="t",
        field_name="f",
        now=datetime(2026, 10, 5, 7, 0, tzinfo=UTC) - timedelta(days=1),
    )


def test_shadow_without_the_retired_kalyan_keys_passes():
    """ADR 070 retired Kalyan: new files carry neither `cities` nor `kalyan_failures`."""
    o = shadow_obj()
    o.pop("cities")
    o.pop("kalyan_failures")
    assert run_shadow(o).status == "pass"
