"""ml.leak_guard -- refuse to score a prediction that used an input not yet known (ADR 061).

Three timing leaks in this repo were caught only by human review: training labels that matured
after the test day (the missing embargo, ADR 038 A1 / #1930), a same-day India VIX close used to
predict that same day's move (ADR 038 A2 / #1949), and nowcast inputs captured after the reading
they estimate (the ADR 055 Kalman audit, #2070). This module makes that class of mistake a hard
error in the evaluation harness instead of a review finding.

The contract: every input carries `known_at`, the tz-aware UTC instant it became KNOWN
(publication or capture time -- never the value date; conventions live in ml.known_at). A
prediction made at moment `t` may use an input only if `known_at < t`, strictly. Equal is a leak:
an input published at the same instant the prediction is made cannot have been used to make it.

    assert_known_before(t, inputs)   raises TimingLeakError if any input has known_at >= t
    filter_known_before(t, inputs)   the inputs a caller MAY use at t (for callers that filter)
    LeakGuard(mode="raise"|"report") a per-run accumulator: "raise" refuses to score; "report"
                                     records every violation without changing the run, for
                                     registered analyses whose published numbers must not move
                                     (ADR 061: a report-mode guard documents a known leak)

Fail closed: a tz-naive timestamp, None, NaT or an unparseable value is a NaiveTimestampError,
never silently read as UTC. There is no "couldn't check, allow" path.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import pandas as pd

Mode = Literal["raise", "report"]
# Report summaries keep this many example violations (full counts are always kept).
_MAX_EXAMPLES = 5


class LeakGuardError(ValueError):
    """Base class: the guard could not certify that every input was known in time."""


class NaiveTimestampError(LeakGuardError):
    """A timestamp without a time zone (or a missing one). Refused, never assumed UTC."""


class TimingLeakError(LeakGuardError):
    """At least one input became known at or after the prediction moment."""

    def __init__(
        self, prediction_moment: pd.Timestamp, violations: list[Violation], context: str = ""
    ) -> None:
        self.prediction_moment = prediction_moment
        self.violations = violations
        self.context = context
        shown = "; ".join(v.describe() for v in violations[:_MAX_EXAMPLES])
        more = (
            f" (+{len(violations) - _MAX_EXAMPLES} more)" if len(violations) > _MAX_EXAMPLES else ""
        )
        where = f"{context}: " if context else ""
        super().__init__(
            f"{where}{len(violations)} input(s) not known before the prediction moment "
            f"{prediction_moment.isoformat()}: {shown}{more}"
        )


def to_utc(ts: Any, *, what: str = "timestamp") -> pd.Timestamp:
    """A tz-aware UTC pd.Timestamp, or NaiveTimestampError. Accepts pd.Timestamp, datetime and
    ISO strings WITH an offset or 'Z'. A naive value is rejected, not assumed to be UTC: that
    assumption is exactly how an IST wall-clock time silently becomes 5.5 hours early."""
    if ts is None or (not isinstance(ts, str) and pd.isna(ts)):
        raise NaiveTimestampError(f"{what} is missing ({ts!r}); cannot certify when it was known")
    if isinstance(ts, (pd.Timestamp, datetime, str)):
        try:
            out = pd.Timestamp(ts)
        except (ValueError, TypeError) as exc:
            raise NaiveTimestampError(f"{what} {ts!r} is not a timestamp") from exc
    else:
        raise NaiveTimestampError(f"{what} {ts!r} has unsupported type {type(ts).__name__}")
    if out is pd.NaT or pd.isna(out):
        raise NaiveTimestampError(f"{what} {ts!r} is NaT")
    if out.tzinfo is None:
        raise NaiveTimestampError(f"{what} {ts!r} has no time zone; refusing to assume UTC")
    return out.tz_convert("UTC")


@dataclass(frozen=True)
class KnownInput:
    """One input to one prediction. `known_at` is validated to tz-aware UTC on construction."""

    name: str
    source: str
    known_at: pd.Timestamp

    def __post_init__(self) -> None:
        object.__setattr__(self, "known_at", to_utc(self.known_at, what=f"{self.name}.known_at"))


@dataclass(frozen=True)
class Violation:
    prediction_moment: pd.Timestamp
    input: KnownInput

    @property
    def late_by_s(self) -> float:
        """Seconds the input became known at/after the prediction moment (>= 0)."""
        return float((self.input.known_at - self.prediction_moment).total_seconds())

    def describe(self) -> str:
        return (
            f"{self.input.name} [{self.input.source}] known_at "
            f"{self.input.known_at.isoformat()} (+{self.late_by_s:.0f}s)"
        )


def find_leaks(prediction_moment: Any, inputs: Iterable[KnownInput]) -> list[Violation]:
    """Every input with known_at >= prediction_moment (strict: equal is a leak)."""
    t = to_utc(prediction_moment, what="prediction_moment")
    return [Violation(t, x) for x in inputs if x.known_at >= t]


def assert_known_before(
    prediction_moment: Any, inputs: Iterable[KnownInput], *, context: str = ""
) -> None:
    """Refuse to score: TimingLeakError if any input was not known strictly before the moment."""
    t = to_utc(prediction_moment, what="prediction_moment")
    bad = find_leaks(t, inputs)
    if bad:
        raise TimingLeakError(t, bad, context)


def filter_known_before(prediction_moment: Any, inputs: Iterable[KnownInput]) -> list[KnownInput]:
    """The inputs known strictly before the moment, in their original order."""
    t = to_utc(prediction_moment, what="prediction_moment")
    return [x for x in inputs if x.known_at < t]


@dataclass
class LeakGuard:
    """Checks every prediction of one evaluation run.

    mode="raise": the first leaky prediction raises TimingLeakError (the run refuses to score).
    mode="report": violations are counted and summarised; the run's numbers are untouched. Use
    only where a registered/published number would otherwise change -- the report IS the finding.
    """

    name: str
    mode: Mode = "raise"
    n_checks: int = 0
    n_inputs: int = 0
    # Predictions the caller could not check (e.g. no provenance). Only meaningful in report
    # mode; a raise-mode caller must raise instead of counting.
    n_unchecked: int = 0
    violations: list[Violation] = field(default_factory=list)
    contexts_with_violation: list[str] = field(default_factory=list)
    _examples: list[tuple[str, Violation]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.mode not in ("raise", "report"):
            raise ValueError(f"LeakGuard mode must be 'raise' or 'report', got {self.mode!r}")

    def check(
        self, prediction_moment: Any, inputs: Iterable[KnownInput], *, context: str = ""
    ) -> list[Violation]:
        items = list(inputs)
        t = to_utc(prediction_moment, what="prediction_moment")
        bad = find_leaks(t, items)
        self.n_checks += 1
        self.n_inputs += len(items)
        if bad:
            if self.mode == "raise":
                raise TimingLeakError(t, bad, f"{self.name} {context}".strip())
            self.violations.extend(bad)
            self.contexts_with_violation.append(context)
            if len(self._examples) < _MAX_EXAMPLES:
                self._examples.append((context, bad[0]))
        return bad

    def summary(self) -> dict[str, Any]:
        by_source = Counter(v.input.source for v in self.violations)
        return {
            "name": self.name,
            "mode": self.mode,
            "n_checks": self.n_checks,
            "n_inputs_checked": self.n_inputs,
            "n_unchecked": self.n_unchecked,
            "n_violations": len(self.violations),
            "n_checks_with_violation": len(self.contexts_with_violation),
            "violations_by_source": dict(sorted(by_source.items())),
            "max_late_by_s": max((v.late_by_s for v in self.violations), default=None),
            "examples": [{"context": c, "input": v.describe()} for c, v in self._examples],
        }
