"""Tests for ml.sources.polite_http (ADR 059, G1a) -- all HTTP mocked, no live requests."""

from __future__ import annotations

from datetime import UTC, datetime

import ml.sources.grt as grt
import ml.sources.kalyan as kalyan
import pytest
import requests
from ml.sources import polite_http
from ml.sources.base import SourceNetworkError, SourceStructureError, validate_rate_22k
from ml.sources.polite_http import (
    MAX_ATTEMPTS,
    MIN_HOST_INTERVAL_S,
    USER_AGENT,
    PolitePolicy,
    backoff_delay_s,
    parse_retry_after,
)

_URL = "https://retailer.example/rate"


class _Resp:
    def __init__(self, status_code=200, headers=None, text="ok"):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _policy(responses, calls, sleeps, clock=None):
    """A PolitePolicy whose sleep advances a fake clock, serving `responses` in order."""
    clock = clock or _Clock()

    def fake_sleep(s):
        sleeps.append(s)
        clock.t += s

    policy = PolitePolicy(sleep=fake_sleep, clock=clock, rand=lambda: 0.5)
    seq = list(responses)

    def send(url, **kw):
        calls.append((url, kw))
        item = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(item, Exception):
            raise item
        return item

    return policy, send


def _run(monkeypatch, policy, send, method="get", url=_URL):
    monkeypatch.setattr(requests, method, send)
    return policy.request(method.upper(), url, source="test", timeout=5)


# ── Retry-After parsing ─────────────────────────────────────────────────────


def test_parse_retry_after_seconds_and_http_date():
    assert parse_retry_after("120") == 120.0
    now = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
    assert parse_retry_after("Fri, 25 Sep 2026 12:00:30 GMT", now=now) == 30.0
    assert parse_retry_after("Fri, 25 Sep 2026 11:00:00 GMT", now=now) == 0.0


@pytest.mark.parametrize("value", [None, "", "soon", "-5"])
def test_parse_retry_after_unparseable_is_none_not_zero(value):
    assert parse_retry_after(value) is None


# ── Backoff ─────────────────────────────────────────────────────────────────


def test_backoff_is_exponential_with_bounded_jitter():
    lo = [backoff_delay_s(a, rand=lambda: 0.0) for a in (1, 2, 3)]
    hi = [backoff_delay_s(a, rand=lambda: 1.0) for a in (1, 2, 3)]
    assert lo == [1.0, 2.0, 4.0]  # never instant: at least half the nominal step
    assert hi == [2.0, 4.0, 8.0]
    assert backoff_delay_s(20, rand=lambda: 1.0) == polite_http.BACKOFF_CAP_S


# ── Policy behaviour ────────────────────────────────────────────────────────


def test_success_sends_identifying_user_agent_once(monkeypatch):
    calls, sleeps = [], []
    policy, send = _policy([_Resp(200)], calls, sleeps)
    resp = _run(monkeypatch, policy, send)
    assert resp.status_code == 200
    assert len(calls) == 1
    assert calls[0][1]["headers"]["User-Agent"] == USER_AGENT
    assert "@" in USER_AGENT  # identifies a contact, not a spoofed browser
    assert sleeps == []


def test_connection_error_retries_once_with_backoff_then_gives_up(monkeypatch):
    calls, sleeps = [], []
    policy, send = _policy([requests.ConnectionError("down")], calls, sleeps)
    with pytest.raises(SourceNetworkError):
        _run(monkeypatch, policy, send)
    assert len(calls) == MAX_ATTEMPTS == 2  # hard cap on attempts
    assert sleeps[0] == pytest.approx(backoff_delay_s(1, rand=lambda: 0.5))


def test_502_then_success(monkeypatch):
    calls, sleeps = [], []
    policy, send = _policy([_Resp(502), _Resp(200)], calls, sleeps)
    assert _run(monkeypatch, policy, send).status_code == 200
    assert len(calls) == 2


def test_404_is_not_retried(monkeypatch):
    calls, sleeps = [], []
    policy, send = _policy([_Resp(404)], calls, sleeps)
    with pytest.raises(SourceNetworkError):
        _run(monkeypatch, policy, send)
    assert len(calls) == 1


