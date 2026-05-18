"""Tests for ml.basis — pure functions, no I/O."""

from __future__ import annotations

import pytest
from ml.basis import apply_basis_adjustment, compute_basis_factor, should_refit

# ---------------------------------------------------------------------------
# compute_basis_factor
# ---------------------------------------------------------------------------


def test_basis_factor_ratio():
    assert compute_basis_factor(15780.0, 15000.0) == pytest.approx(15780.0 / 15000.0)


def test_basis_factor_equal_prices():
    assert compute_basis_factor(100.0, 100.0) == pytest.approx(1.0)


def test_basis_factor_zero_mcx_raises():
    with pytest.raises(ValueError, match="non-zero"):
        compute_basis_factor(100.0, 0.0)


def test_basis_factor_positive():
    assert compute_basis_factor(15000.0, 14000.0) > 0


def test_basis_factor_ibja_gt_mcx():
    assert compute_basis_factor(16000.0, 15000.0) > 1.0


def test_basis_factor_ibja_lt_mcx():
    assert compute_basis_factor(14000.0, 15000.0) < 1.0


# ---------------------------------------------------------------------------
# apply_basis_adjustment
# ---------------------------------------------------------------------------


def test_apply_basis_identity_when_prices_equal():
    assert apply_basis_adjustment(10000.0, 15000.0, 15000.0) == pytest.approx(10000.0)


def test_apply_basis_scales_correctly():
    adjusted = apply_basis_adjustment(10000.0, 15780.0, 15000.0)
    assert adjusted == pytest.approx(10000.0 * (15780.0 / 15000.0))


def test_apply_basis_higher_ibja_increases_forecast():
    assert apply_basis_adjustment(10000.0, 16000.0, 15000.0) > 10000.0


def test_apply_basis_lower_ibja_decreases_forecast():
    assert apply_basis_adjustment(10000.0, 14000.0, 15000.0) < 10000.0


def test_apply_basis_zero_mcx_raises():
    with pytest.raises(ValueError, match="non-zero"):
        apply_basis_adjustment(10000.0, 15000.0, 0.0)


# ---------------------------------------------------------------------------
# should_refit
# ---------------------------------------------------------------------------


def test_should_refit_false_within_threshold():
    assert should_refit(1.01, 1.00, threshold=0.02) is False


def test_should_refit_true_beyond_threshold():
    assert should_refit(1.03, 1.00, threshold=0.02) is True


def test_should_refit_below_threshold_is_false():
    # 1.9% drift — clearly below 2% threshold
    assert should_refit(1.019, 1.00, threshold=0.02) is False


def test_should_refit_negative_drift():
    assert should_refit(0.97, 1.00, threshold=0.02) is True


def test_should_refit_zero_reference_raises():
    with pytest.raises(ValueError, match="non-zero"):
        should_refit(1.0, 0.0)


def test_should_refit_default_threshold_below_2pct_is_false():
    # 1.9% drift — clearly below 2% default threshold
    assert should_refit(1.019, 1.00) is False


def test_should_refit_default_threshold_above_2pct_is_true():
    assert should_refit(1.025, 1.00) is True
