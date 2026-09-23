"""Tests for ml.direction.config_sweep (M2 diagnosis, GG spec item 5)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from ml.direction.config_sweep import M1_DRIVER_COLS, augment_with_m1_drivers, run_config_sweep
from ml.direction.dataset import FEATURE_COLS


def _make_synthetic_dataset(n: int = 40, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        row: dict = {"as_of_date": f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"}
        for col in FEATURE_COLS:
            if col in (
                "dow",
                "dom",
                "month",
                "days_to_next_festival",
                "days_since_last_duty_change",
            ):
                row[col] = int(rng.integers(0, 7))
            elif col in ("is_festival_window", "duty_change_active"):
                row[col] = bool(rng.integers(0, 2))
            else:
                row[col] = float(rng.uniform(0.5, 2.0) * (i + 1))
        row["label_binary_h2"] = int(rng.integers(0, 2))
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# augment_with_m1_drivers
# ---------------------------------------------------------------------------


class TestAugmentWithM1Drivers:
    def test_adds_all_m1_driver_columns(self) -> None:
        dataset = _make_synthetic_dataset()
        india_vix = pd.Series(
            12.0, index=pd.date_range("2024-12-01", "2026-01-01", freq="D", tz="UTC")
        )
        out = augment_with_m1_drivers(dataset, india_vix=india_vix)
        for col in M1_DRIVER_COLS:
            assert col in out.columns

    def test_india_vix_value_matches_injected_series(self) -> None:
        dataset = _make_synthetic_dataset(n=5)
        idx = pd.date_range("2024-12-01", "2026-01-01", freq="D", tz="UTC")
        india_vix = pd.Series(np.arange(len(idx)) + 10.0, index=idx)
        out = augment_with_m1_drivers(dataset, india_vix=india_vix)
        row0_ts = pd.Timestamp(out.iloc[0]["as_of_date"], tz="UTC")
        expected = india_vix.loc[row0_ts]
        assert out.iloc[0]["india_vix"] == expected

    def test_india_vix_prior_day_uses_previous_close(self) -> None:
        # Amendment A2: a row must not see its own day's VIX close.
        dataset = _make_synthetic_dataset(n=5)
        idx = pd.date_range("2024-12-01", "2026-01-01", freq="D", tz="UTC")
        india_vix = pd.Series(np.arange(len(idx)) + 10.0, index=idx)
        out = augment_with_m1_drivers(dataset, india_vix=india_vix, india_vix_prior_day=True)
        for i in range(len(out)):
            ts = pd.Timestamp(out.iloc[i]["as_of_date"], tz="UTC")
            assert out.iloc[i]["india_vix"] == india_vix.loc[ts - pd.Timedelta(days=1)]
            assert out.iloc[i]["india_vix"] != india_vix.loc[ts]

    def test_does_not_mutate_original_dataset(self) -> None:
        dataset = _make_synthetic_dataset(n=5)
        india_vix = pd.Series(
            12.0, index=pd.date_range("2024-12-01", "2026-01-01", freq="D", tz="UTC")
        )
        augment_with_m1_drivers(dataset, india_vix=india_vix)
        assert "india_vix" not in dataset.columns


# ---------------------------------------------------------------------------
# run_config_sweep
# ---------------------------------------------------------------------------


class TestRunConfigSweep:
    def test_logistic_config_returns_expected_keys(self) -> None:
        dataset = _make_synthetic_dataset()
        result = run_config_sweep(
            dataset, FEATURE_COLS, "label_binary_h2", model="logistic", min_train_size=20
        )
        for key in (
            "n",
            "accuracy",
            "brier",
            "always_up_accuracy",
            "p_value",
            "significant_at_05",
            "ece",
        ):
            assert key in result

    def test_class_weight_balanced_runs_without_error(self) -> None:
        dataset = _make_synthetic_dataset()
        result = run_config_sweep(
            dataset,
            FEATURE_COLS,
            "label_binary_h2",
            model="logistic",
            class_weight="balanced",
            min_train_size=20,
        )
        assert result["n"] > 0

    def test_gbm_uncalibrated_vs_calibrated_both_run(self) -> None:
        dataset = _make_synthetic_dataset()
        uncal = run_config_sweep(
            dataset, FEATURE_COLS, "label_binary_h2", model="gbm", min_train_size=20
        )
        cal = run_config_sweep(
            dataset,
            FEATURE_COLS,
            "label_binary_h2",
            model="gbm",
            calibrate_gbm=True,
            min_train_size=20,
        )
        assert uncal["n"] == cal["n"]

    def test_higher_C_is_lighter_regularization_runs(self) -> None:
        dataset = _make_synthetic_dataset()
        result = run_config_sweep(
            dataset, FEATURE_COLS, "label_binary_h2", model="logistic", C=100.0, min_train_size=20
        )
        assert result["n"] > 0

    def test_unknown_model_raises(self) -> None:
        dataset = _make_synthetic_dataset()
        try:
            run_config_sweep(dataset, FEATURE_COLS, "label_binary_h2", model="nonsense")
            raised = False
        except ValueError:
            raised = True
        assert raised


# ---------------------------------------------------------------------------
# Embargo + score-after cutoff (ADR 038 amendment A1)
# ---------------------------------------------------------------------------


def _with_h2_label_dates(dataset: pd.DataFrame) -> pd.DataFrame:
    """label_date_h2 = the as_of_date two rows later, as in build_dataset."""
    out = dataset.copy()
    dates = out["as_of_date"].tolist()
    out["label_date_h2"] = [dates[i + 2] if i + 2 < len(dates) else None for i in range(len(dates))]
    return out.iloc[:-2].reset_index(drop=True)


class TestEmbargo:
    def test_embargo_trains_only_on_matured_labels(self) -> None:
        dataset = _with_h2_label_dates(_make_synthetic_dataset(n=45))
        result = run_config_sweep(
            dataset,
            FEATURE_COLS,
            "label_binary_h2",
            min_train_size=20,
            return_raw=True,
            embargo_label_date_col="label_date_h2",
        )
        raw = result["raw"]
        assert result["n"] > 0
        for as_of, max_label in zip(raw["as_of_date"], raw["train_max_label_date"], strict=True):
            assert max_label < as_of

    def test_without_embargo_the_last_training_labels_are_unmatured(self) -> None:
        # The leak the embargo closes: the label of row i-1 matures after row i.
        dataset = _with_h2_label_dates(_make_synthetic_dataset(n=45))
        dates = dataset["as_of_date"].tolist()
        labels = dataset["label_date_h2"].tolist()
        assert all(labels[i - 1] > dates[i] for i in range(1, len(dates)))

    def test_embargo_changes_predictions_vs_leaky_protocol(self) -> None:
        dataset = _with_h2_label_dates(_make_synthetic_dataset(n=45))
        leaky = run_config_sweep(
            dataset, FEATURE_COLS, "label_binary_h2", min_train_size=20, return_raw=True
        )
        embargoed = run_config_sweep(
            dataset,
            FEATURE_COLS,
            "label_binary_h2",
            min_train_size=20,
            return_raw=True,
            embargo_label_date_col="label_date_h2",
        )
        assert leaky["raw"]["y_prob"] != embargoed["raw"]["y_prob"]

    def test_score_after_as_of_scores_only_later_days(self) -> None:
        dataset = _with_h2_label_dates(_make_synthetic_dataset(n=45))
        cutoff = dataset["as_of_date"].iloc[34]
        result = run_config_sweep(
            dataset,
            FEATURE_COLS,
            "label_binary_h2",
            min_train_size=20,
            return_raw=True,
            embargo_label_date_col="label_date_h2",
            score_after_as_of=cutoff,
        )
        scored = result["raw"]["as_of_date"]
        assert scored
        assert all(d > cutoff for d in scored)
        assert len(scored) == len(dataset) - 35

    def test_score_after_last_day_scores_nothing(self) -> None:
        dataset = _with_h2_label_dates(_make_synthetic_dataset(n=45))
        result = run_config_sweep(
            dataset,
            FEATURE_COLS,
            "label_binary_h2",
            min_train_size=20,
            return_raw=True,
            score_after_as_of=dataset["as_of_date"].iloc[-1],
        )
        assert result["raw"]["y_true"] == []
