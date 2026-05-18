"""Tests for ml.ibja — all HTTP mocked, no live requests."""

from __future__ import annotations

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


def test_append_returns_false_on_fetch_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr(ibja, "_get_with_retry", lambda _h: None)
    p = tmp_path / "ibja.parquet"
    assert ibja.append_ibja_today(p) is False
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
