"""ml.ingest_checks -- data-quality checks applied to every data source AT INGESTION.

Why this exists (item 2f, 2026-10): Kalyan's placeholder ``updated_time`` ("01 Jan 1970 00:00",
IST) parsed cleanly to ``1969-12-31T18:30:00+00:00`` and was persisted into
``data/fusion_snapshots.parquet`` 445 times (2026-07-22 .. 2026-09-24) before anyone noticed it in
analysis. ``ml.sources.base.validate_observed_at`` now rejects it per reading at the adapter; this
module is the second, independent layer: it re-validates what is *stored*, for every source, and
adds the checks a single reading cannot do (day-over-day jump, duplicates, ordering, cadence
staleness, cross-source consistency).

Design rules (repo rule 98a, fail closed):
  * Every check returns a ``list[Violation]``; an empty list is the only "pass".
  * Missing / unparseable / wrong-typed input is itself a violation, never a skip.
  * A string number ("13700"), a bool, NaN, inf, a negative price, an epoch placeholder, a naive
    timestamp, a future timestamp are all violations. Nothing is coerced into passing.
  * Pure functions: ``now`` is always a parameter; nothing here reads the clock except ``main``.

NOT wired into any live path (no scraper, workflow or ``ml.inference`` imports it). The CLI
``python -m ml.ingest_checks`` evaluates the committed data files and writes
``data/data_quality_status.json``; see docs/INGEST_QUALITY_CHECKS.md for the optional,
non-blocking CI step.

Bound provenance: every constant below was derived from the committed history on 2026-10-05
(master de27ee08..1189d22a) with the rule stated beside it, and reproduced by
``scripts/replay_ingest_checks.py`` (reports/ingest_checks_replay_2026-10-05.json).
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

SCHEMA_VERSION = 1
Severity = Literal["block", "warn"]

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATUS_PATH = DATA_DIR / "data_quality_status.json"

# --------------------------------------------------------------------------------------
# Machine codes (stable API: dashboards / CI grep these)
# --------------------------------------------------------------------------------------
FILE_MISSING = "FILE_MISSING"
FILE_UNPARSEABLE = "FILE_UNPARSEABLE"
SCHEMA_MISSING_FIELD = "SCHEMA_MISSING_FIELD"
SCHEMA_BAD_TYPE = "SCHEMA_BAD_TYPE"
SCHEMA_UNKNOWN_VALUE = "SCHEMA_UNKNOWN_VALUE"
EMPTY_SOURCE = "EMPTY_SOURCE"
TS_UNPARSEABLE = "TS_UNPARSEABLE"
TS_NAIVE = "TS_NAIVE"
TS_EPOCH_PLACEHOLDER = "TS_EPOCH_PLACEHOLDER"
TS_FUTURE = "TS_FUTURE"
TS_NOT_AFTER_PREVIOUS = "TS_NOT_AFTER_PREVIOUS"
TS_ORDER = "TS_ORDER"
DUPLICATE_KEY = "DUPLICATE_KEY"
VALUE_NOT_NUMERIC = "VALUE_NOT_NUMERIC"
VALUE_NOT_FINITE = "VALUE_NOT_FINITE"
VALUE_NON_POSITIVE = "VALUE_NON_POSITIVE"
VALUE_OUT_OF_RANGE = "VALUE_OUT_OF_RANGE"
VALUE_OUTSIDE_HISTORY = "VALUE_OUTSIDE_HISTORY"
UNIT_SUSPECT_PER_10G = "UNIT_SUSPECT_PER_10G"
UNIT_SUSPECT_PER_G = "UNIT_SUSPECT_PER_G"
JUMP_WARN = "JUMP_WARN"
JUMP_BLOCK = "JUMP_BLOCK"
STALE_WARN = "STALE_WARN"
STALE_BLOCK = "STALE_BLOCK"
GAP_WARN = "GAP_WARN"
GAP_BLOCK = "GAP_BLOCK"
RATIO_WARN = "RATIO_WARN"
RATIO_BLOCK = "RATIO_BLOCK"
CROSS_SOURCE_WARN = "CROSS_SOURCE_WARN"
CROSS_SOURCE_BLOCK = "CROSS_SOURCE_BLOCK"
NULL_VALUE = "NULL_VALUE"
SOURCE_REPORTED_FAILURE = "SOURCE_REPORTED_FAILURE"
NOT_COMMITTED = "NOT_COMMITTED"


@dataclass(frozen=True)
class Violation:
    """One failed check. ``value`` is the offending value, JSON-safe (strings for odd types)."""

    source: str
    check: str
    severity: Severity
    code: str
    message: str
    value: Any = None
    where: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "check": self.check,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "value": _jsonable(self.value),
            "where": self.where,
        }


@dataclass
class SourceReport:
    """Result of evaluating one source. ``status`` is the worst severity present."""

    source: str
    path: str
    status: str  # pass | warn | block | not_committed
    n_rows: int = 0
    newest_at: str | None = None
    violations: list[Violation] = field(default_factory=list)
    note: str | None = None

    def counts_by_code(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in self.violations:
            out[f"{v.severity}:{v.code}"] = out.get(f"{v.severity}:{v.code}", 0) + 1
        return dict(sorted(out.items()))

    def to_dict(self, max_violations: int = 50) -> dict[str, Any]:
        n_block = sum(v.severity == "block" for v in self.violations)
        n_warn = sum(v.severity == "warn" for v in self.violations)
        # Blocks first so truncation never hides the worst findings.
        ordered = sorted(self.violations, key=lambda v: (v.severity != "block", v.code))
        return {
            "status": self.status,
            "path": self.path,
            "n_rows": self.n_rows,
            "newest_at": self.newest_at,
            "n_block": n_block,
            "n_warn": n_warn,
            "counts_by_code": self.counts_by_code(),
            "violations": [v.to_dict() for v in ordered[:max_violations]],
            "violations_truncated": max(0, len(ordered) - max_violations),
            "note": self.note,
        }


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, pd.Timestamp | datetime | date):
        return value.isoformat()
    return repr(value)[:200]


def _v(
    source: str,
    check: str,
    severity: Severity,
    code: str,
    message: str,
    value: Any = None,
    where: str | None = None,
) -> Violation:
    return Violation(source, check, severity, code, message, value, where)


# --------------------------------------------------------------------------------------
# Bounds (derived from committed history; see module docstring for provenance)
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Bounds:
    """Hard range (block) plus an optional wider-than-history warn band.

    ``hard`` = [0.5 x historical min, 2 x historical max] so a per-10g value in a per-gram field
    (x10) or the reverse (/10) is always outside it. ``warn`` = [0.8 x min, 1.25 x max]: a new
    all-time high is information, not an error.
    """

    hard_lo: float
    hard_hi: float
    warn_lo: float | None = None
    warn_hi: float | None = None


@dataclass(frozen=True)
class JumpLimits:
    """Per-step |ln(v/prev)| limits: warn = 2 x and block = 4 x the historical 99.9th percentile."""

    warn: float
    block: float


def _jl(p999: float) -> JumpLimits:
    return JumpLimits(warn=round(2 * p999, 4), block=round(4 * p999, 4))


# 22K Rs/g (retailer boards, Tanishq prices.json, IBJA-calibrated). History: prices.json
# 12,890..15,075 (n=751, 2026-04-14..2026-10-05); fusion_snapshots 12,xxx..15,042. Upper hard bound
# 25,000 equals ml.sources.base.MAX_PLAUSIBLE_RATE_22K and scraper RANGE_MAX (same "impossible").
RATE_22K_PER_G = Bounds(hard_lo=6445.0, hard_hi=25000.0, warn_lo=10312.0, warn_hi=18844.0)
# 24K / 18K Tanishq per g, scaled by purity from the 22K bounds.
RATE_24K_PER_G = Bounds(7032.0, 27273.0, 11249.0, 20557.0)
RATE_18K_PER_G = Bounds(5273.0, 20455.0, 8437.0, 15418.0)
# IBJA pm_916 / am_916 Rs per 10 g: committed 44,122..148,944 (n=273, 2022-01-19..2026-10-05).
# No lower warn band: the 2022 regime is 3x lower than today by real market history.
IBJA_916_PER_10G = Bounds(hard_lo=22061.0, hard_hi=297888.0, warn_hi=186180.0)
# Seeds (22K per 10 g, 2013..2026-09-23): label 22,477..156,490; proxy 22,464..156,251.
SEED_22K_PER_10G = Bounds(hard_lo=11232.0, hard_hi=312980.0, warn_hi=195613.0)

# Macro: committed feature store (n=232, 2025-01-09..2026-10-05). hard = [0.5 min, 2 max] except
# vol indices (x3 high: a vol spike is real) ; warn band as above.
MACRO_BOUNDS: dict[str, Bounds] = {
    "gold_usd": Bounds(1341.0, 9715.0, 2147.0, 6072.0),  # 2,684..4,858 USD/oz
    "usd_inr": Bounds(42.1, 193.1, 67.4, 120.7),  # 84.2..96.6
    "us_10y_yield": Bounds(2.0, 10.6, 3.2, 6.6),  # 4.00..5.30 (percent)
    "dxy": Bounds(48.6, 218.4, 77.8, 136.5),  # 97.2..109.2
    "sensex": Bounds(35955.0, 171441.0, 57528.0, 107151.0),  # 71,910..85,720
    "vix": Bounds(7.1, 101.5, 11.4, 42.3),  # 14.2..33.8
    "crude_wti": Bounds(27.6, 217.3, 44.2, 135.8),  # 55.3..108.7
    "tips": Bounds(50.6, 220.2, 81.0, 137.7),  # 101.3..110.1 (TIP ETF price)
    "india_vix": Bounds(5.2, 43.5, 8.3, 18.1),  # 10.3..14.5 (n=13 only)
}
# Log-change per sqrt(calendar day) p99.9 from the feature store sorted by as_of_date
# (backfill_yfinance + live_pit), max-of-both rounded up.
MACRO_JUMP: dict[str, JumpLimits] = {
    "gold_usd": _jl(0.044),
    "usd_inr": _jl(0.015),
    "us_10y_yield": _jl(0.032),
    "dxy": _jl(0.013),
    "sensex": _jl(0.022),
    "vix": _jl(0.32),
    "crude_wti": _jl(0.081),
    "tips": _jl(0.012),
    "india_vix": _jl(0.204),
}
# Tanishq 22K per reading |pct|: p99.9 = 0.0371 (max 0.0547 = real 2026-05-13 move).
TANISHQ_JUMP = _jl(0.0371)
# IBJA gap-scaled |ln| / sqrt(days): p99.9 = 0.0535, max 0.0598 (2026-05-13).
IBJA_JUMP = _jl(0.0535)
# Fusion board readings, step between consecutive captures of the same series: p99.9 0.0264.
FUSION_JUMP = _jl(0.0264)
# Seeds, day over day: p99.9 label 0.0658 / proxy 0.0718; max 0.1103 (2026-01-30 real crash).
SEED_JUMP = _jl(0.0718)
# IBJA ibja_pm/am same-day: max 1.64% over 264 rows; warn 2x, block 4x.
IBJA_AM_PM_WARN = 0.033
IBJA_AM_PM_BLOCK = 0.066

# Staleness / gap limits in hours (newest value age vs expected publish cadence).
# Tanishq prices: 8 runs/day target, p50 gap 3.0h, p99 37h, p99.9 94h, max 101h (CF blocks).
TANISHQ_STALE_WARN_H = 48.0
TANISHQ_STALE_BLOCK_H = 168.0
# fusion_snapshots per source: 6h cron, p50 5.8h, p99 13.1h, max 25.7h.
FUSION_STALE_WARN_H = 24.0
FUSION_STALE_BLOCK_H = 72.0
# shadow_fusion_output.json: same 6h cron, tighter because it is a single latest-state file.
SHADOW_STALE_WARN_H = 12.0
SHADOW_STALE_BLOCK_H = 48.0
# feature store: one live_pit row per day; max live as_of gap 4 days (weekend + holiday).
FEATURE_STORE_STALE_WARN_H = 120.0
FEATURE_STORE_STALE_BLOCK_H = 240.0
# IBJA: business days (ml.ibja.business_days_since); IBJA skips Sat/Sun/central holidays.
IBJA_STALE_WARN_BD = 3
IBJA_STALE_BLOCK_BD = 7
# Before this date the IBJA store was a sparse backfill (gaps up to 123 days); inter-row gap
# checks apply only from the first live-capture row on.
IBJA_LIVE_ERA_START = "2026-04-17"

# Observation age (capture - observed_at) per fusion source, hours. grt observed==capture;
# malabar max 53.5h; ibja is a clock-assumed publish time (max 96.2h across weekend + holiday);
# kalyan "updated_time" max 21h.
FUSION_OBS_AGE_H: dict[str, tuple[float, float]] = {
    "grt": (1.0, 24.0),
    "malabar": (96.0, 192.0),
    "ibja": (144.0, 240.0),
    "kalyan": (36.0, 96.0),
}
NATIONAL_SOURCES = ("ibja", "grt", "malabar")
KALYAN_CITIES = ("Bangalore", "Chennai", "Hyderabad", "Ernakulam")

# Cross-source: max relative spread over 301 same-capture national triples was 3.44%.
CROSS_SOURCE_WARN_PCT = 0.05
CROSS_SOURCE_BLOCK_PCT = 0.10
# Tanishq 22K / (IBJA pm_916 / 10): observed 0.9894..1.0450 over 118 pairs (calibration slope
# 1.0131). Warn outside [0.95, 1.08], block outside [0.85, 1.20].
TANISHQ_IBJA_WARN = (0.95, 1.08)
TANISHQ_IBJA_BLOCK = (0.85, 1.20)
# Purity ratios: 24K/22K = 24/22 = 1.0909 (observed 1.09088..1.09094); 18K/22K = 0.81818.
KARAT_RATIO_TOL_WARN = 0.005
KARAT_RATIO_TOL_BLOCK = 0.02
# IBJA purity columns vs 916 (observed dev <= 0.5% except one pm_750 row at +3.4%).
IBJA_PURITY_TOL_WARN = 0.005
IBJA_PURITY_TOL_BLOCK = 0.05
IBJA_PURITIES = {"999": 999, "995": 995, "750": 750, "585": 585}
# Label (with-duty, rounded) vs proxy: observed -10.9% .. +7.3%, p99.9 6.2%.
SEED_LABEL_PROXY_WARN = 0.15
SEED_LABEL_PROXY_BLOCK = 0.30

MIN_PLAUSIBLE_YEAR = 2020  # same threshold as ml.sources.base.MIN_PLAUSIBLE_YEAR
# The static seeds legitimately start 2013-01-01; epoch placeholders (1969/1970) still fail.
SEED_MIN_YEAR = 2000
DEFAULT_MAX_FUTURE = timedelta(minutes=5)
# GRT observed_at is stamped ~0.5s AFTER capture_utc in the same run, so row-level
# observed-vs-capture comparisons allow a small skew.
OBS_VS_CAPTURE_SKEW = timedelta(minutes=5)


# --------------------------------------------------------------------------------------
# Primitive checks
# --------------------------------------------------------------------------------------


def parse_timestamp(value: Any) -> tuple[pd.Timestamp | None, str | None]:
    """Parse a timestamp strictly. Returns (ts, None) or (None, reason).

    Numbers (epoch ints/floats) are rejected: an epoch is ambiguous in unit and is exactly the
    shape a placeholder takes. Bools, NaN/NaT, None and blank strings are rejected.
    """
    if value is None or isinstance(value, bool):
        return None, f"not a timestamp: {value!r}"
    if isinstance(value, int | float):
        return None, f"numeric timestamp not accepted (ambiguous epoch): {value!r}"
    if isinstance(value, str):
        if not value.strip():
            return None, "blank timestamp string"
        try:
            ts = pd.Timestamp(value)
        except (ValueError, TypeError, OverflowError) as exc:
            return None, f"unparseable timestamp {value!r}: {exc}"
    elif isinstance(value, pd.Timestamp | datetime):
        ts = pd.Timestamp(value)
    else:
        return None, f"unsupported timestamp type {type(value).__name__}"
    if pd.isna(ts):
        return None, "NaT"
    return ts, None


def check_timestamp(
    value: Any,
    *,
    source: str,
    field_name: str,
    now: datetime | pd.Timestamp,
    prev: Any = None,
    where: str | None = None,
    require_tz: bool = True,
    max_future: timedelta = DEFAULT_MAX_FUTURE,
    strictly_after_prev: bool = False,
    min_year: int = MIN_PLAUSIBLE_YEAR,
    check: str = "timestamp",
) -> list[Violation]:
    """Timestamp sanity: parseable, tz-aware, not an epoch placeholder, not future, ordered.

    ``prev`` (optional) is the previous reading's timestamp: ``value`` must not be before it
    (or must be strictly after, with ``strictly_after_prev``).
    """
    ts, reason = parse_timestamp(value)
    if ts is None:
        return [_v(source, check, "block", TS_UNPARSEABLE, f"{field_name}: {reason}", value, where)]
    out: list[Violation] = []
    if ts.tzinfo is None:
        if require_tz:
            out.append(
                _v(
                    source,
                    check,
                    "block",
                    TS_NAIVE,
                    f"{field_name}: timezone-naive timestamp {ts.isoformat()}",
                    value,
                    where,
                )
            )
        ts = ts.tz_localize("UTC")
    ts_utc = ts.tz_convert("UTC")
    if ts_utc.year < min_year:
        out.append(
            _v(
                source,
                check,
                "block",
                TS_EPOCH_PLACEHOLDER,
                f"{field_name}: implausible {ts_utc.isoformat()} (year before "
                f"{min_year}) -- source likely sent a placeholder timestamp",
                value,
                where,
            )
        )
        return out  # nothing else about a placeholder is meaningful
    now_ts = pd.Timestamp(now)
    now_ts = now_ts.tz_localize("UTC") if now_ts.tzinfo is None else now_ts.tz_convert("UTC")
    if ts_utc > now_ts + max_future:
        out.append(
            _v(
                source,
                check,
                "block",
                TS_FUTURE,
                f"{field_name}: {ts_utc.isoformat()} is {(ts_utc - now_ts)} in the future "
                f"(reference {now_ts.isoformat()})",
                value,
                where,
            )
        )
    if prev is not None:
        pts, _ = parse_timestamp(prev)
        if pts is not None:
            pts = pts.tz_localize("UTC") if pts.tzinfo is None else pts.tz_convert("UTC")
            if ts_utc < pts or (strictly_after_prev and ts_utc == pts):
                out.append(
                    _v(
                        source,
                        check,
                        "block",
                        TS_NOT_AFTER_PREVIOUS,
                        f"{field_name}: {ts_utc.isoformat()} is not after previous "
                        f"reading {pts.isoformat()}",
                        value,
                        where,
                    )
                )
    return out


def check_number(
    value: Any,
    *,
    source: str,
    field_name: str,
    bounds: Bounds,
    where: str | None = None,
    check: str = "value",
) -> list[Violation]:
    """Numeric sanity: a real finite positive number inside bounds.

    A string number, a bool, None, NaN, inf, zero/negative are violations (never coerced).
    Out of range is classified as a unit slip when value/10 or value*10 lands in range.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        # numpy scalars subclass float/int via numbers; plain str/None/Decimal etc. fail here.
        try:
            import numbers

            if isinstance(value, numbers.Real) and not isinstance(value, bool):
                value = float(value)
            else:
                raise TypeError
        except TypeError:
            return [
                _v(
                    source,
                    check,
                    "block",
                    VALUE_NOT_NUMERIC,
                    f"{field_name}: expected a number, got {type(value).__name__} {value!r}",
                    value,
                    where,
                )
            ]
    fv = float(value)
    if not math.isfinite(fv):
        return [_v(source, check, "block", VALUE_NOT_FINITE, f"{field_name}: {fv!r}", fv, where)]
    if fv <= 0:
        return [
            _v(
                source,
                check,
                "block",
                VALUE_NON_POSITIVE,
                f"{field_name}: non-positive price {fv}",
                fv,
                where,
            )
        ]
    if not (bounds.hard_lo <= fv <= bounds.hard_hi):
        if bounds.hard_lo <= fv / 10.0 <= bounds.hard_hi:
            return [
                _v(
                    source,
                    check,
                    "block",
                    UNIT_SUSPECT_PER_10G,
                    f"{field_name}: {fv:,.2f} looks like a per-10g value in a per-gram field "
                    f"(/10 = {fv / 10:,.2f} is in range)",
                    fv,
                    where,
                )
            ]
        if bounds.hard_lo <= fv * 10.0 <= bounds.hard_hi:
            return [
                _v(
                    source,
                    check,
                    "block",
                    UNIT_SUSPECT_PER_G,
                    f"{field_name}: {fv:,.2f} looks like a per-gram value in a per-10g field "
                    f"(x10 = {fv * 10:,.2f} is in range)",
                    fv,
                    where,
                )
            ]
        return [
            _v(
                source,
                check,
                "block",
                VALUE_OUT_OF_RANGE,
                f"{field_name}: {fv:,.2f} outside hard range "
                f"[{bounds.hard_lo:,.0f}, {bounds.hard_hi:,.0f}]",
                fv,
                where,
            )
        ]
    if (bounds.warn_lo is not None and fv < bounds.warn_lo) or (
        bounds.warn_hi is not None and fv > bounds.warn_hi
    ):
        return [
            _v(
                source,
                check,
                "warn",
                VALUE_OUTSIDE_HISTORY,
                f"{field_name}: {fv:,.2f} outside the historical band "
                f"[{bounds.warn_lo}, {bounds.warn_hi}] (new extreme or regime change)",
                fv,
                where,
            )
        ]
    return []


