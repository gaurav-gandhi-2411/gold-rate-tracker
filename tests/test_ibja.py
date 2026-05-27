"""Tests for ml.ibja — all HTTP mocked, no live requests."""

from __future__ import annotations

from pathlib import Path

import ml.ibja as ibja
import pandas as pd

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_IBJA_HTML = """\
<html><body>
<table>
  <thead><tr><th>Purity</th><th>AM</th><th>PM</th></tr></thead>
  <tbody>
    <tr><td>Gold 999</td><td>157821</td><td>157739</td></tr>
    <tr><td>Gold 995</td><td>157189</td><td>157107</td></tr>
    <tr><td>Gold 916</td><td>144564</td><td>144490</td></tr>
    <tr><td>Gold 750</td><td>118366</td><td>118304</td></tr>
    <tr><td>Gold 585</td><td>92325</td><td>92278</td></tr>
  </tbody>
</table>
</body></html>
"""

_MISSING_PURITY_HTML = """\
<html><body>
<table>
  <thead><tr><th>Metal</th><th>Buy</th><th>Sell</th></tr></thead>
  <tbody>
    <tr><td>Silver</td><td>100</td><td>101</td></tr>
  </tbody>
</table>
</body></html>
"""


# ---------------------------------------------------------------------------
# fetch_ibja_daily
# ---------------------------------------------------------------------------


def test_fetch_returns_all_purities(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: _IBJA_HTML)
    result = ibja.fetch_ibja_daily()
    assert result["am_999"] == 157821.0
    assert result["pm_999"] == 157739.0
    assert result["am_995"] == 157189.0
    assert result["pm_585"] == 92278.0


def test_fetch_returns_ten_keys(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: _IBJA_HTML)
    result = ibja.fetch_ibja_daily()
    assert len(result) == 10  # 5 purities × 2 (AM + PM)


def test_fetch_network_failure_returns_empty(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: None)
    assert ibja.fetch_ibja_daily() == {}


def test_fetch_empty_html_returns_empty(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: "<html><body></body></html>")
    assert ibja.fetch_ibja_daily() == {}


def test_fetch_unrecognised_purities_returns_empty(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: _MISSING_PURITY_HTML)
    assert ibja.fetch_ibja_daily() == {}


def test_fetch_916_am_correct(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: _IBJA_HTML)
    result = ibja.fetch_ibja_daily()
    assert result["am_916"] == 144564.0


# ---------------------------------------------------------------------------
# load_ibja_parquet
# ---------------------------------------------------------------------------


def test_load_returns_empty_if_missing(tmp_path):
    df = ibja.load_ibja_parquet(tmp_path / "nonexistent.parquet")
    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_load_returns_dataframe(tmp_path):
    p = tmp_path / "ibja.parquet"
    pd.DataFrame([{"date": "2026-05-18", "am_999": 157821.0}]).to_parquet(p, index=False)
    df = ibja.load_ibja_parquet(p)
    assert len(df) == 1
    assert df.iloc[0]["am_999"] == 157821.0


# ---------------------------------------------------------------------------
# append_ibja_today
# ---------------------------------------------------------------------------


def test_append_creates_parquet(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: _IBJA_HTML)
    p = tmp_path / "ibja.parquet"
    assert ibja.append_ibja_today(p) is True
    assert p.exists()


def test_append_adds_date_column(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: _IBJA_HTML)
    p = tmp_path / "ibja.parquet"
    ibja.append_ibja_today(p)
    df = pd.read_parquet(p)
    assert "date" in df.columns
    assert len(df) == 1


def test_append_does_not_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: _IBJA_HTML)
    p = tmp_path / "ibja.parquet"
    ibja.append_ibja_today(p)
    assert ibja.append_ibja_today(p) is False
    assert len(pd.read_parquet(p)) == 1


def test_append_returns_none_on_fetch_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: None)
    p = tmp_path / "ibja.parquet"
    assert ibja.append_ibja_today(p) is None
    assert not p.exists()


