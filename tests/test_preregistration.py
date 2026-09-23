"""Tests for ml.direction.preregistration (GG spec item 4 / ADR 038)."""

from __future__ import annotations

import json

import numpy as np
import pytest
from ml.direction.preregistration import (
    PREREGISTERED_CONFIG,
    PREREGISTERED_N_FOR_POWER,
    _always_up_loss,
    _misclassification_loss,
    append_shadow_result,
    score_config,
)


class TestFrozenConfig:
    def test_config_matches_adr_034_config_j(self) -> None:
        assert PREREGISTERED_CONFIG["model"] == "gbm"
        assert PREREGISTERED_CONFIG["class_weight"] == "balanced"
        assert PREREGISTERED_CONFIG["calibrate_gbm"] is True
        assert PREREGISTERED_CONFIG["label_col"] == "label_binary_h2"
        assert PREREGISTERED_CONFIG["horizon"] == 2

    def test_n_for_power_is_frozen_positive_number(self) -> None:
        assert pytest.approx(135.9, abs=0.1) == PREREGISTERED_N_FOR_POWER


class TestLossFunctions:
    def test_misclassification_loss_at_threshold(self) -> None:
        y_true = [1, 0, 1, 0]
        y_prob = [0.6, 0.4, 0.3, 0.9]  # correct, correct, wrong, wrong
        loss = _misclassification_loss(y_true, y_prob)
        assert loss == [0.0, 0.0, 1.0, 1.0]

    def test_always_up_loss_wrong_exactly_when_down(self) -> None:
        y_true = [1, 0, 1, 0, 0]
        loss = _always_up_loss(y_true)
        assert loss == [0.0, 1.0, 0.0, 1.0, 1.0]


class TestScoreConfig:
    def test_model_better_than_baseline_gives_low_one_sided_p(self) -> None:
        rng = np.random.default_rng(0)
        n = 100
        y_true = list(rng.integers(0, 2, n))
        # model predicts near-perfectly; always-up baseline is wrong ~50% of the time
        y_prob = [0.95 if t == 1 else 0.05 for t in y_true]
        result = score_config(y_true, y_prob, horizon=2)
        assert result["p_value"] < 0.05
        assert result["significant_at_05"] is True
        assert result["accuracy"] > result["always_up_accuracy"]

    def test_model_no_better_than_baseline_not_significant(self) -> None:
        rng = np.random.default_rng(1)
        n = 100
        y_true = list(rng.integers(0, 2, n))
        y_prob = [1.0] * n  # identical to always-up
        result = score_config(y_true, y_prob, horizon=2)
        assert result["mean_diff"] == pytest.approx(0.0)
        assert result["significant_at_05"] is False

    def test_returns_positive_finite_effective_n(self) -> None:
        # effective_n = n * gamma_0/long_run_var can exceed n under negative
        # autocorrelation (see evaluate_reframed's own DM tests for the
        # positive-autocorrelation-shrinks-it case) -- this just checks the
        # value is a sane positive, finite number, not bounded by n.
        rng = np.random.default_rng(2)
        n = 80
        y_true = list(rng.integers(0, 2, n))
        y_prob = list(rng.uniform(0, 1, n))
        result = score_config(y_true, y_prob, horizon=2)
        assert 0 < result["effective_n"] < float("inf")


class TestAppendShadowResult:
    def test_appends_and_persists(self, tmp_path) -> None:
        path = tmp_path / "shadow.json"
        result1 = {"arm": "live_h2", "n": 161, "effective_n": 119.38, "p_value": 0.0034}
        append_shadow_result(result1, path=path)
        history = json.loads(path.read_text(encoding="utf-8"))
        assert len(history["runs"]) == 1
        assert history["runs"][0]["arm"] == "live_h2"
        assert history["runs"][0]["reached_preregistered_n"] is False

        result2 = {"arm": "live_h2", "n": 200, "effective_n": 140.0, "p_value": 0.001}
        append_shadow_result(result2, path=path)
        history = json.loads(path.read_text(encoding="utf-8"))
        assert len(history["runs"]) == 2
        assert history["runs"][1]["reached_preregistered_n"] is True

    def test_proxy_arm_never_gets_h2_reached_flag(self, tmp_path) -> None:
        path = tmp_path / "shadow.json"
        result = {
            "arm": "proxy_deadzone_h1_equivalent",
            "n": 329,
            "effective_n": 329.0,
            "p_value": 0.98,
        }
        append_shadow_result(result, path=path)
        history = json.loads(path.read_text(encoding="utf-8"))
        assert history["runs"][0]["reached_preregistered_n"] is None
