"""ml/markup.py -- F1 "store markup meter": model/data layer only (UI comes later, flagged).

    markup_pct(t) = (retailer_22k_per_gram(t) / ibja_22k_per_gram_in_force(t) - 1) * 100

GST status of the two sides being compared (spec item: state VERIFIED vs INFERRED):
  * IBJA rates (data/ibja_rates.parquet) exclude GST and making charges -- stated directly in
    scripts/analysis_derived_premium.py's docstring ("IBJA rates exclude GST, so parity excludes
    the 3% IGST") and consistent with IBJA publishing a bullion-market reference rate, not a
    consumer billing price. VERIFIED (repo convention, cited above).
  * Tanishq's displayed 22K board rate is PRE-GST -- VERIFIED 2026-05-19 by ml/calibration.py's own
    sampling (21 aligned trading days, median tanishq/ibja_916_pm ratio 1.017 -- consistent with a
    markup-only ratio, no embedded 3% GST). Both sides of the Tanishq/IBJA comparison are pre-GST,
    so markup_pct for "tanishq" is a genuine board-rate/board-rate markup, not contaminated by GST.
  * GRT, Malabar and Kalyan's GST treatment on their displayed board rate has NOT been independently
    verified by this repo -- INFERRED only, not verified: ml/sources/{grt,kalyan,malabar}.py's own
    docstrings make no GST claim either way, and no sampling exercise like ml/calibration.py's has
    been run against them. It is inferred (not verified) that they follow the same "board rate is
    pre-GST, GST/making charges added at billing" convention common to Indian jewellery retail,
    because that is the market-wide display norm Tanishq itself follows -- but this has not been
    checked directly. Do not read markup_pct for grt/malabar/kalyan_* as a verified pure-markup
    number until that GST check is done the same way ml/calibration.py did it for Tanishq.

Timing -- pairing a retailer reading with "the IBJA rate in force" at read time:
IBJA publishes an AM fix (~09:30 IST) and a PM fix (~17:00 IST) on weekdays only (ml/ibja.py). This
module has no per-fix publish timestamp -- only one row per CALENDAR DAY with (possibly null)
am_916/pm_916 columns -- so "in force" is defined by a fixed, conservative IST wall-clock rule
rather than the empirical publish time:

    reading IST time >= 17:00           -> that day's PM fix
    12:00 <= reading IST time < 17:00    -> that day's AM fix
    reading IST time <  12:00            -> the PREVIOUS day's PM fix

When the wanted fix isn't available yet (not published, or a weekend/holiday with no IBJA row at
all), the most recently published fix at or before the wanted slot is used instead and the pairing
is flagged ``stale=True``. This single fallback mechanism covers both "IBJA hasn't published yet"
and "weekend/holiday" the same way -- see ``resolve_ibja_fix``. The fallback can only ever look
*backward* in time (never at same-day data published after the wanted slot) -- see
``_ibja_fix_slots``'s ordering and ``resolve_ibja_fix``'s bisect, which is what makes this a
no-look-ahead pairing.
"""

from __future__ import annotations

import bisect
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pandas as pd

from ml.ibja import IBJA_PARQUET, load_ibja_parquet
from ml.sources.kalyan import KALYAN_CITIES

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PRICES_JSON = DATA_DIR / "prices.json"
FUSION_SNAPSHOTS_PARQUET = DATA_DIR / "fusion_snapshots.parquet"
MARKUP_TODAY_JSON = DATA_DIR / "markup_today.json"

IST = ZoneInfo("Asia/Kolkata")

# Trailing windows for "today vs its own history" (spec item 2).
TRAILING_WINDOW_DAYS_LONG = 90
TRAILING_WINDOW_DAYS_SHORT = 30

# Minimum strictly-earlier days required before a category is assigned at all.
# Below this, a 25th/75th-percentile split is too underdetermined to freeze a
# meaningful boundary from (e.g. 2 points gives a "p25" that is just one of the
# two points). FIXED, not tuned against observed markup data.
MIN_TRAILING_DAYS_FOR_CATEGORY = 10

CATEGORY_LOWER = "lower than usual"
CATEGORY_USUAL = "usual"
CATEGORY_HIGHER = "higher than usual"

MARKUP_TODAY_SCHEMA_VERSION = 1