def check_jump(
    prev: float,
    cur: float,
    *,
    source: str,
    field_name: str,
    limits: JumpLimits,
    gap_days: float | None = None,
    where: str | None = None,
) -> list[Violation]:
    """Step-change check on |ln(cur/prev)|, divided by sqrt(gap_days) when a gap is given.

    Callers must have passed both values through :func:`check_number` first; a non-positive or
    non-finite input here is a violation too (fail closed), not a silent skip.
    """
    try:
        ok = prev > 0 and cur > 0 and math.isfinite(prev) and math.isfinite(cur)
    except TypeError:
        ok = False
    if not ok:
        return [
            _v(
                source,
                "jump",
                "block",
                VALUE_NOT_FINITE,
                f"{field_name}: cannot compute step change from {prev!r} -> {cur!r}",
                cur,
                where,
            )
        ]
    step = abs(math.log(cur / prev))
    if gap_days is not None:
        step /= math.sqrt(max(gap_days, 1.0))
    if step > limits.block:
        return [
            _v(
                source,
                "jump",
                "block",
                JUMP_BLOCK,
                f"{field_name}: {prev:,.2f} -> {cur:,.2f} is a {step:.3%} step "
                f"(block limit {limits.block:.3%})",
                cur,
                where,
            )
        ]
    if step > limits.warn:
        return [
            _v(
                source,
                "jump",
                "warn",
                JUMP_WARN,
                f"{field_name}: {prev:,.2f} -> {cur:,.2f} is a {step:.3%} step "
                f"(warn limit {limits.warn:.3%})",
                cur,
                where,
            )
        ]
    return []


