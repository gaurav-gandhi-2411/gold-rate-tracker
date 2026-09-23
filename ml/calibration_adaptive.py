"""ml.calibration_adaptive — offline, held-out comparison of Adaptive
Conformal Inference (ACI) against the current (static empirical-quantile)
calibration band (M3, GG spec item 6, 2026-09-23).

SHADOW / OFFLINE ONLY: does not write to data/calibration_band_coverage.json
and is never called by ml.calibration or ml.inference. The weekend/carry-
forward stratum comparison GG also asked for already exists and is running
in production shadow (ml.calibration.evaluate_stratified_band_coverage,
PR #1825) — its first live-scored week lands 2026-09-27; this module does
not duplicate it, and does not cite that shadow's earlier n=87 dev-run
number as production evidence (GG's explicit instruction).

Adaptive Conformal Inference (ACI, Gibbs & Candès 2021): instead of a FIXED
quantile level, maintain a target miscoverage rate that adapts after every
scored day based on whether the band actually covered that day's real
value — widening after a miss, narrowing after a hit — so the band tracks
genuine drift in residual behavior without needing the walk-forward window
to already contain examples of that drift (the static band's own
`min_train`/`half_life` mechanism is a fixed-window analogue of the same
idea; ACI updates online, one day at a time, and needs no window-size
choice at all).

Both methods share the identical fit (same Huber regression, same
recency-weighted residuals, same scoring-set construction as
ml.calibration.evaluate_empirical_band_coverage) so the comparison isolates
the band-sizing RULE, not a difference in what's being fit.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.calibration import (
    _DEFAULT_HALF_LIFE,
    _DEFAULT_HUBER_EPSILON,
    _MIN_FIT_OBSERVATIONS,
    _SCORING_MAX_IBJA_AGE_DAYS,
    _fit_robust,
    _merge_overlap,
    _recency_weights,
    _weighted_percentile,
)


def _wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = successes / n
    denom = 1 + z**2 / n
    centre = p + z**2 / (2 * n)
    margin = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))
    return ((centre - margin) / denom, (centre + margin) / denom)


def _build_scoring_set(
    ibja_df: pd.DataFrame, tanishq_df: pd.DataFrame, max_age_days: int
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray]:
    """Identical construction to ml.calibration.evaluate_empirical_band_coverage
    (same-day fit pairs + asof-matched scoring set, gated at max_age_days) —
    duplicated rather than imported since that function does the fit+scoring
    inline in one loop; this module needs the same inputs for TWO band-sizing
    rules scored in the same pass."""
    same_day = _merge_overlap(ibja_df, tanishq_df)
    X = same_day["ibja_per_g"].to_numpy().reshape(-1, 1)
    y = same_day["tanishq_22k"].to_numpy()
    same_day_dates = pd.to_datetime(same_day["date"]).to_numpy()

    ibja_sorted = ibja_df[["date", "pm_916"]].dropna(subset=["pm_916"]).copy()
    ibja_sorted["date_dt"] = pd.to_datetime(ibja_sorted["date"])
    ibja_sorted["ibja_per_g"] = ibja_sorted["pm_916"] / 10.0
    ibja_sorted = ibja_sorted.sort_values("date_dt")

    tanishq_sorted = tanishq_df[["date", "22k"]].copy()
    tanishq_sorted["date_dt"] = pd.to_datetime(tanishq_sorted["date"])
    tanishq_sorted = tanishq_sorted.sort_values("date_dt")

    scoring = pd.merge_asof(
        tanishq_sorted,
        ibja_sorted[["date_dt", "ibja_per_g", "date"]].rename(columns={"date": "ibja_date"}),
        on="date_dt",
        direction="backward",
    )
    scoring = scoring.dropna(subset=["ibja_per_g"])
    scoring["gap_days"] = (scoring["date_dt"] - pd.to_datetime(scoring["ibja_date"])).dt.days
    scoring = (
        scoring[scoring["gap_days"] < max_age_days].sort_values("date_dt").reset_index(drop=True)
    )
    return scoring, X, y, same_day_dates


def evaluate_adaptive_conformal_vs_static(
    ibja_df: pd.DataFrame,
    tanishq_df: pd.DataFrame,
    level: int,
    gamma: float = 0.01,
    huber_epsilon: float = _DEFAULT_HUBER_EPSILON,
    half_life: float = _DEFAULT_HALF_LIFE,
    min_train: int = _MIN_FIT_OBSERVATIONS,
    max_age_days: int = _SCORING_MAX_IBJA_AGE_DAYS,
) -> dict:
    """Score the static (production-matching) band and an ACI band on the
    identical scoring set, day by day, strictly held out (each day's fit
    uses only same-day pairs strictly before it — no leakage in either
    method).

    ACI update (Gibbs & Candès 2021): target miscoverage alpha_target =
    1 - level/100. alpha_t starts at alpha_target; after each scored day,
    alpha_{t+1} = alpha_t + gamma * (alpha_target - miss_t), where miss_t=1
    if that day's real value fell OUTSIDE the band, else 0. alpha_t is
    clipped to [0.01, 0.99] (a band can't target >99% or <1% miscoverage
    without degenerating). The ACI band's half-width on day t is the
    recency-weighted (1 - alpha_t) quantile of |residual| from the SAME
    walk-forward training fit the static band uses that day — the two
    methods differ only in which quantile LEVEL they use, not in what
    they're fit on.

    Returns {static: {n, n_in_band, coverage, wilson_ci_95}, adaptive: {...},
    final_alpha, n_days_alpha_above_target, n_days_alpha_below_target}.
    """
    scoring, X, y, same_day_dates = _build_scoring_set(ibja_df, tanishq_df, max_age_days)
    alpha_target = 1.0 - level / 100.0
    alpha_t = alpha_target

    static_hits = 0
    adaptive_hits = 0
    n_scored = 0
    alpha_path: list[float] = []

    for _, srow in scoring.iterrows():
        t = np.datetime64(srow["date_dt"])
        train_mask = same_day_dates < t
        n_train = int(train_mask.sum())
        if n_train < min_train:
            continue

        X_train = X[train_mask]
        y_train = y[train_mask]
        weights = _recency_weights(n_train, half_life)
        slope, intercept = _fit_robust(
            X_train, y_train, huber_epsilon=huber_epsilon, weights=weights
        )

        train_pred = slope * X_train[:, 0] + intercept
        train_abs_residuals = np.abs(y_train - train_pred)

        static_half_width = _weighted_percentile(train_abs_residuals, weights, level)
        adaptive_level = max(1.0, min(99.0, (1.0 - alpha_t) * 100.0))
        adaptive_half_width = _weighted_percentile(train_abs_residuals, weights, adaptive_level)

        pred_t = slope * srow["ibja_per_g"] + intercept
        actual_err = abs(srow["22k"] - pred_t)

        n_scored += 1
        static_hit = actual_err <= static_half_width
        adaptive_hit = actual_err <= adaptive_half_width
        static_hits += int(static_hit)
        adaptive_hits += int(adaptive_hit)

        miss = 0.0 if adaptive_hit else 1.0
        alpha_t = alpha_t + gamma * (alpha_target - miss)
        alpha_t = max(0.01, min(0.99, alpha_t))
        alpha_path.append(alpha_t)

    static_ci = _wilson_ci(static_hits, n_scored)
    adaptive_ci = _wilson_ci(adaptive_hits, n_scored)

    return {
        "n": n_scored,
        "level": level,
        "static": {
            "n_in_band": static_hits,
            "coverage": (static_hits / n_scored) if n_scored else None,
            "wilson_ci_95": list(static_ci),
        },
        "adaptive": {
            "n_in_band": adaptive_hits,
            "coverage": (adaptive_hits / n_scored) if n_scored else None,
            "wilson_ci_95": list(adaptive_ci),
        },
        "final_alpha": alpha_t,
        "alpha_target": alpha_target,
        "gamma": gamma,
        "n_days_alpha_above_target": sum(1 for a in alpha_path if a > alpha_target),
        "n_days_alpha_below_target": sum(1 for a in alpha_path if a < alpha_target),
    }