# (retailer_key, fusion `source` value, fusion `city` value or None for national).
# Kalyan cities are pulled from ml.sources.kalyan.KALYAN_CITIES so this list can
# never drift from the set of cities that source module actually registers.
FUSION_RETAILERS: tuple[tuple[str, str, str | None], ...] = (
    ("grt", "grt", None),
    ("malabar", "malabar", None),
    *((f"kalyan_{city_name.lower()}", "kalyan", city_name) for city_name in KALYAN_CITIES),
)


# ---------------------------------------------------------------------------
# IBJA fix timing/pairing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedIbjaFix:
    """The IBJA fix actually used for one reading, and what was originally wanted.

    ``stale`` is True whenever ``(fix_date, fix_type) != (wanted_date, wanted_fix)`` --
    i.e. a fallback occurred, whether because the wanted fix hasn't been published
    yet or because the wanted date has no IBJA row at all (weekend/holiday).
    """

    wanted_date: date
    wanted_fix: str  # "am" | "pm"
    fix_date: date
    fix_type: str  # "am" | "pm"
    rate_per_gram: float
    stale: bool


def _to_ist(observed_at: datetime) -> datetime:
    """Normalize to an aware datetime (assuming UTC if naive) and convert to IST."""
    aware = observed_at if observed_at.tzinfo is not None else observed_at.replace(tzinfo=UTC)
    return aware.astimezone(IST)


def _wanted_fix(observed_at: datetime) -> tuple[date, str]:
    """Which IBJA fix is nominally "in force" at ``observed_at`` (see module docstring)."""
    ist = _to_ist(observed_at)
    d, t = ist.date(), ist.time()
    if t >= time(17, 0):
        return d, "pm"
    if t >= time(12, 0):
        return d, "am"
    return d - timedelta(days=1), "pm"


def _slot_key(d: date, fix: str) -> int:
    """Total order over (date, fix) pairs: am before pm, both increasing with date."""
    return d.toordinal() * 2 + (1 if fix == "pm" else 0)


def _ibja_fix_slots(ibja_df: pd.DataFrame) -> list[tuple[int, date, str, float]]:
    """Every published (date, fix) slot in ``ibja_df`` with a non-null rate, sorted ascending.

    Rate is converted from Rs/10g (the parquet's native unit) to Rs/gram.
    """
    slots: list[tuple[int, date, str, float]] = []
    for row in ibja_df.itertuples(index=False):
        try:
            d = date.fromisoformat(str(row.date)[:10])
        except ValueError:
            continue
        for fix, col in (("am", "am_916"), ("pm", "pm_916")):
            value = getattr(row, col, None)
            if value is None or (isinstance(value, float) and math.isnan(value)):
                continue
            slots.append((_slot_key(d, fix), d, fix, float(value) / 10.0))
    slots.sort(key=lambda s: s[0])
    return slots


def resolve_ibja_fix(observed_at: datetime, ibja_df: pd.DataFrame) -> ResolvedIbjaFix:
    """Resolve which IBJA fix was "in force" at ``observed_at`` (see module docstring).

    Falls back to the most recently published fix at or before the wanted slot when the
    wanted one isn't available (not yet published, or a weekend/holiday with no IBJA row) --
    flagged via ``stale=True``. Never looks at a fix published after the wanted slot.

    Raises :class:`ValueError` if ``ibja_df`` has no valid am_916/pm_916 rows, or none at or
    before the wanted slot (``observed_at`` predates all available IBJA history).
    """
    wanted_date, wanted_fix = _wanted_fix(observed_at)
    wanted_key = _slot_key(wanted_date, wanted_fix)

    slots = _ibja_fix_slots(ibja_df)
    if not slots:
        raise ValueError("resolve_ibja_fix: ibja_df has no valid am_916/pm_916 rows")

    keys = [s[0] for s in slots]
    idx = bisect.bisect_right(keys, wanted_key) - 1
    if idx < 0:
        raise ValueError(
            f"resolve_ibja_fix: no IBJA fix at or before {wanted_date} {wanted_fix} "
            f"(earliest available: {slots[0][1]} {slots[0][2]})"
        )

    _, fix_date, fix_type, rate_per_gram = slots[idx]
    return ResolvedIbjaFix(
        wanted_date=wanted_date,
        wanted_fix=wanted_fix,
        fix_date=fix_date,
        fix_type=fix_type,
        rate_per_gram=rate_per_gram,
        stale=(fix_date, fix_type) != (wanted_date, wanted_fix),
    )


