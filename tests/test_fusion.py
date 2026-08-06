"""Hard tests for ml.fusion — the core reliability-weighted consensus math."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from ml.fusion import (
    BASE_BAND_PCT,
    DEFAULT_WEIGHTS,
    DISAGREEMENT_BAND_MULTIPLIER,
    DISAGREEMENT_THRESHOLD_PCT,
    NATIONAL_DERIVED_BAND_MULTIPLIER,
    compute_city_markup,
    default_weight_fn,
    fuse_city_price,
    fuse_national_benchmark,
)
from ml.sources.base import SourceReading

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def reading(source: str, rate: float, city: str | None = None) -> SourceReading:
    return SourceReading(
        source=source, city=city, rate_22k=rate, observed_at=NOW, attribution=f"{source} test"
    )


# ---------------------------------------------------------------------------
# default_weight_fn
# ---------------------------------------------------------------------------


def test_default_weight_fn_known_sources():
    readings = [reading("ibja", 13000), reading("grt", 13100), reading("malabar", 13050)]
    weights = default_weight_fn(readings)
    assert weights == {
        "ibja": DEFAULT_WEIGHTS["ibja"],
        "grt": DEFAULT_WEIGHTS["grt"],
        "malabar": DEFAULT_WEIGHTS["malabar"],
    }


def test_default_weight_fn_unknown_source_gets_fallback():
    readings = [reading("some_new_source", 13000)]
    weights = default_weight_fn(readings)
    assert weights["some_new_source"] == 0.5


# ---------------------------------------------------------------------------
# fuse_national_benchmark — known-input cases
# ---------------------------------------------------------------------------


def test_fuse_single_reading_returns_that_value_exactly():
    result = fuse_national_benchmark([reading("ibja", 13100)])
    assert result.value == 13100
    assert result.disagreement is False
    assert result.sources_used == ("ibja",)


def test_fuse_weighted_average_is_correct():
    # ibja=1.0, grt=0.7, malabar=0.7 -> weighted mean, hand-computed.
    readings = [reading("ibja", 13000), reading("grt", 13100), reading("malabar", 13100)]
    result = fuse_national_benchmark(readings)
    expected = (13000 * 1.0 + 13100 * 0.7 + 13100 * 0.7) / (1.0 + 0.7 + 0.7)
    assert result.value == pytest.approx(expected)


def test_fuse_equal_weights_is_plain_average():
    def equal_weight_fn(readings):
        return {r.source: 1.0 for r in readings}

    readings = [reading("a", 100), reading("b", 200), reading("c", 300)]
    result = fuse_national_benchmark(readings, weight_fn=equal_weight_fn)
    assert result.value == pytest.approx(200.0)


def test_fuse_empty_readings_raises():
    with pytest.raises(ValueError, match="no readings"):
        fuse_national_benchmark([])


def test_fuse_zero_total_weight_raises():
    def zero_weight_fn(readings):
        return {r.source: 0.0 for r in readings}

    with pytest.raises(ValueError, match="positive"):
        fuse_national_benchmark([reading("ibja", 13000)], weight_fn=zero_weight_fn)


# ---------------------------------------------------------------------------
# Weight-function seam — proving callers can swap it without touching fusion.py
# ---------------------------------------------------------------------------


def test_custom_weight_fn_changes_result_without_touching_fuse_function():
    readings = [reading("ibja", 13000), reading("grt", 14000)]

    def all_weight_on_grt(readings):
        return {r.source: (1.0 if r.source == "grt" else 0.0001) for r in readings}

    result = fuse_national_benchmark(readings, weight_fn=all_weight_on_grt)
    assert result.value == pytest.approx(14000, rel=0.001)


# ---------------------------------------------------------------------------
# Disagreement detection
# ---------------------------------------------------------------------------


def test_no_disagreement_when_sources_agree_within_threshold():
    # All within DISAGREEMENT_THRESHOLD_PCT (2%) of each other.
    readings = [reading("ibja", 13000), reading("grt", 13100), reading("malabar", 13050)]
    result = fuse_national_benchmark(readings)
    assert result.disagreement is False
    assert result.band_half_width == pytest.approx(BASE_BAND_PCT * result.value)


def test_disagreement_triggers_when_one_source_diverges_beyond_threshold():
    # malabar is >2% away from the other two -- must trigger disagreement.
    readings = [reading("ibja", 13000), reading("grt", 13000), reading("malabar", 14000)]
    result = fuse_national_benchmark(readings)
    assert result.disagreement is True
    assert result.band_half_width == pytest.approx(
        BASE_BAND_PCT * result.value * DISAGREEMENT_BAND_MULTIPLIER
    )


def _b_for_exact_deviation(a: float, target_pct: float) -> float:
    """With two equal-weighted readings a, b: mean=(a+b)/2, and b's deviation
    from the mean is |b-a|/(a+b). Solving |b-a|/(a+b) = target_pct for b
    (b > a) gives b = a*(1+target_pct)/(1-target_pct) -- the exact b that
    makes max_dev_pct equal target_pct.
    """
    return a * (1 + target_pct) / (1 - target_pct)


def test_disagreement_threshold_boundary_just_under_is_not_disagreement():
    def equal_weight_fn(readings):
        return {r.source: 1.0 for r in readings}

    base = 10000.0
    b = _b_for_exact_deviation(base, DISAGREEMENT_THRESHOLD_PCT - 1e-6)
    readings = [reading("a", base), reading("b", b)]
    result = fuse_national_benchmark(readings, weight_fn=equal_weight_fn)
    assert result.disagreement is False


def test_disagreement_threshold_boundary_just_over_is_disagreement():
    def equal_weight_fn(readings):
        return {r.source: 1.0 for r in readings}

    base = 10000.0
    b = _b_for_exact_deviation(base, DISAGREEMENT_THRESHOLD_PCT + 1e-6)
    readings = [reading("a", base), reading("b", b)]
    result = fuse_national_benchmark(readings, weight_fn=equal_weight_fn)
    assert result.disagreement is True


# ---------------------------------------------------------------------------
# compute_city_markup
# ---------------------------------------------------------------------------


def test_city_markup_above_national():
    national = fuse_national_benchmark([reading("ibja", 13000)])
    city_reading = reading("kalyan", 13260, city="Bangalore")
    markup = compute_city_markup(city_reading, national)
    assert markup == pytest.approx(13260 / 13000)


def test_city_markup_below_national():
    national = fuse_national_benchmark([reading("ibja", 13000)])
    city_reading = reading("kalyan", 12870, city="Chennai")
    markup = compute_city_markup(city_reading, national)
    assert markup == pytest.approx(12870 / 13000)


def test_city_markup_equal_national_is_one():
    national = fuse_national_benchmark([reading("ibja", 13000)])
    city_reading = reading("kalyan", 13000, city="Hyderabad")
    assert compute_city_markup(city_reading, national) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# fuse_city_price — coverage states
# ---------------------------------------------------------------------------


def test_fuse_city_price_with_local_source_is_kalyan_anchored():
    """A same-cycle Kalyan reading yields coverage="kalyan_anchored", NOT "city_specific" --
    43/43 accumulated shadow cycles show zero city-to-city variation (ADR 026 update), so
    the label must never claim location-specific pricing."""
    national = fuse_national_benchmark([reading("ibja", 13000), reading("grt", 13050)])
    city_reading = reading("kalyan", 13135, city="Bangalore")
    result = fuse_city_price(city_reading, national, city="Bangalore")
    assert result.coverage == "kalyan_anchored"
    assert result.markup is not None
    # value == national.value * markup == city_reading.rate_22k algebraically (v1, no smoothing).
    assert result.value == pytest.approx(city_reading.rate_22k)
    assert "National retail consensus" in result.attribution
    assert result.band_half_width == national.band_half_width


def test_fuse_city_price_without_local_source_is_national_derived():
    national = fuse_national_benchmark([reading("ibja", 13000), reading("grt", 13050)])
    result = fuse_city_price(None, national, city="Pune")
    assert result.coverage == "national_derived"
    assert result.markup is None
    assert result.value == national.value
    assert "Pune" in result.attribution
    assert "National retail consensus" in result.attribution


def test_fuse_city_price_national_derived_band_is_wider_than_national():
    national = fuse_national_benchmark([reading("ibja", 13000), reading("grt", 13050)])
    result = fuse_city_price(None, national, city="Pune")
    assert result.band_half_width == pytest.approx(
        national.band_half_width * NATIONAL_DERIVED_BAND_MULTIPLIER
    )
    assert result.band_half_width > national.band_half_width


def test_fuse_city_price_never_fabricates_a_number_when_uncovered():
    # The whole point: an uncovered city gets the national value, not something
    # invented to look city-specific.
    national = fuse_national_benchmark([reading("ibja", 13000)])
    result = fuse_city_price(None, national, city="SomeTownNeverRegistered")
    assert result.value == national.value
