"""Tests for ml.direction.gate — product gate decision logic.

All tests are pure (no I/O, no network).  They verify the four-gate logic
and that ship=True is only produced when all conditions hold simultaneously.
"""

from __future__ import annotations

from ml.direction.gate import MIN_OOS_FOLDS, decide_direction_signal

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _passing_baseline() -> dict:
    """Construct a synthetic baseline dict that satisfies all four gates."""
    return {
        "n_test_folds": 50,  # G1: >= 30
        "logistic_metrics": {
            "model": "logistic",
            "n": 50,
            "accuracy": 0.75,  # G4: > always_up_accuracy (0.70)
            "brier": 0.18,  # G3: < always_up_brier (0.21)
            "log_loss": 0.55,
            "always_up_accuracy": 0.70,
            "always_up_brier": 0.21,
            "p_value": 0.02,  # G2: significant_at_05 requires p < 0.05
            "significant_at_05": True,  # G2: must be True
            "n_discordant": 20,
        },
    }


# ---------------------------------------------------------------------------
# Gate returns ship=False on bad inputs
# ---------------------------------------------------------------------------


class TestGateInvalidInputs:
    """Gate must return ship=False with base_rate_fallback for bad inputs."""

    def test_none_baseline(self) -> None:
        result = decide_direction_signal(None)
        assert result["ship"] is False
        assert result["basis"] == "base_rate_fallback"
        assert result["reason"] == "no eval results"

    def test_empty_dict(self) -> None:
        result = decide_direction_signal({})
        assert result["ship"] is False
        assert result["basis"] == "base_rate_fallback"

    def test_missing_n_test_folds(self) -> None:
        baseline = _passing_baseline()
        del baseline["n_test_folds"]
        result = decide_direction_signal(baseline)
        assert result["ship"] is False
        assert result["basis"] == "base_rate_fallback"

    def test_missing_logistic_metrics(self) -> None:
        baseline = _passing_baseline()
        del baseline["logistic_metrics"]
        result = decide_direction_signal(baseline)
        assert result["ship"] is False
        assert result["basis"] == "base_rate_fallback"

    def test_empty_logistic_metrics(self) -> None:
        baseline = _passing_baseline()
        baseline["logistic_metrics"] = {}
        result = decide_direction_signal(baseline)
        assert result["ship"] is False

    def test_missing_required_logistic_key(self) -> None:
        baseline = _passing_baseline()
        del baseline["logistic_metrics"]["significant_at_05"]
        result = decide_direction_signal(baseline)
        assert result["ship"] is False
        assert "missing logistic_metrics keys" in result["reason"]


# ---------------------------------------------------------------------------
# Gate G1: insufficient folds
# ---------------------------------------------------------------------------


class TestGateG1InsufficientFolds:
    """G1: n_test_folds must be >= MIN_OOS_FOLDS."""

    def test_exactly_min_minus_1_fails(self) -> None:
        baseline = _passing_baseline()
        baseline["n_test_folds"] = MIN_OOS_FOLDS - 1
        result = decide_direction_signal(baseline)
        assert result["ship"] is False
        assert "insufficient OOS folds" in result["reason"]

    def test_zero_folds_fails(self) -> None:
        baseline = _passing_baseline()
        baseline["n_test_folds"] = 0
        result = decide_direction_signal(baseline)
        assert result["ship"] is False

    def test_exactly_min_passes_g1(self) -> None:
        """Exactly MIN_OOS_FOLDS should pass G1 (all other gates still pass)."""
        baseline = _passing_baseline()
        baseline["n_test_folds"] = MIN_OOS_FOLDS
        result = decide_direction_signal(baseline)
        # Should ship since all other gates also pass
        assert result["ship"] is True


# ---------------------------------------------------------------------------
# Gate G2: not significant
# ---------------------------------------------------------------------------


class TestGateG2NotSignificant:
    """G2: significant_at_05 must be True."""

    def test_not_significant_fails(self) -> None:
        baseline = _passing_baseline()
        baseline["logistic_metrics"]["significant_at_05"] = False
        result = decide_direction_signal(baseline)
        assert result["ship"] is False
        assert "not significant" in result["reason"]

    def test_p_value_above_threshold_in_reason(self) -> None:
        baseline = _passing_baseline()
        baseline["logistic_metrics"]["significant_at_05"] = False
        baseline["logistic_metrics"]["p_value"] = 0.12
        result = decide_direction_signal(baseline)
        assert "p=" in result["reason"] or "not significant" in result["reason"]


