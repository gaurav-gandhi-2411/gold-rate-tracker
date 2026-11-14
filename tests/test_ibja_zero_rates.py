"""A fix the page shows as 0 is no rate: it is stored as missing and filled later (P0 root cause).

ibjarates.com shows 0 for a fix that is not published yet. ``ml/ibja.py`` stored that 0 as a rate;
the upsert (``existing_pm_null``) and the PDF backfill (``_fill_null_rates``) only looked for NaN, so
the real PM fix never replaced it, and ml.nextfix then built forecast folds from a price of 0
(four folds were set aside on 2026-11-13: days 2026-11-06, 11-10, 11-11, 11-12). Every test pins
the date; none reads the real clock.
"""

from __future__ import annotations

from datetime import date

import ml.ibja as ibja
import pandas as pd
import pytest

_DAY = date(2026, 11, 12)


class _FakeDate(date):
    @classmethod
    def today(cls) -> _FakeDate:
        return cls(_DAY.year, _DAY.month, _DAY.day)


def _page(am: str, pm: str) -> str:
    return (
        "<html><body><table><thead><tr><th>Purity</th><th>AM</th><th>PM</th></tr></thead><tbody>"
        f"<tr><td>Gold 999</td><td>{am}</td><td>{pm}</td></tr>"
        f"<tr><td>Gold 916</td><td>{am}</td><td>{pm}</td></tr>"
        "</tbody></table></body></html>"
    )


@pytest.fixture(autouse=True)
def _pinned(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(ibja, "date", _FakeDate)
    monkeypatch.setattr(ibja, "datetime", _FakeNow)  # fetched_at stamps read the clock too


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (144490.0, True),
        (1, True),
        (0, False),
        (0.0, False),
        (-5.0, False),
        (float("nan"), False),
        (float("inf"), False),
        (None, False),
        ("144490", False),
    ],
)
def test_is_rate(value, expected):
    assert ibja.is_rate(value) is expected


def test_a_zero_on_the_page_is_stored_as_missing_not_as_a_rate(monkeypatch):
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: _page("144564", "0"))
    result = ibja.fetch_ibja_daily()
    assert result["am_916"] == 144564.0
    assert "pm_916" not in result and "pm_999" not in result


def test_a_page_of_only_zeros_is_a_failed_fetch_not_a_row_of_zeros(monkeypatch, tmp_path):
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: _page("0", "0"))
    assert ibja.fetch_ibja_daily() == {}
    p = tmp_path / "ibja.parquet"
    assert ibja.append_ibja_today(p) is None and not p.exists()


def test_an_early_run_writes_no_zero_and_the_post_fix_run_fills_the_pm_rate(monkeypatch, tmp_path):
    p = tmp_path / "ibja.parquet"
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: _page("144564", "0"))
    assert ibja.append_ibja_today(p) is True
    row = pd.read_parquet(p).iloc[0]
    assert row["date"] == "2026-11-12" and pd.isna(row.get("pm_916", float("nan")))
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: _page("144564", "144490"))
    assert ibja.append_ibja_today(p) is True
    assert pd.read_parquet(p).iloc[0]["pm_916"] == 144490.0


def test_a_stored_zero_pm_rate_is_replaced_by_the_real_fix(monkeypatch, tmp_path):
    # the stored state in the real outage: a row whose pm_916 is 0.0 (not NaN)
    p = tmp_path / "ibja.parquet"
    pd.DataFrame(
        [{"date": "2026-11-12", "fetched_at": "x", "am_916": 144564.0, "pm_916": 0.0}]
    ).to_parquet(p, index=False)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: _page("144564", "144490"))
    assert ibja.append_ibja_today(p) is True
    df = pd.read_parquet(p)
    assert len(df) == 1 and df.iloc[0]["pm_916"] == 144490.0


def test_a_stored_positive_pm_rate_is_still_never_overwritten(monkeypatch, tmp_path):
    p = tmp_path / "ibja.parquet"
    pd.DataFrame(
        [{"date": "2026-11-12", "fetched_at": "x", "am_916": 144564.0, "pm_916": 144000.0}]
    ).to_parquet(p, index=False)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: _page("144564", "144490"))
    assert ibja.append_ibja_today(p) is False
    assert pd.read_parquet(p).iloc[0]["pm_916"] == 144000.0


def test_the_pdf_backfill_replaces_a_stored_zero_and_leaves_positive_rates():
    stored = pd.DataFrame(
        [
            {"date": "2026-11-11", "am_916": 100.0, "pm_916": 0.0},
            {"date": "2026-11-10", "am_916": 100.0, "pm_916": 101.0},
        ]
    )
    pdf = pd.DataFrame(
        [
            {"date": "2026-11-11", "am_916": 999.0, "pm_916": 144490.0},
            {"date": "2026-11-10", "am_916": 999.0, "pm_916": 555.0},
        ]
    )
    out, changed = ibja._fill_null_rates(stored, pdf)
    by = out.set_index("date")
    assert changed == 1
    assert by.loc["2026-11-11", "pm_916"] == 144490.0 and by.loc["2026-11-11", "am_916"] == 100.0
    assert by.loc["2026-11-10", "pm_916"] == 101.0  # a stored rate is never overwritten


