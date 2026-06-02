"""Experiment 1 (Phi7A): drift-naive vs flat-naive at h=5.

drift_naive: forecast = last_value + h * EWMA(recent daily deltas).
EWMA spans tested: [5, 10, 20] trading days.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ml.backtest import _HORIZON, _MIN_CONTEXT_DAYS, yield_folds
from ml.metrics import compute_wilcoxon_p

_EWMA_SPANS: list[int] = [5, 10, 20]


def forecast_drift_naive(context: pd.Series, horizon: int, span: int) -> list[float]:
    """Drift-naive forecast: last_value + h * EWMA(recent daily deltas).

    Parameters
    ----------
    context : date-indexed Series of IBJA-916-PM values.
    horizon : number of steps to forecast.
    span    : EWMA span in trading days.
    """
    deltas = context.diff().dropna()
    if len(deltas) == 0:
        last = float(context.iloc[-1])
        return [last] * horizon
    ewma_delta = float(deltas.ewm(span=span, adjust=False).mean().iloc[-1])
    last_val = float(context.iloc[-1])
    return [last_val + (h + 1) * ewma_delta for h in range(horizon)]


def apply_gate(
    mae_variant: float,
    mae_naive: float,
    wilcoxon_p: float | None,
    n_folds_ge30ctx: int,
) -> tuple[bool, float]:
    """Apply the pre-registered Phi7 promotion gate.

    Returns (beats_naive, pct_improvement).
    Gate: >=2% MAE improvement AND Wilcoxon p<0.05 AND >=30 ge30ctx folds.
    """
    pct = (mae_naive - mae_variant) / mae_naive if mae_naive > 0 else 0.0
    beats = pct >= 0.02 and wilcoxon_p is not None and wilcoxon_p < 0.05 and n_folds_ge30ctx >= 30
    return beats, pct


def run_drift_naive_experiment(
    ibja_series: pd.Series,
    spans: list[int] = _EWMA_SPANS,
    horizon: int = _HORIZON,
    min_context: int = _MIN_CONTEXT_DAYS,
) -> list[dict[str, Any]]:
    """Run drift_naive for each span, return list of result dicts."""
    results: list[dict[str, Any]] = []

    for span in spans:
        mae_drift_ge30: list[float] = []
        mae_naive_ge30: list[float] = []

        for context, actuals in yield_folds(ibja_series, horizon=horizon, min_context=min_context):
            if len(context) < 30:
                continue  # gate: >=30-context folds only

            last_val = float(context.iloc[-1])
            naive_fc = [last_val] * horizon
            drift_fc = forecast_drift_naive(context, horizon, span)

            mae_drift_fold = float(np.mean([abs(drift_fc[h] - actuals[h]) for h in range(horizon)]))
            mae_naive_fold = float(np.mean([abs(naive_fc[h] - actuals[h]) for h in range(horizon)]))
            mae_drift_ge30.append(mae_drift_fold)
            mae_naive_ge30.append(mae_naive_fold)

        n_folds_ge30ctx = len(mae_drift_ge30)
        if n_folds_ge30ctx == 0:
            continue

        mae_variant = float(np.mean(mae_drift_ge30))
        mae_naive = float(np.mean(mae_naive_ge30))
        paired_diffs = [d - n for d, n in zip(mae_drift_ge30, mae_naive_ge30, strict=False)]
        wilcoxon_p = compute_wilcoxon_p(paired_diffs) if n_folds_ge30ctx >= 6 else None

        beats_naive, pct_improvement = apply_gate(
            mae_variant, mae_naive, wilcoxon_p, n_folds_ge30ctx
        )

        results.append(
            {
                "name": f"drift_naive_span{span}",
                "experiment": "Phi7A-Exp1",
                "horizon": horizon,
                "params": {"span": span, "ewma_type": "daily_delta"},
                "mae_variant": round(mae_variant, 2),
                "mae_naive": round(mae_naive, 2),
                "pct_improvement": round(pct_improvement, 5),
                "wilcoxon_p": wilcoxon_p,
                "n_folds_ge30ctx": n_folds_ge30ctx,
                "beats_naive": beats_naive,
            }
        )

    return results