def test_append_preserves_existing_rows(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: _IBJA_HTML)
    p = tmp_path / "ibja.parquet"
    seed = pd.DataFrame([{"date": "2026-05-01", "am_999": 155000.0}])
    seed.to_parquet(p, index=False)
    ibja.append_ibja_today(p)
    df = pd.read_parquet(p)
    assert len(df) == 2
    assert "2026-05-01" in df["date"].values


# ---------------------------------------------------------------------------
# Fixtures for backfill_ibja_from_pdf tests
# ---------------------------------------------------------------------------

_IBJA_HTML_WITH_PDF_LINK = """\
<html><body>
<table id="TodayRatesTableDataYes" class="tableContainer ctrate">
  <tr><th>Purity</th><th>AM</th><th>PM </th></tr>
  <tr>
    <td>Gold 999</td>
    <td><span id="lblGold999_AM">157821</span></td>
    <td><span id="lblGold999_PM">157739</span></td>
  </tr>
</table>
<a href="../UploadedFiles/30DaysPdf/Pdf_3078_20260518_Daily Opening and Closing Market Rate.pdf">
  Previous 30 Days
</a>
</body></html>
"""

_FIXTURE_PDF_PATH = Path(__file__).parent / "fixtures" / "ibja_30day_sample.pdf"


def _make_backfill_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2026-05-18",
                "fetched_at": "2026-05-18T00:00:00+00:00",
                "am_999": 157821.0,
                "pm_999": 157739.0,
                "am_995": 157189.0,
                "pm_995": 157107.0,
                "am_916": 144564.0,
                "pm_916": 144489.0,
                "am_750": 118366.0,
                "pm_750": 118304.0,
                "am_585": 92325.0,
                "pm_585": 92277.0,
            },
            {
                "date": "2026-05-15",
                "fetched_at": "2026-05-18T00:00:00+00:00",
                "am_999": 158159.0,
                "pm_999": 158210.0,
                "am_995": 157526.0,
                "pm_995": 157577.0,
                "am_916": 144874.0,
                "pm_916": 144920.0,
                "am_750": 118619.0,
                "pm_750": 118658.0,
                "am_585": 92523.0,
                "pm_585": 92553.0,
            },
        ]
    )


# ---------------------------------------------------------------------------
# _extract_pdf_url
# ---------------------------------------------------------------------------


def test_extract_pdf_url_found():
    url = ibja._extract_pdf_url(_IBJA_HTML_WITH_PDF_LINK)
    assert url is not None
    assert url.startswith("https://ibjarates.com/")
    assert url.endswith(".pdf")
    assert "30DaysPdf" in url


def test_extract_pdf_url_not_found():
    assert ibja._extract_pdf_url("<html><body></body></html>") is None


# ---------------------------------------------------------------------------
# _parse_ibja_pdf — uses real fixture PDF (tests/fixtures/ibja_30day_sample.pdf)
# ---------------------------------------------------------------------------


def test_parse_ibja_pdf_row_count():
    pdf_bytes = _FIXTURE_PDF_PATH.read_bytes()
    df = ibja._parse_ibja_pdf(pdf_bytes)
    assert len(df) == 21  # 21 trading days in the fixture (SAT/SUN/holidays filtered)


def test_parse_ibja_pdf_columns_present():
    pdf_bytes = _FIXTURE_PDF_PATH.read_bytes()
    df = ibja._parse_ibja_pdf(pdf_bytes)
    assert {"date", "am_916", "pm_916", "am_999", "pm_999"} <= set(df.columns)


def test_parse_ibja_pdf_sample_values():
    pdf_bytes = _FIXTURE_PDF_PATH.read_bytes()
    df = ibja._parse_ibja_pdf(pdf_bytes)
    row = df[df["date"] == "2026-05-18"].iloc[0]
    assert row["am_916"] == 144564.0
    assert row["pm_916"] == 144489.0


def test_parse_ibja_pdf_no_weekend_rows():
    pdf_bytes = _FIXTURE_PDF_PATH.read_bytes()
    df = ibja._parse_ibja_pdf(pdf_bytes)
    # Fixture: 18-Apr-26=SAT, 19-Apr-26=SUN — both must be absent
    assert "2026-04-18" not in df["date"].values
    assert "2026-04-19" not in df["date"].values


