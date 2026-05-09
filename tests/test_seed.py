"""Tests for ml/seed_history.py — compute_rates() unit tests."""

from ml.seed_history import INDIA_RETAIL_PREMIUM, TROY_OZ_TO_GRAM, compute_rates

# Fixture: GC=F = $3000.00/oz, USD/INR = 84.00
# base_24k = 3000 * 84 / 31.1035 * 1.15
#           = 252000 / 31.1035 * 1.15
#           = 8103.024... * 1.15
#           = 9318.477...
# 24k = round(9318.477)        = 9318
# 22k = round(9318.477 * 22/24) = round(8541.937) = 8542
# 18k = round(9318.477 * 18/24) = round(6988.858) = 6989

FIXTURE_GC = 3000.0
FIXTURE_INR = 84.0


def _expected(gold_usd: float, usd_inr: float) -> dict:
    base = gold_usd * usd_inr / TROY_OZ_TO_GRAM * INDIA_RETAIL_PREMIUM
    return {
        "24k": int(round(base)),
        "22k": int(round(base * 22 / 24)),
        "18k": int(round(base * 18 / 24)),
    }


def test_compute_rates_24k():
    rates = compute_rates(FIXTURE_GC, FIXTURE_INR)
    exp = _expected(FIXTURE_GC, FIXTURE_INR)
    assert abs(rates["24k"] - exp["24k"]) <= 1


def test_compute_rates_22k():
    rates = compute_rates(FIXTURE_GC, FIXTURE_INR)
    exp = _expected(FIXTURE_GC, FIXTURE_INR)
    assert abs(rates["22k"] - exp["22k"]) <= 1


def test_compute_rates_18k():
    rates = compute_rates(FIXTURE_GC, FIXTURE_INR)
    exp = _expected(FIXTURE_GC, FIXTURE_INR)
    assert abs(rates["18k"] - exp["18k"]) <= 1


def test_compute_rates_karat_ordering():
    rates = compute_rates(FIXTURE_GC, FIXTURE_INR)
    assert rates["24k"] > rates["22k"] > rates["18k"]


def test_compute_rates_24k_is_base():
    """22K and 18K should be proportional to 24K (within rounding)."""
    rates = compute_rates(FIXTURE_GC, FIXTURE_INR)
    assert abs(rates["22k"] - round(rates["24k"] * 22 / 24)) <= 1
    assert abs(rates["18k"] - round(rates["24k"] * 18 / 24)) <= 1


def test_compute_rates_returns_ints():
    rates = compute_rates(FIXTURE_GC, FIXTURE_INR)
    assert isinstance(rates["22k"], int)
    assert isinstance(rates["24k"], int)
    assert isinstance(rates["18k"], int)


def test_compute_rates_realistic_range():
    # At $3000/oz + 84 INR/USD, 22K should be in a sensible Indian retail range
    rates = compute_rates(FIXTURE_GC, FIXTURE_INR)
    assert 5000 < rates["22k"] < 20000


def test_compute_rates_high_price():
    # Near peak: $5000/oz + 85 INR/USD
    rates = compute_rates(5000.0, 85.0)
    assert rates["24k"] > rates["22k"] > rates["18k"]
    assert rates["22k"] < 25000  # sanity threshold from main()
