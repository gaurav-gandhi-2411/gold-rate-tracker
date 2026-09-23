"""ml.direction.evaluate_reframed — M2 shadow evaluation harness.

Runs an EMBARGO-AWARE expanding-window walk-forward evaluation across the
reframed targets (ml.direction.reframed_targets), multiple horizons, and
three models (class-weighted logistic, gradient boosting, a simple
ensemble), reporting Brier skill score vs. walk-forward climatology,
calibration (ECE), and significance (the existing McNemar-style sign test
plus a Diebold-Mariano test on Brier loss).

SHADOW ONLY: writes to data/direction_reframed_results.json, never touches
data/direction_baseline.json or ml.direction.gate. The live gate and its
inputs are unchanged, per GG's spec (2026-09-23).

Embargo, and why the existing h1/h2 harness (ml.direction.evaluate) didn't
need one but this one does: for horizon H, a training row's label doesn't
MATURE (become knowable) until H trading days after its own as_of_date. The
existing harness's expanding window `train_df = dataset.iloc[:i]` silently
assumes every row before the test index already has a matured label by the
time of that test -- true by construction for h=1 (matures the next day,
i.e. always before the next row's as_of_date) but NOT generally true for
h=2 and not at all a safe assumption for h=5/h=10, where several of the
most recent "prior" rows have labels that mature AFTER the test row's own
date. This module's `_embargo_eligible_indices` fixes that: eligibility is
determined by comparing label_date_hN to the test row's as_of_date
directly, not by row position.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV

from ml.direction.dataset import DATA_DIR, FEATURE_COLS, build_dataset
from ml.direction.evaluate import _impute_with_means, compute_direction_metrics
from ml.direction.models import _LGBM_AVAILABLE, _safe_cv, fit_logistic
from ml.direction.reframed_targets import (
    add_buyer_decision_binary,
    add_deadzone_binary,
    add_detrended_binary,
)

if _LGBM_AVAILABLE:
    from lightgbm import LGBMClassifier

RESULTS_JSON: Path = DATA_DIR / "direction_reframed_results.json"
MIN_TRAIN_SIZE: int = 20
DEFAULT_HORIZONS: tuple[int, ...] = (5, 10)

TARGET_BUILDERS: dict[str, object] = {
    "raw_binary": lambda df, h: df[f"label_binary_h{h}"],
    "deadzone": add_deadzone_binary,
    "detrended": add_detrended_binary,
    "buyer_decision": add_buyer_decision_binary,
}


# ---------------------------------------------------------------------------
# Embargo
# ---------------------------------------------------------------------------


def _embargo_eligible_indices(
    as_of_dates: list[str], label_dates: list[str | None], test_idx: int
) -> list[int]:
    """Indices j < test_idx whose label had already matured (label_date_hN
    strictly before as_of_dates[test_idx]) -- see module docstring."""
    cutoff = as_of_dates[test_idx]
    return [
        j
        for j in range(test_idx)
        if label_dates[j] is not None and str(label_dates[j]) < str(cutoff)
    ]


# ---------------------------------------------------------------------------
# Calibrated gradient boosting (mirrors fit_logistic's calibration wrapper --
# ml.direction.models.fit_lightgbm returns an UNCALIBRATED raw LGBMClassifier,
# fine for the live pipeline's existing usage, but GG's spec for M2
# explicitly wants calibration fitted inside the walk-forward for every model,
# not just logistic)
# ---------------------------------------------------------------------------


def _fit_lightgbm_calibrated(
    X_train: np.ndarray,
    y_train: list[int],
    calibration_method: str = "sigmoid",
    cv: int = 3,
    random_state: int = 42,
) -> object | None:
    if not _LGBM_AVAILABLE:
        return None
    base = LGBMClassifier(n_estimators=100, random_state=random_state, verbose=-1)
    safe = _safe_cv(y_train, cv)
    if safe < 2:
        base.fit(X_train, y_train)
        return base
    calibrated = CalibratedClassifierCV(base, method=calibration_method, cv=safe)
    calibrated.fit(X_train, y_train)
    return calibrated


# ---------------------------------------------------------------------------
# Brier skill score vs. walk-forward climatology
# ---------------------------------------------------------------------------


def brier_skill_score(brier_model: float, brier_climatology: float) -> float:
    """BSS = 1 - brier_model / brier_climatology. Positive means the model
    beats always-predict-the-training-mean; 0 means it's exactly as good;
    negative means it's worse. Returns NaN if climatology's own Brier is 0
    (degenerate: a single-class training set at every fold)."""
    if brier_climatology == 0:
        return float("nan")
    return 1.0 - (brier_model / brier_climatology)


# ---------------------------------------------------------------------------
# Diebold-Mariano test on Brier (squared-error) loss
# ---------------------------------------------------------------------------


def diebold_mariano_test(
    loss_a: list[float], loss_b: list[float], horizon: int, alternative: str = "two-sided"
) -> dict:
    """DM test: H0 is loss_a and loss_b have equal predictive accuracy.

    d_t = loss_a[t] - loss_b[t]; DM = mean(d) / sqrt(long-run variance of
    mean(d) / n). Uses a Newey-West-style long-run variance with truncation
    lag = horizon - 1 (the standard DM recommendation for h-step-ahead
    forecast errors, which are autocorrelated up to lag h-1 even under the
    null, since consecutive h-step folds share h-1 days of outcome overlap).

    alternative: "two-sided" (default, backward-compatible), or "less"
    (one-sided H1: mean_d < 0, i.e. loss_a — the MODEL — is lower/better
    than loss_b — the baseline; this is the test GG's spec asks for
    throughout: "better than baseline", not merely "different from it").

    Returns {dm_stat, p_value, mean_diff, n, gamma_0, long_run_var,
    effective_n} — negative dm_stat/mean_diff means loss_a is lower (a beats
    b). effective_n = n * gamma_0 / long_run_var, the standard HAC
    effective-sample-size correction: it equals n when there's no
    autocorrelation (long_run_var == gamma_0) and shrinks as overlapping-
    horizon autocorrelation inflates long_run_var above gamma_0. p_value is
    from a normal approximation (n here is small enough that this is
    approximate, not exact — noted as such in the report).
    """
    d = np.asarray(loss_a) - np.asarray(loss_b)
    n = len(d)
    if n < 2:
        return {
            "dm_stat": None,
            "p_value": None,
            "mean_diff": None,
            "n": n,
            "gamma_0": None,
            "long_run_var": None,
            "effective_n": None,
        }

    mean_d = float(np.mean(d))
    max_lag = max(0, horizon - 1)
    d_centered = d - mean_d
    gamma_0 = float(np.mean(d_centered**2))
    long_run_var = gamma_0
    for lag in range(1, min(max_lag, n - 1) + 1):
        gamma_lag = float(np.mean(d_centered[lag:] * d_centered[:-lag]))
        long_run_var += 2 * (1 - lag / (max_lag + 1)) * gamma_lag
    long_run_var = max(long_run_var, 1e-12)

    dm_stat = mean_d / math.sqrt(long_run_var / n)
    if alternative == "two-sided":
        p_value = float(2 * (1 - _std_normal_cdf(abs(dm_stat))))
    elif alternative == "less":
        # H1: mean_d < 0 (loss_a/model beats loss_b/baseline).
        p_value = float(_std_normal_cdf(dm_stat))
    elif alternative == "greater":
        p_value = float(1 - _std_normal_cdf(dm_stat))
    else:
        raise ValueError(f"unknown alternative: {alternative!r}")

    effective_n_val = n * gamma_0 / long_run_var if long_run_var > 0 else float(n)

    return {
        "dm_stat": float(dm_stat),
        "p_value": p_value,
        "mean_diff": mean_d,
        "n": n,
        "gamma_0": gamma_0,
        "long_run_var": long_run_var,
        "effective_n": float(effective_n_val),
    }


def _std_normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# ---------------------------------------------------------------------------
# Walk-forward evaluation for one (target, horizon) combination
# ---------------------------------------------------------------------------


def run_walk_forward_reframed(
    dataset: pd.DataFrame,
    target_name: str,
    horizon: int,
    feature_cols: list[str] = FEATURE_COLS,
    min_train_size: int = MIN_TRAIN_SIZE,
    target_builders: dict[str, object] | None = None,
) -> dict:
    """Embargo-aware walk-forward for one target/horizon combination.

    `target_builders` overrides TARGET_BUILDERS, e.g. to swap the INR-only
    buyer_decision builder for its unit-free percentage variant on a non-INR
    dataset (see ml.direction.price_units)."""
    label_date_col = f"label_date_h{horizon}"
    builder = (target_builders or TARGET_BUILDERS)[target_name]
    labels = builder(dataset, horizon)  # type: ignore[operator]

    df = dataset.copy()
    df["_label"] = labels
    df = df[df["_label"].notna()].reset_index(drop=True)
    df["_label"] = df["_label"].astype(int)

    n = len(df)
    as_of_dates = df["as_of_date"].astype(str).tolist()
    label_dates = df[label_date_col].tolist()

    y_true_all: list[int] = []
    log_prob_all: list[float] = []
    gbm_prob_all: list[float] = []
    ens_prob_all: list[float] = []
    clim_prob_all: list[float] = []
    n_skipped = 0

    for i in range(min_train_size, n):
        eligible = _embargo_eligible_indices(as_of_dates, label_dates, i)
        if len(eligible) < min_train_size:
            n_skipped += 1
            continue

        train_idx = np.array(eligible, dtype=int)
        y_train = df["_label"].iloc[train_idx].tolist()
        if len(set(y_train)) < 2:
            n_skipped += 1
            continue

        X_train_raw = df[feature_cols].iloc[train_idx].values.astype(float)
        test_row = df.iloc[i]
        X_test_raw = np.array([[test_row[c] for c in feature_cols]], dtype=float)

        col_means = np.nanmean(X_train_raw, axis=0)
        X_train = _impute_with_means(X_train_raw, col_means)
        X_test = _impute_with_means(X_test_raw, col_means)

        log_model = fit_logistic(X_train, y_train, random_state=42, cv=3, class_weight="balanced")
        log_prob = float(log_model.predict_proba(X_test)[0, 1])

        gbm_model = _fit_lightgbm_calibrated(X_train, y_train, random_state=42)
        if gbm_model is not None:
            # gbm_model is object | None (LGBMClassifier/CalibratedClassifierCV via a
            # guarded import); the None case is handled in the else branch.
            gbm_prob = float(gbm_model.predict_proba(X_test)[0, 1])  # type: ignore[attr-defined]
        else:
            gbm_prob = 0.5

        ens_prob = (log_prob + gbm_prob) / 2.0
        clim_prob = float(np.mean(y_train))

        y_true_all.append(int(test_row["_label"]))
        log_prob_all.append(log_prob)
        gbm_prob_all.append(gbm_prob)
        ens_prob_all.append(ens_prob)
        clim_prob_all.append(clim_prob)

    n_test_folds = len(y_true_all)
    result: dict = {
        "target": target_name,
        "horizon": horizon,
        "n_test_folds": n_test_folds,
        "n_skipped_folds": n_skipped,
        "min_train_size": min_train_size,
    }
    if n_test_folds == 0:
        result["error"] = "no eligible test folds (embargo + label availability too restrictive)"
        return result

    models_probs = {
        "logistic_balanced": log_prob_all,
        "gbm_calibrated": gbm_prob_all,
        "ensemble": ens_prob_all,
    }
    climatology_brier = float(np.mean((np.array(clim_prob_all) - np.array(y_true_all)) ** 2))
    result["climatology_brier"] = climatology_brier
    result["always_up_rate"] = float(np.mean(y_true_all))
    # Raw per-fold arrays, additive — lets a downstream statistical-correction
    # pass (HAC/block-bootstrap/effective-n) recompute directly from the
    # actual fold-level predictions rather than re-running the walk-forward.
    result["raw"] = {
        "y_true": y_true_all,
        "model_probs": {k: list(v) for k, v in models_probs.items()},
        "climatology_probs": clim_prob_all,
    }

    always_up_loss = list((1.0 - np.array(y_true_all)) ** 2)

    for model_name, probs in models_probs.items():
        # significant_at_05 here (from compute_direction_metrics) is ALREADY
        # a McNemar-style sign test of model vs. always-up — see that
        # function's docstring. Kept as-is; DM below is the second,
        # spec-required significance test on the same always-up comparison.
        metrics = compute_direction_metrics(y_true_all, probs, model_name)
        model_loss = list((np.array(probs) - np.array(y_true_all)) ** 2)
        clim_loss = list((np.array(clim_prob_all) - np.array(y_true_all)) ** 2)
        result[model_name] = {
            **{k: v for k, v in metrics.items() if k != "reliability"},
            "reliability": metrics["reliability"],
            "brier_skill_score_vs_climatology": brier_skill_score(
                metrics["brier"], climatology_brier
            ),
            "diebold_mariano_vs_always_up": diebold_mariano_test(
                model_loss, always_up_loss, horizon
            ),
            "diebold_mariano_vs_climatology": diebold_mariano_test(model_loss, clim_loss, horizon),
        }

    return result


def run_all(
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    targets: tuple[str, ...] = tuple(TARGET_BUILDERS.keys()),
) -> dict:
    dataset = build_dataset(extra_horizons=horizons, verbose=False)
    results: dict = {}
    for horizon in horizons:
        for target in targets:
            key = f"{target}_h{horizon}"
            print(f"Running {key}...")
            results[key] = run_walk_forward_reframed(dataset, target, horizon)
    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "as_of_date_range": f"{dataset['as_of_date'].min()} to {dataset['as_of_date'].max()}",
        "n_base_rows": len(dataset),
        "results": results,
    }


def main() -> None:
    output = run_all()
    RESULTS_JSON.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_JSON.write_text(json.dumps(output, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nWrote {RESULTS_JSON}")
    for key, r in output["results"].items():
        if "error" in r:
            print(f"  {key}: {r['error']}")
            continue
        ens = r["ensemble"]
        print(
            f"  {key}: n={r['n_test_folds']} acc={ens['accuracy']:.4f} "
            f"(base={ens['always_up_accuracy']:.4f}) brier={ens['brier']:.4f} "
            f"BSS={ens['brier_skill_score_vs_climatology']:.4f} "
            f"p={ens['p_value']:.4f} sig={ens['significant_at_05']} "
            f"ece={ens['ece']:.4f}"
        )


if __name__ == "__main__":
    main()