def check_staleness(
    newest: Any,
    *,
    source: str,
    now: datetime | pd.Timestamp,
    warn_hours: float,
    block_hours: float,
    where: str | None = None,
    check: str = "staleness",
) -> list[Violation]:
    """Age of the newest value vs the expected publish cadence. Unparseable newest = block."""
    ts, reason = parse_timestamp(newest)
    if ts is None:
        return [_v(source, check, "block", TS_UNPARSEABLE, f"newest: {reason}", newest, where)]
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    now_ts = pd.Timestamp(now)
    now_ts = now_ts.tz_localize("UTC") if now_ts.tzinfo is None else now_ts.tz_convert("UTC")
    age_h = (now_ts - ts).total_seconds() / 3600.0
    if age_h > block_hours:
        return [
            _v(
                source,
                check,
                "block",
                STALE_BLOCK,
                f"newest value is {age_h:.1f}h old (block > {block_hours:g}h)",
                ts.isoformat(),
                where,
            )
        ]
    if age_h > warn_hours:
        return [
            _v(
                source,
                check,
                "warn",
                STALE_WARN,
                f"newest value is {age_h:.1f}h old (warn > {warn_hours:g}h)",
                ts.isoformat(),
                where,
            )
        ]
    return []


def check_gap(
    prev: pd.Timestamp,
    cur: pd.Timestamp,
    *,
    source: str,
    warn_hours: float,
    block_hours: float,
    where: str | None = None,
) -> list[Violation]:
    """Inter-reading gap vs cadence (a past outage; same limits as staleness)."""
    gap_h = (cur - prev).total_seconds() / 3600.0
    if gap_h > block_hours:
        return [
            _v(
                source,
                "gap",
                "block",
                GAP_BLOCK,
                f"{gap_h:.1f}h gap between consecutive readings (block > {block_hours:g}h)",
                gap_h,
                where,
            )
        ]
    if gap_h > warn_hours:
        return [
            _v(
                source,
                "gap",
                "warn",
                GAP_WARN,
                f"{gap_h:.1f}h gap between consecutive readings (warn > {warn_hours:g}h)",
                gap_h,
                where,
            )
        ]
    return []


def _strict_float(value: Any) -> float | None:
    """A real number as float, or None for str/bool/None/other (never coerce "13720")."""
    import numbers

    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    return float(value)


def check_ratio(
    numerator: Any,
    denominator: Any,
    *,
    expected: float,
    tol_warn: float,
    tol_block: float,
    source: str,
    field_name: str,
    where: str | None = None,
) -> list[Violation]:
    """|num/den / expected - 1| within tolerance (purity ratios, cross-field consistency)."""
    try:
        sn, sd = _strict_float(numerator), _strict_float(denominator)
        if sn is None or sd is None:
            raise ValueError
        n, d = sn, sd
        if not (math.isfinite(n) and math.isfinite(d)) or d == 0:
            raise ValueError
    except (TypeError, ValueError):
        return [
            _v(
                source,
                "ratio",
                "block",
                VALUE_NOT_FINITE,
                f"{field_name}: cannot form ratio from {numerator!r} / {denominator!r}",
                numerator,
                where,
            )
        ]
    dev = abs((n / d) / expected - 1.0)
    if dev > tol_block:
        return [
            _v(
                source,
                "ratio",
                "block",
                RATIO_BLOCK,
                f"{field_name}: ratio {n / d:.5f} deviates {dev:.2%} from expected "
                f"{expected:.5f} (block > {tol_block:.1%})",
                n / d,
                where,
            )
        ]
    if dev > tol_warn:
        return [
            _v(
                source,
                "ratio",
                "warn",
                RATIO_WARN,
                f"{field_name}: ratio {n / d:.5f} deviates {dev:.2%} from expected "
                f"{expected:.5f} (warn > {tol_warn:.1%})",
                n / d,
                where,
            )
        ]
    return []


def check_cross_source(
    values: Mapping[str, Any], *, source: str = "cross_source", where: str | None = None
) -> list[Violation]:
    """Max relative spread across same-moment national 22K/g readings (retailers vs IBJA)."""
    clean: dict[str, float] = {}
    out: list[Violation] = []
    for name, val in values.items():
        bad = check_number(
            val, source=name, field_name="rate_22k", bounds=RATE_22K_PER_G, where=where
        )
        if any(b.severity == "block" for b in bad):
            out.extend(bad)
        else:
            clean[name] = float(val)
    if len(clean) < 2:
        if not out:
            out.append(
                _v(
                    source,
                    "cross_source",
                    "warn",
                    CROSS_SOURCE_WARN,
                    f"fewer than 2 usable sources to compare ({sorted(clean)})",
                    len(clean),
                    where,
                )
            )
        return out
    lo, hi = min(clean.values()), max(clean.values())
    spread = (hi - lo) / ((hi + lo) / 2)
    if spread > CROSS_SOURCE_BLOCK_PCT:
        out.append(
            _v(
                source,
                "cross_source",
                "block",
                CROSS_SOURCE_BLOCK,
                f"sources disagree by {spread:.2%} (block > {CROSS_SOURCE_BLOCK_PCT:.0%}): "
                f"{dict(sorted(clean.items()))}",
                spread,
                where,
            )
        )
    elif spread > CROSS_SOURCE_WARN_PCT:
        out.append(
            _v(
                source,
                "cross_source",
                "warn",
                CROSS_SOURCE_WARN,
                f"sources disagree by {spread:.2%} (warn > {CROSS_SOURCE_WARN_PCT:.0%}): "
                f"{dict(sorted(clean.items()))}",
                spread,
                where,
            )
        )
    return out


def check_retailer_vs_ibja(
    tanishq_22k: Any, ibja_pm_916_per_10g: Any, *, where: str | None = None
) -> list[Violation]:
    """Tanishq 22K/g vs IBJA 22K (pm_916 per 10 g / 10): ratio within the observed band."""
    try:
        st, si = _strict_float(tanishq_22k), _strict_float(ibja_pm_916_per_10g)
        if st is None or si is None:
            raise ValueError
        t, i = st, si / 10.0
        if not (math.isfinite(t) and math.isfinite(i)) or i <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return [
            _v(
                "tanishq_vs_ibja",
                "cross_source",
                "block",
                VALUE_NOT_FINITE,
                f"cannot compare tanishq {tanishq_22k!r} with ibja {ibja_pm_916_per_10g!r}",
                tanishq_22k,
                where,
            )
        ]
    r = t / i
    if not (TANISHQ_IBJA_BLOCK[0] <= r <= TANISHQ_IBJA_BLOCK[1]):
        sev, code = "block", CROSS_SOURCE_BLOCK
    elif not (TANISHQ_IBJA_WARN[0] <= r <= TANISHQ_IBJA_WARN[1]):
        sev, code = "warn", CROSS_SOURCE_WARN
    else:
        return []
    return [
        _v(
            "tanishq_vs_ibja",
            "cross_source",
            sev,  # type: ignore[arg-type]
            code,
            f"tanishq/ibja ratio {r:.4f} outside band (warn {TANISHQ_IBJA_WARN}, "
            f"block {TANISHQ_IBJA_BLOCK})",
            r,
            where,
        )
    ]


def check_source_reading(
    *,
    source: str,
    city: Any,
    rate_22k: Any,
    observed_at: Any,
    now: datetime | pd.Timestamp,
    where: str | None = None,
) -> list[Violation]:
    """Row-level check for one ``ml.sources.base.SourceReading``-shaped record.

    ``now`` is the reference clock for the future test: the capture time when replaying stored
    rows, the wall clock for a live reading.
    """
    out: list[Violation] = []
    if source not in (*NATIONAL_SOURCES, "kalyan"):
        out.append(
            _v(
                source,
                "schema",
                "block",
                SCHEMA_UNKNOWN_VALUE,
                f"unknown source {source!r}",
                source,
                where,
            )
        )
    city_missing = city is None or (isinstance(city, float) and math.isnan(city))
    if source == "kalyan":
        if city_missing or city not in KALYAN_CITIES:
            out.append(
                _v(
                    source,
                    "schema",
                    "block",
                    SCHEMA_UNKNOWN_VALUE,
                    f"kalyan reading needs a registered city, got {city!r}",
                    city,
                    where,
                )
            )
    elif not city_missing:
        out.append(
            _v(
                source,
                "schema",
                "block",
                SCHEMA_UNKNOWN_VALUE,
                f"{source} is a national source but carries city {city!r}",
                city,
                where,
            )
        )
    out += check_number(
        rate_22k, source=source, field_name="rate_22k", bounds=RATE_22K_PER_G, where=where
    )
    out += check_timestamp(
        observed_at,
        source=source,
        field_name="observed_at",
        now=now,
        max_future=OBS_VS_CAPTURE_SKEW,
        where=where,
    )
    return out


# --------------------------------------------------------------------------------------
# Source evaluators (pure: take loaded data + ``now``)
# --------------------------------------------------------------------------------------


def _worst(violations: Sequence[Violation]) -> str:
    if any(v.severity == "block" for v in violations):
        return "block"
    if violations:
        return "warn"
    return "pass"


