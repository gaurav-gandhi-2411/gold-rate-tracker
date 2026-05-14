"""Tests for ml/seed_history.py — compute_rates() and time-varying premium."""

from datetime import date

from ml.seed_history import (
    IMPORT_DUTY_BREAK_DATE,
    TROY_OZ_TO_GRAM,
    _retail_premium_for_date,
    compute_rates,
)

# Fixture: GC=F = $3000.00/oz, USD/INR = 84.00, pre-break date (1.15 premium)
# base_24k = 3000 * 84 / 31.1035 * 1.15
#           = 252000 / 31.1035 * 1.15
#           = 8103.024... * 1.15
#           = 9318.477...
# 24k = round(9318.477)         = 9318
# 22k = round(9318.477 * 22/24) = round(8541.937) = 8542
# 18k = round(9318.477 * 18/24) = round(6988.858) = 6989

FIXTURE_GC = 3000.0
FIXTURE_INR = 84.0
PRE_BREAK_DATE = date(2024, 6, 15)  # before 2024-07-23, premium = 1.15
POST_BREAK_DATE = date(2025, 1, 15)  # after  2024-07-23, premium = 1.11


def _expected(gold_usd: float, usd_inr: float, d: date) -> dict:
    premium = _retail_premium_for_date(d)
    base = gold_usd * usd_inr / TROY_OZ_TO_GRAM * premium
    return {
        "24k": int(round(base)),
        "22k": int(round(base * 22 / 24)),
        "18k": int(round(base * 18 / 24)),
    }


# ---------------------------------------------------------------------------
# Basic compute_rates correctness (pre-break date → 1.15)
# ---------------------------------------------------------------------------


def test_compute_rates_24k():
    rates = compute_rates(FIXTURE_GC, FIXTURE_INR, for_date=PRE_BREAK_DATE)
    exp = _expected(FIXTURE_GC, FIXTURE_INR, PRE_BREAK_DATE)
    assert abs(rates["24k"] - exp["24k"]) <= 1


def test_compute_rates_22k():
    rates = compute_rates(FIXTURE_GC, FIXTURE_INR, for_date=PRE_BREAK_DATE)
    exp = _expected(FIXTURE_GC, FIXTURE_INR, PRE_BREAK_DATE)
    assert abs(rates["22k"] - exp["22k"]) <= 1


def test_compute_rates_18k():
    rates = compute_rates(FIXTURE_GC, FIXTURE_INR, for_date=PRE_BREAK_DATE)
    exp = _expected(FIXTURE_GC, FIXTURE_INR, PRE_BREAK_DATE)
    assert abs(rates["18k"] - exp["18k"]) <= 1


def test_compute_rates_karat_ordering():
    rates = compute_rates(FIXTURE_GC, FIXTURE_INR, for_date=PRE_BREAK_DATE)
    assert rates["24k"] > rates["22k"] > rates["18k"]


def test_compute_rates_24k_is_base():
    """22K and 18K should be proportional to 24K (within rounding)."""
    rates = compute_rates(FIXTURE_GC, FIXTURE_INR, for_date=PRE_BREAK_DATE)
    assert abs(rates["22k"] - round(rates["24k"] * 22 / 24)) <= 1
    assert abs(rates["18k"] - round(rates["24k"] * 18 / 24)) <= 1


def test_compute_rates_returns_ints():
    rates = compute_rates(FIXTURE_GC, FIXTURE_INR, for_date=PRE_BREAK_DATE)
    assert isinstance(rates["22k"], int)
    assert isinstance(rates["24k"], int)
    assert isinstance(rates["18k"], int)


def test_compute_rates_realistic_range():
    rates = compute_rates(FIXTURE_GC, FIXTURE_INR, for_date=PRE_BREAK_DATE)
    assert 5000 < rates["22k"] < 20000


def test_compute_rates_high_price():
    rates = compute_rates(5000.0, 85.0, for_date=PRE_BREAK_DATE)
    assert rates["24k"] > rates["22k"] > rates["18k"]
    assert rates["22k"] < 25000


# ---------------------------------------------------------------------------
# Time-varying premium
# ---------------------------------------------------------------------------


def test_premium_pre_break_is_115():
    assert _retail_premium_for_date(PRE_BREAK_DATE) == 1.15


def test_premium_post_break_is_111():
    assert _retail_premium_for_date(POST_BREAK_DATE) == 1.11


def test_premium_on_break_date_is_111():
    """Break date itself is post-break (>= IMPORT_DUTY_BREAK_DATE)."""
    assert _retail_premium_for_date(IMPORT_DUTY_BREAK_DATE) == 1.11


def test_premium_day_before_break_is_115():
    day_before = date(2024, 7, 22)
    assert _retail_premium_for_date(day_before) == 1.15


def test_post_break_rates_lower_than_pre_break():
    """Same gold/FX inputs → post-break rates are ~3.5% lower than pre-break."""
    pre = compute_rates(FIXTURE_GC, FIXTURE_INR, for_date=PRE_BREAK_DATE)
    post = compute_rates(FIXTURE_GC, FIXTURE_INR, for_date=POST_BREAK_DATE)
    ratio = post["22k"] / pre["22k"]
    assert abs(ratio - (1.11 / 1.15)) < 0.001, f"Expected ratio ~{1.11/1.15:.4f}, got {ratio:.4f}"


def test_post_break_karat_ordering():
    rates = compute_rates(FIXTURE_GC, FIXTURE_INR, for_date=POST_BREAK_DATE)
    assert rates["24k"] > rates["22k"] > rates["18k"]
