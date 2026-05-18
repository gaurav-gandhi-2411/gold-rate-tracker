"""Tests for ml.mcx — yfinance mocked, no live HTTP."""

from __future__ import annotations

import ml.mcx as mcx
import pandas as pd
import yfinance


def _make_yf_df(dates: list[str], closes: list[float]) -> pd.DataFrame:
    idx = pd.to_datetime(dates)
    return pd.DataFrame({"close": closes}, index=idx)


# ---------------------------------------------------------------------------
# backfill_mcx_bhavcopy
# ---------------------------------------------------------------------------


def test_backfill_saves_parquet(tmp_path, monkeypatch):
    monkeypatch.setattr(
        yfinance,
        "download",
        lambda *_a, **_kw: _make_yf_df(["2026-01-02", "2026-01-03"], [2600.0, 2610.0]),
    )
    out = tmp_path / "mcx.parquet"
    result = mcx.backfill_mcx_bhavcopy("2026-01-01", out=out)
    assert out.exists()
    assert len(result) == 2


def test_backfill_columns_present(tmp_path, monkeypatch):
    monkeypatch.setattr(
        yfinance, "download", lambda *_a, **_kw: _make_yf_df(["2026-01-02"], [2600.0])
    )
    out = tmp_path / "mcx.parquet"
    mcx.backfill_mcx_bhavcopy("2026-01-01", out=out)
    df = pd.read_parquet(out)
    assert {"date", "close_usd", "ticker"} <= set(df.columns)


def test_backfill_ticker_is_gcf(tmp_path, monkeypatch):
    monkeypatch.setattr(
        yfinance, "download", lambda *_a, **_kw: _make_yf_df(["2026-01-02"], [2600.0])
    )
    out = tmp_path / "mcx.parquet"
    mcx.backfill_mcx_bhavcopy("2026-01-01", out=out)
    df = pd.read_parquet(out)
    assert (df["ticker"] == "GC=F").all()


def test_backfill_empty_yfinance_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(yfinance, "download", lambda *_a, **_kw: pd.DataFrame())
    out = tmp_path / "mcx.parquet"
    result = mcx.backfill_mcx_bhavcopy("2026-01-01", out=out)
    assert result.empty
    assert not out.exists()


def test_backfill_close_rounded_to_2dp(tmp_path, monkeypatch):
    monkeypatch.setattr(
        yfinance, "download", lambda *_a, **_kw: _make_yf_df(["2026-01-02"], [2600.12345])
    )
    out = tmp_path / "mcx.parquet"
    mcx.backfill_mcx_bhavcopy("2026-01-01", out=out)
    df = pd.read_parquet(out)
    val = df.iloc[0]["close_usd"]
    assert round(val, 2) == val


# ---------------------------------------------------------------------------
# append_mcx_today_yfinance
# ---------------------------------------------------------------------------


def test_append_creates_parquet(tmp_path, monkeypatch):
    monkeypatch.setattr(
        yfinance,
        "download",
        lambda *_a, **_kw: _make_yf_df(["2026-01-02", "2026-01-03"], [2600.0, 2610.0]),
    )
    p = tmp_path / "mcx.parquet"
    assert mcx.append_mcx_today_yfinance(p) is True
    assert p.exists()


def test_append_does_not_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(
        yfinance,
        "download",
        lambda *_a, **_kw: _make_yf_df(["2026-01-02", "2026-01-03"], [2600.0, 2610.0]),
    )
    p = tmp_path / "mcx.parquet"
    mcx.append_mcx_today_yfinance(p)
    assert mcx.append_mcx_today_yfinance(p) is False
    assert len(pd.read_parquet(p)) == 1


def test_append_returns_false_on_yfinance_exception(tmp_path, monkeypatch):
    def _fail(*_a, **_kw):
        raise RuntimeError("network error")

    monkeypatch.setattr(yfinance, "download", _fail)
    p = tmp_path / "mcx.parquet"
    assert mcx.append_mcx_today_yfinance(p) is False


def test_append_returns_false_on_empty_response(tmp_path, monkeypatch):
    monkeypatch.setattr(yfinance, "download", lambda *_a, **_kw: pd.DataFrame())
    p = tmp_path / "mcx.parquet"
    assert mcx.append_mcx_today_yfinance(p) is False


def test_append_columns_present(tmp_path, monkeypatch):
    monkeypatch.setattr(
        yfinance,
        "download",
        lambda *_a, **_kw: _make_yf_df(["2026-01-02", "2026-01-03"], [2600.0, 2610.0]),
    )
    p = tmp_path / "mcx.parquet"
    mcx.append_mcx_today_yfinance(p)
    df = pd.read_parquet(p)
    assert {"date", "close_usd", "ticker"} <= set(df.columns)