def _report(
    source: str,
    path: str,
    n_rows: int,
    newest: Any,
    violations: list[Violation],
    note: str | None = None,
) -> SourceReport:
    ts, _ = parse_timestamp(newest) if newest is not None else (None, None)
    return SourceReport(
        source=source,
        path=path,
        status=_worst(violations),
        n_rows=n_rows,
        newest_at=ts.isoformat() if ts is not None else None,
        violations=violations,
        note=note,
    )


def _require_fields(row: Any, fields: Iterable[str], *, source: str, where: str) -> list[Violation]:
    if not isinstance(row, Mapping):
        return [
            _v(
                source,
                "schema",
                "block",
                SCHEMA_BAD_TYPE,
                f"record is {type(row).__name__}, expected object",
                row,
                where,
            )
        ]
    return [
        _v(
            source,
            "schema",
            "block",
            SCHEMA_MISSING_FIELD,
            f"missing field {f!r}",
            None,
            where,
        )
        for f in fields
        if f not in row
    ]


def check_tanishq_prices(
    records: Any,
    *,
    now: datetime | pd.Timestamp,
    series: bool = True,
    check_newest_staleness: bool = True,
    path: str = "data/prices.json",
) -> SourceReport:
    """data/prices.json: list of {timestamp, 22k, 24k, 18k, source}, Rs per gram, append-only."""
    src = "tanishq_prices"
    if not isinstance(records, list):
        return _report(
            src,
            path,
            0,
            None,
            [_v(src, "schema", "block", SCHEMA_BAD_TYPE, "top level is not a list", records)],
        )
    if not records:
        return _report(src, path, 0, None, [_v(src, "schema", "block", EMPTY_SOURCE, "empty")])
    out: list[Violation] = []
    prev_ts: pd.Timestamp | None = None
    prev_22: float | None = None
    seen: set[str] = set()
    newest: pd.Timestamp | None = None
    for i, rec in enumerate(records):
        where = f"row {i}"
        miss = _require_fields(
            rec, ("timestamp", "22k", "24k", "18k", "source"), source=src, where=where
        )
        if miss:
            out += miss
            continue
        where = f"row {i} @ {rec['timestamp']}"
        ts_v = check_timestamp(
            rec["timestamp"],
            source=src,
            field_name="timestamp",
            now=now,
            prev=prev_ts if series else None,
            strictly_after_prev=True,
            where=where,
        )
        out += ts_v
        ts, _ = parse_timestamp(rec["timestamp"])
        key = ts.tz_convert("UTC").isoformat() if ts is not None and ts.tzinfo else None
        if key is not None:
            if series and key in seen:
                out.append(
                    _v(src, "duplicates", "block", DUPLICATE_KEY, "duplicate timestamp", key, where)
                )
            seen.add(key)
        r22 = check_number(
            rec["22k"], source=src, field_name="22k", bounds=RATE_22K_PER_G, where=where
        )
        r24 = check_number(
            rec["24k"], source=src, field_name="24k", bounds=RATE_24K_PER_G, where=where
        )
        r18 = check_number(
            rec["18k"], source=src, field_name="18k", bounds=RATE_18K_PER_G, where=where
        )
        out += r22 + r24 + r18
        if not r22 and not r24:
            out += check_ratio(
                rec["24k"],
                rec["22k"],
                expected=24 / 22,
                tol_warn=KARAT_RATIO_TOL_WARN,
                tol_block=KARAT_RATIO_TOL_BLOCK,
                source=src,
                field_name="24k/22k",
                where=where,
            )
        if not r22 and not r18:
            out += check_ratio(
                rec["18k"],
                rec["22k"],
                expected=18 / 22,
                tol_warn=KARAT_RATIO_TOL_WARN,
                tol_block=KARAT_RATIO_TOL_BLOCK,
                source=src,
                field_name="18k/22k",
                where=where,
            )
        if not isinstance(rec["source"], str) or not rec["source"].strip():
            out.append(
                _v(
                    src,
                    "schema",
                    "block",
                    SCHEMA_BAD_TYPE,
                    "source label blank/non-string",
                    rec["source"],
                    where,
                )
            )
        good22 = not any(v.severity == "block" for v in r22)
        if series and good22 and prev_22 is not None:
            out += check_jump(
                prev_22,
                float(rec["22k"]),
                source=src,
                field_name="22k",
                limits=TANISHQ_JUMP,
                where=where,
            )
        if (
            series
            and ts is not None
            and ts.tzinfo is not None
            and prev_ts is not None
            and ts > prev_ts
        ):
            out += check_gap(
                prev_ts,
                ts,
                source=src,
                warn_hours=TANISHQ_STALE_WARN_H,
                block_hours=TANISHQ_STALE_BLOCK_H,
                where=where,
            )
        if ts is not None and ts.tzinfo is not None and ts.year >= MIN_PLAUSIBLE_YEAR:
            prev_ts = ts if prev_ts is None or ts > prev_ts else prev_ts
            newest = prev_ts
        if good22:
            prev_22 = float(rec["22k"])
    if check_newest_staleness and newest is not None:
        out += check_staleness(
            newest,
            source=src,
            now=now,
            warn_hours=TANISHQ_STALE_WARN_H,
            block_hours=TANISHQ_STALE_BLOCK_H,
        )
    elif check_newest_staleness:
        out.append(_v(src, "staleness", "block", TS_UNPARSEABLE, "no usable timestamp in any row"))
    return _report(src, path, len(records), newest, out)


_OUTCOMES = {"success", "skipped", "failure", "blocked", "error"}


def check_scrape_outcomes(
    records: Any,
    *,
    now: datetime | pd.Timestamp,
    series: bool = True,
    check_newest_staleness: bool = True,
    path: str = "data/tanishq_scrape_outcomes.jsonl",
) -> SourceReport:
    """tanishq_scrape_outcomes.jsonl: {timestamp, outcome, fetch_method, trigger, slot_ist, blocked}."""
    src = "tanishq_scrape_outcomes"
    if not isinstance(records, list) or not records:
        return _report(
            src, path, 0, None, [_v(src, "schema", "block", EMPTY_SOURCE, "no records", records)]
        )
    out: list[Violation] = []
    prev: pd.Timestamp | None = None
    seen: set[str] = set()
    newest: pd.Timestamp | None = None
    for i, rec in enumerate(records):
        where = f"row {i}"
        miss = _require_fields(rec, ("timestamp", "outcome"), source=src, where=where)
        if miss:
            out += miss
            continue
        where = f"row {i} @ {rec['timestamp']}"
        out += check_timestamp(
            rec["timestamp"],
            source=src,
            field_name="timestamp",
            now=now,
            prev=prev if series else None,
            where=where,
        )
        ts, _ = parse_timestamp(rec["timestamp"])
        if ts is not None and ts.tzinfo is not None and ts.year >= MIN_PLAUSIBLE_YEAR:
            k = ts.tz_convert("UTC").isoformat()
            if series and k in seen:
                out.append(
                    _v(src, "duplicates", "block", DUPLICATE_KEY, "duplicate timestamp", k, where)
                )
            seen.add(k)
            if series and prev is not None and ts > prev:
                out += check_gap(
                    prev,
                    ts,
                    source=src,
                    warn_hours=TANISHQ_STALE_WARN_H,
                    block_hours=TANISHQ_STALE_BLOCK_H,
                    where=where,
                )
            prev = ts if prev is None or ts > prev else prev
            newest = prev
        if rec["outcome"] not in _OUTCOMES:
            out.append(
                _v(
                    src,
                    "schema",
                    "warn",
                    SCHEMA_UNKNOWN_VALUE,
                    f"unknown outcome {rec['outcome']!r}",
                    rec["outcome"],
                    where,
                )
            )
        if "blocked" in rec and not isinstance(rec["blocked"], bool) and rec["blocked"] is not None:
            out.append(
                _v(
                    src,
                    "schema",
                    "warn",
                    SCHEMA_BAD_TYPE,
                    "blocked is not a bool",
                    rec["blocked"],
                    where,
                )
            )
    if check_newest_staleness and newest is not None:
        out += check_staleness(
            newest,
            source=src,
            now=now,
            warn_hours=TANISHQ_STALE_WARN_H,
            block_hours=TANISHQ_STALE_BLOCK_H,
        )
    return _report(src, path, len(records), newest, out)


_IBJA_COLS = ["am_999", "pm_999", "am_995", "pm_995", "am_916", "pm_916",
              "am_750", "pm_750", "am_585", "pm_585"]  # fmt: skip


def _ibja_bounds(col: str) -> Bounds:
    purity = int(col.split("_")[1])
    f = purity / 916.0
    b = IBJA_916_PER_10G
    return Bounds(b.hard_lo * f, b.hard_hi * f, None, None if b.warn_hi is None else b.warn_hi * f)


def _ist_today(now: datetime | pd.Timestamp) -> date:
    n = pd.Timestamp(now)
    n = n.tz_localize("UTC") if n.tzinfo is None else n.tz_convert("UTC")
    return (n + timedelta(hours=5, minutes=30)).date()


