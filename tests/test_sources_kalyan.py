"""Tests for ml.sources.kalyan — all HTTP mocked, no live requests."""

from __future__ import annotations

import ml.sources.kalyan as kalyan
import pytest
from ml.sources.base import SourceNetworkError, SourceStructureError

# Real payload shape captured 2026-07-19 against the live endpoint (ADR 026).
_BANGALORE_PAYLOAD = {
    "html": "<h3>LATEST PRICE OF</h3><h4>BANGALORE</h4>...",
    "place_name": "BANGALORE",
    "content": "Looking for the gold rate today in Bangalore?",
    "previous_dates_html": (
        '<li><div class="oi-rt-dt">\n                            Jul 14\n                          '
        '</div><span class="oi-22-ct">INR 13090.00</span>'
        '<div class="oi-rt-dt">\n                            Jul 14\n                          </div></li>'
        '<li><div class="oi-rt-dt">\n                            Jul 18\n                          '
        '</div><span class="oi-22-ct">INR 13135.00</span>'
        '<div class="oi-rt-dt">\n                            Jul 18\n                          </div></li>'
    ),
    "today_18k": "N/A",
    "today_21k": "N/A",
    "today_22k": "INR 13135.00",
    "today_24k": "N/A",
    "updated_time": "19 Jul 2026 16:00",
    "heading": "",
    "disclaimer": "*Board Rate Last Refreshed on 19-07-2026 16:00:04 IST.",
    "is_today": True,
}


class _FakeResponse:
    def __init__(self, json_data=None, status_code=200, raise_exc=None):
        self._json_data = json_data
        self.status_code = status_code
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc

    def json(self):
        if self._json_data is None:
            raise ValueError("no JSON")
        return self._json_data


def test_fetch_bangalore_happy_path(monkeypatch):
    monkeypatch.setattr(kalyan.requests, "post", lambda *a, **kw: _FakeResponse(_BANGALORE_PAYLOAD))
    raw = kalyan.fetch_kalyan_city("Bangalore")

    assert raw.reading.source == "kalyan"
    assert raw.reading.city == "Bangalore"
    assert raw.reading.rate_22k == 13135.0
    assert "BANGALORE" in raw.reading.attribution
    assert raw.place_name == "BANGALORE"
    assert raw.is_today is True


def test_history_parsed_from_previous_dates_html(monkeypatch):
    monkeypatch.setattr(kalyan.requests, "post", lambda *a, **kw: _FakeResponse(_BANGALORE_PAYLOAD))
    raw = kalyan.fetch_kalyan_city("Bangalore")
    assert len(raw.history) == 2
    assert raw.history[0].label == "Jul 14"
    assert raw.history[0].rate_22k == 13090.0
    assert raw.history[1].rate_22k == 13135.0


def test_observed_at_parsed_as_ist_converted_to_utc(monkeypatch):
    monkeypatch.setattr(kalyan.requests, "post", lambda *a, **kw: _FakeResponse(_BANGALORE_PAYLOAD))
    raw = kalyan.fetch_kalyan_city("Bangalore")
    # "19 Jul 2026 16:00" IST == 10:30 UTC (IST is UTC+5:30)
    assert raw.reading.observed_at.hour == 10
    assert raw.reading.observed_at.minute == 30
    assert raw.reading.observed_at.day == 19


def test_unregistered_city_raises_key_error():
    with pytest.raises(KeyError):
        kalyan.fetch_kalyan_city("Nowhereville")


def test_network_error_wrapped(monkeypatch):
    import requests

    def boom(*a, **kw):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(kalyan.requests, "post", boom)
    with pytest.raises(SourceNetworkError):
        kalyan.fetch_kalyan_city("Bangalore")


def test_non_2xx_status_raises_network_error(monkeypatch):
    import requests

    resp = _FakeResponse(raise_exc=requests.HTTPError("500"))
    monkeypatch.setattr(kalyan.requests, "post", lambda *a, **kw: resp)
    with pytest.raises(SourceNetworkError):
        kalyan.fetch_kalyan_city("Bangalore")


def test_missing_expected_fields_raises_structure_error(monkeypatch):
    incomplete = {"place_name": "BANGALORE"}  # missing today_22k, updated_time, is_today
    monkeypatch.setattr(kalyan.requests, "post", lambda *a, **kw: _FakeResponse(incomplete))
    with pytest.raises(SourceStructureError):
        kalyan.fetch_kalyan_city("Bangalore")


def test_unparseable_rate_raises_structure_error(monkeypatch):
    bad = dict(_BANGALORE_PAYLOAD, today_22k="not a number")
    monkeypatch.setattr(kalyan.requests, "post", lambda *a, **kw: _FakeResponse(bad))
    with pytest.raises(SourceStructureError):
        kalyan.fetch_kalyan_city("Bangalore")


def test_unparseable_updated_time_raises_structure_error(monkeypatch):
    bad = dict(_BANGALORE_PAYLOAD, updated_time="not a date")
    monkeypatch.setattr(kalyan.requests, "post", lambda *a, **kw: _FakeResponse(bad))
    with pytest.raises(SourceStructureError):
        kalyan.fetch_kalyan_city("Bangalore")


def test_non_json_response_raises_structure_error(monkeypatch):
    monkeypatch.setattr(kalyan.requests, "post", lambda *a, **kw: _FakeResponse(None))
    with pytest.raises(SourceStructureError):
        kalyan.fetch_kalyan_city("Bangalore")


def test_one_bad_history_point_does_not_invalidate_reading(monkeypatch):
    payload = dict(_BANGALORE_PAYLOAD)
    payload["previous_dates_html"] = (
        '<li><div class="oi-rt-dt">Jul 14</div><span class="oi-22-ct">garbage</span>'
        '<div class="oi-rt-dt">Jul 14</div></li>'
        '<li><div class="oi-rt-dt">Jul 18</div><span class="oi-22-ct">INR 13135.00</span>'
        '<div class="oi-rt-dt">Jul 18</div></li>'
    )
    monkeypatch.setattr(kalyan.requests, "post", lambda *a, **kw: _FakeResponse(payload))
    raw = kalyan.fetch_kalyan_city("Bangalore")
    assert raw.reading.rate_22k == 13135.0  # today's reading unaffected
    assert len(raw.history) == 1  # the garbage point is dropped, not fatal
    assert raw.history[0].rate_22k == 13135.0


def test_fetch_kalyan_returns_bare_reading(monkeypatch):
    monkeypatch.setattr(kalyan.requests, "post", lambda *a, **kw: _FakeResponse(_BANGALORE_PAYLOAD))
    reading = kalyan.fetch_kalyan("Bangalore")
    assert reading.rate_22k == 13135.0
    assert reading.city == "Bangalore"


@pytest.mark.parametrize("city", list(kalyan.KALYAN_CITIES))
def test_all_registered_cities_have_valid_id_tuples(city):
    country_id, state_id, city_id = kalyan.KALYAN_CITIES[city]
    assert isinstance(country_id, int) and country_id > 0
    assert isinstance(state_id, int) and state_id > 0
    assert isinstance(city_id, int) and city_id > 0