def test_429_without_retry_after_stops_and_cools_host_off(monkeypatch):
    calls, sleeps = [], []
    policy, send = _policy([_Resp(429), _Resp(200)], calls, sleeps)
    with pytest.raises(SourceNetworkError, match="backing off"):
        _run(monkeypatch, policy, send)
    assert len(calls) == 1  # no guessed retry against a host that said "too many"
    # The same host is not contacted again for the rest of the process...
    with pytest.raises(SourceNetworkError, match="asked us to back off"):
        _run(monkeypatch, policy, send)
    assert len(calls) == 1
    # ...but a different host is unaffected.
    _run(monkeypatch, policy, send, url="https://other.example/x")
    assert len(calls) == 2


def test_429_short_retry_after_is_waited_out_then_retried(monkeypatch):
    calls, sleeps = [], []
    policy, send = _policy([_Resp(429, {"Retry-After": "7"}), _Resp(200)], calls, sleeps)
    assert _run(monkeypatch, policy, send).status_code == 200
    assert len(calls) == 2
    assert sleeps[0] >= 7


def test_503_long_retry_after_is_honoured_not_retried(monkeypatch):
    calls, sleeps = [], []
    policy, send = _policy([_Resp(503, {"Retry-After": "3600"}), _Resp(200)], calls, sleeps)
    with pytest.raises(SourceNetworkError):
        _run(monkeypatch, policy, send)
    assert len(calls) == 1
    assert sleeps == []  # we don't sit on a runner for an hour; the host is skipped instead
    with pytest.raises(SourceNetworkError, match="asked us to back off"):
        _run(monkeypatch, policy, send)
    assert len(calls) == 1


def test_bare_503_is_an_ordinary_transient_retry(monkeypatch):
    calls, sleeps = [], []
    policy, send = _policy([_Resp(503), _Resp(200)], calls, sleeps)
    assert _run(monkeypatch, policy, send).status_code == 200
    assert len(calls) == 2


def test_same_host_requests_are_spaced(monkeypatch):
    calls, sleeps = [], []
    policy, send = _policy([_Resp(200)], calls, sleeps)
    _run(monkeypatch, policy, send)
    _run(monkeypatch, policy, send)
    assert len(calls) == 2
    assert sleeps == [pytest.approx(MIN_HOST_INTERVAL_S)]


# ── Adapters actually go through the policy ─────────────────────────────────


def test_kalyan_cities_share_one_host_cooloff(monkeypatch):
    """A 429 on the first Kalyan city must stop the remaining cities hitting the host."""
    calls = []

    def post(url, **kw):
        calls.append(url)
        return _Resp(429)

    monkeypatch.setattr(kalyan.requests, "post", post)
    for city in kalyan.KALYAN_CITIES:
        with pytest.raises(SourceNetworkError):
            kalyan.fetch_kalyan_city(city)
    assert len(calls) == 1


def test_grt_uses_polite_user_agent(monkeypatch):
    seen = {}

    def get(url, **kw):
        seen.update(kw["headers"])
        return _Resp(200, text='{"purity":"22 KT","amount":13200}')

    monkeypatch.setattr(grt.requests, "get", get)
    assert grt.fetch_grt().rate_22k == 13200.0
    assert seen["User-Agent"] == USER_AGENT


# ── Plausibility (ADR 059 G1b) ──────────────────────────────────────────────


@pytest.mark.parametrize("rate", [0.0, 131350.0, 1999.0, 25001.0, float("nan"), float("inf")])
def test_validate_rate_22k_rejects_implausible(rate):
    with pytest.raises(SourceStructureError):
        validate_rate_22k(rate, source="t")


def test_grt_implausible_rate_is_structure_error(monkeypatch):
    # e.g. a per-10g figure picked up where a per-g one was expected
    monkeypatch.setattr(
        grt.requests, "get", lambda *a, **kw: _Resp(200, text='{"purity":"22 KT","amount":131350}')
    )
    with pytest.raises(SourceStructureError, match="implausible"):
        grt.fetch_grt()
