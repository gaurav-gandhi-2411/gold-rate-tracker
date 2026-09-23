"""ml.range_forecast.conformal -- walk-forward split conformal (CQR) calibration.

Uses a single, uniform nonconformity score for EVERY model regardless of how
its raw bounds were produced (parametric-scale models like GARCH/EWMA and
directly-quantile models like the LightGBM quantile regressor and Chronos):
the CQR score of Romano, Patterson & Candes (2019),
    s = max(lo - actual, actual - hi)
This is a deliberate simplification of the task brief's "|return|/predicted
scale, OR the CQR score for quantile models" -- CQR is valid for any base
interval predictor (it does not require the bounds to come from a quantile
regressor specifically), so one code path calibrates every model rather than
switching formula by model family. Flagged in the PR/report as a
methodological choice to review.

Calibration is embargo-aware and walk-forward: at as-of position `cur`, the
score of a PAST forecast made at position `t_prev` for horizon `h` is usable
only once its outcome has matured, i.e. `t_prev + h <= cur` -- exactly the
task brief's embargo rule -- and only the most recent `window` matured
scores are used (a rolling calibration set, not the full expanding history:
volatility regimes drift, and a rolling window tracks that).
"""

from __future__ import annotations

import numpy as np

MIN_CALIBRATION_N = 20  # below this, calibration is undefined -- raw bounds pass through unchanged
DEFAULT_WINDOW = 250


def cqr_score(lo: np.ndarray, hi: np.ndarray, actual: np.ndarray) -> np.ndarray:
    lo, hi, actual = (
        np.asarray(lo, dtype=float),
        np.asarray(hi, dtype=float),
        np.asarray(actual, dtype=float),
    )
    return np.maximum(lo - actual, actual - hi)


def _conformal_quantile(scores: np.ndarray, level: float) -> float:
    """Finite-sample-corrected empirical quantile of the nonconformity
    scores (Romano et al. 2019, eq. 3): ceil((n+1) * level) / n-th order
    statistic, clipped so the quantile index never exceeds 1.0 (returns the
    sample max when n is small relative to `level`)."""
    n = len(scores)
    if n == 0:
        return float("nan")
    q_level = min(1.0, np.ceil((n + 1) * level) / n)
    return float(np.quantile(scores, q_level, method="higher"))


def matured_window_indices(
    as_of_positions: np.ndarray, horizon: int, cur_idx: int, window: int
) -> np.ndarray:
    """Indices j < cur_idx whose forecast has matured as of
    as_of_positions[cur_idx] (as_of_positions[j] + horizon <=
    as_of_positions[cur_idx]), restricted to the most recent `window` such
    indices. `as_of_positions` must be ascending."""
    cur = as_of_positions[cur_idx]
    cutoff = cur - horizon
    end = int(np.searchsorted(as_of_positions, cutoff, side="right"))
    end = min(end, cur_idx)  # never include the current forecast itself
    start = max(0, end - window)
    return np.arange(start, end)


def walk_forward_conformal(
    as_of_positions: np.ndarray,
    horizon: int,
    lo: np.ndarray,
    hi: np.ndarray,
    actual: np.ndarray,
    level: float,
    window: int = DEFAULT_WINDOW,
    min_calibration_n: int = MIN_CALIBRATION_N,
) -> dict:
    """Embargo-aware, rolling-window walk-forward CQR calibration.

    Returns dict with calibrated_lo, calibrated_hi (float arrays, NaN where
    fewer than `min_calibration_n` matured scores are available -- see
    `n_matured`), q_hat, n_matured (int array).
    """
    as_of_positions = np.asarray(as_of_positions, dtype=np.int64)
    lo, hi, actual = (
        np.asarray(lo, dtype=float),
        np.asarray(hi, dtype=float),
        np.asarray(actual, dtype=float),
    )
    n = len(as_of_positions)
    scores = cqr_score(lo, hi, actual)

    calibrated_lo = np.full(n, np.nan)
    calibrated_hi = np.full(n, np.nan)
    q_hats = np.full(n, np.nan)
    n_matured = np.zeros(n, dtype=np.int64)

    for i in range(n):
        idx = matured_window_indices(as_of_positions, horizon, i, window)
        n_matured[i] = len(idx)
        if len(idx) < min_calibration_n:
            continue
        q_hat = _conformal_quantile(scores[idx], level)
        q_hats[i] = q_hat
        calibrated_lo[i] = lo[i] - q_hat
        calibrated_hi[i] = hi[i] + q_hat

    return {
        "calibrated_lo": calibrated_lo,
        "calibrated_hi": calibrated_hi,
        "q_hat": q_hats,
        "n_matured": n_matured,
        "raw_scores": scores,
    }


def assert_no_unmatured_leakage(
    as_of_positions: np.ndarray, horizon: int, cur_idx: int, used_indices: np.ndarray
) -> None:
    """Test helper: raises AssertionError if any `used_indices` entry has not
    matured as of `as_of_positions[cur_idx]`."""
    cur = as_of_positions[cur_idx]
    for j in used_indices:
        assert as_of_positions[j] + horizon <= cur, (
            f"embargo violation: forecast at position {as_of_positions[j]} (h={horizon}) "
            f"used at position {cur} before maturing"
        )