def test_the_pdf_backfill_does_not_copy_a_zero_over_a_missing_value():
    stored = pd.DataFrame([{"date": "2026-11-11", "am_916": 100.0, "pm_916": float("nan")}])
    pdf = pd.DataFrame([{"date": "2026-11-11", "am_916": 100.0, "pm_916": 0.0}])
    out, changed = ibja._fill_null_rates(stored, pdf)
    assert changed == 0 and pd.isna(out.iloc[0]["pm_916"])


@pytest.mark.parametrize(
    ("make", "expected"),
    [
        (lambda np: np.float64(144490.0), True),
        (lambda np: np.float32(144490.0), True),
        (lambda np: np.int64(144490), True),  # an int-typed parquet column is still a rate
        (lambda np: np.int32(5), True),
        (lambda np: np.int64(0), False),
        (lambda np: np.float32(0.0), False),
        (lambda np: np.float64("nan"), False),
        (lambda np: np.bool_(True), False),
        (lambda np: True, False),
    ],
)
def test_is_rate_accepts_every_numpy_number_type(make, expected):
    import numpy as np

    assert ibja.is_rate(make(np)) is expected
    assert ibja.is_rate(pd.NA) is False


def test_an_int_typed_stored_rate_is_not_overwritten_or_rewritten(tmp_path, monkeypatch):
    # an int64 pm_916 column read back from parquet must count as a stored rate, not as missing
    p = tmp_path / "ibja.parquet"
    pd.DataFrame(
        [{"date": "2026-11-12", "fetched_at": "x", "am_916": 144564, "pm_916": 144000}]
    ).to_parquet(p, index=False)
    assert str(pd.read_parquet(p)["pm_916"].dtype).startswith("int")
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: _page("144564", "144490"))
    assert ibja.append_ibja_today(p) is False
    assert pd.read_parquet(p).iloc[0]["pm_916"] == 144000
    pdf = pd.DataFrame([{"date": "2026-11-12", "am_916": 999.0, "pm_916": 555.0}])
    out, changed = ibja._fill_null_rates(pd.read_parquet(p), pdf)
    assert changed == 0 and out.iloc[0]["pm_916"] == 144000


class _FakeNow:
    @classmethod
    def now(cls, tz=None):
        from datetime import datetime

        return datetime(2026, 11, 14, 6, 0, tzinfo=tz)


def _fake_pdfplumber(monkeypatch, table_rows):
    """A pdfplumber stand-in whose one page has the given table (header + units + data rows)."""
    import sys
    import types

    class _Page:
        def extract_tables(self):
            return [[["Date"] * 13, ["units"] * 13, *table_rows]]

    class _Pdf:
        def __init__(self):
            self.pages = [_Page()]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    mod = types.ModuleType("pdfplumber")
    mod.open = lambda _b: _Pdf()
    monkeypatch.setitem(sys.modules, "pdfplumber", mod)
    monkeypatch.setattr(ibja, "datetime", _FakeNow)


def _pdf_row(day, am916, pm916):
    # Date, 999 AM/PM, 995 AM/PM, 916 AM/PM, 750 AM/PM, 585 AM/PM, silver AM/PM
    cells = [
        "1000",
        "1000",
        "990",
        "990",
        str(am916),
        str(pm916),
        "750",
        "750",
        "585",
        "585",
        "1",
        "1",
    ]
    return [day, *cells]


def test_the_pdf_parse_stores_a_zero_rate_as_missing(monkeypatch):
    _fake_pdfplumber(
        monkeypatch,
        [_pdf_row("11-Nov-26", "144000", "0"), _pdf_row("12-Nov-26", "144500", "144490")],
    )
    frame = ibja._parse_ibja_pdf(b"%PDF-fake")
    by = frame.set_index("date")
    assert pd.isna(by.loc["2026-11-11", "pm_916"]) and by.loc["2026-11-11", "am_916"] == 144000.0
    assert by.loc["2026-11-12", "pm_916"] == 144490.0
    assert (frame[[c for c in frame.columns if c.startswith(("am_", "pm_"))]].stack() > 0).all()


def test_the_pdf_parse_of_no_rows_is_an_empty_frame_not_an_error(monkeypatch):
    _fake_pdfplumber(monkeypatch, [])
    assert ibja._parse_ibja_pdf(b"%PDF-fake").empty