def check_ibja_rates(
    df: pd.DataFrame,
    *,
    now: datetime | pd.Timestamp,
    series: bool = True,
    check_newest_staleness: bool = True,
    path: str = "data/ibja_rates.parquet",
) -> SourceReport:
    """ibja_rates.parquet: IBJA AM/PM fixes, Rs per 10 g, by purity, one row per IST date."""
    from ml.ibja import business_days_since

    src = "ibja_rates"
    if not isinstance(df, pd.DataFrame) or df.empty:
        return _report(src, path, 0, None, [_v(src, "schema", "block", EMPTY_SOURCE, "no rows")])
    out: list[Violation] = []
    missing = [c for c in ("date", "fetched_at", *_IBJA_COLS) if c not in df.columns]
    for c in missing:
        out.append(_v(src, "schema", "block", SCHEMA_MISSING_FIELD, f"missing column {c!r}"))
    if "date" not in df.columns:
        return _report(src, path, len(df), None, out)
    today_ist = _ist_today(now)
    prev_date: pd.Timestamp | None = None
    seen: set[str] = set()
    newest_date: pd.Timestamp | None = None
    last_row_idx = len(df) - 1
    live_start = pd.Timestamp(IBJA_LIVE_ERA_START)
    sorted_idx = sorted(
        range(len(df)),
        key=lambda i: (
            pd.Timestamp(df["date"].iloc[i])
            if _is_isodate(df["date"].iloc[i])
            else pd.Timestamp.max
        ),
    )
    for i in range(len(df)):
        row = df.iloc[i]
        d = row["date"]
        where = f"row {i} date={d}"
        if not _is_isodate(d):
            out.append(
                _v(
                    src,
                    "timestamp",
                    "block",
                    TS_UNPARSEABLE,
                    f"date {d!r} is not YYYY-MM-DD",
                    d,
                    where,
                )
            )
            continue
        dt = pd.Timestamp(d)
        if dt.date() > today_ist + timedelta(days=1):
            out.append(
                _v(
                    src,
                    "timestamp",
                    "block",
                    TS_FUTURE,
                    f"date {d} is in the future (IST today {today_ist})",
                    d,
                    where,
                )
            )
        if dt.year < MIN_PLAUSIBLE_YEAR:
            out.append(
                _v(
                    src,
                    "timestamp",
                    "block",
                    TS_EPOCH_PLACEHOLDER,
                    f"date {d} before {MIN_PLAUSIBLE_YEAR}",
                    d,
                    where,
                )
            )
        if series:
            if str(d) in seen:
                out.append(
                    _v(src, "duplicates", "block", DUPLICATE_KEY, f"duplicate date {d}", d, where)
                )
            seen.add(str(d))
            if prev_date is not None and dt < prev_date:
                out.append(
                    _v(
                        src,
                        "ordering",
                        "warn",
                        TS_ORDER,
                        f"row dated {d} appears after {prev_date.date()} (file not date-ordered)",
                        d,
                        where,
                    )
                )
        newest_date = dt if newest_date is None or dt > newest_date else newest_date
        if "fetched_at" in df.columns:
            out += check_timestamp(
                row["fetched_at"], source=src, field_name="fetched_at", now=now, where=where
            )
        # per-column value checks
        good: dict[str, float] = {}
        null_am: list[str] = []
        null_pm: list[str] = []
        for c in _IBJA_COLS:
            if c not in df.columns:
                continue
            val = row[c]
            if _is_null(val):
                (null_am if c.startswith("am_") else null_pm).append(c)
                continue
            bad = check_number(val, source=src, field_name=c, bounds=_ibja_bounds(c), where=where)
            out += bad
            if not any(b.severity == "block" for b in bad):
                good[c] = float(val)
        if null_am:
            out.append(
                _v(src, "value", "block", NULL_VALUE, f"null AM columns {null_am}", None, where)
            )
        if null_pm and i != last_row_idx:
            # The newest row may legitimately lack PM (fix publishes ~17:00 IST).
            out.append(
                _v(
                    src,
                    "value",
                    "warn",
                    NULL_VALUE,
                    f"null PM columns on a past date {null_pm}",
                    None,
                    where,
                )
            )
        for half in ("am", "pm"):
            base = good.get(f"{half}_916")
            if base is None:
                continue
            for k, pur in IBJA_PURITIES.items():
                v = good.get(f"{half}_{k}")
                if v is None:
                    continue
                out += check_ratio(
                    v,
                    base,
                    expected=pur / 916.0,
                    tol_warn=IBJA_PURITY_TOL_WARN,
                    tol_block=IBJA_PURITY_TOL_BLOCK,
                    source=src,
                    field_name=f"{half}_{k}/{half}_916",
                    where=where,
                )
        if "am_916" in good and "pm_916" in good:
            dev = abs(good["pm_916"] / good["am_916"] - 1.0)
            if dev > IBJA_AM_PM_BLOCK:
                out.append(
                    _v(
                        src,
                        "ratio",
                        "block",
                        RATIO_BLOCK,
                        f"pm/am differ {dev:.2%} same day (block > {IBJA_AM_PM_BLOCK:.1%})",
                        dev,
                        where,
                    )
                )
            elif dev > IBJA_AM_PM_WARN:
                out.append(
                    _v(
                        src,
                        "ratio",
                        "warn",
                        RATIO_WARN,
                        f"pm/am differ {dev:.2%} same day (warn > {IBJA_AM_PM_WARN:.1%})",
                        dev,
                        where,
                    )
                )
        ref = good.get("pm_916", good.get("am_916"))
        if series and ref is not None:
            # jump vs previous DATE-ordered reading (file order may be unsorted, see TS_ORDER)
            j = sorted_idx.index(i)
            if j > 0:
                pi = sorted_idx[j - 1]
                prow = df.iloc[pi]
                pref = (
                    prow.get("pm_916") if not _is_null(prow.get("pm_916")) else prow.get("am_916")
                )
                if (
                    _is_isodate(prow["date"])
                    and not _is_null(pref)
                    and isinstance(pref, int | float)
                ):
                    gap = (dt - pd.Timestamp(prow["date"])).days
                    out += check_jump(
                        float(pref),
                        ref,
                        source=src,
                        field_name="pm_916|am_916",
                        limits=IBJA_JUMP,
                        gap_days=float(gap),
                        where=where,
                    )
                    if dt >= live_start and pd.Timestamp(prow["date"]) >= live_start and gap > 0:
                        bd = business_days_since(pd.Timestamp(prow["date"]).date(), dt.date())
                        if bd > IBJA_STALE_BLOCK_BD:
                            out.append(
                                _v(
                                    src,
                                    "gap",
                                    "block",
                                    GAP_BLOCK,
                                    f"{bd} business days between consecutive rows (block > {IBJA_STALE_BLOCK_BD})",
                                    bd,
                                    where,
                                )
                            )
                        elif bd > IBJA_STALE_WARN_BD:
                            out.append(
                                _v(
                                    src,
                                    "gap",
                                    "warn",
                                    GAP_WARN,
                                    f"{bd} business days between consecutive rows (warn > {IBJA_STALE_WARN_BD})",
                                    bd,
                                    where,
                                )
                            )
        if series:
            prev_date = dt if prev_date is None or dt > prev_date else prev_date
    if check_newest_staleness and newest_date is not None:
        bd = business_days_since(newest_date.date(), today_ist)
        if bd > IBJA_STALE_BLOCK_BD:
            out.append(
                _v(
                    src,
                    "staleness",
                    "block",
                    STALE_BLOCK,
                    f"newest IBJA date {newest_date.date()} is {bd} business days old (block > {IBJA_STALE_BLOCK_BD})",
                    str(newest_date.date()),
                )
            )
        elif bd > IBJA_STALE_WARN_BD:
            out.append(
                _v(
                    src,
                    "staleness",
                    "warn",
                    STALE_WARN,
                    f"newest IBJA date {newest_date.date()} is {bd} business days old (warn > {IBJA_STALE_WARN_BD})",
                    str(newest_date.date()),
                )
            )
    elif check_newest_staleness:
        out.append(_v(src, "staleness", "block", TS_UNPARSEABLE, "no parseable date in any row"))
    return _report(src, path, len(df), newest_date, out)


def _is_isodate(v: Any) -> bool:
    if not isinstance(v, str) or len(v) != 10:
        return False
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return True


def _is_null(v: Any) -> bool:
    if v is None:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


_FUSION_COLS = ("capture_utc", "as_of_date", "schema_version", "source", "city", "rate_22k",
                "observed_at", "attribution")  # fmt: skip


def check_fusion_snapshots(
    df: pd.DataFrame,
    *,
    now: datetime | pd.Timestamp,
    series: bool = True,
    check_newest_staleness: bool = True,
    path: str = "data/fusion_snapshots.parquet",
) -> SourceReport:
    """fusion_snapshots.parquet: one row per (capture, source[, city]) SourceReading, Rs/g 22K."""
    src = "fusion_snapshots"
    if not isinstance(df, pd.DataFrame) or df.empty:
        return _report(src, path, 0, None, [_v(src, "schema", "block", EMPTY_SOURCE, "no rows")])
    out: list[Violation] = []
    for c in _FUSION_COLS:
        if c not in df.columns:
            out.append(_v(src, "schema", "block", SCHEMA_MISSING_FIELD, f"missing column {c!r}"))
    if any(v.code == SCHEMA_MISSING_FIELD for v in out):
        return _report(src, path, len(df), None, out)
    seen: set[tuple[Any, Any, Any]] = set()
    last_by_series: dict[tuple[str, Any], tuple[pd.Timestamp, float]] = {}
    newest_by_source: dict[str, pd.Timestamp] = {}
    by_capture: dict[str, dict[str, float]] = {}
    caps = pd.to_datetime(df["capture_utc"], utc=True, errors="coerce", format="ISO8601")
    order = sorted(
        range(len(df)),
        key=lambda i: (
            caps.iloc[i] if pd.notna(caps.iloc[i]) else pd.Timestamp.max.tz_localize("UTC")
        ),
    )
    prev_cap_pos: pd.Timestamp | None = None
    for i in range(len(df)):
        r = df.iloc[i]
        where = f"row {i} {r['source']}/{r['city']} cap={r['capture_utc']}"
        cap = caps.iloc[i]
        cap_v = check_timestamp(
            r["capture_utc"], source=src, field_name="capture_utc", now=now, where=where
        )
        out += cap_v
        city = r["city"] if not _is_null(r["city"]) else None
        out += check_source_reading(
            source=str(r["source"]), city=city, rate_22k=r["rate_22k"],
            observed_at=r["observed_at"], now=cap if pd.notna(cap) else now, where=where,
        )  # fmt: skip
        if series:
            key = (r["capture_utc"], r["source"], city)
            if key in seen:
                out.append(
                    _v(
                        src,
                        "duplicates",
                        "block",
                        DUPLICATE_KEY,
                        f"duplicate {key}",
                        str(key),
                        where,
                    )
                )
            seen.add(key)
            if pd.notna(cap) and prev_cap_pos is not None and cap < prev_cap_pos:
                out.append(
                    _v(
                        src,
                        "ordering",
                        "warn",
                        TS_ORDER,
                        "capture_utc earlier than the row above",
                        r["capture_utc"],
                        where,
                    )
                )
        if pd.notna(cap):
            prev_cap_pos = cap if prev_cap_pos is None or cap > prev_cap_pos else prev_cap_pos
        v = r["schema_version"]
        if _is_null(v) or isinstance(v, bool) or int(v) != v or v < 1:
            out.append(
                _v(src, "schema", "block", SCHEMA_BAD_TYPE, f"schema_version {v!r}", v, where)
            )
        if not isinstance(r["attribution"], str) or not r["attribution"].strip():
            out.append(
                _v(src, "schema", "block", SCHEMA_BAD_TYPE, "blank attribution", None, where)
            )
        obs, _ = parse_timestamp(r["observed_at"])
        if (
            obs is not None
            and obs.tzinfo is not None
            and obs.year >= MIN_PLAUSIBLE_YEAR
            and pd.notna(cap)
            and r["source"] in FUSION_OBS_AGE_H
        ):
            age_h = (cap - obs.tz_convert("UTC")).total_seconds() / 3600
            warn_h, block_h = FUSION_OBS_AGE_H[str(r["source"])]
            if age_h > block_h:
                out.append(
                    _v(
                        src,
                        "staleness",
                        "block",
                        STALE_BLOCK,
                        f"observed_at is {age_h:.1f}h older than capture (block > {block_h:g}h)",
                        age_h,
                        where,
                    )
                )
            elif age_h > warn_h:
                out.append(
                    _v(
                        src,
                        "staleness",
                        "warn",
                        STALE_WARN,
                        f"observed_at is {age_h:.1f}h older than capture (warn > {warn_h:g}h)",
                        age_h,
                        where,
                    )
                )
    # series pass in capture order (jump per source/city, cross-source per capture, newest)
    for i in order:
        r = df.iloc[i]
        cap = caps.iloc[i]
        if pd.isna(cap):
            continue
        city = r["city"] if not _is_null(r["city"]) else None
        rate = r["rate_22k"]
        ok = (
            isinstance(rate, int | float)
            and not isinstance(rate, bool)
            and math.isfinite(rate)
            and RATE_22K_PER_G.hard_lo <= rate <= RATE_22K_PER_G.hard_hi
        )
        obs, _ = parse_timestamp(r["observed_at"])
        obs_ok = obs is not None and obs.tzinfo is not None and obs.year >= MIN_PLAUSIBLE_YEAR
        if not ok or not obs_ok:
            continue  # row-level violation already recorded; do not advance series state on junk
        s = str(r["source"])
        newest_by_source[s] = max(newest_by_source.get(s, cap), cap)
        if series:
            k = (s, city)
            if k in last_by_series:
                out += check_jump(
                    last_by_series[k][1],
                    float(rate),
                    source=src,
                    field_name=f"rate_22k[{s}/{city}]",
                    limits=FUSION_JUMP,
                    where=f"row {i} cap={r['capture_utc']}",
                )
            last_by_series[k] = (cap, float(rate))
            if city is None and s in NATIONAL_SOURCES:
                by_capture.setdefault(str(r["capture_utc"]), {})[s] = float(rate)
    if series:
        for cap_s, vals in by_capture.items():
            if len(vals) >= 2:
                out += check_cross_source(vals, source=src, where=f"capture {cap_s}")
    if check_newest_staleness:
        for s in (*NATIONAL_SOURCES, "kalyan"):
            if s not in newest_by_source:
                out.append(
                    _v(
                        src,
                        "staleness",
                        "block",
                        STALE_BLOCK,
                        f"no usable {s} reading in the store",
                        None,
                        s,
                    )
                )
                continue
            out += [
                Violation(src, x.check, x.severity, x.code, f"{s}: {x.message}", x.value, s)
                for x in check_staleness(
                    newest_by_source[s],
                    source=src,
                    now=now,
                    warn_hours=FUSION_STALE_WARN_H,
                    block_hours=FUSION_STALE_BLOCK_H,
                )
            ]
    newest = max(newest_by_source.values()) if newest_by_source else None
    return _report(src, path, len(df), newest, out)


