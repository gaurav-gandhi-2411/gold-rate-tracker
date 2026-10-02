"""Polite HTTP access for retailer sources (ADR 059, decision G1a).

Every retailer fetch in ``ml/sources/`` goes through :func:`polite_request` so the
access policy lives in one place instead of four copies:

* **One identifying, ordinary User-Agent** (:data:`USER_AGENT`) -- the same string all
  three Python adapters already sent before this module existed; it names the project
  and a contact address, so an operator who sees our traffic can reach us.
* **Per-host spacing.** Requests to the same host inside one process are spaced at
  least :data:`MIN_HOST_INTERVAL_S` apart. Kalyan is fetched once per registered city
  back to back; without this that was four POSTs to one host in well under a second.
* **A hard cap on attempts** (:data:`MAX_ATTEMPTS`, i.e. at most one retry) and only
  for failures a retry can plausibly fix: a connection error / timeout, or HTTP
  502/503/504.
* **Exponential backoff with jitter** between attempts (:func:`backoff_delay_s`).
* **HTTP 429 / 503 ``Retry-After`` is honoured.** A short ``Retry-After`` (at most
  :data:`MAX_RETRY_AFTER_S`) is waited out once before the single retry. A longer one,
  or any 429 at all once retries are spent, puts the host in a cool-off for the rest of
  this process: every later request to that host this cycle fails locally with
  :class:`SourceNetworkError` without touching the network. A 429 with no
  ``Retry-After`` is treated as "stop for this cycle" -- we do not guess a delay and
  try again against a host that has just told us to slow down.

Failure semantics are unchanged for callers: every network-side failure surfaces as
:class:`~ml.sources.base.SourceNetworkError`, exactly as the adapters raised before.

Test seam: ``requests.get`` / ``requests.post`` are looked up on the ``requests``
module at call time (never bound at import), so existing tests that monkeypatch
``<adapter>.requests.get`` keep intercepting every call. :data:`DEFAULT_POLICY` holds
the per-process host state; :func:`reset_default_policy` clears it (tests/conftest.py
does this around every test and replaces its sleep with a no-op).
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import requests

from ml.sources.base import SourceNetworkError

# The identifying UA every Python retailer adapter already used (unchanged value).
USER_AGENT = "gold-rate-tracker/1.0 (portfolio project; gaurav.gandhi2411@gmail.com)"

# At most one retry. These sources run on a 3h/6h schedule; the next scheduled cycle is
# the real retry, so a second in-process retry buys little and costs the site a request.
MAX_ATTEMPTS = 2
# Backoff base/cap for the exponential schedule (seconds). With MAX_ATTEMPTS=2 only the
# first step (2s nominal, 1-2s after jitter) is ever used; the cap bounds any future raise.
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 30.0
# Longest Retry-After we will wait out in-process. Anything longer ends the cycle for
# that host (the next cron run is at least 3 hours away, far beyond any sane header).
MAX_RETRY_AFTER_S = 60.0
# Minimum spacing between two requests to the same host in one process.
MIN_HOST_INTERVAL_S = 2.0

RETRYABLE_STATUS = frozenset({502, 503, 504})
RATE_LIMIT_STATUS = frozenset({429, 503})


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Parse an HTTP ``Retry-After`` header into seconds (RFC 9110 section 10.2.3).

    Accepts either delta-seconds (``"120"``) or an HTTP-date. Returns ``None`` when the
    header is absent or unparseable -- callers must treat that as "no guidance", never
    as zero. A date in the past yields ``0.0``.
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    return max(0.0, (when - now).total_seconds())


def backoff_delay_s(
    attempt: int,
    *,
    base_s: float = BACKOFF_BASE_S,
    cap_s: float = BACKOFF_CAP_S,
    rand: Callable[[], float] = random.random,
) -> float:
    """Exponential backoff with "equal jitter" for retry number ``attempt`` (1-based).

    ``d = min(cap, base * 2**(attempt-1))``; the result is uniform in ``[d/2, d]`` -- it
    always waits at least half the nominal step (so a retry is never instant) while
    still spreading concurrent retriers apart.
    """
    d = min(cap_s, base_s * (2 ** max(0, attempt - 1)))
    return d / 2 + rand() * (d / 2)


@dataclass
class PolitePolicy:
    """Per-process host state: last request time and cool-off deadlines."""

    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    rand: Callable[[], float] = random.random
    last_request_at: dict[str, float] = field(default_factory=dict)
    cooloff_until: dict[str, float] = field(default_factory=dict)

    def reset(self) -> None:
        self.last_request_at.clear()
        self.cooloff_until.clear()

    def _wait_for_host(self, host: str) -> None:
        last = self.last_request_at.get(host)
        if last is None:
            return
        remaining = MIN_HOST_INTERVAL_S - (self.clock() - last)
        if remaining > 0:
            self.sleep(remaining)

    def request(
        self,
        method: str,
        url: str,
        *,
        source: str,
        headers: dict[str, str] | None = None,
        timeout: float,
        **kwargs: Any,
    ) -> Any:
        """Issue one logical request under the policy. Returns a 2xx response.

        Raises :class:`SourceNetworkError` on every network-side failure, including a
        host that is cooling off after a 429/503 earlier in this process.
        """
        host = urlsplit(url).netloc.lower()
        until = self.cooloff_until.get(host)
        if until is not None and self.clock() < until:
            raise SourceNetworkError(
                f"{source}: {host} asked us to back off (429/503 Retry-After) earlier this "
                "cycle -- not requesting again until the next scheduled run"
            )

        merged_headers = {"User-Agent": USER_AGENT, **(headers or {})}
        send = getattr(requests, method.lower())
        last_error: str = "no attempt made"

        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._wait_for_host(host)
            self.last_request_at[host] = self.clock()
            try:
                resp = send(url, headers=merged_headers, timeout=timeout, **kwargs)
            except requests.RequestException as exc:
                last_error = f"request failed: {exc}"
                if attempt < MAX_ATTEMPTS:
                    self.sleep(backoff_delay_s(attempt, rand=self.rand))
                    continue
                raise SourceNetworkError(f"{source}: {last_error}") from exc

            status = getattr(resp, "status_code", 200)
            resp_headers = getattr(resp, "headers", None) or {}

            if status in RATE_LIMIT_STATUS:
                retry_after = parse_retry_after(resp_headers.get("Retry-After"))
                last_error = f"HTTP {status} (Retry-After={retry_after})"
                short_wait = retry_after is not None and retry_after <= MAX_RETRY_AFTER_S
                if attempt < MAX_ATTEMPTS and short_wait:
                    self.sleep(max(retry_after or 0.0, MIN_HOST_INTERVAL_S))
                    continue
                if status == 429 or retry_after is not None:
                    # Explicit "slow down": stop hitting this host for the rest of the run.
                    cooloff = retry_after if retry_after is not None else float("inf")
                    self.cooloff_until[host] = self.clock() + max(cooloff, 0.0)
                    raise SourceNetworkError(f"{source}: {last_error} -- backing off this host")
                # Bare 503 (no Retry-After): an ordinary transient server error.

            if status in RETRYABLE_STATUS:
                last_error = f"HTTP {status}"
                if attempt < MAX_ATTEMPTS:
                    self.sleep(backoff_delay_s(attempt, rand=self.rand))
                    continue
                raise SourceNetworkError(f"{source}: {last_error} after {attempt} attempts")

            try:
                resp.raise_for_status()
            except requests.RequestException as exc:
                raise SourceNetworkError(f"{source}: request failed: {exc}") from exc
            return resp

        raise SourceNetworkError(f"{source}: {last_error}")  # pragma: no cover - loop exits


DEFAULT_POLICY = PolitePolicy()


def reset_default_policy() -> None:
    """Clear the process-wide host state (test helper; also safe in production)."""
    DEFAULT_POLICY.reset()


def polite_request(
    method: str,
    url: str,
    *,
    source: str,
    headers: dict[str, str] | None = None,
    timeout: float,
    **kwargs: Any,
) -> Any:
    """Module-level entry point using :data:`DEFAULT_POLICY` (see module docstring)."""
    return DEFAULT_POLICY.request(
        method, url, source=source, headers=headers, timeout=timeout, **kwargs
    )
