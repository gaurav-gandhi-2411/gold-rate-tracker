"""Common types for retail gold-price source adapters (ADR 026)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


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