def check_shadow_fusion_output(
    obj: Any,
    *,
    now: datetime | pd.Timestamp,
    check_newest_staleness: bool = True,
    path: str = "data/shadow_fusion_output.json",
) -> SourceReport:
    """shadow_fusion_output.json: latest 6h cycle (benchmark, per-city, per-source failures)."""
    src = "shadow_fusion_output"
    if not isinstance(obj, Mapping):
        return _report(
            src, path, 0, None, [_v(src, "schema", "block", SCHEMA_BAD_TYPE, "not an object", obj)]
        )
    out = _require_fields(
        obj,
        (
            "capture_utc",
            "as_of_date",
            "national_failures",
            "national_benchmark",
        ),
        source=src,
        where="root",
    )
    if out:
        return _report(src, path, 1, None, out)
    out += check_timestamp(obj["capture_utc"], source=src, field_name="capture_utc", now=now)
    if check_newest_staleness:
        out += check_staleness(
            obj["capture_utc"],
            source=src,
            now=now,
            warn_hours=SHADOW_STALE_WARN_H,
            block_hours=SHADOW_STALE_BLOCK_H,
        )
    if not _is_isodate(obj["as_of_date"]):
        out.append(
            _v(
                src,
                "timestamp",
                "block",
                TS_UNPARSEABLE,
                "as_of_date not YYYY-MM-DD",
                obj["as_of_date"],
            )
        )
    nb = obj["national_benchmark"]
    if nb is None:
        out.append(_v(src, "schema", "warn", NULL_VALUE, "national_benchmark is null this cycle"))
    elif not isinstance(nb, Mapping):
        out.append(
            _v(src, "schema", "block", SCHEMA_BAD_TYPE, "national_benchmark is not an object", nb)
        )
    else:
        bad = check_number(
            nb.get("value"),
            source=src,
            field_name="national_benchmark.value",
            bounds=RATE_22K_PER_G,
        )
        out += bad
        hw = nb.get("band_half_width")
        if (
            not bad
            and isinstance(hw, int | float)
            and not isinstance(hw, bool)
            and math.isfinite(hw)
            and hw >= 0
        ):
            if hw > 0.1 * float(nb["value"]):
                out.append(
                    _v(
                        src,
                        "value",
                        "warn",
                        VALUE_OUT_OF_RANGE,
                        f"band_half_width {hw} > 10% of value",
                        hw,
                    )
                )
        else:
            out.append(
                _v(src, "value", "block", VALUE_NOT_NUMERIC, f"band_half_width invalid: {hw!r}", hw)
            )
        used = nb.get("sources_used")
        if not isinstance(used, list) or not used:
            out.append(
                _v(
                    src,
                    "schema",
                    "block",
                    SCHEMA_BAD_TYPE,
                    "sources_used empty or not a list",
                    used,
                )
            )
    # Kalyan was retired (ADR 070): the per-city map and the Kalyan failure map are gone from new
    # files, so they are checked only when an older file still carries them.
    if "cities" not in obj:
        pass
    elif isinstance(obj["cities"], Mapping):
        for city, c in obj["cities"].items():
            if not isinstance(c, Mapping):
                out.append(
                    _v(
                        src,
                        "schema",
                        "block",
                        SCHEMA_BAD_TYPE,
                        f"city {city} entry not an object",
                        c,
                        city,
                    )
                )
                continue
            out += check_number(
                c.get("value"),
                source=src,
                field_name=f"cities.{city}.value",
                bounds=RATE_22K_PER_G,
                where=city,
            )
    else:
        out.append(
            _v(src, "schema", "block", SCHEMA_BAD_TYPE, "cities is not an object", obj["cities"])
        )
    for key in ("national_failures", "kalyan_failures"):
        if key not in obj:
            continue  # kalyan_failures: retired with Kalyan (ADR 070)
        fails = obj[key]
        if not isinstance(fails, Mapping):
            out.append(
                _v(src, "schema", "block", SCHEMA_BAD_TYPE, f"{key} is not an object", fails)
            )
            continue
        for name, msg in fails.items():
            sev: Severity = "warn"
            out.append(
                _v(
                    src,
                    "source_failure",
                    sev,
                    SOURCE_REPORTED_FAILURE,
                    f"{key}[{name}]: {msg}",
                    str(msg)[:200],
                    name,
                )
            )
    return _report(src, path, 1, obj["capture_utc"], out)


_FS_COLUMNS = ("capture_utc", "as_of_date", "schema_version", "source", "partial", *MACRO_BOUNDS,
               "ibja_pm_916", "ibja_am_916", "tanishq_22k")  # fmt: skip
_FS_CORE = ("gold_usd", "usd_inr", "us_10y_yield", "dxy", "sensex", "vix")
_FS_ASOF_MAX_LAG_DAYS = 6  # observed max 5 (macro), 4 (ibja/tanishq)


def check_feature_store(
    df: pd.DataFrame,
    *,
    now: datetime | pd.Timestamp,
    series: bool = True,
    check_newest_staleness: bool = True,
    path: str = "data/feature_store/snapshots.parquet",
) -> SourceReport:
    """feature_store snapshots: the committed macro + IBJA + Tanishq PIT record (one row per day)."""
    src = "feature_store"
    if not isinstance(df, pd.DataFrame) or df.empty:
        return _report(src, path, 0, None, [_v(src, "schema", "block", EMPTY_SOURCE, "no rows")])
    out: list[Violation] = []
    for c in _FS_COLUMNS:
        if c not in df.columns:
            out.append(_v(src, "schema", "block", SCHEMA_MISSING_FIELD, f"missing column {c!r}"))
    if out:
        return _report(src, path, len(df), None, out)
    seen: set[tuple[Any, Any]] = set()
    prev_by_col: dict[tuple[str, str], tuple[pd.Timestamp, float]] = {}
    ordered = sorted(
        range(len(df)), key=lambda i: (str(df["source"].iloc[i]), str(df["as_of_date"].iloc[i]))
    )
    newest_live: pd.Timestamp | None = None
    for i in range(len(df)):
        r = df.iloc[i]
        where = f"row {i} {r['source']} as_of={r['as_of_date']}"
        out += check_timestamp(
            r["capture_utc"], source=src, field_name="capture_utc", now=now, where=where
        )
        if not _is_isodate(r["as_of_date"]):
            out.append(
                _v(
                    src,
                    "timestamp",
                    "block",
                    TS_UNPARSEABLE,
                    "as_of_date not YYYY-MM-DD",
                    r["as_of_date"],
                    where,
                )
            )
            continue
        asof = pd.Timestamp(r["as_of_date"])
        if asof.year < MIN_PLAUSIBLE_YEAR:
            out.append(
                _v(
                    src,
                    "timestamp",
                    "block",
                    TS_EPOCH_PLACEHOLDER,
                    "as_of_date before 2020",
                    r["as_of_date"],
                    where,
                )
            )
        if series:
            k = (r["source"], r["as_of_date"])
            if k in seen:
                out.append(
                    _v(src, "duplicates", "block", DUPLICATE_KEY, f"duplicate {k}", str(k), where)
                )
            seen.add(k)
        for c in (*MACRO_BOUNDS, "ibja_pm_916", "ibja_am_916", "tanishq_22k"):
            val = r[c]
            if _is_null(val):
                if c in _FS_CORE:
                    out.append(
                        _v(
                            src,
                            "value",
                            "block",
                            NULL_VALUE,
                            f"core field {c} is null",
                            None,
                            where,
                        )
                    )
                continue
            b = MACRO_BOUNDS.get(c) or (
                IBJA_916_PER_10G if c.startswith("ibja") else RATE_22K_PER_G
            )
            out += check_number(val, source=src, field_name=c, bounds=b, where=where)
            a = r.get(f"{c}_asof_date")
            if _is_null(a) or not _is_isodate(a):
                # schema_version 1 rows (2026-06-07/08, since rewritten in place) predate the
                # ibja/tanishq *_asof_date columns: a missing one there is old-schema, not a fault.
                legacy = (
                    _is_null(a) and not _is_null(r["schema_version"]) and r["schema_version"] < 3
                )
                out.append(
                    _v(
                        src,
                        "timestamp",
                        "warn" if legacy else "block",
                        SCHEMA_MISSING_FIELD if legacy else TS_UNPARSEABLE,
                        f"{c}_asof_date invalid: {a!r}",
                        a,
                        where,
                    )
                )
                continue
            ad = pd.Timestamp(str(a))
            if ad > asof:
                out.append(
                    _v(
                        src,
                        "timestamp",
                        "block",
                        TS_FUTURE,
                        f"{c}_asof_date {a} is after as_of_date {r['as_of_date']}",
                        a,
                        where,
                    )
                )
            elif (asof - ad).days > _FS_ASOF_MAX_LAG_DAYS:
                out.append(
                    _v(
                        src,
                        "staleness",
                        "warn",
                        STALE_WARN,
                        f"{c} value is {(asof - ad).days}d older than as_of_date (warn > {_FS_ASOF_MAX_LAG_DAYS}d)",
                        (asof - ad).days,
                        where,
                    )
                )
        if r["source"] == "live_pit":
            cap_ts, _ = parse_timestamp(r["capture_utc"])
            if (
                cap_ts is not None
                and cap_ts.tzinfo is not None
                and cap_ts.year >= MIN_PLAUSIBLE_YEAR
            ):
                cap_ts = cap_ts.tz_convert("UTC")
                newest_live = cap_ts if newest_live is None or cap_ts > newest_live else newest_live
    if series:
        for i in ordered:
            r = df.iloc[i]
            if not _is_isodate(r["as_of_date"]):
                continue
            asof = pd.Timestamp(r["as_of_date"])
            for c, lim in {
                **MACRO_JUMP,
                "ibja_pm_916": _jl(0.057),
                "tanishq_22k": TANISHQ_JUMP,
            }.items():
                val = r[c]
                if (
                    _is_null(val)
                    or not isinstance(val, int | float)
                    or not math.isfinite(val)
                    or val <= 0
                ):
                    continue
                key = (str(r["source"]), c)
                if key in prev_by_col:
                    pa, pv = prev_by_col[key]
                    if asof > pa:
                        out += check_jump(
                            pv,
                            float(val),
                            source=src,
                            field_name=c,
                            limits=lim,
                            gap_days=float((asof - pa).days),
                            where=f"row {i} {r['source']} as_of={r['as_of_date']}",
                        )
                prev_by_col[key] = (asof, float(val))
    if check_newest_staleness:
        if newest_live is None:
            out.append(
                _v(
                    src,
                    "staleness",
                    "block",
                    STALE_BLOCK,
                    "no live_pit row with a usable capture_utc",
                )
            )
        else:
            out += check_staleness(
                newest_live,
                source=src,
                now=now,
                warn_hours=FEATURE_STORE_STALE_WARN_H,
                block_hours=FEATURE_STORE_STALE_BLOCK_H,
            )
    # cross-source: tanishq vs ibja pm_916 on the same row
    for i in range(len(df)):
        r = df.iloc[i]
        if not _is_null(r["tanishq_22k"]) and not _is_null(r["ibja_pm_916"]):
            out += [
                Violation(
                    src,
                    x.check,
                    x.severity,
                    x.code,
                    x.message,
                    x.value,
                    f"row {i} as_of={r['as_of_date']}",
                )
                for x in check_retailer_vs_ibja(r["tanishq_22k"], r["ibja_pm_916"])
            ]
    return _report(src, path, len(df), newest_live, out)


