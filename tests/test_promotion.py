"""Tests for ml/promotion.py — champion/challenger promotion gate."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from ml.promotion import (
    _PROMOTION_THRESHOLD,
    evaluate_promotion,
    promote,
    rollback,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_version(version: str = "1", mae: float = 100.0, stage: str = "Production"):
    mv = MagicMock()
    mv.version = version
    mv.current_stage = stage
    mv.tags = {"val_mae": str(mae)}
    return mv


def _patch_client(production_versions=None, archived_versions=None):
    """Context that patches MlflowClient with controllable version lists."""

    class FakeClient:
        def get_latest_versions(self, name, stages):
            if "Production" in stages:
                return production_versions if production_versions is not None else []
            if "Archived" in stages:
                return archived_versions if archived_versions is not None else []
            return []

        def transition_model_version_stage(self, **kwargs):
            pass

        def set_model_version_tag(self, *args, **kwargs):
            pass

    return patch("ml.promotion.MlflowClient", return_value=FakeClient())


# ---------------------------------------------------------------------------
# 1. Candidate 1.9% better → REJECTED (under 2% threshold)
# ---------------------------------------------------------------------------


def test_reject_under_2pct():
    """1.9% improvement does not meet the strict < 2% gate."""
    production_mae = 100.0
    candidate_mae = 98.1  # threshold = 100 * 0.98 = 98.0 → 98.1 >= 98.0 → REJECTED

    with _patch_client([_mock_version("1", production_mae)]):
        result = evaluate_promotion("lgbm", "run-abc", candidate_mae)

    assert result.promoted is False
    assert "REJECTED" in result.reason
    assert result.production_mae == production_mae
    assert result.candidate_mae == candidate_mae


# ---------------------------------------------------------------------------
# 2. Candidate 2.5% better → PROMOTED
# ---------------------------------------------------------------------------


def test_promote_over_2pct():
    """2.5% improvement strictly clears the gate."""
    production_mae = 100.0
    candidate_mae = 97.5  # threshold = 98.0 → 97.5 < 98.0 → PROMOTED

    with _patch_client([_mock_version("1", production_mae)]):
        result = evaluate_promotion("lgbm", "run-def", candidate_mae)

    assert result.promoted is True
    assert "PROMOTED" in result.reason
    assert result.production_mae == production_mae


# ---------------------------------------------------------------------------
# 3. Candidate worse than production → REJECTED
# ---------------------------------------------------------------------------


def test_reject_when_worse():
    """A candidate with higher MAE than production is always rejected."""
    with _patch_client([_mock_version("1", 100.0)]):
        result = evaluate_promotion("lgbm", "run-xyz", 110.0)

    assert result.promoted is False
    assert "REJECTED" in result.reason
    assert "worse" in result.reason


# ---------------------------------------------------------------------------
# 4. Candidate equal to production → REJECTED
# ---------------------------------------------------------------------------


def test_reject_equal_mae():
    """Exact equality is not an improvement; must be rejected."""
    production_mae = 225.65
    with _patch_client([_mock_version("1", production_mae)]):
        result = evaluate_promotion("lgbm", "run-eq", production_mae)

    assert result.promoted is False
    assert "REJECTED" in result.reason
    assert "equal" in result.reason.lower()


# ---------------------------------------------------------------------------
# 5. Exactly 2.0% better → REJECTED (strictly < 0.98 × production required)
# ---------------------------------------------------------------------------


def test_reject_exactly_2pct():
    """Exactly 2% improvement hits the boundary: candidate == production * 0.98.
    The gate is strict less-than, so exactly 2% is rejected."""
    production_mae = 100.0
    candidate_mae = production_mae * _PROMOTION_THRESHOLD  # == 98.0, NOT < 98.0
    with _patch_client([_mock_version("1", production_mae)]):
        result = evaluate_promotion("lgbm", "run-boundary", candidate_mae)

    assert (
        result.promoted is False
    ), f"Exactly {_PROMOTION_THRESHOLD*100}% improvement should be REJECTED (strict <)"


# ---------------------------------------------------------------------------
# 6. No prior production version → always PROMOTE (first run)
# ---------------------------------------------------------------------------


def test_first_run_auto_promote():
    """When no production version exists, always promote the first candidate."""
    with _patch_client(production_versions=[]):
        result = evaluate_promotion("lgbm", "run-first", 200.0)

    assert result.promoted is True
    assert result.production_mae is None
    assert "first-run" in result.reason.lower() or "no prior" in result.reason.lower()


# ---------------------------------------------------------------------------
# 7. MLflow Registry unreachable → raise RuntimeError, do NOT promote
# ---------------------------------------------------------------------------


def test_registry_unreachable_raises():
    """If MlflowClient raises, the function raises RuntimeError."""

    class BrokenClient:
        def get_latest_versions(self, *args, **kwargs):
            raise ConnectionError("server down")

    with (
        patch("ml.promotion.MlflowClient", return_value=BrokenClient()),
        pytest.raises(RuntimeError, match="unreachable"),
    ):
        evaluate_promotion("lgbm", "run-offline", 100.0)


# ---------------------------------------------------------------------------
# 8. promote() transitions candidate to Production, archives prior
# ---------------------------------------------------------------------------


def test_promote_transitions_stages():
    """promote() calls transition_model_version_stage with correct args."""
    mock_client = MagicMock()
    with patch("ml.promotion.MlflowClient", return_value=mock_client):
        promote("lgbm", candidate_version=2)

    mock_client.transition_model_version_stage.assert_called_once_with(
        name="gold-rate-lgbm",
        version="2",
        stage="Production",
        archive_existing_versions=True,
    )


# ---------------------------------------------------------------------------
# 9. rollback() finds most-recent archived version
# ---------------------------------------------------------------------------


def test_rollback_picks_highest_archived_version():
    """rollback() without target_version promotes the highest-numbered archived version."""
    arch_v1 = _mock_version("1", stage="Archived")
    arch_v3 = _mock_version("3", stage="Archived")

    mock_client = MagicMock()
    mock_client.get_latest_versions.return_value = [arch_v1, arch_v3]

    with patch("ml.promotion.MlflowClient", return_value=mock_client):
        rollback("lgbm")

    mock_client.transition_model_version_stage.assert_called_once_with(
        name="gold-rate-lgbm",
        version="3",
        stage="Production",
        archive_existing_versions=True,
    )


def test_rollback_uses_specific_version_when_given():
    """rollback(target_version=1) promotes version 1 regardless of archived list."""
    mock_client = MagicMock()
    with patch("ml.promotion.MlflowClient", return_value=mock_client):
        rollback("lgbm", target_version=1)

    mock_client.transition_model_version_stage.assert_called_once_with(
        name="gold-rate-lgbm",
        version="1",
        stage="Production",
        archive_existing_versions=True,
    )


def test_rollback_raises_if_no_archived():
    """rollback() raises if there are no archived versions to revert to."""
    mock_client = MagicMock()
    mock_client.get_latest_versions.return_value = []

    with (
        patch("ml.promotion.MlflowClient", return_value=mock_client),
        pytest.raises(RuntimeError, match="No archived versions"),
    ):
        rollback("lgbm")


# ---------------------------------------------------------------------------
# 10. Sign-convention confirmation tests
# ---------------------------------------------------------------------------


def test_sign_convention_better_candidate_promoted():
    """Lower MAE = better; a 3% lower candidate must be promoted."""
    production_mae = 300.0
    candidate_mae = production_mae * 0.97  # 3% lower → should PROMOTE
    with _patch_client([_mock_version("1", production_mae)]):
        result = evaluate_promotion("nbeats", "run-sign", candidate_mae)
    assert result.promoted is True


def test_sign_convention_worse_candidate_rejected():
    """Higher MAE = worse; candidate 5% HIGHER must be rejected."""
    production_mae = 200.0
    candidate_mae = production_mae * 1.05  # 5% higher → worse, must REJECT
    with _patch_client([_mock_version("1", production_mae)]):
        result = evaluate_promotion("nbeats", "run-worse", candidate_mae)
    assert result.promoted is False
    assert "worse" in result.reason


# ---------------------------------------------------------------------------
# 11. Unknown model key raises ValueError
# ---------------------------------------------------------------------------


def test_unknown_model_key_raises():
    with pytest.raises(ValueError, match="Unknown model key"):
        evaluate_promotion("xgboost", "run-xyz", 100.0)
