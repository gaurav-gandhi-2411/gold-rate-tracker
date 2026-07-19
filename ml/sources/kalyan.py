"""Kalyan Jewellers city-level gold-rate adapter (ADR 026).

Data source: ``www.kalyanjewellers.net/kalyan_gold_rates/ajax/get_rate`` — a
same-origin AJAX endpoint the site's own city-selector dropdown calls. No
scraping/DOM-parsing needed; it returns structured JSON. Verified reachable
from a GitHub Actions runner IP (2026-07-19, torn-down diagnostic workflow,
see ADR 026) — unlike Tanishq, this endpoint is not Cloudflare-fronted.

City coverage is deliberately limited to cities where Kalyan's own dropdown
gives an unambiguous, literal city-name label (verified by hand, ADR 026):
Bangalore, Chennai, Hyderabad, and Ernakulam (Kochi's commercial district).
Mumbai/Delhi/Kolkata only have *neighborhood* entries (Andheri, Karol Bagh,
Camac Street, ...) in Kalyan's own dropdown — picking one as a metro stand-in
is a labeling decision, not a scraping detail, and is deliberately left
unregistered pending that decision (ADR 026).

(country_id, state_id, city_id) triples found via a real browser session
against Kalyan's own get_state_ajax / get_city_ajax endpoints, 2026-07-19.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import requests

from ml.sources.base import SourceNetworkError, SourceReading, SourceStructureError

_ENDPOINT = "https://www.kalyanjewellers.net/kalyan_gold_rates/ajax/get_rate"
_REFERER = "https://www.kalyanjewellers.net/gold-rate/Gold-Rate-Today"
_USER_AGENT = "gold-rate-tracker/1.0 (portfolio project; gaurav.gandhi2411@gmail.com)"
_TIMEOUT = 20
_IST_OFFSET = timedelta(
    hours=5, minutes=30
)  # Kalyan's updated_time is IST wall-clock, no tz suffix

# (country_id, state_id, city_id) — see module docstring for how these cities were chosen.
KALYAN_CITIES: dict[str, tuple[int, int, int]] = {
    "Bangalore": (1, 8, 40),
    "Chennai": (1, 16, 120),
    "Hyderabad": (1, 17, 143),
    "Ernakulam": (1, 9, 63),
}

_RATE_RE = re.compile(r"INR\s*([\d.]+)")


@dataclass(frozen=True)
class KalyanHistoryPoint:
    label: str
    rate_22k: float


@dataclass(frozen=True)
class KalyanRawReading:
    """Full parsed payload, including the 5-day history the endpoint ships.

    The PIT snapshot store captures this in full (history is exactly what
    Option 2's weight-learning will eventually want). The fusion engine only
    consumes ``.reading`` (a plain :class:`SourceReading`) — it doesn't need
    history, and the two concerns are kept separate on purpose.
    """

    reading: SourceReading
    place_name: str
    is_today: bool
    history: list[KalyanHistoryPoint]


def _parse_rate(value: str, *, field: str) -> float:
    m = _RATE_RE.search(value or "")
    if not m:
        raise SourceStructureError(f"kalyan: could not parse rate from {field}={value!r}")
    return float(m.group(1))


def fetch_kalyan_city(city_name: str) -> KalyanRawReading:
    """Fetch Kalyan's current board rate for a registered city.

    Raises :class:`KeyError` for an unregistered city name (a programming
    error, not a runtime source failure), :class:`SourceNetworkError` on a
    transient failure, or :class:`SourceStructureError` if the response's
    expected shape is gone (the endpoint changed).
    """
    country_id, state_id, city_id = KALYAN_CITIES[city_name]

    try:
        resp = requests.post(
            _ENDPOINT,
            headers={
                "User-Agent": _USER_AGENT,
                "Referer": _REFERER,
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
            data={"countryId": country_id, "stateId": state_id, "cityId": city_id},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except requests.RequestException as exc:
        raise SourceNetworkError(f"kalyan: request failed for {city_name}: {exc}") from exc
    except ValueError as exc:  # json.JSONDecodeError subclasses ValueError
        raise SourceStructureError(f"kalyan: non-JSON response for {city_name}") from exc

    place_name = payload.get("place_name")
    today_22k = payload.get("today_22k")
    updated_time = payload.get("updated_time")
    is_today = payload.get("is_today")

    if not place_name or not today_22k or not updated_time or is_today is None:
        raise SourceStructureError(
            f"kalyan: expected fields missing for {city_name} — got keys {sorted(payload.keys())}"
        )

    rate = _parse_rate(today_22k, field="today_22k")

    try:
        naive_ist = datetime.strptime(updated_time, "%d %b %Y %H:%M")
    except ValueError as exc:
        raise SourceStructureError(
            f"kalyan: unparseable updated_time {updated_time!r} for {city_name}"
        ) from exc
    observed_at = (naive_ist - _IST_OFFSET).replace(tzinfo=UTC)

    history: list[KalyanHistoryPoint] = []
    prev_html = payload.get("previous_dates_html", "")
    for label, rate_str in re.findall(
        r'<div class="oi-rt-dt">\s*([^<]+?)\s*</div><span class="oi-22-ct">([^<]+)</span>',
        prev_html,
    ):
        try:
            history.append(
                KalyanHistoryPoint(
                    label=label.strip(), rate_22k=_parse_rate(rate_str, field="history")
                )
            )
        except SourceStructureError:
            continue  # a single unparseable history point doesn't invalidate today's reading

    reading = SourceReading(
        source="kalyan",
        city=city_name,
        rate_22k=rate,
        observed_at=observed_at,
        attribution=f"Kalyan Jewellers — {place_name} board rate",
    )
    return KalyanRawReading(
        reading=reading, place_name=place_name, is_today=bool(is_today), history=history
    )


def fetch_kalyan(city_name: str) -> SourceReading:
    """Fusion-facing entry point: fetch + return just the reading."""
    return fetch_kalyan_city(city_name).reading
