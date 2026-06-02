"""Unit tests for ml/experiments/premium_carry.py -- mocked data, no live calls."""

from __future__ import annotations

from ml.experiments.premium_carry import compute_premium_carry_forecast


def test_flat_carry_equals_flat_naive() -> None:
    """With flat carry, premium_carry is algebraically identical to flat-naive."""
    ibja_last = 6000.0
    gold_usd = 3000.0
    usd_inr = 84.0
    horizon = 5
    fc = compute_premium_carry_forecast(ibja_last, gold_usd, usd_inr, horizon)
    assert len(fc) == horizon
    for h in range(horizon):
        assert abs(fc[h] - ibja_last) < 1e-6, (
            f"h={h}: expected {ibja_last:.6f}, got {fc[h]:.6f} "
            "(flat-carry should collapse to flat-naive)"
        )


def test_flat_carry_output_length() -> None:
    for h in [1, 5, 10, 20]:
        fc = compute_premium_carry_forecast(6000.0, 3000.0, 84.0, h)
        assert len(fc) == h


def test_zero_gold_usd_returns_flat() -> None:
    """Guard against division by zero when gold_usd is zero."""
    fc = compute_premium_carry_forecast(6000.0, 0.0, 84.0, 5)
    assert all(v == 6000.0 for v in fc)


def test_zero_usd_inr_returns_flat() -> None:
    fc = compute_premium_carry_forecast(6000.0, 3000.0, 0.0, 5)
    assert all(v == 6000.0 for v in fc)


def test_different_macro_values_still_collapse() -> None:
    """Verify algebraic identity holds for varied inputs."""
    for ibja in [5000.0, 6000.0, 7500.0]:
        for g in [2500.0, 3000.0, 3200.0]:
            for fx in [82.0, 84.0, 86.0]:
                fc = compute_premium_carry_forecast(ibja, g, fx, 5)
                for v in fc:
                    assert abs(v - ibja) < 1e-4, f"identity failed: ibja={ibja} g={g} fx={fx}"
