"""Tests for ml.direction.preregistration (GG spec item 4 / ADR 038, v2 = ADR 042)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from ml.direction.dataset import FEATURE_COLS
from ml.direction.preregistration import (
    CONFIRMATORY_AFTER_AS_OF,
    EMBARGO_LABEL_DATE_COL,
    PREREGISTERED_CONFIG,
    PREREGISTERED_N_FOR_POWER,
    PREREGISTERED_N_FOR_POWER_V1,
    PROTOCOL_VERSION,
    REQUIRE_CONSECUTIVE_LABELS,
    _always_up_loss,
    _misclassification_loss,
    append_shadow_result,
    run_live_arm,
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
        assert pytest.approx(144.1673, abs=1e-4) == PREREGISTERED_N_FOR_POWER_V1

    def test_v2_power_target_never_below_v1(self) -> None:
        # GG decision G1/G2: the v2 target may rise with the clean effect size, never fall.
        from ml.direction.preregistration import REFERENCE

        assert PREREGISTERED_N_FOR_POWER >= PREREGISTERED_N_FOR_POWER_V1
        expected = max(PREREGISTERED_N_FOR_POWER_V1, REFERENCE["n_for_power"])
        assert expected == PREREGISTERED_N_FOR_POWER


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

        result2 = {
            "arm": "live_h2",
            "n": 1200,
            "effective_n": PREREGISTERED_N_FOR_POWER + 1.0,
            "p_value": 0.001,
        }
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


def _dataset_straddling_registration(n: int = 60) -> pd.DataFrame:
    """Daily rows from 2026-08-01, so some as_of_dates fall after the
    registration date; label_date_h2 = the as_of_date two rows later."""
    rng = np.random.default_rng(42)
    dates = pd.date_range("2026-08-01", periods=n, freq="D").strftime("%Y-%m-%d").tolist()
    rows = []
    for i, as_of in enumerate(dates):
        row: dict = {"as_of_date": as_of, "label_date_h2": dates[i + 2] if i + 2 < n else None}
        for col in FEATURE_COLS:
            row[col] = float(rng.uniform(0.5, 2.0))
        row["label_binary_h2"] = int(rng.integers(0, 2))
        rows.append(row)
    return pd.DataFrame(rows).iloc[:-2].reset_index(drop=True)


class TestAmendmentA1:
    """ADR 038 amendment A1: embargo >= h and post-registration days only."""

    def test_amendment_constants(self) -> None:
        from ml.direction.preregistration import (
            CONFIRMATORY_AFTER_AS_OF_V1,
            PROTOCOL_VERSION_V1,
        )

        assert CONFIRMATORY_AFTER_AS_OF_V1 == "2026-09-23"
        assert PROTOCOL_VERSION_V1 == "adr038-A2"
        assert EMBARGO_LABEL_DATE_COL == "label_date_h2"

    def test_v2_constants(self) -> None:
        """ADR 042: v2 supersedes v1 -- later registration date, clean labels."""
        assert CONFIRMATORY_AFTER_AS_OF == "2026-09-24"
        assert PROTOCOL_VERSION == "adr042-v2"
        assert REQUIRE_CONSECUTIVE_LABELS is True

    def test_live_arm_builds_consecutive_day_labels(self, monkeypatch) -> None:
        import ml.direction.preregistration as pr

        seen = {}

        def fake_build(**kwargs):
            seen.update(kwargs)
            return _dataset_straddling_registration()

        monkeypatch.setattr(pr, "build_dataset", fake_build)
        result = pr.run_live_arm()
        assert seen == {"require_consecutive": True}
        assert result["consecutive_day_labels"] is True

    def test_live_arm_scores_only_post_registration_days_with_embargo(self) -> None:
        dataset = _dataset_straddling_registration()
        result = run_live_arm(dataset)
        dates = result["scored_as_of_dates"]
        expected = int((dataset["as_of_date"] > CONFIRMATORY_AFTER_AS_OF).sum())
        assert expected > 0
        assert result["n"] == len(dates) == expected
        assert all(d > CONFIRMATORY_AFTER_AS_OF for d in dates)
        for as_of, max_label in zip(dates, result["train_max_label_dates"], strict=True):
            assert max_label < as_of
        assert result["protocol_version"] == PROTOCOL_VERSION

    def test_live_arm_with_no_post_registration_days_scores_zero(self) -> None:
        dataset = _dataset_straddling_registration()
        dataset = dataset[dataset["as_of_date"] <= CONFIRMATORY_AFTER_AS_OF]
        result = run_live_arm(dataset)
        assert result["n"] == 0
        assert result["p_value"] is None
        assert result["significant_at_05"] is False
        # Must serialise as valid JSON (no bare NaN token) for the shadow log.
        json.loads(json.dumps(result, allow_nan=False))

    def test_append_handles_no_effective_n_yet(self, tmp_path) -> None:
        path = tmp_path / "shadow.json"
        append_shadow_result({"arm": "live_h2", "n": 1, "effective_n": None}, path=path)
        history = json.loads(path.read_text(encoding="utf-8"))
        assert history["runs"][0]["reached_preregistered_n"] is False


class TestAmendmentA2:
    """ADR 038 amendment A2: prior-day VIX in the proxy arm; reproducible reference."""

    def test_proxy_arm_requests_prior_day_vix(self, tmp_path, monkeypatch) -> None:
        import ml.direction.config_sweep as cs
        import ml.direction.preregistration as pr

        seen = {}

        def fake_augment(df, india_vix=None, india_vix_prior_day=False):
            seen["prior_day"] = india_vix_prior_day
            return df

        monkeypatch.setattr(cs, "augment_with_m1_drivers", fake_augment)
        labels = pd.DataFrame(
            {"label_22k_per_10g": [70000.0, 72000.0, 69000.0]},
            index=pd.date_range("2026-01-01", periods=3, freq="D"),
        )
        path = tmp_path / "labels.parquet"
        labels.to_parquet(path)
        pr.build_proxy_deadzone_dataset(label_path=path)
        assert seen["prior_day"] is True

    def test_reference_is_self_consistent(self) -> None:
        import math

        from ml.direction.preregistration import REFERENCE, REFERENCE_V1
        from ml.direction.stats_corrections import n_for_power

        assert REFERENCE_V1["n"] == 161
        assert REFERENCE["n"] == 146
        for ref in (REFERENCE_V1, REFERENCE):
            assert pytest.approx(ref["n_for_power"], abs=1e-3) == n_for_power(
                ref["mean_diff"], math.sqrt(ref["long_run_var"])
            )
            assert len(ref["fold_digest"]) == 64
            # the effect the power target is built on must favour the model
            assert ref["mean_diff"] < 0
