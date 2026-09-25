"""Common types for retail gold-price source adapters (ADR 026)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

# Below this, an observed_at is treated as corrupt rather than real -- e.g.
# Kalyan's own `updated_time` field started returning the sentinel string
# "01 Jan 1970 00:00" (their own placeholder for "no live update this cycle",
# confirmed 2026-09-24) whenever the board is stale, which parses cleanly as
# a plain epoch-zero datetime with no exception raised. A bare strptime/parse
# success is not enough to trust a source-provided timestamp; every adapter
# that parses one must also run it through :func:`validate_observed_at`.
# Public (not underscore-prefixed): ml.fusion_snapshot_store re-uses the same
# threshold so "corrupt" means the same thing at write time and read time.
MIN_PLAUSIBLE_YEAR = 2020
MAX_FUTURE_SKEW = timedelta(days=1)


class SourceNetworkError(Exception):
    """Transient failure: timeout, connection error, non-2xx status.

    Distinct from :class:`SourceStructureError` on purpose — the fusion
    driver logs which failure mode occurred. A source going quiet on the
    network is expected sometimes (a source failing is normal, not an
    alert, per ADR 025's precedent). A structure change is the repo's known
    recurring failure class and worth telling apart from routine flakiness.
    """


class SourceStructureError(Exception):
    """The response came back (2xx) but its expected shape is gone.

    Raised when expected JSON keys / HTML patterns are missing — the site
    changed. This is the silent-breakage failure mode the whole canary
    concept exists to catch; it must never be swallowed into a generic
    "fetch failed" bucket indistinguishable from a network blip.
    """


def validate_observed_at(observed_at: datetime, *, source: str) -> datetime:
    """Fail closed on an implausible ``observed_at`` before it reaches a reading.

    A source-provided timestamp that parses without error is not necessarily
    real data -- a source can substitute a placeholder/sentinel value (an
    all-zero epoch being the classic case) that a plain ``strptime``/date
    constructor accepts happily. Every adapter that derives ``observed_at``
    from a *source-provided* field (as opposed to its own wall-clock
    ``datetime.now(UTC)``) must pass the result through this check.

    Raises :class:`SourceStructureError` -- treated identically to any other
    structure failure by every caller (shadow_fusion, the tier-3 fusion
    fallback): this source is skipped for the current cycle, never silently
    recorded with a corrupt timestamp.
    """
    if observed_at.year < MIN_PLAUSIBLE_YEAR:
        raise SourceStructureError(
            f"{source}: implausible observed_at {observed_at.isoformat()!r} "
            f"(year before {MIN_PLAUSIBLE_YEAR}) — source likely sent a placeholder timestamp"
        )
    if observed_at > datetime.now(UTC) + MAX_FUTURE_SKEW:
        raise SourceStructureError(
            f"{source}: implausible observed_at {observed_at.isoformat()!r} "
            "(more than a day in the future)"
        )
    return observed_at


# Plausible per-gram 22K range, INR. Same bounds as scraper/scrape.js's RANGE_MIN /
# RANGE_MAX for Tanishq, so "implausible" means the same thing for every retailer. A
# rate outside it is a parsing/unit error (per-10g vs per-g, a placeholder like 0 or
# 999999), never a real market price -- fail closed rather than display it (ADR 059).
MIN_PLAUSIBLE_RATE_22K = 2000.0
MAX_PLAUSIBLE_RATE_22K = 25000.0


def validate_rate_22k(rate: float, *, source: str) -> float:
    """Fail closed on an implausible 22K per-gram rate before it reaches a reading.

    Raises :class:`SourceStructureError` (the same "skip this source this cycle"
    treatment every caller already gives a structure failure) when ``rate`` is not a
    finite number inside [MIN_PLAUSIBLE_RATE_22K, MAX_PLAUSIBLE_RATE_22K].
    """
    if not math.isfinite(rate) or not (MIN_PLAUSIBLE_RATE_22K <= rate <= MAX_PLAUSIBLE_RATE_22K):
        raise SourceStructureError(
            f"{source}: implausible 22K rate {rate!r} (outside "
            f"Rs.{MIN_PLAUSIBLE_RATE_22K:.0f}-{MAX_PLAUSIBLE_RATE_22K:.0f}/g) — "
            "likely a unit or parsing error; not recorded"
        )
    return rate


@dataclass(frozen=True)
class SourceReading:
    """One source's rate observation, honestly attributed.

    ``city`` is ``None`` for a national-level reading (GRT, Malabar, IBJA
    today). ``attribution`` is a human-readable string naming exactly what
    was read and from where — never generic ("gold rate"), always specific
    enough that a reader can tell this number came from this source and
    nowhere else (e.g. "Kalyan Jewellers — BANGALORE board rate").
    """

    source: str
    city: str | None
    rate_22k: float
    observed_at: datetime
    attribution: str


class SourceAdapter(Protocol):
    """Structural interface every source adapter satisfies.

    Per this repo's convention, a Protocol (not an ABC) — concrete adapters
    implement this shape without inheriting from anything.
    """

    def fetch(self) -> SourceReading:
        """Fetch and parse the current rate.

        Raises :class:`SourceNetworkError` on a transient failure or
        :class:`SourceStructureError` if the response's expected shape is
        gone. Never returns a fabricated or partially-guessed reading.
        """
        ...
