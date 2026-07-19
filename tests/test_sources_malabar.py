"""Tests for ml.sources.malabar — all HTTP mocked, no live requests."""

from __future__ import annotations

import ml.sources.malabar as malabar
import pytest
from ml.sources.base import SourceNetworkError, SourceStructureError

# Real payload shape captured 2026-07-19 against the live GraphQL endpoint (ADR 026).
_REAL_PAYLOAD = {
    "data": {
        "getMetalRate": {
            "items": [
                {
                    "entry_date": "2026-07-18 00:00:00",
                    "entry_time": "03:55:29",
                    "purity": "14k",
                    "unit": "G",
                    "rate": "8359.00",
                    "country": "India",
                    "state": "",
                },
                {
                    "entry_date": "2026-07-18 00:00:00",
                    "entry_time": "03:57:50",
                    "purity": "22k",
                    "unit": "G",
                    "rate": "13135.00",
                    "country": "India",
                    "state": "",
                },
                {
                    "entry_date": "2024-11-11 00:00:00",
                    "entry_time": "07:35:40",
                    "purity": "99.93",
                    "unit": "Grms",
                    "rate": "7871.00",
                    "country": "India",
                    "state": "",
                },
            ]
        }
    }
}


class _FakeResponse:
    def __init__(self, json_data=None, raise_exc=None):
        self._json_data = json_data
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc

    def json(self):
        if self._json_data is None:
            raise ValueError("no JSON")
        return self._json_data


def test_fetch_malabar_happy_path(monkeypatch):
    monkeypatch.setattr(malabar.requests, "get", lambda *a, **kw: _FakeResponse(_REAL_PAYLOAD))
    reading = malabar.fetch_malabar()
    assert reading.source == "malabar"
    assert reading.city is None
    assert reading.rate_22k == 13135.0
    assert "Malabar" in reading.attribution


def test_observed_at_uses_entry_date_and_time_in_ist(monkeypatch):
    monkeypatch.setattr(malabar.requests, "get", lambda *a, **kw: _FakeResponse(_REAL_PAYLOAD))
    reading = malabar.fetch_malabar()
    # "2026-07-18 03:57:50" IST -> UTC is 5:30 earlier -> 2026-07-17 22:27:50
    assert reading.observed_at.day == 17
    assert reading.observed_at.hour == 22
    assert reading.observed_at.minute == 27


def test_latest_of_multiple_22k_entries_wins(monkeypatch):
    payload = {
        "data": {
            "getMetalRate": {
                "items": [
                    {
                        "entry_date": "2026-07-17 00:00:00",
                        "entry_time": "03:00:00",
                        "purity": "22k",
                        "rate": "13000.00",
                    },
                    {
                        "entry_date": "2026-07-18 00:00:00",
                        "entry_time": "03:57:50",
                        "purity": "22k",
                        "rate": "13135.00",
                    },
                ]
            }
        }
    }
    monkeypatch.setattr(malabar.requests, "get", lambda *a, **kw: _FakeResponse(payload))
    reading = malabar.fetch_malabar()
    assert reading.rate_22k == 13135.0


def test_network_error_wrapped(monkeypatch):
    import requests

    def boom(*a, **kw):
        raise requests.ConnectionError("dns failure")

    monkeypatch.setattr(malabar.requests, "get", boom)
    with pytest.raises(SourceNetworkError):
        malabar.fetch_malabar()


def test_non_2xx_status_raises_network_error(monkeypatch):
    import requests

    resp = _FakeResponse(raise_exc=requests.HTTPError("500"))
    monkeypatch.setattr(malabar.requests, "get", lambda *a, **kw: resp)
    with pytest.raises(SourceNetworkError):
        malabar.fetch_malabar()


def test_missing_data_shape_raises_structure_error(monkeypatch):
    monkeypatch.setattr(
        malabar.requests, "get", lambda *a, **kw: _FakeResponse({"unexpected": "shape"})
    )
    with pytest.raises(SourceStructureError):
        malabar.fetch_malabar()


def test_no_22k_item_raises_structure_error(monkeypatch):
    payload = {"data": {"getMetalRate": {"items": [{"purity": "18k", "rate": "10000.00"}]}}}
    monkeypatch.setattr(malabar.requests, "get", lambda *a, **kw: _FakeResponse(payload))
    with pytest.raises(SourceStructureError):
        malabar.fetch_malabar()


def test_unparseable_rate_raises_structure_error(monkeypatch):
    payload = {"data": {"getMetalRate": {"items": [{"purity": "22k", "rate": "not-a-number"}]}}}
    monkeypatch.setattr(malabar.requests, "get", lambda *a, **kw: _FakeResponse(payload))
    with pytest.raises(SourceStructureError):
        malabar.fetch_malabar()


def test_non_json_response_raises_structure_error(monkeypatch):
    monkeypatch.setattr(malabar.requests, "get", lambda *a, **kw: _FakeResponse(None))
    with pytest.raises(SourceStructureError):
        malabar.fetch_malabar()
