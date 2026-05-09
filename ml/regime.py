"""
regime.py — 2-state HMM for gold volatility regime detection.

Trains a GaussianHMM on daily log-returns of gold_usd.  Labels the two
states canonically so the mapping is stable across re-trains:
  0 = low-vol   (quieter, trend-following conditions)
  1 = high-vol  (volatile, risk-off conditions)

The labeling is determined by emission std: the HMM state with the smaller
std is always mapped to canonical label 0.

Usage (from ml/forecast.py):
    from ml.regime import add_regime_to_macro
    macro_df = add_regime_to_macro(macro_df)   # adds 'regime' column
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

N_STATES = 2
N_ITER = 200
MIN_ROWS = 30  # minimum log-return observations needed to fit HMM

REGIME_FEATURE_COLS: list[str] = ["regime"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _log_returns(macro_df: pd.DataFrame) -> tuple[np.ndarray, pd.Index]:
    """
    Compute daily log-returns of gold_usd, forward-filling gaps first.

    Returns (X, valid_idx) where X has shape (n_valid, 1) and valid_idx is
    the DatetimeIndex of rows with a valid log-return (all rows except the
    first, or any row where gold_usd was NaN even after ffill).
    """
    gold = macro_df["gold_usd"].ffill()
    log_ret = np.log(gold / gold.shift(1)).dropna()
    return log_ret.values.reshape(-1, 1), log_ret.index


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fit_regime_model(macro_df: pd.DataFrame) -> tuple:
    """
    Fit a 2-state GaussianHMM on gold_usd log-returns.

    Returns (model, perm) where perm is an int array of length N_STATES:
      perm[hmm_state_index] = canonical_label
    Canonical 0 = low-vol; canonical 1 = high-vol (sorted by emission std).

    Raises
    ------
    ImportError  if hmmlearn is not installed.
    RuntimeError if macro_df has fewer than MIN_ROWS valid log-return rows.
    """
    try:
        from hmmlearn.hmm import GaussianHMM
    except ImportError as exc:
        raise ImportError(
            "hmmlearn is required for regime detection. " "Install it with: pip install hmmlearn"
        ) from exc

    X, _ = _log_returns(macro_df)
    if len(X) < MIN_ROWS:
        raise RuntimeError(f"Need at least {MIN_ROWS} log-return rows to fit HMM; got {len(X)}")

    model = GaussianHMM(
        n_components=N_STATES,
        covariance_type="diag",
        n_iter=N_ITER,
        random_state=42,
    )
    model.fit(X)

    # Canonical labeling: sort HMM states by emission std (ascending).
    # perm[hmm_state] = canonical_label, so low-vol HMM state → 0, high-vol → 1.
    #
    # covars_ shape varies by hmmlearn version:
    #   0.2.x diag: (N_STATES, n_features)
    #   0.3.x diag: (N_STATES, n_features, n_features)  <- changed in 0.3.x
    # Flatten each state's covariance block and take element [0] so this
    # works regardless of shape, for 1-dimensional gold log-return input.
    variances = np.asarray(model.covars_).reshape(N_STATES, -1)[:, 0]
    stds = np.sqrt(variances)  # shape (N_STATES,)
    order = np.argsort(stds).tolist()  # plain list of ints — safe indexing
    perm = np.empty(N_STATES, dtype=int)
    for canonical, hmm_state in enumerate(order):
        perm[int(hmm_state)] = canonical

    return model, perm


def add_regime_to_macro(macro_df: pd.DataFrame) -> pd.DataFrame:
    """
    Fit the HMM and add a 'regime' column to macro_df.

    regime = 0  → low-vol state
    regime = 1  → high-vol state
    regime = NaN → first row (no log-return available)

    Returns a new DataFrame; does not modify the input.  If hmmlearn is not
    installed or there is insufficient data, returns the original DataFrame
    unchanged (without a 'regime' column) so that forecast.py can fall back
    to the base feature set.
    """
    try:
        model, perm = fit_regime_model(macro_df)
    except (ImportError, RuntimeError) as exc:
        print(f"  Regime model skipped: {exc}")
        return macro_df.copy()

    X, valid_idx = _log_returns(macro_df)
    hmm_states = model.predict(X)
    canonical = perm[hmm_states].astype(float)

    macro = macro_df.copy()
    macro["regime"] = np.nan
    macro.loc[valid_idx, "regime"] = canonical
    return macro


def write_regime_json(model, perm: np.ndarray, macro_df: pd.DataFrame, out_path: Path) -> dict:
    """
    Compute current-regime diagnostics and write data/regime.json.

    Schema
    ------
    {
      "as_of":                  "<ISO date of latest macro row>",
      "generated_at":           "<ISO UTC timestamp>",
      "state":                  0 | 1,
      "label":                  "low-vol" | "high-vol",
      "probability":            float,   # P(current state | all observations)
      "days_in_regime":         int,     # consecutive days in this state
      "transition_probability": float,   # P(switch to other state next day)
      "emission": {
        "low_vol":  {"mean_pct": float, "std_pct": float},
        "high_vol": {"mean_pct": float, "std_pct": float}
      },
      "transition_matrix": {
        "low_vol_to_low_vol":   float,
        "low_vol_to_high_vol":  float,
        "high_vol_to_high_vol": float,
        "high_vol_to_low_vol":  float
      }
    }
    """
    X, valid_idx = _log_returns(macro_df)
    hmm_states = model.predict(X)
    posteriors = model.predict_proba(X)  # shape (n, N_STATES) in HMM state space
    canonical = perm[hmm_states]

    current_hmm = int(hmm_states[-1])
    current_canon = int(canonical[-1])
    current_label = "low-vol" if current_canon == 0 else "high-vol"
    current_prob = float(posteriors[-1, current_hmm])

    # Consecutive days in current canonical state
    streak = 1
    for s in reversed(canonical[:-1].tolist()):
        if int(s) == current_canon:
            streak += 1
        else:
            break

    # Identify which HMM state index maps to each canonical label
    low_hmm = int(np.where(perm == 0)[0][0])
    high_hmm = int(np.where(perm == 1)[0][0])

    # Emission std: robust to covars_ shape change in hmmlearn 0.3.x
    variances = np.asarray(model.covars_).reshape(N_STATES, -1)[:, 0]
    stds = np.sqrt(variances)
    means = model.means_.ravel()

    result = {
        "as_of": str(valid_idx[-1].date()),
        "generated_at": datetime.now(UTC).isoformat(),
        "state": current_canon,
        "label": current_label,
        "probability": round(current_prob, 4),
        "days_in_regime": streak,
        "transition_probability": round(float(model.transmat_[current_hmm, 1 - current_hmm]), 4),
        "emission": {
            "low_vol": {
                "mean_pct": round(float(means[low_hmm]) * 100, 4),
                "std_pct": round(float(stds[low_hmm]) * 100, 4),
            },
            "high_vol": {
                "mean_pct": round(float(means[high_hmm]) * 100, 4),
                "std_pct": round(float(stds[high_hmm]) * 100, 4),
            },
        },
        "transition_matrix": {
            "low_vol_to_low_vol": round(float(model.transmat_[low_hmm, low_hmm]), 4),
            "low_vol_to_high_vol": round(float(model.transmat_[low_hmm, high_hmm]), 4),
            "high_vol_to_high_vol": round(float(model.transmat_[high_hmm, high_hmm]), 4),
            "high_vol_to_low_vol": round(float(model.transmat_[high_hmm, low_hmm]), 4),
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2) + "\n")
    return result
