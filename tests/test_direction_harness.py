"""Tests for ml.direction dataset building and walk-forward harness.

All tests are deterministic and require no network access.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
from ml.direction.dataset import (
    DEAD_BAND_PER_GRAM,
    FEATURE_COLS,
    build_dataset,
    make_label,
)
from ml.direction.evaluate import (
    MIN_TRAIN_SIZE,
    append_history,
    compute_calibration,
    run_walk_forward,
)

# ---------------------------------------------------------------------------
# make_label
# ---------------------------------------------------------------------------


class TestMakeLabel:
    """Tests for the make_label label-generation function."""

    def test_clear_up(self) -> None:
        """Delta well above dead band → 'up', binary 1."""
        ternary, binary = make_label(70000.0, 71000.0, DEAD_BAND_PER_GRAM)
        # delta_per_gram = (71000 - 70000) / 10 = 100 >> 50
        assert ternary == "up"
        assert binary == 1

    def test_clear_down(self) -> None:
        """Delta well below dead band → 'down', binary 0."""
        ternary, binary = make_label(70000.0, 69000.0, DEAD_BAND_PER_GRAM)
        # delta_per_gram = -100 << -50
        assert ternary == "down"
        assert binary == 0

    def test_flat_within_dead_band(self) -> None:
        """Delta within dead band → 'flat'."""
        # delta_per_gram = (70200 - 70000) / 10 = 20, inside 50 band
        ternary, binary = make_label(70000.0, 70200.0, DEAD_BAND_PER_GRAM)
        assert ternary == "flat"
        # Still up in absolute terms → binary == 1
        assert binary == 1

    def test_flat_down_within_dead_band(self) -> None:
        """Flat down (within band but negative) → 'flat', binary 0."""
        ternary, binary = make_label(70000.0, 69800.0, DEAD_BAND_PER_GRAM)
        assert ternary == "flat"
        assert binary == 0

    def test_exact_dead_band_boundary_not_up(self) -> None:
        """Delta == dead band exactly is NOT 'up' (strict inequality)."""
        ternary, _binary = make_label(70000.0, 70500.0, DEAD_BAND_PER_GRAM)
        # delta_per_gram exactly 50 → not > 50
        assert ternary == "flat"

    def test_exact_dead_band_boundary_not_down(self) -> None:
        """Delta == -dead_band exactly is NOT 'down'."""
        ternary, _binary = make_label(70000.0, 69500.0, DEAD_BAND_PER_GRAM)
        # delta_per_gram exactly -50 → not < -50
        assert ternary == "flat"

    def test_binary_only_reflects_strict_greater(self) -> None:
        """Binary label is 1 iff next > current (strict), regardless of dead band."""
        _, binary_eq = make_label(70000.0, 70000.0, DEAD_BAND_PER_GRAM)
        _, binary_up = make_label(70000.0, 70001.0, DEAD_BAND_PER_GRAM)
        _, binary_down = make_label(70000.0, 69999.0, DEAD_BAND_PER_GRAM)
        assert binary_eq == 0
        assert binary_up == 1
        assert binary_down == 0

    def test_custom_dead_band(self) -> None:
        """Custom dead band of 0 turns everything into up or down."""
        ternary, _ = make_label(70000.0, 70001.0, 0.0)
        assert ternary == "up"
        ternary2, _ = make_label(70000.0, 69999.0, 0.0)
        assert ternary2 == "down"


# ---------------------------------------------------------------------------
# build_dataset — injected DataFrames (no parquet I/O)
# ---------------------------------------------------------------------------


def _make_snapshots(as_of_dates: list[str], ibja_pm_916: list[float]) -> pd.DataFrame:
    """Helper: build a minimal snapshots DataFrame."""
    rows = []
    for i, (aod, pm916) in enumerate(zip(as_of_dates, ibja_pm_916, strict=True)):
        row: dict = {
            "as_of_date": aod,
            "ibja_pm_916_asof_date": aod,  # fresh by default
            "ibja_pm_916": pm916,
            "n_macro_null": 0,
        }
        for col in FEATURE_COLS:
            if col == "ibja_pm_916":
                row[col] = pm916
            elif col in (
                "dow",
                "dom",
                "month",
                "days_to_next_festival",
                "days_since_last_duty_change",
            ):
                row[col] = i % 7
            elif col in ("is_festival_window", "duty_change_active"):
                row[col] = False
            else:
                row[col] = float(i + 1)
        rows.append(row)
    return pd.DataFrame(rows)


def _make_ibja(dates: list[str], pm_916_vals: list[float]) -> pd.DataFrame:
    """Helper: build a minimal ibja_rates DataFrame."""
    return pd.DataFrame({"date": dates, "pm_916": pm_916_vals})


class TestBuildDataset:
    """Tests for build_dataset with injected DataFrames."""

    def test_label_comes_from_next_ibja_day(self) -> None:
        """Label for snapshot t must come from IBJA day t+1, not day t."""
        snap_dates = ["2025-01-01", "2025-01-02", "2025-01-03"]
        snap_pm916 = [70000.0, 71000.0, 72000.0]
        snaps_df = _make_snapshots(snap_dates, snap_pm916)

        ibja_dates = ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"]
        ibja_pm916 = [70000.0, 71000.0, 72000.0, 74000.0]
        ibja_df = _make_ibja(ibja_dates, ibja_pm916)

        ds = build_dataset(snapshots_df=snaps_df, ibja_df=ibja_df)

        # snapshot 2025-01-01 → current=70000, label from IBJA 2025-01-02=71000
        row0 = ds[ds["as_of_date"] == "2025-01-01"].iloc[0]
        assert row0["current_pm916"] == 70000.0
        assert row0["next_pm916"] == 71000.0
        assert row0["label_date"] == "2025-01-02"

        # snapshot 2025-01-03 → next IBJA is 2025-01-04=74000
        row2 = ds[ds["as_of_date"] == "2025-01-03"].iloc[0]
        assert row2["next_pm916"] == 74000.0

    def test_stale_ibja_row_excluded(self) -> None:
        """A snapshot whose ibja_pm_916_asof_date < as_of_date must be excluded."""
        snap_dates = ["2025-01-01", "2025-01-02", "2025-01-03"]
        snap_pm916 = [70000.0, 71000.0, 72000.0]
        snaps_df = _make_snapshots(snap_dates, snap_pm916)

        # Make row 1 stale (asof_date is day before snapshot)
        snaps_df.at[1, "ibja_pm_916_asof_date"] = "2025-01-01"

        ibja_df = _make_ibja(
            ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"],
            [70000.0, 71000.0, 72000.0, 73000.0],
        )
        ds = build_dataset(snapshots_df=snaps_df, ibja_df=ibja_df)
        # 2025-01-02 should NOT appear in the dataset
        assert "2025-01-02" not in ds["as_of_date"].values

    def test_high_macro_null_excluded(self) -> None:
        """Rows with n_macro_null > max_n_macro_null must be excluded."""
        snap_dates = ["2025-01-01", "2025-01-02", "2025-01-03"]
        snap_pm916 = [70000.0, 71000.0, 72000.0]
        snaps_df = _make_snapshots(snap_dates, snap_pm916)

        # Set row 1 to have 4 macro nulls (> default max of 3)
        snaps_df.at[1, "n_macro_null"] = 4

        ibja_df = _make_ibja(
            ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"],
            [70000.0, 71000.0, 72000.0, 73000.0],
        )
        ds = build_dataset(snapshots_df=snaps_df, ibja_df=ibja_df)
        assert "2025-01-02" not in ds["as_of_date"].values

    def test_last_snapshot_excluded_no_next_ibja(self) -> None:
        """The last snapshot has no next IBJA entry and must be dropped."""
        snap_dates = ["2025-01-01", "2025-01-02"]
        snap_pm916 = [70000.0, 71000.0]
        snaps_df = _make_snapshots(snap_dates, snap_pm916)

        # IBJA only goes up to 2025-01-02 — no entry after the last snapshot
        ibja_df = _make_ibja(["2025-01-01", "2025-01-02"], [70000.0, 71000.0])
        ds = build_dataset(snapshots_df=snaps_df, ibja_df=ibja_df)

        # Only first row should remain (has next IBJA at 2025-01-02)
        assert len(ds) == 1
        assert ds.iloc[0]["as_of_date"] == "2025-01-01"

    def test_output_columns_present(self) -> None:
        """Dataset must contain all required output columns."""
        snaps_df = _make_snapshots(["2025-01-01", "2025-01-02"], [70000.0, 71000.0])
        ibja_df = _make_ibja(
            ["2025-01-01", "2025-01-02", "2025-01-03"],
            [70000.0, 71000.0, 72000.0],
        )
        ds = build_dataset(snapshots_df=snaps_df, ibja_df=ibja_df)
        required = {
            "as_of_date",
            "current_pm916",
            "next_pm916",
            "delta_per_gram",
            "label_ternary",
            "label_binary",
            "label_date",
            "ibja_pm_916_asof_date",
            "n_macro_null",
        }
        assert required.issubset(set(ds.columns))

    def test_delta_per_gram_correct(self) -> None:
        """delta_per_gram should equal (next - current) / 10."""
        snaps_df = _make_snapshots(["2025-01-01"], [70000.0])
        ibja_df = _make_ibja(["2025-01-01", "2025-01-02"], [70000.0, 71000.0])
        ds = build_dataset(snapshots_df=snaps_df, ibja_df=ibja_df)
        expected_delta = (71000.0 - 70000.0) / 10.0
        assert abs(ds.iloc[0]["delta_per_gram"] - expected_delta) < 1e-9

    def test_h1_h2_labels_leak_free_and_correct(self) -> None:
        """h1 label = next IBJA day, h2 = 2nd-next; both strictly after as_of_date."""
        snaps_df = _make_snapshots(["2025-01-01"], [70000.0])
        ibja_df = _make_ibja(
            ["2025-01-01", "2025-01-02", "2025-01-03"],
            [70000.0, 71000.0, 69000.0],
        )
        ds = build_dataset(snapshots_df=snaps_df, ibja_df=ibja_df)
        row = ds.iloc[0]
        # h1: 70000 -> 71000 (up); h2: 70000 -> 69000 (down)
        assert row["label_binary_h1"] == 1
        assert row["label_date_h1"] == "2025-01-02"
        assert row["label_binary_h2"] == 0
        assert row["label_date_h2"] == "2025-01-03"
        # unsuffixed columns mirror h1
        assert row["label_binary"] == row["label_binary_h1"]
        # leak-free: both label dates strictly after as_of_date
        assert row["label_date_h1"] > row["as_of_date"]
        assert row["label_date_h2"] > row["label_date_h1"]

    def test_h2_absent_when_no_second_next_ibja(self) -> None:
        """When only one future IBJA exists, h2 is None but the row is still kept (h1)."""
        snaps_df = _make_snapshots(["2025-01-01"], [70000.0])
        ibja_df = _make_ibja(["2025-01-01", "2025-01-02"], [70000.0, 71000.0])
        ds = build_dataset(snapshots_df=snaps_df, ibja_df=ibja_df)
        assert len(ds) == 1
        assert ds.iloc[0]["label_binary_h1"] == 1
        assert ds.iloc[0]["label_binary_h2"] is None or pd.isna(ds.iloc[0]["label_binary_h2"])


# ---------------------------------------------------------------------------
# run_walk_forward smoke test
# ---------------------------------------------------------------------------


def _make_synthetic_dataset(n: int = 35, seed: int = 42) -> pd.DataFrame:
    """Build a synthetic 2-class dataset with FEATURE_COLS and labels."""
    rng = np.random.default_rng(seed)
    rows = []
    base_price = 70000.0
    for i in range(n):
        price = base_price + float(rng.integers(-500, 500))
        next_price = price + float(rng.integers(-600, 600))
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
        next2_price = next_price + float(rng.integers(-600, 600))
        row["current_pm916"] = price
        row["next_pm916"] = next_price
        row["delta_per_gram"] = (next_price - price) / 10.0
        row["label_binary"] = int(next_price > price)
        row["label_ternary"] = "up" if row["label_binary"] else "down"
        row["label_date"] = f"2025-{((i + 1) // 28) + 1:02d}-{((i + 1) % 28) + 1:02d}"
        # Explicit per-horizon labels (h1 mirrors the unsuffixed columns).
        row["label_binary_h1"] = row["label_binary"]
        row["label_binary_h2"] = int(next2_price > price)
        row["ibja_pm_916_asof_date"] = row["as_of_date"]
        row["n_macro_null"] = 0
        rows.append(row)
    return pd.DataFrame(rows)


class TestRunWalkForward:
    """Smoke tests for run_walk_forward."""

    def test_returns_expected_keys(self) -> None:
        """run_walk_forward must return all expected top-level keys."""
        ds = _make_synthetic_dataset(n=35, seed=42)
        result = run_walk_forward(ds, min_train_size=MIN_TRAIN_SIZE)

        required_keys = {
            "n_test_folds",
            "n_skipped_folds",
            "min_train_size",
            "always_up_baseline_accuracy",
            "logistic_metrics",
            "lightgbm_metrics",
            "persistence_metrics",
            "significance_vs_always_up",
            "feature_importances",
            "as_of_date_range",
            "generated_at_utc",
        }
        assert required_keys.issubset(result.keys())

    def test_n_test_folds_positive(self) -> None:
        """At least one test fold must be produced with 35 rows."""
        ds = _make_synthetic_dataset(n=35, seed=42)
        result = run_walk_forward(ds, min_train_size=MIN_TRAIN_SIZE)
        assert result["n_test_folds"] > 0

    def test_logistic_metrics_have_required_keys(self) -> None:
        """logistic_metrics must contain standard metric keys."""
        ds = _make_synthetic_dataset(n=35, seed=42)
        result = run_walk_forward(ds, min_train_size=MIN_TRAIN_SIZE)
        metric_keys = {
            "model",
            "n",
            "accuracy",
            "brier",
            "log_loss",
            "always_up_accuracy",
            "always_up_brier",
            "p_value",
            "significant_at_05",
            "n_discordant",
        }
        assert metric_keys.issubset(result["logistic_metrics"].keys())

    def test_accuracy_in_valid_range(self) -> None:
        """Model accuracy must be a valid probability in [0, 1]."""
        ds = _make_synthetic_dataset(n=35, seed=42)
        result = run_walk_forward(ds, min_train_size=MIN_TRAIN_SIZE)
        acc = result["logistic_metrics"]["accuracy"]
        assert 0.0 <= acc <= 1.0

    def test_always_up_accuracy_equals_mean_label(self) -> None:
        """always_up_baseline_accuracy must equal mean of label_binary OOS."""
        ds = _make_synthetic_dataset(n=35, seed=42)
        result = run_walk_forward(ds, min_train_size=MIN_TRAIN_SIZE)
        # always_up_accuracy in logistic_metrics and always_up_baseline_accuracy
        # should match (both computed from same y_true_all)
        assert (
            abs(
                result["always_up_baseline_accuracy"]
                - result["logistic_metrics"]["always_up_accuracy"]
            )
            < 1e-9
        )

    def test_feature_importances_have_feature_names(self) -> None:
        """feature_importances should map to FEATURE_COLS names."""
        ds = _make_synthetic_dataset(n=35, seed=42)
        result = run_walk_forward(ds, min_train_size=MIN_TRAIN_SIZE)
        fi = result["feature_importances"]["logistic"]
        if fi:  # may be empty if no folds produced importances
            assert all(k in FEATURE_COLS for k in fi)

    def test_deterministic_with_seed_42(self) -> None:
        """Two runs on the same data must produce identical OOS accuracy."""
        ds = _make_synthetic_dataset(n=35, seed=42)
        r1 = run_walk_forward(ds, min_train_size=MIN_TRAIN_SIZE)
        r2 = run_walk_forward(ds, min_train_size=MIN_TRAIN_SIZE)
        assert r1["logistic_metrics"]["accuracy"] == r2["logistic_metrics"]["accuracy"]

    def test_logistic_metrics_have_ece_and_reliability(self) -> None:
        """Calibration is the primary metric — ece + reliability must be present."""
        ds = _make_synthetic_dataset(n=35, seed=42)
        result = run_walk_forward(ds, min_train_size=MIN_TRAIN_SIZE)
        lm = result["logistic_metrics"]
        assert "ece" in lm and 0.0 <= lm["ece"] <= 1.0
        assert isinstance(lm["reliability"], list)

    def test_h2_horizon_runs_on_h2_labels(self) -> None:
        """run_walk_forward(label_col='label_binary_h2') evaluates the 2-day horizon."""
        ds = _make_synthetic_dataset(n=35, seed=42)
        result = run_walk_forward(ds, min_train_size=MIN_TRAIN_SIZE, label_col="label_binary_h2")
        assert result["n_test_folds"] > 0
        assert 0.0 <= result["logistic_metrics"]["accuracy"] <= 1.0


class TestCalibration:
    """compute_calibration — ECE + reliability bins."""

    def test_perfectly_calibrated_low_ece(self) -> None:
        # Predictions exactly match outcome frequency within each bin → ECE ~ 0.
        y_prob = [0.0, 0.0, 1.0, 1.0] * 10
        y_true = [0, 0, 1, 1] * 10
        ece, bins = compute_calibration(y_true, y_prob)
        assert ece < 1e-9
        assert all(b["count"] > 0 for b in bins)

    def test_miscalibrated_high_ece(self) -> None:
        # Always predicts 0.9 but the truth is 0 → large gap.
        y_prob = [0.9] * 20
        y_true = [0] * 20
        ece, _ = compute_calibration(y_true, y_prob)
        assert ece > 0.8

    def test_empty_input(self) -> None:
        ece, bins = compute_calibration([], [])
        assert ece == 0.0
        assert bins == []


class TestAppendHistory:
    """append_history writes one compact, parseable per-run record (all horizons)."""

    @staticmethod
    def _nested_result() -> dict:
        def _wf(n: int, base: float, acc: float, ece: float) -> dict:
            return {
                "n_test_folds": n,
                "always_up_baseline_accuracy": base,
                "logistic_metrics": {
                    "accuracy": acc,
                    "brier": 0.26,
                    "ece": ece,
                    "p_value": 0.42,
                    "significant_at_05": False,
                },
                "probability_gate": {"ship": False, "basis": "base_rate_fallback"},
                "timing_gate": {"ship": False, "basis": "hold_dark"},
            }

        return {
            "generated_at_utc": "2026-06-13T00:00:00+00:00",
            "as_of_date_range": "2025-01-09 to 2026-06-05",
            "horizons": {
                "h1": _wf(93, 0.5376, 0.4946, 0.1208),
                "h2": _wf(92, 0.6196, 0.6087, 0.0864),
            },
        }

    def test_appends_one_record_per_call(self, tmp_path) -> None:
        path = tmp_path / "history.jsonl"
        result = self._nested_result()
        append_history(result, path=path)
        append_history(result, path=path)
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2, "each call appends exactly one record"
        rec = json.loads(lines[0])
        assert rec["h1_n_folds"] == 93
        assert rec["h2_n_folds"] == 92
        assert rec["h1_logistic_acc"] == 0.4946
        assert rec["h2_logistic_ece"] == 0.0864
        assert rec["h1_prob_ship"] is False
        assert rec["h2_timing_ship"] is False
