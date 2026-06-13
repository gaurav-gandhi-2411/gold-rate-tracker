"""ml.direction.evaluate — walk-forward OOS evaluation harness.

Runs an expanding-window walk-forward evaluation to determine whether a
directional classifier beats the "always-up" base-rate baseline on real
gold price data.  Writes results to data/direction_baseline.json.

Usage:
    python -m ml.direction.evaluate
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ml.direction.dataset import (
    DATA_DIR,
    FEATURE_COLS,
    build_dataset,
)
from ml.direction.models import (
    fit_lightgbm,
    fit_logistic,
    lightgbm_feature_importances,
    logistic_feature_importances,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MIN_TRAIN_SIZE: int = 20
BASELINE_JSON: Path = DATA_DIR / "direction_baseline.json"
# Append-only measurement log: one compact record per eval run, so the model
# self-measures as the feature store grows toward a regime where a signal could
# clear the baseline. The full latest result lives in BASELINE_JSON; this is the
# trend over time.
HISTORY_JSONL: Path = DATA_DIR / "direction_eval_history.jsonl"

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def compute_calibration(
    y_true: list[int],
    y_prob: list[float],
    n_bins: int = 10,
) -> tuple[float, list[dict]]:
    """Expected Calibration Error (ECE) + reliability bins.

    Calibration is the PRIMARY quality metric: a well-calibrated probability
    ("58% up") is honest and useful even when its 0.5-threshold accuracy only
    matches the base rate. ECE is the count-weighted mean gap between predicted
    confidence and observed frequency across equal-width probability bins.

    Returns (ece, reliability_bins) where each bin is
    {lo, hi, mean_pred, frac_pos, count}. Empty bins are omitted from the list
    but contribute 0 to ECE (their weight is 0).

    NOTE: with ~100 folds ECE is itself noisy; treat it as directional, not exact.
    """
    arr_true = np.asarray(y_true, dtype=float)
    arr_prob = np.asarray(y_prob, dtype=float)
    n = len(arr_true)
    if n == 0:
        return 0.0, []

    edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    bins: list[dict] = []
    for b in range(n_bins):
        lo, hi = edges[b], edges[b + 1]
        # Last bin is closed on the right so p==1.0 lands somewhere.
        in_bin = (arr_prob >= lo) & (arr_prob < hi) if b < n_bins - 1 else (arr_prob >= lo)
        count = int(in_bin.sum())
        if count == 0:
            continue
        mean_pred = float(arr_prob[in_bin].mean())
        frac_pos = float(arr_true[in_bin].mean())
        ece += (count / n) * abs(mean_pred - frac_pos)
        bins.append(
            {
                "lo": round(lo, 2),
                "hi": round(hi, 2),
                "mean_pred": round(mean_pred, 4),
                "frac_pos": round(frac_pos, 4),
                "count": count,
            }
        )
    return float(ece), bins


def compute_direction_metrics(
    y_true: list[int],
    y_prob: list[float],
    model_name: str,
) -> dict:
    """Compute classification + calibration metrics vs the always-up baseline.

    Args:
        y_true: Ground-truth binary labels (1 = up, 0 = not-up).
        y_prob: Model's predicted P(up) for each observation.
        model_name: Human-readable model identifier.

    Returns:
        Dict containing: model, n, accuracy, brier, log_loss,
        always_up_accuracy, always_up_brier, p_value, significant_at_05,
        n_discordant, ece, reliability.
    """
    arr_true = np.asarray(y_true, dtype=float)
    arr_prob = np.asarray(y_prob, dtype=float)
    n = len(arr_true)

    # Model predictions at 0.5 threshold
    preds = (arr_prob >= 0.5).astype(float)
    accuracy = float(np.mean(preds == arr_true))

    # Brier score
    brier = float(np.mean((arr_prob - arr_true) ** 2))

    # Log-loss (clipped to avoid log(0))
    eps = 1e-7
    arr_prob_clipped = np.clip(arr_prob, eps, 1 - eps)
    log_loss = float(
        -np.mean(
            arr_true * np.log(arr_prob_clipped) + (1 - arr_true) * np.log(1 - arr_prob_clipped)
        )
    )

    # Always-up baseline
    always_up_accuracy = float(np.mean(arr_true))
    always_up_brier = float(np.mean((1.0 - arr_true) ** 2))

    # Significance: paired McNemar-style sign test
    # model_correct[i] = 1 if model prediction matches truth
    model_correct = (preds == arr_true).astype(int)
    # always-up correct[i] = 1 if truth == 1
    always_up_correct = arr_true.astype(int)

    b = int(np.sum((model_correct == 1) & (always_up_correct == 0)))  # model right, baseline wrong
    c = int(np.sum((model_correct == 0) & (always_up_correct == 1)))  # model wrong, baseline right
    n_discordant = b + c

    if n_discordant == 0:
        p_value = 1.0
    else:
        k = min(b, c)
        total = n_discordant
        try:
            from scipy.stats import binomtest  # type: ignore[import]

            result = binomtest(k, total, 0.5, alternative="two-sided")
            p_value = float(result.pvalue)
        except ImportError:
            # Exact binomial via math.comb
            p_value = _exact_binomial_two_sided(k, total)

    significant_at_05: bool = (p_value < 0.05) and (accuracy > always_up_accuracy)

    ece, reliability = compute_calibration(y_true, y_prob)

    return {
        "model": model_name,
        "n": n,
        "accuracy": accuracy,
        "brier": brier,
        "log_loss": log_loss,
        "always_up_accuracy": always_up_accuracy,
        "always_up_brier": always_up_brier,
        "p_value": p_value,
        "significant_at_05": significant_at_05,
        "n_discordant": n_discordant,
        "ece": ece,
        "reliability": reliability,
    }


def _exact_binomial_two_sided(k: int, n: int) -> float:
    """Compute two-sided exact binomial p-value for H0: p=0.5.

    Sums the probability mass for all outcomes at least as extreme as k.
    """
    if n == 0:
        return 1.0
    total_mass = 0.0
    observed_p = 0.5**n
    target = math.comb(n, k) * observed_p
    for i in range(n + 1):
        mass = math.comb(n, i) * observed_p
        if mass <= target + 1e-12:
            total_mass += mass
    return min(total_mass, 1.0)


# ---------------------------------------------------------------------------
# Walk-forward helpers
# ---------------------------------------------------------------------------


def _impute_with_means(X: np.ndarray, col_means: np.ndarray) -> np.ndarray:
    """Replace NaN values with training-split column means.

    Args:
        X: Feature matrix possibly containing NaN values.
        col_means: Per-column means computed on the training split.

    Returns:
        Copy of X with NaNs replaced by the corresponding column mean.
    """
    X_out = X.copy()
    for j in range(X_out.shape[1]):
        mask = np.isnan(X_out[:, j])
        if mask.any():
            fill = col_means[j] if not np.isnan(col_means[j]) else 0.0
            X_out[mask, j] = fill
    return X_out


def _average_importances(
    importance_list: list[dict[str, float]],
) -> dict[str, float]:
    """Average per-fold feature importances across folds.

    Args:
        importance_list: List of {feature: importance} dicts, one per fold.

    Returns:
        Dict with averaged importance values.  Folds where a feature was
        absent contribute 0.
    """
    if not importance_list:
        return {}
    all_keys: set[str] = set()
    for d in importance_list:
        all_keys.update(d.keys())
    result: dict[str, float] = {}
    for key in all_keys:
        vals = [d.get(key, 0.0) for d in importance_list]
        result[key] = float(np.mean(vals))
    return result


def _flatten_metrics(metrics_dict: dict) -> dict:
    """Extract scalar fields from a compute_direction_metrics result.

    Returns a clean subset for JSON serialisation.
    """
    return {
        "model": metrics_dict["model"],
        "n": metrics_dict["n"],
        "accuracy": metrics_dict["accuracy"],
        "brier": metrics_dict["brier"],
        "log_loss": metrics_dict["log_loss"],
        "always_up_accuracy": metrics_dict["always_up_accuracy"],
        "always_up_brier": metrics_dict["always_up_brier"],
        "p_value": metrics_dict["p_value"],
        "significant_at_05": metrics_dict["significant_at_05"],
        "n_discordant": metrics_dict["n_discordant"],
        "ece": metrics_dict["ece"],
        "reliability": metrics_dict["reliability"],
    }


def _print_summary(result: dict) -> None:
    """Print a human-readable walk-forward summary to stdout."""
    print("=== Phi23 Walk-Forward Results ===")
    print(f"  Date range        : {result.get('as_of_date_range')}")
    print(f"  Generated at      : {result.get('generated_at_utc')}")
    print(f"  Min train size    : {result.get('min_train_size')}")
    print(f"  Test folds        : {result.get('n_test_folds')}")
    print(f"  Skipped folds     : {result.get('n_skipped_folds')}")
    print(f"  Always-up baseline accuracy: {result.get('always_up_baseline_accuracy', 0):.4f}")
    print()
    for model_key in ("logistic", "lightgbm", "persistence"):
        m = result.get(f"{model_key}_metrics")
        if m is None:
            continue
        print(f"  [{m['model']}]")
        print(f"    accuracy  : {m['accuracy']:.4f}  (baseline: {m['always_up_accuracy']:.4f})")
        print(f"    brier     : {m['brier']:.4f}  (baseline brier: {m['always_up_brier']:.4f})")
        print(f"    ECE       : {m.get('ece', float('nan')):.4f}  (calibration — lower is better)")
        print(f"    log_loss  : {m['log_loss']:.4f}")
        print(f"    p_value   : {m['p_value']:.4f}  significant@0.05: {m['significant_at_05']}")
        print(f"    n_discordant: {m['n_discordant']}")
        print()

    print("  Top-5 logistic feature importances:")
    log_fi = result.get("feature_importances", {}).get("logistic", {})
    for feat, imp in sorted(log_fi.items(), key=lambda x: -x[1])[:5]:
        print(f"    {feat}: {imp:.4f}")
    print()
    print("  Top-5 lightgbm feature importances:")
    lgbm_fi = result.get("feature_importances", {}).get("lightgbm", {})
    for feat, imp in sorted(lgbm_fi.items(), key=lambda x: -x[1])[:5]:
        print(f"    {feat}: {imp:.4f}")


# ---------------------------------------------------------------------------
# Walk-forward evaluation
# ---------------------------------------------------------------------------


def run_walk_forward(
    dataset: pd.DataFrame,
    feature_cols: list[str] = FEATURE_COLS,
    min_train_size: int = MIN_TRAIN_SIZE,
    calibration_method: str = "sigmoid",
    verbose: bool = False,
    label_col: str = "label_binary",
) -> dict:
    """Run an expanding-window walk-forward evaluation for one horizon.

    For each test index i starting at min_train_size, train on rows [0:i]
    and predict for row i.  Aggregates per-fold predictions and computes
    OOS metrics for logistic regression, LightGBM, and a persistence
    baseline (prev row's label).

    Args:
        dataset: Output of build_dataset(); must be sorted by as_of_date.
        feature_cols: Feature columns to use.
        min_train_size: Minimum training rows before first test fold.
        calibration_method: Calibration method for logistic ("sigmoid" / "isotonic").
        verbose: If True, print fold progress.
        label_col: Binary label column for the horizon under test
            ("label_binary"/"label_binary_h1" for h=1, "label_binary_h2" for h=2).
            Rows with a missing (NaN) label for this horizon are dropped first.

    Returns:
        Nested dict with OOS metrics, feature importances, and metadata.
    """
    dataset = dataset.sort_values("as_of_date").reset_index(drop=True)
    # Drop rows that have no label for THIS horizon (e.g. the last row for h=2).
    if label_col in dataset.columns:
        dataset = dataset[dataset[label_col].notna()].reset_index(drop=True)
    n = len(dataset)

    y_true_all: list[int] = []
    log_prob_all: list[float] = []
    lgbm_prob_all: list[float] = []
    prev_label_all: list[float] = []

    log_fi_list: list[dict[str, float]] = []
    lgbm_fi_list: list[dict[str, float]] = []
    n_skipped = 0

    for i in range(min_train_size, n):
        train_df = dataset.iloc[:i]
        test_row = dataset.iloc[i]

        y_train = train_df[label_col].astype(int).tolist()

        # Skip fold if only one class in training labels
        if len(set(y_train)) < 2:
            n_skipped += 1
            continue

        X_train_raw = train_df[feature_cols].values.astype(float)
        X_test_raw = np.array([[test_row[col] for col in feature_cols]], dtype=float)

        # Impute with training means
        col_means = np.nanmean(X_train_raw, axis=0)
        X_train = _impute_with_means(X_train_raw, col_means)
        X_test = _impute_with_means(X_test_raw, col_means)

        # --- Logistic ---
        log_model = fit_logistic(
            X_train, y_train, calibration_method=calibration_method, random_state=42, cv=3
        )
        log_prob = float(log_model.predict_proba(X_test)[0, 1])

        # --- LightGBM ---
        lgbm_model = fit_lightgbm(X_train, y_train, random_state=42)
        if lgbm_model is not None:
            # lgbm_model is object | None (LGBMClassifier is a guarded import); the
            # None case is handled in the else branch.
            lgbm_prob = float(lgbm_model.predict_proba(X_test)[0, 1])  # type: ignore[attr-defined]
        else:
            lgbm_prob = 0.5  # neutral fallback

        # Persistence baseline: previous row's binary label (same horizon)
        prev_lbl = int(dataset.iloc[i - 1][label_col]) if i > 0 else 1
        prev_label_prob = float(prev_lbl)

        y_true_all.append(int(test_row[label_col]))
        log_prob_all.append(log_prob)
        lgbm_prob_all.append(lgbm_prob)
        prev_label_all.append(prev_label_prob)

        # Feature importances
        log_fi = logistic_feature_importances(log_model, feature_cols)
        lgbm_fi = lightgbm_feature_importances(lgbm_model, feature_cols)
        if log_fi:
            log_fi_list.append(log_fi)
        if lgbm_fi:
            lgbm_fi_list.append(lgbm_fi)

        if verbose and (i - min_train_size) % 10 == 0:
            print(f"  fold {i}/{n - 1}  log_prob={log_prob:.3f}  y_true={int(test_row[label_col])}")

    n_test_folds = len(y_true_all)
    always_up_baseline_accuracy = float(np.mean(y_true_all)) if y_true_all else 0.0

    log_metrics = compute_direction_metrics(y_true_all, log_prob_all, "logistic")
    lgbm_metrics = compute_direction_metrics(y_true_all, lgbm_prob_all, "lightgbm")
    persistence_metrics = compute_direction_metrics(y_true_all, prev_label_all, "persistence")

    avg_log_fi = _average_importances(log_fi_list)
    avg_lgbm_fi = _average_importances(lgbm_fi_list)

    date_range = (
        f"{dataset['as_of_date'].iloc[0]} to {dataset['as_of_date'].iloc[-1]}"
        if len(dataset) > 0
        else "N/A"
    )

    result = {
        "n_test_folds": n_test_folds,
        "n_skipped_folds": n_skipped,
        "min_train_size": min_train_size,
        "always_up_baseline_accuracy": always_up_baseline_accuracy,
        "logistic_metrics": _flatten_metrics(log_metrics),
        "lightgbm_metrics": _flatten_metrics(lgbm_metrics),
        "persistence_metrics": _flatten_metrics(persistence_metrics),
        "significance_vs_always_up": {
            "p_value": log_metrics["p_value"],
            "significant_at_05": log_metrics["significant_at_05"],
        },
        "feature_importances": {
            "logistic": avg_log_fi,
            "lightgbm": avg_lgbm_fi,
        },
        "as_of_date_range": date_range,
        "generated_at_utc": datetime.now(UTC).isoformat(),
    }
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


# Per-horizon label columns evaluated by main().
HORIZONS: tuple[tuple[str, str], ...] = (
    ("h1", "label_binary_h1"),
    ("h2", "label_binary_h2"),
)


def append_history(result: dict, path: Path = HISTORY_JSONL) -> None:
    """Append one compact per-run record (all horizons) to the history log."""
    record: dict = {
        "generated_at_utc": result.get("generated_at_utc"),
        "as_of_date_range": result.get("as_of_date_range"),
    }
    for hkey, wf in result.get("horizons", {}).items():
        log = wf.get("logistic_metrics", {})
        record[f"{hkey}_n_folds"] = wf.get("n_test_folds")
        record[f"{hkey}_base_rate"] = wf.get("always_up_baseline_accuracy")
        record[f"{hkey}_logistic_acc"] = log.get("accuracy")
        record[f"{hkey}_logistic_brier"] = log.get("brier")
        record[f"{hkey}_logistic_ece"] = log.get("ece")
        record[f"{hkey}_logistic_p"] = log.get("p_value")
        record[f"{hkey}_prob_ship"] = wf.get("probability_gate", {}).get("ship")
        record[f"{hkey}_timing_ship"] = wf.get("timing_gate", {}).get("ship")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def main() -> None:
    """Build dataset, run per-horizon walk-forward eval, write JSON + history."""
    # Imported here (not at module top) to keep the gate's import graph one-directional.
    from ml.direction.gate import decide_direction_signal, decide_timing_signal

    dataset = build_dataset(verbose=True)

    if len(dataset) < MIN_TRAIN_SIZE + 1:
        print(f"Dataset too small ({len(dataset)} rows). Need at least {MIN_TRAIN_SIZE + 1}")
        return

    horizons: dict[str, dict] = {}
    for hkey, label_col in HORIZONS:
        wf = run_walk_forward(dataset, label_col=label_col)
        wf["horizon"] = hkey
        # Two independent gates, both DARK until earned (see ml/direction/gate.py).
        wf["probability_gate"] = decide_direction_signal(wf)
        wf["timing_gate"] = decide_timing_signal(wf)
        horizons[hkey] = wf

        print(f"\n========== horizon {hkey} ==========")
        _print_summary(wf)
        pg, tg = wf["probability_gate"], wf["timing_gate"]
        print(f"  probability_gate: ship={pg['ship']} — {pg['reason']}")
        print(f"  timing_gate:      ship={tg['ship']} — {tg['reason']}")

    result = {
        "schema_version": 2,
        "generated_at_utc": horizons["h1"]["generated_at_utc"],
        "as_of_date_range": horizons["h1"]["as_of_date_range"],
        "min_train_size": MIN_TRAIN_SIZE,
        "horizons": horizons,
    }

    BASELINE_JSON.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_JSON.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    append_history(result)
    print(f"\nWrote {BASELINE_JSON}")
    print(f"Appended measurement to {HISTORY_JSONL}")


if __name__ == "__main__":
    main()
