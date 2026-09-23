"""Tests for ml.direction.gate — product gate decision logic.

All tests are pure (no I/O, no network).  They verify the four-gate logic
and that ship=True is only produced when all conditions hold simultaneously.
"""

from __future__ import annotations

from pathlib import Path

from ml.direction.gate import (
    ECE_MAX_PROB,
    MIN_OOS_FOLDS,
    decide_direction_signal,
    decide_timing_signal,
    is_signal_promoted,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _passing_baseline() -> dict:
    """Construct a synthetic baseline dict that satisfies all five gates."""
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
            "ece": 0.04,  # G5: well-calibrated (<= ECE_MAX_PROB)
        },
    }


def _timing_passing_baseline() -> dict:
    """A baseline that passes the probability gate AND the stricter timing gate."""
    b = _passing_baseline()
    b["n_test_folds"] = 70  # T1: >= 60
    b["logistic_metrics"].update(
        {
            "accuracy": 0.80,  # T2: edge 0.10 >= 0.05
            "always_up_accuracy": 0.70,
            "brier": 0.15,
            "ece": 0.03,  # T3: <= 0.05
            "p_value": 0.005,  # T4: < 0.01
        }
    )
    return b


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

        # Flip G5 (calibration)
        b5 = _passing_baseline()
        b5["logistic_metrics"]["ece"] = ECE_MAX_PROB + 0.01
        assert decide_direction_signal(b5)["ship"] is False


# ---------------------------------------------------------------------------
# Gate G5: calibration (ECE)
# ---------------------------------------------------------------------------


class TestGateG5Calibration:
    """G5: ECE must be <= ECE_MAX_PROB to call a probability well-calibrated."""

    def test_missing_ece_key_fails(self) -> None:
        baseline = _passing_baseline()
        del baseline["logistic_metrics"]["ece"]
        result = decide_direction_signal(baseline)
        assert result["ship"] is False
        assert "missing logistic_metrics keys" in result["reason"]

    def test_high_ece_fails(self) -> None:
        baseline = _passing_baseline()
        baseline["logistic_metrics"]["ece"] = ECE_MAX_PROB + 0.05
        result = decide_direction_signal(baseline)
        assert result["ship"] is False
        assert "calibrated" in result["reason"]

    def test_ece_at_threshold_passes(self) -> None:
        baseline = _passing_baseline()
        baseline["logistic_metrics"]["ece"] = ECE_MAX_PROB  # <= boundary
        result = decide_direction_signal(baseline)
        assert result["ship"] is True


# ---------------------------------------------------------------------------
# Timing gate (stricter, separate)
# ---------------------------------------------------------------------------


class TestTimingGate:
    """decide_timing_signal is gated stricter than the probability gate."""

    def test_dark_when_probability_gate_fails(self) -> None:
        # A baseline that fails the probability gate (not significant) must also
        # fail timing, with the probability-gate reason surfaced.
        baseline = _passing_baseline()
        baseline["logistic_metrics"]["significant_at_05"] = False
        result = decide_timing_signal(baseline)
        assert result["ship"] is False
        assert result["basis"] == "hold_dark"
        assert "probability gate not passed" in result["reason"]

    def test_dark_when_prob_passes_but_folds_too_few(self) -> None:
        # Passes probability gate (50 folds >= 30) but below timing's 60-fold bar.
        baseline = _passing_baseline()  # 50 folds
        assert decide_direction_signal(baseline)["ship"] is True
        result = decide_timing_signal(baseline)
        assert result["ship"] is False
        assert "insufficient folds" in result["reason"]

    def test_dark_when_edge_too_small(self) -> None:
        baseline = _timing_passing_baseline()
        baseline["logistic_metrics"]["accuracy"] = 0.72  # edge 0.02 < 0.05
        result = decide_timing_signal(baseline)
        assert result["ship"] is False
        assert "edge" in result["reason"]

    def test_dark_when_p_too_weak(self) -> None:
        baseline = _timing_passing_baseline()
        baseline["logistic_metrics"]["p_value"] = 0.03  # >= 0.01
        result = decide_timing_signal(baseline)
        assert result["ship"] is False
        assert "significance too weak" in result["reason"]

    def test_ships_only_when_all_strict_conditions_hold(self) -> None:
        baseline = _timing_passing_baseline()
        result = decide_timing_signal(baseline)
        assert result["ship"] is True
        assert result["basis"] == "timing_model"

    def test_timing_strictly_harder_than_probability(self) -> None:
        # A baseline that ships the probability gate but NOT timing proves the
        # timing gate is strictly stricter.
        baseline = _passing_baseline()  # 50 folds, edge 0.05, p=0.02
        assert decide_direction_signal(baseline)["ship"] is True
        assert decide_timing_signal(baseline)["ship"] is False


# ---------------------------------------------------------------------------
# TestPromotionGateNeverWired — the proof GG's 2026-09-23 spec asked for:
# a model that PASSES decide_direction_signal/decide_timing_signal must
# still not reach users without a separate, explicit promotion record.
# ---------------------------------------------------------------------------


class TestPromotionGateNeverWired:
    def test_passing_both_gates_does_not_imply_promoted(self) -> None:
        """A model can ship=True on BOTH gates and still not be promoted --
        is_signal_promoted is a wholly separate, human-authorized check."""
        baseline = _timing_passing_baseline()
        assert decide_direction_signal(baseline)["ship"] is True
        assert decide_timing_signal(baseline)["ship"] is True
        assert is_signal_promoted(path=Path("/nonexistent/no-such-file.json")) is False

    def test_promoted_only_when_record_file_exists(self, tmp_path: Path) -> None:
        record = tmp_path / "direction_promotion_record.json"
        assert is_signal_promoted(path=record) is False
        record.write_text('{"approved_by": "GG"}')
        assert is_signal_promoted(path=record) is True

    def test_default_promotion_record_does_not_currently_exist(self) -> None:
        """Regression guard: the repo must not accidentally ship a promotion
        record — its mere presence is what the check script treats as
        authorization, so an accidental commit of this file would silently
        flip every future direction-signal wiring to 'promoted'."""
        assert is_signal_promoted() is False

    def test_no_live_surface_references_the_gate_decision(self) -> None:
        """Static sweep matching scripts/check_direction_signal_not_wired_
        without_promotion.py's own logic — belt-and-suspenders: this proves
        the CURRENT repo state from the test suite itself, not only from a
        CI-only script."""
        import re

        root = Path(__file__).resolve().parent.parent
        surface_files = [
            root / "app.js",
            root / "i18n.js",
            root / "index.html",
            root / "service-worker.js",
            root / "ml" / "notifications.py",
            root / "ml" / "notification_routing.py",
            root / "ml" / "public_copy.py",
        ]
        forbidden = re.compile(
            r"\bprobability_gate\b|\btiming_gate\b|\bdecide_direction_signal\b|\bdecide_timing_signal\b"
        )
        for path in surface_files:
            assert path.exists(), f"expected surface file missing: {path}"
            for line in path.read_text(encoding="utf-8").splitlines():
                assert not forbidden.search(line), f"{path} references the gate directly: {line!r}"