def check_macro_frame(
    df: pd.DataFrame,
    *,
    now: datetime | pd.Timestamp,
    kind: Literal["daily", "intraday"],
    path: str,
    series: bool = True,
    check_newest_staleness: bool = True,
) -> SourceReport:
    """macro_cache.parquet (daily) / macro_intraday.parquet (1h bars): index = bar start, UTC.

    Neither file is committed (regenerated each CI run, ml/macro.py), so there is no committed
    history to replay; bounds are the feature-store ones and per-bar jump limits are the DAILY
    limits (an hourly move cannot be larger than the daily limit in a sane feed) -- INFERRED, not
    replay-verified. Run it in CI right after the macro step (docs/INGEST_QUALITY_CHECKS.md).
    """
    src = f"macro_{kind}"
    if not isinstance(df, pd.DataFrame) or df.empty:
        return _report(src, path, 0, None, [_v(src, "schema", "block", EMPTY_SOURCE, "no rows")])
    out: list[Violation] = []
    cols = (
        ["gold_usd", "usd_inr"]
        if kind == "intraday"
        else [c for c in MACRO_BOUNDS if c in df.columns]
    )
    for c in cols:
        if c not in df.columns:
            out.append(_v(src, "schema", "block", SCHEMA_MISSING_FIELD, f"missing column {c!r}"))
    if out:
        return _report(src, path, len(df), None, out)
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex):
        return _report(
            src,
            path,
            len(df),
            None,
            [
                _v(
                    src,
                    "schema",
                    "block",
                    SCHEMA_BAD_TYPE,
                    f"index is {type(idx).__name__}, not DatetimeIndex",
                )
            ],
        )
    if idx.tz is None:
        out.append(_v(src, "timestamp", "block", TS_NAIVE, "index is timezone-naive"))
    if series and idx.has_duplicates:
        out.append(
            _v(
                src,
                "duplicates",
                "block",
                DUPLICATE_KEY,
                f"{int(idx.duplicated().sum())} duplicated index entries",
            )
        )
    if series and not idx.is_monotonic_increasing:
        out.append(_v(src, "ordering", "warn", TS_ORDER, "index not sorted ascending"))
    prev_val: dict[str, tuple[pd.Timestamp, float]] = {}
    max_future = timedelta(hours=1) if kind == "intraday" else timedelta(days=1)
    for t0 in idx:
        out += check_timestamp(
            t0,
            source=src,
            field_name="index",
            now=now,
            max_future=max_future,
            require_tz=False,
            where=str(t0),
        )
    for ts_raw, row in df.iterrows():
        ts = ts_raw if isinstance(ts_raw, pd.Timestamp) else pd.Timestamp(str(ts_raw))
        for c in cols:
            val = row[c]
            if _is_null(val):
                if kind == "daily" and c in _FS_CORE:
                    out.append(_v(src, "value", "warn", NULL_VALUE, f"{c} null", None, str(ts)))
                continue
            bad = check_number(val, source=src, field_name=c, bounds=MACRO_BOUNDS[c], where=str(ts))
            out += bad
            if series and not any(b.severity == "block" for b in bad):
                if c in prev_val and ts > prev_val[c][0]:
                    pts, pv = prev_val[c]
                    gap = (ts - pts).total_seconds() / 86400.0
                    lim = MACRO_JUMP[c]
                    out += check_jump(
                        pv,
                        float(val),
                        source=src,
                        field_name=c,
                        limits=lim,
                        gap_days=None if kind == "intraday" else gap,
                        where=str(ts),
                    )
                prev_val[c] = (ts, float(val))
    newest = idx.max()
    if check_newest_staleness:
        out += check_staleness(
            newest, source=src, now=now,
            warn_hours=6.0 if kind == "intraday" else 120.0,  # 1h bars: 6h covers lunch/close; daily: weekend+holiday
            block_hours=48.0 if kind == "intraday" else 14 * 24.0,  # 14d matches the macro CI hard stale threshold
        )  # fmt: skip
    return _report(src, path, len(df), newest, out)


def check_history_seed(
    df: pd.DataFrame,
    *,
    kind: Literal["label", "proxy"],
    now: datetime | pd.Timestamp,
    series: bool = True,
    path: str,
) -> SourceReport:
    """history_seed_inr22k_{label,proxy}.parquet: static daily 22K Rs/10g backfill (2013..2026-09-23)."""
    src = f"history_seed_{kind}"
    col = "label_22k_per_10g" if kind == "label" else "proxy_22k_per_10g"
    required = (
        ("raw_pre_duty", "raw_with_duty", col, "is_walk_forward_oos")
        if kind == "label"
        else (
            "raw_pre_duty",
            "duty_pct",
            "raw_with_duty",
            col,
            "is_walk_forward_oos",
            "roll_adjusted",
        )
    )
    if not isinstance(df, pd.DataFrame) or df.empty:
        return _report(src, path, 0, None, [_v(src, "schema", "block", EMPTY_SOURCE, "no rows")])
    out = [
        _v(src, "schema", "block", SCHEMA_MISSING_FIELD, f"missing column {c!r}")
        for c in required
        if c not in df.columns
    ]
    if out:
        return _report(src, path, len(df), None, out)
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex):
        return _report(
            src,
            path,
            len(df),
            None,
            [_v(src, "schema", "block", SCHEMA_BAD_TYPE, "index is not a DatetimeIndex")],
        )
    if idx.tz is None:
        out.append(_v(src, "timestamp", "block", TS_NAIVE, "index is timezone-naive"))
    if series and idx.has_duplicates:
        out.append(
            _v(
                src,
                "duplicates",
                "block",
                DUPLICATE_KEY,
                f"{int(idx.duplicated().sum())} duplicated dates",
            )
        )
    if series and not idx.is_monotonic_increasing:
        out.append(_v(src, "ordering", "block", TS_ORDER, "index not sorted ascending"))
    prev_ts: pd.Timestamp | None = None
    prev_val: float | None = None
    for ts, row in df.iterrows():
        where = str(ts.date()) if isinstance(ts, pd.Timestamp) else str(ts)
        out += check_timestamp(
            ts,
            source=src,
            field_name="date",
            now=now,
            require_tz=False,
            where=where,
            min_year=SEED_MIN_YEAR,
        )
        for c in ("raw_pre_duty", "raw_with_duty", col):
            bad = check_number(
                row[c], source=src, field_name=c, bounds=SEED_22K_PER_10G, where=where
            )
            out += bad
        if "duty_pct" in df.columns:
            d = row["duty_pct"]
            if (
                _is_null(d)
                or isinstance(d, bool)
                or not isinstance(d, int | float)
                or not (0 <= d <= 30)
            ):
                out.append(
                    _v(
                        src,
                        "value",
                        "block",
                        VALUE_OUT_OF_RANGE,
                        f"duty_pct {d!r} outside [0, 30]",
                        d,
                        where,
                    )
                )
        for flag in ("is_walk_forward_oos", "roll_adjusted"):
            if flag in df.columns and not isinstance(row[flag], bool | np.bool_):
                out.append(
                    _v(
                        src,
                        "schema",
                        "block",
                        SCHEMA_BAD_TYPE,
                        f"{flag} not bool",
                        row[flag],
                        where,
                    )
                )
        val = row[col]
        ok = (
            isinstance(val, int | float)
            and not isinstance(val, bool)
            and math.isfinite(val)
            and val > 0
        )
        if (
            series
            and ok
            and prev_val is not None
            and isinstance(ts, pd.Timestamp)
            and prev_ts is not None
        ):
            out += check_jump(
                prev_val, float(val), source=src, field_name=col, limits=SEED_JUMP, where=where
            )
            if (ts - prev_ts).days > 5:
                out.append(
                    _v(
                        src,
                        "gap",
                        "warn",
                        GAP_WARN,
                        f"{(ts - prev_ts).days}d gap in a daily series",
                        (ts - prev_ts).days,
                        where,
                    )
                )
        if ok:
            prev_val = float(val)
        if isinstance(ts, pd.Timestamp):
            prev_ts = ts
    return _report(src, path, len(df), idx.max() if len(idx) else None, out)