# ---------------------------------------------------------------------------
# Gate G3: model brier >= always_up brier
# ---------------------------------------------------------------------------


class TestGateG3Brier:
    """G3: model brier must be strictly less than always_up_brier."""

    def test_brier_equal_fails(self) -> None:
        baseline = _passing_baseline()
        baseline["logistic_metrics"]["brier"] = 0.21
        baseline["logistic_metrics"]["always_up_brier"] = 0.21
        result = decide_direction_signal(baseline)
        assert result["ship"] is False
        assert "Brier" in result["reason"]

    def test_brier_worse_fails(self) -> None:
        baseline = _passing_baseline()
        baseline["logistic_metrics"]["brier"] = 0.25
        baseline["logistic_metrics"]["always_up_brier"] = 0.21
        result = decide_direction_signal(baseline)
        assert result["ship"] is False

    def test_brier_just_better_passes_g3(self) -> None:
        """Brier just below always_up_brier should pass G3 (all other gates pass)."""
        baseline = _passing_baseline()
        baseline["logistic_metrics"]["brier"] = 0.2099
        baseline["logistic_metrics"]["always_up_brier"] = 0.21
        result = decide_direction_signal(baseline)
        assert result["ship"] is True


# ---------------------------------------------------------------------------
# Gate G4: accuracy not beating baseline
# ---------------------------------------------------------------------------


class TestGateG4Accuracy:
    """G4: model accuracy must be strictly greater than always_up_accuracy."""

    def test_accuracy_equal_fails(self) -> None:
        baseline = _passing_baseline()
        baseline["logistic_metrics"]["accuracy"] = 0.70
        baseline["logistic_metrics"]["always_up_accuracy"] = 0.70
        result = decide_direction_signal(baseline)
        assert result["ship"] is False
        assert "accuracy" in result["reason"]

    def test_accuracy_below_baseline_fails(self) -> None:
        baseline = _passing_baseline()
        baseline["logistic_metrics"]["accuracy"] = 0.65
        baseline["logistic_metrics"]["always_up_accuracy"] = 0.70
        result = decide_direction_signal(baseline)
        assert result["ship"] is False

    def test_accuracy_just_above_passes_g4(self) -> None:
        """Accuracy just above baseline should pass G4 (all other gates pass)."""
        baseline = _passing_baseline()
        baseline["logistic_metrics"]["accuracy"] = 0.7001
        baseline["logistic_metrics"]["always_up_accuracy"] = 0.70
        result = decide_direction_signal(baseline)
        assert result["ship"] is True


# ---------------------------------------------------------------------------
# All gates pass → ship=True
# ---------------------------------------------------------------------------


class TestGateAllPass:
    """Gate returns ship=True only when all four conditions hold."""

    def test_all_conditions_met(self) -> None:
        baseline = _passing_baseline()
        result = decide_direction_signal(baseline)
        assert result["ship"] is True
        assert result["basis"] == "model_calibrated"
        assert "logistic beats always-up OOS" in result["reason"]

    def test_reason_contains_key_numbers(self) -> None:
        """Reason string must include accuracy, brier, p-value, and fold count."""
        baseline = _passing_baseline()
        result = decide_direction_signal(baseline)
        reason = result["reason"]
        assert "acc" in reason
        assert "brier" in reason
        assert "p=" in reason
        assert "50 folds" in reason

    def test_one_gate_flip_prevents_ship(self) -> None:
        """Flipping any single gate back should prevent shipping."""
        # Flip G1
        b1 = _passing_baseline()
        b1["n_test_folds"] = MIN_OOS_FOLDS - 1
        assert decide_direction_signal(b1)["ship"] is False

        # Flip G2
        b2 = _passing_baseline()
        b2["logistic_metrics"]["significant_at_05"] = False
        assert decide_direction_signal(b2)["ship"] is False

        # Flip G3
        b3 = _passing_baseline()
        b3["logistic_metrics"]["brier"] = 0.21  # equal, not less
        b3["logistic_metrics"]["always_up_brier"] = 0.21
        assert decide_direction_signal(b3)["ship"] is False

        # Flip G4
        b4 = _passing_baseline()
        b4["logistic_metrics"]["accuracy"] = 0.70
        b4["logistic_metrics"]["always_up_accuracy"] = 0.70
        assert decide_direction_signal(b4)["ship"] is False
