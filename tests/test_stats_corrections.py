"""Tests for ml.direction.stats_corrections (M2 statistical corrections,
GG spec 2026-09-23, item 3)."""

from __future__ import annotations

import numpy as np
from ml.direction.stats_corrections import (
    benjamini_hochberg,
    block_bootstrap_confirm,
    bonferroni,
    classify_direction,
    n_for_power,
    one_sided_mcnemar,
    power_for_observed_effect,
)

# ---------------------------------------------------------------------------
# classify_direction
# ---------------------------------------------------------------------------


class TestClassifyDirection:
    def test_negative_mean_diff_is_better(self) -> None:
        assert classify_direction(-0.05) == "BETTER"

    def test_positive_mean_diff_is_worse(self) -> None:
        assert classify_direction(0.05) == "WORSE"

    def test_zero_is_neither(self) -> None:
        assert classify_direction(0.0) == "NEITHER"

    def test_none_is_neither(self) -> None:
        assert classify_direction(None) == "NEITHER"


# ---------------------------------------------------------------------------
# one_sided_mcnemar
# ---------------------------------------------------------------------------


class TestOneSidedMcnemar:
    def test_model_clearly_better_gives_small_p(self) -> None:
        result = one_sided_mcnemar(b=40, c=5, alternative="greater")
        assert result["p_value_one_sided"] < 0.001

    def test_model_clearly_worse_gives_large_p_for_greater(self) -> None:
        """One-sided 'greater' (testing model beats baseline) must NOT
        report significance when the model is actually worse -- this is
        exactly the bug a two-sided test can hide."""
        result = one_sided_mcnemar(b=5, c=40, alternative="greater")
        assert result["p_value_one_sided"] > 0.9

    def test_model_clearly_worse_significant_for_less(self) -> None:
        result = one_sided_mcnemar(b=5, c=40, alternative="less")
        assert result["p_value_one_sided"] < 0.001

    def test_no_discordant_pairs(self) -> None:
        result = one_sided_mcnemar(b=0, c=0)
        assert result["n_discordant"] == 0
        assert result["p_value_one_sided"] == 1.0

    def test_tied_counts_gives_p_near_half(self) -> None:
        result = one_sided_mcnemar(b=20, c=20, alternative="greater")
        assert result["p_value_one_sided"] > 0.4


# ---------------------------------------------------------------------------
# block_bootstrap_confirm
# ---------------------------------------------------------------------------


class TestBlockBootstrapConfirm:
    def test_consistently_negative_series_gives_small_p(self) -> None:
        rng = np.random.default_rng(0)
        loss_diff = list(-0.2 + rng.normal(0, 0.02, 200))  # model consistently better
        result = block_bootstrap_confirm(loss_diff, horizon=5, n_boot=500, seed=1)
        assert result["p_value_one_sided"] < 0.05

    def test_no_effect_series_gives_large_p(self) -> None:
        rng = np.random.default_rng(0)
        loss_diff = list(rng.normal(0, 0.1, 200))  # no real effect
        result = block_bootstrap_confirm(loss_diff, horizon=5, n_boot=500, seed=1)
        assert result["p_value_one_sided"] > 0.1

    def test_too_short_series_returns_none(self) -> None:
        result = block_bootstrap_confirm([0.1, 0.2], horizon=10, n_boot=100)
        assert result["p_value_one_sided"] is None
        assert result["n_boot"] == 0

    def test_block_length_matches_horizon(self) -> None:
        result = block_bootstrap_confirm([0.1] * 50, horizon=7, n_boot=50)
        assert result["block_length"] == 7

    def test_reproducible_with_same_seed(self) -> None:
        rng = np.random.default_rng(3)
        loss_diff = list(rng.normal(-0.05, 0.1, 100))
        r1 = block_bootstrap_confirm(loss_diff, horizon=5, n_boot=300, seed=7)
        r2 = block_bootstrap_confirm(loss_diff, horizon=5, n_boot=300, seed=7)
        assert r1["p_value_one_sided"] == r2["p_value_one_sided"]


# ---------------------------------------------------------------------------
# bonferroni / benjamini_hochberg
# ---------------------------------------------------------------------------


class TestBonferroni:
    def test_threshold_scales_with_family_size(self) -> None:
        result = bonferroni([0.01] * 10, alpha=0.05)
        assert result["threshold"] == 0.005

    def test_survives_correction(self) -> None:
        result = bonferroni([0.001, 0.5, 0.8], alpha=0.05)
        assert result["significant"] == [True, False, False]

    def test_none_pvalue_never_significant(self) -> None:
        result = bonferroni([None, 0.001], alpha=0.05)
        assert result["significant"] == [False, True]

    def test_empty_family(self) -> None:
        result = bonferroni([], alpha=0.05)
        assert result["m"] == 0
        assert result["significant"] == []


class TestBenjaminiHochberg:
    def test_more_permissive_than_bonferroni(self) -> None:
        p_values = [0.001, 0.01, 0.02, 0.03, 0.04, 0.5, 0.6, 0.7, 0.8, 0.9]
        bh = benjamini_hochberg(p_values, alpha=0.05)
        bonf = bonferroni(p_values, alpha=0.05)
        assert sum(bh["significant"]) >= sum(bonf["significant"])

    def test_all_large_p_values_none_significant(self) -> None:
        result = benjamini_hochberg([0.5, 0.6, 0.7, 0.8], alpha=0.05)
        assert result["n_significant"] == 0

    def test_all_tiny_p_values_all_significant(self) -> None:
        result = benjamini_hochberg([0.0001, 0.0002, 0.0003], alpha=0.05)
        assert result["n_significant"] == 3

    def test_none_values_excluded_from_ranking(self) -> None:
        result = benjamini_hochberg([None, 0.001, 0.002], alpha=0.05)
        assert result["m"] == 3
        assert result["significant"][0] is False


# ---------------------------------------------------------------------------
# power_for_observed_effect / n_for_power
# ---------------------------------------------------------------------------


class TestPowerCalculations:
    def test_larger_n_gives_more_power(self) -> None:
        low_n = power_for_observed_effect(50, mean_diff=-0.05, std_diff=0.3)
        high_n = power_for_observed_effect(500, mean_diff=-0.05, std_diff=0.3)
        assert high_n > low_n

    def test_zero_std_returns_nan(self) -> None:
        import math

        assert math.isnan(power_for_observed_effect(100, -0.05, 0.0))

    def test_n_for_power_roundtrips_with_power_for_observed_effect(self) -> None:
        mean_diff, std_diff = -0.1, 0.3
        n_needed = n_for_power(mean_diff, std_diff, power=0.80, alpha=0.05)
        achieved_power = power_for_observed_effect(n_needed, mean_diff, std_diff, alpha=0.05)
        assert abs(achieved_power - 0.80) < 0.01

    def test_larger_effect_needs_smaller_n(self) -> None:
        n_small_effect = n_for_power(-0.02, 0.3, power=0.80)
        n_large_effect = n_for_power(-0.2, 0.3, power=0.80)
        assert n_large_effect < n_small_effect

    def test_zero_mean_diff_needs_infinite_n(self) -> None:
        assert n_for_power(0.0, 0.3) == float("inf")