def check_seed_label_vs_proxy(label: pd.DataFrame, proxy: pd.DataFrame) -> list[Violation]:
    """Cross-source: label_22k_per_10g vs proxy_22k_per_10g on shared dates (same instrument)."""
    src = "history_seed_label_vs_proxy"
    try:
        joined = label["label_22k_per_10g"].to_frame().join(proxy["proxy_22k_per_10g"], how="inner")
    except KeyError as exc:
        return [_v(src, "cross_source", "block", SCHEMA_MISSING_FIELD, f"missing column {exc}")]
    out: list[Violation] = []
    if len(joined) < 0.9 * min(len(label), len(proxy)):
        out.append(
            _v(
                src,
                "cross_source",
                "block",
                CROSS_SOURCE_BLOCK,
                f"only {len(joined)} shared dates of {len(label)}/{len(proxy)}",
                len(joined),
            )
        )
    for ts, row in joined.iterrows():
        dev = abs(float(row["label_22k_per_10g"]) / float(row["proxy_22k_per_10g"]) - 1.0)
        if not math.isfinite(dev) or dev > SEED_LABEL_PROXY_BLOCK:
            out.append(
                _v(
                    src,
                    "cross_source",
                    "block",
                    CROSS_SOURCE_BLOCK,
                    f"label vs proxy differ {dev:.2%}",
                    dev,
                    str(ts),
                )
            )
        elif dev > SEED_LABEL_PROXY_WARN:
            out.append(
                _v(
                    src,
                    "cross_source",
                    "warn",
                    CROSS_SOURCE_WARN,
                    f"label vs proxy differ {dev:.2%}",
                    dev,
                    str(ts),
                )
            )
    return out


# --------------------------------------------------------------------------------------
# Loading + CLI
# --------------------------------------------------------------------------------------


def _load_json(path: Path) -> tuple[Any, Violation | None]:
    name = path.name
    if not path.exists():
        return None, _v(name, "file", "block", FILE_MISSING, f"{path} not found", str(path))
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as exc:
        return None, _v(name, "file", "block", FILE_UNPARSEABLE, f"{path}: {exc}", str(path))


def _load_jsonl(path: Path) -> tuple[list[Any] | None, Violation | None]:
    name = path.name
    if not path.exists():
        return None, _v(name, "file", "block", FILE_MISSING, f"{path} not found", str(path))
    rows: list[Any] = []
    try:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError as exc:
                    return None, _v(
                        name, "file", "block", FILE_UNPARSEABLE, f"{path}:{n}: {exc}", str(path)
                    )
    except OSError as exc:
        return None, _v(name, "file", "block", FILE_UNPARSEABLE, f"{path}: {exc}", str(path))
    return rows, None


def _load_parquet(path: Path) -> tuple[pd.DataFrame | None, Violation | None]:
    name = path.name
    if not path.exists():
        return None, _v(name, "file", "block", FILE_MISSING, f"{path} not found", str(path))
    try:
        return pd.read_parquet(path), None
    except Exception as exc:
        return None, _v(name, "file", "block", FILE_UNPARSEABLE, f"{path}: {exc}", str(path))


def _failed(source: str, path: Path, v: Violation | None) -> SourceReport:
    viol = v or _v(source, "file", "block", FILE_UNPARSEABLE, f"{path}: no data loaded")
    return SourceReport(source=source, path=str(path), status="block", violations=[viol])


def evaluate_all(
    data_dir: Path,
    now: datetime | pd.Timestamp,
    *,
    macro_cache: Path | None = None,
    macro_intraday: Path | None = None,
    check_newest_staleness: bool = True,
) -> dict[str, SourceReport]:
    """Run every source evaluator against the committed files under ``data_dir``."""
    reports: dict[str, SourceReport] = {}

    obj, err = _load_json(data_dir / "prices.json")
    reports["tanishq_prices"] = (
        _failed("tanishq_prices", data_dir / "prices.json", err)
        if err
        else check_tanishq_prices(obj, now=now, check_newest_staleness=check_newest_staleness)
    )

    rows, err = _load_jsonl(data_dir / "tanishq_scrape_outcomes.jsonl")
    reports["tanishq_scrape_outcomes"] = (
        _failed("tanishq_scrape_outcomes", data_dir / "tanishq_scrape_outcomes.jsonl", err)
        if err
        else check_scrape_outcomes(rows, now=now, check_newest_staleness=check_newest_staleness)
    )

    df, err = _load_parquet(data_dir / "ibja_rates.parquet")
    reports["ibja_rates"] = (
        _failed("ibja_rates", data_dir / "ibja_rates.parquet", err)
        if err or df is None
        else check_ibja_rates(df, now=now, check_newest_staleness=check_newest_staleness)
    )

    df, err = _load_parquet(data_dir / "fusion_snapshots.parquet")
    reports["fusion_snapshots"] = (
        _failed("fusion_snapshots", data_dir / "fusion_snapshots.parquet", err)
        if err or df is None
        else check_fusion_snapshots(df, now=now, check_newest_staleness=check_newest_staleness)
    )

    obj, err = _load_json(data_dir / "shadow_fusion_output.json")
    reports["shadow_fusion_output"] = (
        _failed("shadow_fusion_output", data_dir / "shadow_fusion_output.json", err)
        if err
        else check_shadow_fusion_output(obj, now=now, check_newest_staleness=check_newest_staleness)
    )

    df, err = _load_parquet(data_dir / "feature_store" / "snapshots.parquet")
    reports["feature_store"] = (
        _failed("feature_store", data_dir / "feature_store" / "snapshots.parquet", err)
        if err or df is None
        else check_feature_store(df, now=now, check_newest_staleness=check_newest_staleness)
    )

    frames: dict[str, pd.DataFrame] = {}
    seed_kinds: tuple[Literal["label", "proxy"], ...] = ("label", "proxy")
    for kind in seed_kinds:
        p = data_dir / f"history_seed_inr22k_{kind}.parquet"
        df, err = _load_parquet(p)
        name = f"history_seed_{kind}"
        if err or df is None:
            reports[name] = _failed(
                name, p, err or _v(name, "file", "block", FILE_UNPARSEABLE, "no frame")
            )
        else:
            frames[kind] = df
            reports[name] = check_history_seed(
                df,
                kind=kind,
                now=now,
                series=True,
                path=str(p),
            )
    if len(frames) == 2:
        xs = check_seed_label_vs_proxy(frames["label"], frames["proxy"])
        for name in ("history_seed_label", "history_seed_proxy"):
            reports[name].violations.extend(xs if name.endswith("label") else [])
            reports[name].status = _worst(reports[name].violations)

    macro_inputs: tuple[tuple[Literal["daily", "intraday"], Path | None], ...] = (
        ("daily", macro_cache),
        ("intraday", macro_intraday),
    )
    for mkind, mpath in macro_inputs:
        name = f"macro_{mkind}"
        if mpath is None:
            reports[name] = SourceReport(
                source=name,
                path=f"data/macro_{'cache' if mkind == 'daily' else 'intraday'}.parquet",
                status="not_committed",
                note="regenerated each CI run and not committed (ml/macro.py); pass --macro-"
                f"{'cache' if mkind == 'daily' else 'intraday'} PATH to evaluate it",
            )
            continue
        df, err = _load_parquet(mpath)
        if err or df is None:
            reports[name] = _failed(
                name, mpath, err or _v(name, "file", "block", FILE_UNPARSEABLE, "no frame")
            )
        else:
            reports[name] = check_macro_frame(df, now=now, kind=mkind, path=str(mpath))

    # Cross-source: newest Tanishq reading vs newest IBJA pm_916. Skipped when either file already
    # failed to load (that failure is reported; a second "cannot compare" would be noise).
    load_failed = any(
        v.code in (FILE_MISSING, FILE_UNPARSEABLE)
        for k in ("tanishq_prices", "ibja_rates")
        for v in reports[k].violations
    )
    if not load_failed:
        try:
            prices = json.loads((data_dir / "prices.json").read_text(encoding="utf-8"))
            ibja = pd.read_parquet(data_dir / "ibja_rates.parquet")
            pm = ibja[ibja["pm_916"].notna()].sort_values("date").iloc[-1]
            xs = check_retailer_vs_ibja(prices[-1]["22k"], pm["pm_916"], where=f"ibja {pm['date']}")
        except Exception as exc:  # fail closed: "cannot compare" is a failing result, not a pass
            xs = [
                _v(
                    "tanishq_vs_ibja",
                    "cross_source",
                    "block",
                    FILE_UNPARSEABLE,
                    f"cannot run: {exc}",
                )
            ]
        reports["tanishq_prices"].violations.extend(xs)
        reports["tanishq_prices"].status = _worst(reports["tanishq_prices"].violations)
    return reports


def build_status(
    reports: Mapping[str, SourceReport], now: datetime | pd.Timestamp
) -> dict[str, Any]:
    statuses = [r.status for r in reports.values()]
    overall = "block" if "block" in statuses else "warn" if "warn" in statuses else "pass"
    n_ts = pd.Timestamp(now)
    n_ts = n_ts.tz_localize("UTC") if n_ts.tzinfo is None else n_ts.tz_convert("UTC")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": n_ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "overall_status": overall,
        "advisory_only": True,
        "sources": {k: reports[k].to_dict() for k in sorted(reports)},
    }


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Evaluate committed data files for ingestion quality.")
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--out", type=Path, default=STATUS_PATH)
    ap.add_argument("--now", help="ISO-8601 UTC override of the reference clock (replays/tests)")
    ap.add_argument("--macro-cache", type=Path, default=None)
    ap.add_argument("--macro-intraday", type=Path, default=None)
    ap.add_argument(
        "--strict", action="store_true", help="exit 1 when any source has a block violation"
    )
    args = ap.parse_args(argv)
    now = pd.Timestamp(args.now) if args.now else pd.Timestamp(datetime.now(UTC))
    reports = evaluate_all(
        args.data_dir, now, macro_cache=args.macro_cache, macro_intraday=args.macro_intraday
    )
    status = build_status(reports, now)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for name, rep in sorted(reports.items()):
        print(f"{rep.status:14s} {name:26s} rows={rep.n_rows:<6d} {rep.counts_by_code()}")
    print(f"overall={status['overall_status']} -> {args.out}")
    return 1 if (args.strict and status["overall_status"] == "block") else 0


if __name__ == "__main__":
    raise SystemExit(main())
