"""Tests for ml/macro_drivers.py (item 8, ADR 053) -- release-lag alignment and
no-look-ahead merges, all synthetic. No network access."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from ml import macro_drivers as md

# ---------------------------------------------------------------------------
# COT release-lag
# ---------------------------------------------------------------------------


def test_cot_release_available_date_ordinary_tuesday() -> None:
    # 2024-01-02 is a Tuesday; +3 days = 2024-01-05 (Friday), not a US federal
    # holiday; +1 more conservative business day (GC=F's early close vs COT's
    # 15:30 ET release, see module docstring) = Monday 2024-01-08.
    assert md.cot_release_available_date(date(2024, 1, 2)) == date(2024, 1, 8)


def test_cot_release_available_date_shifts_past_a_holiday_friday() -> None:
    # 2025-07-01 is a Tuesday; +3 days = 2025-07-04 (Friday), Independence Day,
    # shifts to Monday 2025-07-07; +1 more conservative business day = Tuesday
    # 2025-07-08.
    assert md.cot_release_available_date(date(2025, 7, 1)) == date(2025, 7, 8)


def test_cot_release_available_date_never_moves_earlier() -> None:
    for offset in range(0, 60, 7):
        d = date(2020, 1, 7) + timedelta(days=offset)
        if d.weekday() != 1:
            continue
        assert md.cot_release_available_date(d) >= d + timedelta(days=4)


@pytest.mark.parametrize(
    ("as_of", "forced"),
    [
        (date(2013, 10, 1), date(2013, 10, 28)),
        (date(2013, 10, 8), date(2013, 10, 28)),
        (date(2018, 12, 18), date(2019, 3, 11)),
        (date(2019, 1, 22), date(2019, 3, 11)),
    ],
)
def test_cot_release_available_date_shutdown_overrides(as_of: date, forced: date) -> None:
    # The forced catch-up date (2013-10-25, a Friday; 2019-03-08, a Friday) plus the
    # same +1-business-day conservative buffer as the ordinary path.
    assert md.cot_release_available_date(as_of) == forced


def test_cot_release_available_date_outside_shutdown_window_unaffected() -> None:
    # A Tuesday safely after the 2013 window closes: back to the ordinary Friday+3
    # (+1 conservative business day) rule, landing on a date the shutdown override
    # would never produce.
    assert md.cot_release_available_date(date(2013, 10, 29)) == date(2013, 11, 4)


# ---------------------------------------------------------------------------
# Real yield release-lag
# ---------------------------------------------------------------------------


def test_real_yield_available_date_is_the_next_calendar_day() -> None:
    assert md.real_yield_available_date(date(2024, 3, 4)) == date(2024, 3, 5)


def test_real_yield_available_date_never_same_day() -> None:
    for offset in range(10):
        d = date(2024, 1, 1) + timedelta(days=offset)
        assert md.real_yield_available_date(d) > d


# ---------------------------------------------------------------------------
# No-look-ahead alignment
# ---------------------------------------------------------------------------


def test_align_feature_to_decision_dates_never_pulls_a_future_value() -> None:
    source = pd.DataFrame(
        {
            "available_date": pd.to_datetime(["2024-01-05", "2024-01-12", "2024-01-19"]),
            "v": [1.0, 2.0, 3.0],
        }
    )
    decisions = pd.Series(pd.to_datetime(["2024-01-05", "2024-01-10", "2024-01-20"]))
    out = md.align_feature_to_decision_dates(source, ["v"], decisions)
    assert out["v"].tolist() == [1.0, 1.0, 3.0]


def test_align_feature_to_decision_dates_before_any_release_is_nan() -> None:
    source = pd.DataFrame({"available_date": pd.to_datetime(["2024-06-01"]), "v": [9.0]})
    decisions = pd.Series(pd.to_datetime(["2024-01-01", "2024-12-01"]))
    out = md.align_feature_to_decision_dates(source, ["v"], decisions)
    assert pd.isna(out["v"].iloc[0])
    assert out["v"].iloc[1] == 9.0


def test_align_feature_to_decision_dates_preserves_input_order() -> None:
    source = pd.DataFrame(
        {"available_date": pd.to_datetime(["2024-01-01", "2024-02-01"]), "v": [1.0, 2.0]}
    )
    # Out-of-order decision dates -- output must still line up positionally
    # with the INPUT `decisions` series, not the sorted order used internally.
    decisions = pd.Series(pd.to_datetime(["2024-03-01", "2024-01-15", "2023-12-01"]))
    out = md.align_feature_to_decision_dates(source, ["v"], decisions)
    assert out["v"].iloc[0] == 2.0
    assert out["v"].iloc[1] == 1.0
    assert pd.isna(out["v"].iloc[2])


def test_align_feature_to_decision_dates_handles_mixed_date_and_string_dtypes() -> None:
    """Regression: `fetch_cot_disaggregated`/`fetch_real_yield` build `available_date`
    as a column of raw Python `date` objects (via `.map(...)`), while
    `as_of_date` (the real `decision_dates` argument) is a column of ISO date
    strings. `pd.to_datetime` infers a DIFFERENT datetime64 unit for each shape
    ([s] for `date` objects, [us] for strings), which made the very first CI run
    of this function raise `pandas.errors.MergeError: incompatible merge keys`
    (2026-09-24) -- neither of this file's other alignment tests used raw `date`
    objects on the source side, so none of them caught it."""
    source = pd.DataFrame(
        {"available_date": [date(2024, 1, 5), date(2024, 1, 12)], "v": [1.0, 2.0]}
    )
    decisions = pd.Series(["2024-01-05", "2024-01-10", "2024-01-20"])
    out = md.align_feature_to_decision_dates(source, ["v"], decisions)
    assert out["v"].tolist() == [1.0, 1.0, 2.0]


def test_align_feature_equal_available_date_is_usable_same_day() -> None:
    """A decision date exactly equal to a source row's available_date must see
    that row -- "usable at/after" means the boundary itself counts."""
    source = pd.DataFrame({"available_date": pd.to_datetime(["2024-01-05"]), "v": [7.0]})
    decisions = pd.Series(pd.to_datetime(["2024-01-05"]))
    out = md.align_feature_to_decision_dates(source, ["v"], decisions)
    assert out["v"].iloc[0] == 7.0


# ---------------------------------------------------------------------------
# COT / real-yield derived features
# ---------------------------------------------------------------------------


def test_add_cot_features_net_pct_oi_and_change() -> None:
    cot = pd.DataFrame(
        {
            "report_date": [date(2024, 1, i) for i in (2, 9, 16, 23, 30)],
            "open_interest_all": [1000.0] * 5,
            "managed_money_long": [300.0, 320.0, 340.0, 360.0, 380.0],
            "managed_money_short": [100.0, 100.0, 100.0, 100.0, 100.0],
        }
    )
    out = md.add_cot_features(cot, z_window_weeks=3, change_weeks=2)
    assert out["cot_net_pct_oi"].tolist() == pytest.approx([20.0, 22.0, 24.0, 26.0, 28.0])
    # chg_4w here uses change_weeks=2: row 2 (24.0) - row 0 (20.0) = 4.0
    assert out["cot_net_pct_oi_chg_4w"].iloc[2] == pytest.approx(4.0)
    assert pd.isna(out["cot_net_pct_oi_chg_4w"].iloc[0])
    # z-score needs a FULL window (z_window_weeks=3): NaN for the first two rows.
    assert pd.isna(out["cot_net_pct_oi_z"].iloc[1])
    assert not pd.isna(out["cot_net_pct_oi_z"].iloc[2])


def test_add_real_yield_features_change() -> None:
    ry = pd.DataFrame(
        {
            "quote_date": [date(2024, 1, 1) + timedelta(days=i) for i in range(25)],
            "real_yield_10y": [1.5 + 0.01 * i for i in range(25)],
        }
    )
    out = md.add_real_yield_features(ry, change_days=20)
    assert out["real_yield_10y_chg_4w"].iloc[20] == pytest.approx(0.20)
    assert pd.isna(out["real_yield_10y_chg_4w"].iloc[19])


# ---------------------------------------------------------------------------
# Parsing -- raises loudly on empty/malformed input, never silently passes
# ---------------------------------------------------------------------------


def test_parse_cot_json_raises_on_empty_rows() -> None:
    with pytest.raises(md.MacroFetchError, match="zero rows"):
        md._parse_cot_json([])


def test_parse_cot_json_rejects_nonpositive_open_interest() -> None:
    rows = [
        {
            "report_date_as_yyyy_mm_dd": "2024-01-02T00:00:00.000",
            "open_interest_all": "0",
            "m_money_positions_long_all": "10",
            "m_money_positions_short_all": "5",
        }
    ]
    with pytest.raises(md.MacroFetchError, match="non-positive"):
        md._parse_cot_json(rows)


def test_parse_cot_json_happy_path() -> None:
    rows = [
        {
            "report_date_as_yyyy_mm_dd": "2024-01-02T00:00:00.000",
            "open_interest_all": "1000",
            "m_money_positions_long_all": "300",
            "m_money_positions_short_all": "100",
        }
    ]
    out = md._parse_cot_json(rows)
    assert out["report_date"].iloc[0] == date(2024, 1, 2)
    assert out["open_interest_all"].iloc[0] == 1000.0


_SAMPLE_REAL_YIELD_XML = """<?xml version="1.0" encoding="utf-8" standalone="yes" ?>
<pre><title xmlns="http://www.w3.org/2005/Atom" type="text">x</title>
<entry xmlns="http://www.w3.org/2005/Atom">
<content type="application/xml">
<m:properties xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
<d:NEW_DATE xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices" m:type="Edm.DateTime">2024-01-02T00:00:00</d:NEW_DATE>
<d:TC_5YEAR xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices" m:type="Edm.Double">1.76</d:TC_5YEAR>
<d:TC_10YEAR xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices" m:type="Edm.Double">1.74</d:TC_10YEAR>
</m:properties>
</content>
</entry>
</pre>
"""


def test_parse_real_yield_xml_extracts_date_and_10y() -> None:
    out = md._parse_real_yield_xml(_SAMPLE_REAL_YIELD_XML)
    assert out["quote_date"].iloc[0] == date(2024, 1, 2)
    assert out["real_yield_10y"].iloc[0] == pytest.approx(1.74)


def test_parse_real_yield_xml_raises_on_no_rows() -> None:
    with pytest.raises(md.MacroFetchError, match="zero usable rows"):
        md._parse_real_yield_xml('<?xml version="1.0"?><pre></pre>')


# ---------------------------------------------------------------------------
# Retry/fail-loudly plumbing
# ---------------------------------------------------------------------------


def test_get_with_retry_raises_macro_fetch_error_after_exhausting_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def _always_fails(*_args: object, **_kwargs: object) -> None:
        calls["n"] += 1
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr(md.requests, "get", _always_fails)
    monkeypatch.setattr(md.time, "sleep", lambda _seconds: None)
    with pytest.raises(md.MacroFetchError, match="failed after 3 attempts"):
        md._get_with_retry("https://example.invalid/x", max_retries=3)
    assert calls["n"] == 3


def test_get_with_retry_succeeds_after_one_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

    def _fail_once_then_succeed(*_args: object, **_kwargs: object) -> _FakeResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("simulated timeout")
        return _FakeResponse()

    monkeypatch.setattr(md.requests, "get", _fail_once_then_succeed)
    monkeypatch.setattr(md.time, "sleep", lambda _seconds: None)
    resp = md._get_with_retry("https://example.invalid/x", max_retries=3)
    assert calls["n"] == 2
    assert isinstance(resp, _FakeResponse)
