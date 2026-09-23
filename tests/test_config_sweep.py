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
