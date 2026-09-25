"""Malabar Gold & Diamonds national gold-rate adapter (ADR 026).

Data source: Malabar's own public GraphQL endpoint
(``www.malabargoldanddiamonds.com/graphql-magento``, query ``getMetalRate``)
— the same call their live-gold-rate page's React widget makes. Clean
structured JSON, no HTML/DOM parsing needed. Verified reachable from a
GitHub Actions runner IP (2026-07-19, torn-down diagnostic workflow, see
ADR 026).

National only in practice: the schema has a ``state`` field, but it comes
back empty even for an explicit ``state: "Karnataka"`` filter (tested during
ADR 026's research pass) — Malabar's own data doesn't populate it today.
Registered here as a national source only; do not infer state/city
granularity from the schema's mere existence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import requests  # noqa: F401 -- the tests' monkeypatch seam (<adapter>.requests.*)

from ml.sources.base import (
    SourceReading,
    SourceStructureError,
    validate_observed_at,
    validate_rate_22k,
)
from ml.sources.polite_http import polite_request

_QUERY = (
    "query getMetalRate($filter: MetalRateFilterInput) "
    "{ getMetalRate(filter: $filter) "
    "{ items { entry_date entry_time purity unit rate country state } } }"
)
_VARIABLES = '{"filter":{"metal_type":"gold","country":"India"}}'
_URL = (
    "https://www.malabargoldanddiamonds.com/graphql-magento"
    f"?query={quote(_QUERY)}&variables={quote(_VARIABLES)}"
)
_REFERER = "https://www.malabargoldanddiamonds.com/in/pan-india/en/live-gold-rate.html"
_TIMEOUT = 20


def fetch_malabar() -> SourceReading:
    """Fetch Malabar's current national 22K rate.

    Raises :class:`SourceNetworkError` on a transient failure or
    :class:`SourceStructureError` if the expected GraphQL shape is gone or
    no 22k item is present (the schema/data changed).
    """
    try:
        # Polite access (UA, per-host spacing, capped retry, Retry-After): ADR 059.
        resp = polite_request(
            "GET", _URL, source="malabar", headers={"Referer": _REFERER}, timeout=_TIMEOUT
        )
        payload = resp.json()
    except ValueError as exc:
        raise SourceStructureError("malabar: non-JSON response") from exc

    try:
        items = payload["data"]["getMetalRate"]["items"]
    except (KeyError, TypeError) as exc:
        raise SourceStructureError(
            f"malabar: expected data.getMetalRate.items shape missing — got {payload!r}"
        ) from exc

    matches = [item for item in items if item.get("purity") == "22k"]
    if not matches:
        raise SourceStructureError("malabar: no 22k item in getMetalRate response")

    # Multiple stale/duplicate entries can appear (observed during ADR 026's
    # research pass); the most recent entry_date+entry_time wins.
    latest = max(matches, key=lambda item: (item.get("entry_date", ""), item.get("entry_time", "")))

    try:
        rate = float(latest["rate"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceStructureError(f"malabar: unparseable rate {latest.get('rate')!r}") from exc
    rate = validate_rate_22k(rate, source="malabar")

    observed_at = datetime.now(UTC)
    try:
        # entry_date carries its own (always-midnight) time component; the real
        # time-of-day is entry_time, so take only the date part from entry_date.
        date_part = latest["entry_date"].split(" ", 1)[0]
        naive = datetime.strptime(f"{date_part} {latest['entry_time']}", "%Y-%m-%d %H:%M:%S")
    except (KeyError, ValueError):
        naive = None
    if naive is not None:
        # No explicit timezone in the API; Malabar is an Indian retailer publishing
        # IST wall-clock times, same assumption as Kalyan.
        observed_at = validate_observed_at(
            (naive - timedelta(hours=5, minutes=30)).replace(tzinfo=UTC), source="malabar"
        )

    return SourceReading(
        source="malabar",
        city=None,
        rate_22k=rate,
        observed_at=observed_at,
        attribution="Malabar Gold & Diamonds — national board rate",
    )