def test_parse_ibja_pdf_no_holiday_rows():
    pdf_bytes = _FIXTURE_PDF_PATH.read_bytes()
    df = ibja._parse_ibja_pdf(pdf_bytes)
    assert "2026-05-01" not in df["date"].values  # Labour Day / market holiday in fixture


def test_parse_ibja_pdf_date_format():
    pdf_bytes = _FIXTURE_PDF_PATH.read_bytes()
    df = ibja._parse_ibja_pdf(pdf_bytes)
    assert df["date"].str.match(r"\d{4}-\d{2}-\d{2}").all()


def test_parse_ibja_pdf_empty_bytes():
    assert ibja._parse_ibja_pdf(b"not a pdf").empty


# ---------------------------------------------------------------------------
# backfill_ibja_from_pdf
# ---------------------------------------------------------------------------


def test_backfill_from_pdf_creates_parquet(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: _IBJA_HTML_WITH_PDF_LINK)
    monkeypatch.setattr(ibja, "_download_pdf_bytes", lambda _url: b"pdf")
    monkeypatch.setattr(ibja, "_parse_ibja_pdf", lambda _b: _make_backfill_df())
    p = tmp_path / "ibja.parquet"
    count = ibja.backfill_ibja_from_pdf(p)
    assert count == 2
    assert p.exists()


def test_backfill_from_pdf_columns_present(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: _IBJA_HTML_WITH_PDF_LINK)
    monkeypatch.setattr(ibja, "_download_pdf_bytes", lambda _url: b"pdf")
    monkeypatch.setattr(ibja, "_parse_ibja_pdf", lambda _b: _make_backfill_df())
    p = tmp_path / "ibja.parquet"
    ibja.backfill_ibja_from_pdf(p)
    df = pd.read_parquet(p)
    assert {"date", "am_916", "pm_916"} <= set(df.columns)


def test_backfill_from_pdf_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: _IBJA_HTML_WITH_PDF_LINK)
    monkeypatch.setattr(ibja, "_download_pdf_bytes", lambda _url: b"pdf")
    monkeypatch.setattr(ibja, "_parse_ibja_pdf", lambda _b: _make_backfill_df())
    p = tmp_path / "ibja.parquet"
    count1 = ibja.backfill_ibja_from_pdf(p)
    count2 = ibja.backfill_ibja_from_pdf(p)
    assert count1 == 2
    assert count2 == 0
    assert len(pd.read_parquet(p)) == 2


def test_backfill_from_pdf_no_pdf_url(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: "<html><body></body></html>")
    p = tmp_path / "ibja.parquet"
    assert ibja.backfill_ibja_from_pdf(p) == 0


def test_backfill_from_pdf_html_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: None)
    p = tmp_path / "ibja.parquet"
    assert ibja.backfill_ibja_from_pdf(p) == 0


def test_backfill_from_pdf_download_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: _IBJA_HTML_WITH_PDF_LINK)
    monkeypatch.setattr(ibja, "_download_pdf_bytes", lambda _url: None)
    p = tmp_path / "ibja.parquet"
    assert ibja.backfill_ibja_from_pdf(p) == 0


def test_backfill_from_pdf_preserves_existing_rows(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: _IBJA_HTML_WITH_PDF_LINK)
    monkeypatch.setattr(ibja, "_download_pdf_bytes", lambda _url: b"pdf")
    monkeypatch.setattr(ibja, "_parse_ibja_pdf", lambda _b: _make_backfill_df())
    p = tmp_path / "ibja.parquet"
    seed = pd.DataFrame([{"date": "2026-04-01", "am_999": 150000.0}])
    seed.to_parquet(p, index=False)
    count = ibja.backfill_ibja_from_pdf(p)
    df = pd.read_parquet(p)
    assert count == 2
    assert len(df) == 3
    assert "2026-04-01" in df["date"].values
