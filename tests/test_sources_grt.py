"""Tests for ml.sources.grt — all HTTP mocked, no live requests."""

from __future__ import annotations

import ml.sources.grt as grt
import pytest
from ml.sources.base import SourceNetworkError, SourceStructureError

# Fragment shape captured 2026-07-19 against the live page (ADR 026) — GRT's
# Next.js hydration payload embeds the rate as backslash-escaped JSON in the HTML.
_REAL_FRAGMENT = (
    r"{\"type\":\"GOLD\",\"weight\":1,\"unit\":\"G\",\"purity\":\"24 KT\",\"amount\":14340,"
    r"\"sort_order\":0,\"default\":false},{\"type\":\"GOLD\",\"weight\":1,\"unit\":\"G\","
    r"\"purity\":\"22 KT\",\"amount\":13135,\"sort_order\":1,\"default\":true}"
)


class _FakeResponse:
    def __init__(self, text, status_code=200, raise_exc=None):
        self.text = text
        self.status_code = status_code
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc:
            raise self._raise_exc


def test_fetch_grt_happy_path(monkeypatch):
    monkeypatch.setattr(grt.requests, "get", lambda *a, **kw: _FakeResponse(_REAL_FRAGMENT))
    reading = grt.fetch_grt()
    assert reading.source == "grt"
    assert reading.city is None
    assert reading.rate_22k == 13135.0
    assert "GRT" in reading.attribution


def test_fetch_grt_plain_unescaped_json_also_matches(monkeypatch):
    plain = '{"purity":"22 KT","amount":13200}'
    monkeypatch.setattr(grt.requests, "get", lambda *a, **kw: _FakeResponse(plain))
    reading = grt.fetch_grt()
    assert reading.rate_22k == 13200.0


def test_network_error_wrapped(monkeypatch):
    import requests

    def boom(*a, **kw):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(grt.requests, "get", boom)
    with pytest.raises(SourceNetworkError):
        grt.fetch_grt()


def test_non_2xx_status_raises_network_error(monkeypatch):
    import requests

    resp = _FakeResponse("", raise_exc=requests.HTTPError("503"))
    monkeypatch.setattr(grt.requests, "get", lambda *a, **kw: resp)
    with pytest.raises(SourceNetworkError):
        grt.fetch_grt()


def test_missing_pattern_raises_structure_error(monkeypatch):
    monkeypatch.setattr(
        grt.requests, "get", lambda *a, **kw: _FakeResponse("<html>redesigned page</html>")
    )
    with pytest.raises(SourceStructureError):
        grt.fetch_grt()
