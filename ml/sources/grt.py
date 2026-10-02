"""GRT Jewellers national gold-rate adapter (ADR 026).

Data source: ``www.grtjewels.com/gold-rate/`` — the rate is embedded as JSON
inside the page's Next.js hydration payload (server-rendered, no separate
API call, no JS execution needed). Verified reachable from a GitHub Actions
runner IP (2026-07-19, torn-down diagnostic workflow, see ADR 026) — this
site is Cloudflare-fronted but was not blocking GH Actions at that time.

National only: no city selector was found on this page (ADR 026's research
pass). Extraction is a direct regex over the raw response text rather than a
full JSON-tree walk, matching this repo's existing resilient-parsing
convention (``scraper/scrape.js``'s ``parseGoldRates`` does the same against
Tanishq's HTML) — the exact nesting/escaping of GRT's hydration payload is
an implementation detail of their build, not something worth depending on
structurally when a flat pattern match is just as robust and much simpler.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import requests  # noqa: F401 -- the tests' monkeypatch seam (grt.requests.get)

from ml.sources.base import SourceReading, SourceStructureError, validate_rate_22k
from ml.sources.polite_http import polite_request

_URL = "https://www.grtjewels.com/gold-rate/"
_TIMEOUT = 20

# Matches both the escaped-in-page-JSON form (\"purity\":\"22 KT\",\"amount\":13135)
# and a plain form, in case GRT ever serves either.
_RATE_RE = re.compile(r'\\?"purity\\?":\\?"22 KT\\?",\\?"amount\\?":(\d+(?:\.\d+)?)')


def fetch_grt() -> SourceReading:
    """Fetch GRT's current national 22K board rate.

    Raises :class:`SourceNetworkError` on a transient failure or
    :class:`SourceStructureError` if the expected embedded-JSON pattern is
    gone (the page changed).
    """
    # Polite access (UA, per-host spacing, capped retry, Retry-After): ADR 059.
    resp = polite_request("GET", _URL, source="grt", timeout=_TIMEOUT)

    m = _RATE_RE.search(resp.text)
    if not m:
        raise SourceStructureError(
            "grt: 22 KT rate pattern not found in page — structure may have changed"
        )

    rate = validate_rate_22k(float(m.group(1)), source="grt")
    return SourceReading(
        source="grt",
        city=None,
        rate_22k=rate,
        observed_at=datetime.now(UTC),
        attribution="GRT Jewellers — national board rate",
    )
