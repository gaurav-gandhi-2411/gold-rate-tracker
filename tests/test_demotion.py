"""Tests for ml.demotion (ADR 068). Synthetic folds only; nothing is wired to the live path."""

from __future__ import annotations

import math

import numpy as np
from ml.demotion import (
    DemotionParams,
    binom_p_lower,
    demotion_status,
    direction_breach,
    error_breach,
    hac_one_sided_p_worse,
    range_breach,
)


def _folds(n: int, *, good: bool, seed: int = 42) -> list[dict]:
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        y = rng.normal(0, 0.008)
        ret = y + rng.normal(0, 0.002) if good else -y + rng.normal(0, 0.004)
        pm0 = 13500.0
        out.append(
            {
                "pm0": pm0,
                "pm1": pm0 * math.exp(y),
                "ret": ret,
                "p_up": 0.8 if ret > 0 else 0.2,
            }
        )
    return out


def test_good_model_is_not_demoted() -> None:
    s = demotion_status(_folds(80, good=True))
    assert s["demote_any"] is False


def test_inverted_model_is_demoted_on_error_and_direction() -> None:
    s = demotion_status(_folds(80, good=False))
    assert s["demote"]["error"] is True
    assert s["demote"]["direction"] is True


def test_too_few_folds_never_demotes() -> None:
    p = DemotionParams()
    assert error_breach(_folds(p.min_folds - 1, good=False), p)["breach"] is False
    assert direction_breach(_folds(p.min_folds - 1, good=False), p)["breach"] is False
    assert range_breach([False] * (p.min_folds - 1), p)["breach"] is False


def test_persistence_requires_consecutive_breaches() -> None:
    p = DemotionParams(persist=3)
    folds = _folds(80, good=False)
    assert demotion_status(folds, p=p)["demote"]["error"] is True
    # earlier the same series was still good: a breach only on the last check does not demote
    mixed = _folds(70, good=True) + _folds(10, good=False, seed=7)
    assert demotion_status(mixed, p=DemotionParams(persist=10))["demote"]["error"] is False


def test_range_rule_demotes_only_when_coverage_is_well_below_nominal() -> None:
    p = DemotionParams()
    assert range_breach([True] * 48 + [False] * 12, p)["breach"] is False  # 80% covered
    assert range_breach([True] * 30 + [False] * 30, p)["breach"] is True  # 50% covered


def test_hac_p_is_one_sided_in_the_right_direction() -> None:
    worse = np.full(40, 5.0) + np.random.default_rng(1).normal(0, 1, 40)
    better = -worse
    assert hac_one_sided_p_worse(worse, 4) < 0.01
    assert hac_one_sided_p_worse(better, 4) > 0.99


def test_binom_p_lower_matches_hand_value() -> None:
    # P(X <= 0 | n=3, p=0.5) = 1/8
    assert abs(binom_p_lower(0, 3, 0.5) - 0.125) < 1e-12
    assert binom_p_lower(0, 0, 0.5) == 1.0


def _null_series(paths: int, ar: float, seed: int = 42) -> np.ndarray:
    """Zero-mean AR(1) with unit-variance t(4) innovations: the F7 review's null."""
    rng = np.random.default_rng(seed)
    e = rng.standard_t(4, size=(paths, 90)) / math.sqrt(2.0)
    x = np.zeros_like(e)
    for i in range(1, 90):
        x[:, i] = ar * x[:, i - 1] + e[:, i]
    return x[:, 50:]  # 40 observations after a burn-in


def test_hac_size_under_a_true_null_is_near_nominal() -> None:
    # F7: the normal-tail version rejected ~9% here at nominal 5%; the t(lags) tail must be <= 6%
    x = _null_series(3000, ar=0.3)
    rate = np.mean([hac_one_sided_p_worse(row, 4) < 0.05 for row in x])
    assert rate <= 0.06


def test_hac_keeps_power_against_a_clearly_worse_model() -> None:
    # mean loss difference of 1.0 sd (a clearly worse model) must still be caught almost always
    x = _null_series(500, ar=0.3) + 1.0
    rate = np.mean([hac_one_sided_p_worse(row, 4) < 0.05 for row in x])
    assert rate >= 0.9


def test_hac_p_is_a_valid_probability_on_degenerate_input() -> None:
    assert hac_one_sided_p_worse(np.zeros(40), 4) == 1.0
    assert hac_one_sided_p_worse(np.full(40, 2.0), 4) == 0.0
    assert hac_one_sided_p_worse(np.array([1.0]), 4) == 1.0
