"""Two-layer, reliability-weighted retail-price fusion (ADR 026, Option 1).

    retail_price(location) = fused_national_benchmark x location_markup(location)

National benchmark: a reliability-weighted consensus of national-level
sources (IBJA, GRT, Malabar today). City price: the national benchmark
scaled by a city-specific markup derived from a real local source (Kalyan
today) where one exists, honestly labeled national-derived elsewhere.

THE WEIGHTING SEAM (read this before touching weights): ``DEFAULT_WEIGHTS``
and ``default_weight_fn`` are a deliberately simple, static placeholder.
``fuse_national_benchmark`` takes ``weight_fn`` as a parameter specifically
so ADR 026's planned Option 2 (online-learned weights, once
``data/fusion_snapshots.parquet`` has enough history to learn from) can pass
a different function of the *same signature*
(``list[SourceReading] -> dict[str, float]``) without touching this module's
callers. Do not hardcode weights anywhere except ``DEFAULT_WEIGHTS`` below.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ml.sources.base import SourceReading

# Static v1 weights. IBJA is weighted highest because it carries the only
# externally-validated calibration today (R^2=0.963 vs Tanishq, ADR 025) --
# NOT because it has been empirically shown best among these four sources.
# That empirical comparison is exactly what Option 2's learned weights will
# eventually produce; until then this is a documented judgment call.
DEFAULT_WEIGHTS: dict[str, float] = {
    "ibja": 1.0,
    "grt": 0.7,
    "malabar": 0.7,
}
_FALLBACK_WEIGHT = 0.5  # for any national source not in DEFAULT_WEIGHTS

# Simple, static disagreement threshold (fraction of the weighted mean).
# Option 2 replaces this with one derived from each source's realized
# historical noise (ADR 026 Future Work) -- there's no history to derive
# that from yet.
DISAGREEMENT_THRESHOLD_PCT: float = 0.02

# Base confidence-band half-width as a fraction of the fused value, applied
# even when sources agree (there is always some genuine uncertainty).
BASE_BAND_PCT: float = 0.01
# Multiplier applied to the band when sources disagree beyond the threshold.
DISAGREEMENT_BAND_MULTIPLIER: float = 2.0
# Multiplier applied when a city has no local source (national-derived) --
# wider than the national band itself, since a national number is a weaker
# proxy for a specific city's actual retail price than a local reading.
NATIONAL_DERIVED_BAND_MULTIPLIER: float = 1.5

WeightFn = Callable[[list[SourceReading]], dict[str, float]]


def default_weight_fn(readings: list[SourceReading]) -> dict[str, float]:
    """Static per-source weights (ADR 026 Option 1). See module docstring."""
    return {r.source: DEFAULT_WEIGHTS.get(r.source, _FALLBACK_WEIGHT) for r in readings}


@dataclass(frozen=True)
class FusedBenchmark:
    """The fused national benchmark for one cycle."""

    value: float
    band_half_width: float
    disagreement: bool
    sources_used: tuple[str, ...]
    weights_used: dict[str, float]


@dataclass(frozen=True)
class FusedCityPrice:
    """The final per-location price: national benchmark x city markup."""

    city: str | None
    value: float
    band_half_width: float
    coverage: str  # "city_specific" | "national_derived"
    attribution: str
    markup: float | None  # None when coverage == "national_derived"


def fuse_national_benchmark(
    readings: list[SourceReading],
    weight_fn: WeightFn = default_weight_fn,
) -> FusedBenchmark:
    """Fuse national-level source readings into one benchmark + band.

    Raises :class:`ValueError` if ``readings`` is empty -- callers (the
    shadow-fusion driver) must decide what "all national sources failed"
    means for their own reporting; this function never fabricates a value
    from nothing.
    """
    if not readings:
        raise ValueError("fuse_national_benchmark: no readings provided")

    weights = weight_fn(readings)
    total_weight = sum(weights[r.source] for r in readings)
    if total_weight <= 0:
        raise ValueError("fuse_national_benchmark: total weight must be positive")

    weighted_value = sum(r.rate_22k * weights[r.source] for r in readings) / total_weight

    max_dev_pct = max(abs(r.rate_22k - weighted_value) / weighted_value for r in readings)
    disagreement = max_dev_pct > DISAGREEMENT_THRESHOLD_PCT

    band = BASE_BAND_PCT * weighted_value
    if disagreement:
        band *= DISAGREEMENT_BAND_MULTIPLIER

    return FusedBenchmark(
        value=weighted_value,
        band_half_width=band,
        disagreement=disagreement,
        sources_used=tuple(r.source for r in readings),
        weights_used=weights,
    )


def compute_city_markup(city_reading: SourceReading, national: FusedBenchmark) -> float:
    """City rate expressed as a multiple of the national benchmark.

    Computed fresh each cycle -- no smoothing yet (ADR 026: there's no
    history to smooth over on day one). Option 2 is expected to replace a
    single-cycle markup with a rolling/EMA-smoothed one once
    ``data/fusion_snapshots.parquet`` has enough history.
    """
    return city_reading.rate_22k / national.value


def fuse_city_price(
    city_reading: SourceReading | None,
    national: FusedBenchmark,
    *,
    city: str,
) -> FusedCityPrice:
    """Combine the national benchmark with a city-specific reading if one exists.

    ``city_reading is None`` means no local source covers this city this
    cycle (either it isn't a registered city, or the local source failed) --
    the result is honestly labeled ``"national_derived"``, never a
    fabricated city-specific number.
    """
    if city_reading is not None:
        markup = compute_city_markup(city_reading, national)
        value = national.value * markup
        return FusedCityPrice(
            city=city,
            value=value,
            band_half_width=national.band_half_width,
            coverage="city_specific",
            attribution=city_reading.attribution,
            markup=markup,
        )

    sources_note = ", ".join(national.sources_used)
    return FusedCityPrice(
        city=city,
        value=national.value,
        band_half_width=national.band_half_width * NATIONAL_DERIVED_BAND_MULTIPLIER,
        coverage="national_derived",
        attribution=f"National consensus ({sources_note}) — no city-specific source for {city}",
        markup=None,
    )