# ---------------------------------------------------------------------------
# Markup + daily series
# ---------------------------------------------------------------------------


def markup_pct(retailer_rate_per_gram: float, ibja_rate_per_gram: float) -> float:
    """Retailer 22K Rs/g vs IBJA 22K(916) Rs/g, as a percentage (spec's F1 definition)."""
    if ibja_rate_per_gram <= 0:
        raise ValueError("markup_pct: ibja_rate_per_gram must be positive")
    return (retailer_rate_per_gram / ibja_rate_per_gram - 1.0) * 100.0


@dataclass(frozen=True)
class DailyMarkupRow:
    """One retailer's markup for one IST calendar day (its last reading that day)."""

    ist_date: date
    reading_at_utc: datetime
    retailer_rate_per_gram: float
    ibja_fix_date: date
    ibja_fix_type: str
    ibja_rate_per_gram: float
    stale_ibja: bool
    markup_pct: float


def build_daily_markup_series(
    readings: Iterable[tuple[datetime, float]], ibja_df: pd.DataFrame
) -> list[DailyMarkupRow]:
    """One row per IST calendar date -- the LAST reading of that day, per the spec ("daily
    series: last reading per IST date") -- paired with the IBJA fix in force at that reading's
    own timestamp. ``readings`` may be unsorted and may contain multiple readings per day.
    """
    by_date: dict[date, tuple[datetime, float]] = {}
    for observed_at, rate in readings:
        aware = observed_at if observed_at.tzinfo is not None else observed_at.replace(tzinfo=UTC)
        d = aware.astimezone(IST).date()
        if d not in by_date or aware > by_date[d][0]:
            by_date[d] = (aware, rate)

    rows: list[DailyMarkupRow] = []
    for d in sorted(by_date):
        observed_at, rate = by_date[d]
        fix = resolve_ibja_fix(observed_at, ibja_df)
        rows.append(
            DailyMarkupRow(
                ist_date=d,
                reading_at_utc=observed_at,
                retailer_rate_per_gram=rate,
                ibja_fix_date=fix.fix_date,
                ibja_fix_type=fix.fix_type,
                ibja_rate_per_gram=fix.rate_per_gram,
                stale_ibja=fix.stale,
                markup_pct=markup_pct(rate, fix.rate_per_gram),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Today vs its own history
# ---------------------------------------------------------------------------


def _quantile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation quantile (numpy/pandas default 'linear' method)."""
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    idx = q * (n - 1)
    lo, hi = math.floor(idx), math.ceil(idx)
    if lo == hi:
        return sorted_values[int(idx)]
    frac = idx - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def _percentile_rank(value: float, earlier_values: list[float]) -> float:
    """Percent of ``earlier_values`` <= ``value``. ``earlier_values`` must not contain ``value``
    itself -- no-look-ahead is the caller's responsibility (see ``compute_today_position``)."""
    if not earlier_values:
        raise ValueError("_percentile_rank: earlier_values is empty")
    n_le = sum(1 for v in earlier_values if v <= value)
    return 100.0 * n_le / len(earlier_values)


@dataclass(frozen=True)
class MarkupPosition:
    """Today's markup in the context of its own trailing history -- no look-ahead: every
    number here is computed exclusively from days strictly before ``as_of_date``."""

    as_of_date: date
    markup_pct: float
    percentile_90d: float | None
    percentile_30d: float | None
    category: str | None
    p25_90d: float | None
    p75_90d: float | None
    n_history_days: int  # count of strictly-earlier days used for the 90d window (<=90)


def compute_today_position(
    daily: list[DailyMarkupRow], as_of_date: date | None = None
) -> MarkupPosition:
    """Percentile (90d/30d) and category (thresholds = 25th/75th pct of the trailing 90d
    window) for ``as_of_date`` -- using ONLY days strictly before it. ``category`` and the
    p25/p75 thresholds are None until at least ``MIN_TRAILING_DAYS_FOR_CATEGORY`` earlier
    days exist; percentiles are None with zero earlier days.
    """
    if not daily:
        raise ValueError("compute_today_position: daily is empty")
    sorted_daily = sorted(daily, key=lambda r: r.ist_date)
    if as_of_date is None:
        as_of_date = sorted_daily[-1].ist_date

    today_rows = [r for r in sorted_daily if r.ist_date == as_of_date]
    if not today_rows:
        raise ValueError(f"compute_today_position: no row for as_of_date {as_of_date}")
    today = today_rows[-1]

    earlier = [r.markup_pct for r in sorted_daily if r.ist_date < as_of_date]
    window_90 = earlier[-TRAILING_WINDOW_DAYS_LONG:]
    window_30 = earlier[-TRAILING_WINDOW_DAYS_SHORT:]

    percentile_90d = _percentile_rank(today.markup_pct, window_90) if window_90 else None
    percentile_30d = _percentile_rank(today.markup_pct, window_30) if window_30 else None

    category: str | None = None
    p25 = p75 = None
    if len(window_90) >= MIN_TRAILING_DAYS_FOR_CATEGORY:
        ordered = sorted(window_90)
        p25 = _quantile(ordered, 0.25)
        p75 = _quantile(ordered, 0.75)
        if today.markup_pct < p25:
            category = CATEGORY_LOWER
        elif today.markup_pct > p75:
            category = CATEGORY_HIGHER
        else:
            category = CATEGORY_USUAL

    return MarkupPosition(
        as_of_date=as_of_date,
        markup_pct=today.markup_pct,
        percentile_90d=percentile_90d,
        percentile_30d=percentile_30d,
        category=category,
        p25_90d=p25,
        p75_90d=p75,
        n_history_days=len(window_90),
    )


def compute_rolling_positions(daily: list[DailyMarkupRow]) -> list[MarkupPosition]:
    """One ``MarkupPosition`` per day in ``daily`` (chronological order), each computed using
    only days strictly earlier in the same list -- the full no-look-ahead history a caller
    (e.g. scripts/analysis_markup.py's stability/transition-matrix stats) needs, day by day.
    """
    sorted_daily = sorted(daily, key=lambda r: r.ist_date)
    return [
        compute_today_position(sorted_daily[: i + 1], as_of_date=sorted_daily[i].ist_date)
        for i in range(len(sorted_daily))
    ]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

# Sanity floor for a parsed reading timestamp. Real data in this repo starts 2022 (IBJA) /
# 2026 (everything else) -- a parsed year below this is corrupt input, not a genuine reading.
# KNOWN ISSUE (found building this module, not fixed by it -- out of scope for F1): 441/1811
# rows in data/fusion_snapshots.parquet as of 2026-09-24, all source="kalyan" spread across all
# 4 registered cities, carry observed_at="1969-12-31T18:30:00+00:00" -- epoch (1970-01-01
# 00:00:00) minus the 5:30h IST-offset subtraction in ml/sources/kalyan.py's
# fetch_kalyan_city, i.e. Kalyan's own `updated_time` field failed to parse on those captures
# and something upstream substituted a naive epoch datetime rather than raising. This filter
# only prevents that bad data from corrupting markup_pct; it does not fix the root cause.
_MIN_PLAUSIBLE_YEAR = 2020


def load_tanishq_readings(path: Path = PRICES_JSON) -> list[tuple[datetime, float]]:
    """(observed_at, 22K Rs/gram) pairs from data/prices.json.

    Unparseable rows, and rows whose timestamp year is below ``_MIN_PLAUSIBLE_YEAR`` (corrupt
    data, see that constant's docstring), are skipped.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: list[tuple[datetime, float]] = []
    for row in raw:
        try:
            ts = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
            rate = float(row["22k"])
        except (KeyError, ValueError, TypeError):
            continue
        if ts.year < _MIN_PLAUSIBLE_YEAR:
            continue
        out.append((ts, rate))
    return out


def load_fusion_readings(
    source: str, city: str | None = None, path: Path = FUSION_SNAPSHOTS_PARQUET
) -> list[tuple[datetime, float]]:
    """(observed_at, 22K Rs/gram) pairs from data/fusion_snapshots.parquet for one
    (source, city) combination. ``city=None`` matches national rows only (a missing/NaN city
    column value) -- it never matches a row that has an actual city value.

    Unparseable rows, and rows whose timestamp year is below ``_MIN_PLAUSIBLE_YEAR`` (corrupt
    data, see that constant's docstring), are skipped.
    """
    if not path.exists():
        return []
    df = pd.read_parquet(path)
    df = df[df["source"] == source]
    df = df[df["city"].isna()] if city is None else df[df["city"] == city]

    out: list[tuple[datetime, float]] = []
    for row in df.itertuples(index=False):
        try:
            ts = datetime.fromisoformat(str(row.observed_at))
            # cast: itertuples' NamedTuple stub types every field as a broad Union covering
            # all pandas dtypes it can't statically narrow per-column -- a stub gap, not an
            # actual runtime possibility here (rate_22k is a known-numeric parquet column).
            rate = float(cast(Any, row.rate_22k))
        except (ValueError, TypeError):
            continue
        if ts.year < _MIN_PLAUSIBLE_YEAR:
            continue
        out.append((ts, rate))
    return out


def _load_all_retailer_readings(
    prices_path: Path = PRICES_JSON, fusion_path: Path = FUSION_SNAPSHOTS_PARQUET
) -> dict[str, list[tuple[datetime, float]]]:
    """Every registered retailer's raw readings, keyed by retailer_key (see FUSION_RETAILERS)."""
    readings: dict[str, list[tuple[datetime, float]]] = {
        "tanishq": load_tanishq_readings(prices_path)
    }
    for key, source, city in FUSION_RETAILERS:
        readings[key] = load_fusion_readings(source, city, path=fusion_path)
    return readings


# ---------------------------------------------------------------------------
# data/markup_today.json
# ---------------------------------------------------------------------------


def compute_markup_today(
    retailer_readings: dict[str, list[tuple[datetime, float]]],
    ibja_df: pd.DataFrame,
    as_of_date: date,
) -> dict[str, Any]:
    """Pure function (no file I/O): today's markup position per retailer.

    A retailer with no reading on ``as_of_date`` (after per-IST-day dedup) is OMITTED from the
    output entirely -- never fabricated as a null/zero entry.
    """
    retailers: dict[str, Any] = {}
    for name, readings in retailer_readings.items():
        if not readings:
            continue
        daily = build_daily_markup_series(readings, ibja_df)
        matches = [r for r in daily if r.ist_date == as_of_date]
        if not matches:
            continue
        today_row = matches[-1]
        position = compute_today_position(daily, as_of_date=as_of_date)
        retailers[name] = {
            "markup_pct": round(position.markup_pct, 4),
            "percentile_90d": (
                round(position.percentile_90d, 2) if position.percentile_90d is not None else None
            ),
            "category": position.category,
            "ibja_fix_used": {
                "date": today_row.ibja_fix_date.isoformat(),
                "fix": today_row.ibja_fix_type,
            },
            "stale": today_row.stale_ibja,
            "n_history_days": position.n_history_days,
        }
    return {
        "schema_version": MARKUP_TODAY_SCHEMA_VERSION,
        "as_of": as_of_date.isoformat(),
        "retailers": retailers,
    }


def write_markup_today(
    out_path: Path = MARKUP_TODAY_JSON,
    prices_path: Path = PRICES_JSON,
    fusion_path: Path = FUSION_SNAPSHOTS_PARQUET,
    ibja_path: Path = IBJA_PARQUET,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    """Deterministic, no-network entry point the pipeline can call: reads the already-committed
    prices.json / fusion_snapshots.parquet / ibja_rates.parquet, computes each retailer's F1
    markup position, and writes ``out_path``. Returns the same dict that was written.

    Raises :class:`ValueError` if ibja_rates.parquet is missing/empty -- there is nothing to
    pair any retailer reading against.
    """
    ibja_df = load_ibja_parquet(ibja_path)
    if ibja_df.empty:
        raise ValueError(f"write_markup_today: {ibja_path} is empty or missing")

    retailer_readings = _load_all_retailer_readings(prices_path, fusion_path)
    resolved_as_of = as_of_date or datetime.now(IST).date()
    payload = compute_markup_today(retailer_readings, ibja_df, resolved_as_of)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    result = write_markup_today()
    print(json.dumps(result, indent=1))
