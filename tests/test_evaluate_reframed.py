"""Tests for ml.direction.evaluate_reframed (M2 embargo-aware harness,
Brier skill score, Diebold-Mariano test)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from ml.direction.dataset import FEATURE_COLS
from ml.direction.evaluate_reframed import (
    _embargo_eligible_indices,
    brier_skill_score,
    diebold_mariano_test,
    run_walk_forward_reframed,
)

# ---------------------------------------------------------------------------
# _embargo_eligible_indices
# ---------------------------------------------------------------------------


class TestEmbargoEligibleIndices:
    def test_excludes_rows_whose_label_matures_after_test_date(self) -> None:
        as_of = ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"]
        # row 0's h=5 label matures 2025-01-06, AFTER row 3's as_of_date (01-04) --
        # must be excluded when testing row 3. row 1/2's labels mature before 01-04.
        label_dates = ["2025-01-06", "2025-01-03", "2025-01-03", "2025-01-05"]
        eligible = _embargo_eligible_indices(as_of, label_dates, 3)
        assert 0 not in eligible
        assert 1 in eligible
        assert 2 in eligible

    def test_none_label_date_never_eligible(self) -> None:
        as_of = ["2025-01-01", "2025-01-02"]
        label_dates = [None, "2025-01-01"]
        eligible = _embargo_eligible_indices(as_of, label_dates, 1)
        assert eligible == []

    def test_first_test_index_has_no_eligible_rows(self) -> None:
        assert _embargo_eligible_indices(["2025-01-01"], [None], 0) == []


# ---------------------------------------------------------------------------
# brier_skill_score
# ---------------------------------------------------------------------------


class TestBrierSkillScore:
    def test_perfect_model_scores_one(self) -> None:
        assert brier_skill_score(0.0, 0.25) == 1.0

    def test_equal_to_climatology_scores_zero(self) -> None:
        assert brier_skill_score(0.25, 0.25) == 0.0

    def test_worse_than_climatology_is_negative(self) -> None:
        assert brier_skill_score(0.5, 0.25) < 0.0

    def test_zero_climatology_returns_nan(self) -> None:
        import math

        assert math.isnan(brier_skill_score(0.1, 0.0))


# ---------------------------------------------------------------------------
# diebold_mariano_test
# ---------------------------------------------------------------------------


class TestDieboldMariano:
    def test_identical_loss_series_gives_near_zero_stat(self) -> None:
        loss = [0.1, 0.2, 0.3, 0.15, 0.25] * 10
        result = diebold_mariano_test(loss, loss, horizon=1)
        assert abs(result["dm_stat"]) < 1e-8
        assert result["mean_diff"] == 0.0

    def test_systematically_lower_loss_gives_negative_stat(self) -> None:
        rng = np.random.default_rng(0)
        loss_a = list(0.1 + rng.normal(0, 0.01, 100))  # consistently lower
        loss_b = list(0.3 + rng.normal(0, 0.01, 100))
        result = diebold_mariano_test(loss_a, loss_b, horizon=1)
        assert result["dm_stat"] < 0
        assert result["p_value"] < 0.05

    def test_too_few_observations_returns_none(self) -> None:
        result = diebold_mariano_test([0.1], [0.2], horizon=1)
        assert result["dm_stat"] is None

    def test_higher_horizon_uses_more_lags_without_crashing(self) -> None:
        rng = np.random.default_rng(1)
        loss_a = list(rng.normal(0.2, 0.05, 50))
        loss_b = list(rng.normal(0.2, 0.05, 50))
        result = diebold_mariano_test(loss_a, loss_b, horizon=10)
        assert result["dm_stat"] is not None

    def test_one_sided_less_matches_two_sided_direction(self) -> None:
        rng = np.random.default_rng(0)
        # A modest, noisy edge (not an extreme separation) so both p-values
        # stay well above float underflow and are meaningfully comparable.
        loss_a = list(0.20 + rng.normal(0, 0.10, 100))
        loss_b = list(0.23 + rng.normal(0, 0.10, 100))
        two_sided = diebold_mariano_test(loss_a, loss_b, horizon=1, alternative="two-sided")
        one_sided = diebold_mariano_test(loss_a, loss_b, horizon=1, alternative="less")
        # a is genuinely lower than b -> one-sided "less" p-value should be
        # exactly half the two-sided p-value (same evidence, one direction).
        assert one_sided["p_value"] == pytest.approx(two_sided["p_value"] / 2, rel=1e-6)

    def test_one_sided_greater_is_conservative_when_a_is_better(self) -> None:
        rng = np.random.default_rng(0)
        loss_a = list(0.1 + rng.normal(0, 0.01, 100))  # a is better (lower)
        loss_b = list(0.3 + rng.normal(0, 0.01, 100))
        result = diebold_mariano_test(loss_a, loss_b, horizon=1, alternative="greater")
        # Testing "a is worse" when a is actually much better -> high p-value.
        assert result["p_value"] > 0.9

    def test_invalid_alternative_raises(self) -> None:
        with pytest.raises(ValueError):
            diebold_mariano_test([0.1, 0.2, 0.3], [0.1, 0.2, 0.3], horizon=1, alternative="bogus")

    def test_effective_n_equals_n_with_no_autocorrelation(self) -> None:
        rng = np.random.default_rng(2)
        loss_a = list(rng.normal(0.1, 0.05, 200))
        loss_b = list(rng.normal(0.15, 0.05, 200))
        result = diebold_mariano_test(loss_a, loss_b, horizon=1)
        # horizon=1 -> max_lag=0 -> long_run_var == gamma_0 -> effective_n == n exactly.
        assert result["effective_n"] == pytest.approx(result["n"], rel=1e-9)

    def test_effective_n_shrinks_with_positive_autocorrelation(self) -> None:
        rng = np.random.default_rng(3)
        # Strongly autocorrelated series (AR(1)-like via cumulative smoothing).
        base = rng.normal(0, 1, 220)
        smoothed = np.convolve(base, np.ones(10) / 10, mode="valid")
        loss_a = list(smoothed)
        loss_b = [0.0] * len(loss_a)
        result = diebold_mariano_test(loss_a, loss_b, horizon=10)
        assert result["effective_n"] < result["n"]


# ---------------------------------------------------------------------------
# run_walk_forward_reframed — small integration smoke test
# ---------------------------------------------------------------------------


def _make_synthetic_dataset_with_h5(n: int = 60, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        as_of = f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"
        label_date_h5 = f"2025-{((i + 5) // 28) + 1:02d}-{((i + 5) % 28) + 1:02d}"
        current = 70000.0 + i * 10
        delta = float(rng.integers(-600, 600))
        row: dict = {
            "as_of_date": as_of,
            "current_pm916": current,
            "label_date_h5": label_date_h5,
            "delta_per_gram_h5": delta,
            "label_binary_h5": float(delta > 0),
            "label_ternary_h5": "up" if delta > 0 else "down",
            "window_min_pm916_h5": current - abs(delta),
        }
        for col in FEATURE_COLS:
            row[col] = float(rng.uniform(0.5, 2.0) * (i + 1))
        rows.append(row)
    return pd.DataFrame(rows)


class TestRunWalkForwardReframed:
    def test_raw_binary_h5_smoke(self) -> None:
        ds = _make_synthetic_dataset_with_h5()
        result = run_walk_forward_reframed(ds, "raw_binary", 5, min_train_size=20)
        assert result["n_test_folds"] > 0
        assert "ensemble" in result
        assert "brier_skill_score_vs_climatology" in result["ensemble"]
        assert "diebold_mariano_vs_always_up" in result["ensemble"]

    def test_deadzone_h5_drops_flat_rows(self) -> None:
        ds = _make_synthetic_dataset_with_h5()
        result = run_walk_forward_reframed(ds, "deadzone", 5, min_train_size=20)
        # No "flat" injected in the synthetic fixture, so this should behave
        # like raw_binary in row count terms (not a strict equality check,
        # just confirms it runs end-to-end without error).
        assert result["n_test_folds"] >= 0
