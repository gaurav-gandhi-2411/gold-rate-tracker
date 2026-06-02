"""Experiment 3 (Phi7C): horizon sweep at h=10 and h=20.

Re-runs Chronos + flat-naive at h=10 and h=20 using the existing run_backtest() harness.
Also runs drift_naive at h=10/20 using Exp-1 machinery.
Reports the gap-vs-horizon curve (h=5 reference from data/backtest.json).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from ml.backtest import load_ibja_series, run_backtest
from ml.experiments.drift_naive import apply_gate, run_drift_naive_experiment
from ml.metrics import compute_wilcoxon_p

ROOT = Path(__file__).resolve().parent.parent.parent
BACKTEST_JSON = ROOT / "data" / "backtest.json"


def extract_ge30ctx_gate_metrics(
    bt_result: dict[str, Any],
) -> dict[str, Any]:
    """Extract gate-relevant metrics from run_backtest() output, restricted to >=30-context folds.

    run_backtest() aggregates over all folds; the Phi7 gate requires only >=30-context folds.
    """
    folds = bt_result["folds"]
    ge30 = [f for f in folds if not f["sub_30_context"]]
    n = len(ge30)

    if n == 0:
        return {
            "n_folds_ge30ctx": 0,
            "mae_variant": None,
            "mae_naive": None,
            "pct_improvement": None,
            "wilcoxon_p": None,
            "beats_naive": False,
        }

    mae_c_per_fold = [float(np.mean(f["mae_chronos_per_h"])) for f in ge30]
    mae_n_per_fold = [float(np.mean(f["mae_naive_per_h"])) for f in ge30]

    mae_variant = float(np.mean(mae_c_per_fold))
    mae_naive = float(np.mean(mae_n_per_fold))
    paired_diffs = [c - nv for c, nv in zip(mae_c_per_fold, mae_n_per_fold, strict=False)]
    wilcoxon_p = compute_wilcoxon_p(paired_diffs) if n >= 6 else None

    beats_naive, pct_improvement = apply_gate(mae_variant, mae_naive, wilcoxon_p, n)

    return {
        "n_folds_ge30ctx": n,
        "mae_variant": round(mae_variant, 2),
        "mae_naive": round(mae_naive, 2),
        "pct_improvement": round(pct_improvement, 5),
        "wilcoxon_p": wilcoxon_p,
        "beats_naive": beats_naive,
    }


def run_horizon_sweep(
    horizons: list[int],
    model_version: str = "chronos-bolt-tiny",
) -> list[dict[str, Any]]:
    """Run Chronos + drift_naive at each horizon; return list of result dicts.

    Parameters
    ----------
    horizons : list of horizon values to sweep (e.g., [10, 20]).
    model_version : label for the model version tag in results.
    """
    from ml.chronos_forecast import CHRONOS_BOLT_TINY_REVISION, load_chronos_pipeline

    ibja = load_ibja_series()
    print(f"  IBJA: {len(ibja)} rows  ({ibja.index[0]} -> {ibja.index[-1]})")

    print("  Loading Chronos pipeline (once)...")
    pipeline = load_chronos_pipeline()
    print("  Pipeline loaded.")

    results: list[dict[str, Any]] = []

    for h in horizons:
        print(f"\n  Running Chronos backtest at h={h}...")
        bt = run_backtest(ibja, pipeline, horizon=h)
        gate = extract_ge30ctx_gate_metrics(bt)

        results.append(
            {
                "name": f"chronos_h{h}",
                "experiment": "Phi7C-Exp3",
                "horizon": h,
                "params": {
                    "model": model_version,
                    "revision": CHRONOS_BOLT_TINY_REVISION[:8],
                    "n_folds_total": bt["n_folds"],
                    "n_folds_sub_30_context": bt["n_folds_sub_30_context"],
                },
                "mae_variant": gate["mae_variant"],
                "mae_naive": gate["mae_naive"],
                "pct_improvement": gate["pct_improvement"],
                "wilcoxon_p": gate["wilcoxon_p"],
                "n_folds_ge30ctx": gate["n_folds_ge30ctx"],
                "beats_naive": gate["beats_naive"],
            }
        )

        print(f"  Running drift_naive at h={h} (spans=[5,10,20])...")
        drift_results = run_drift_naive_experiment(ibja, horizon=h)
        for r in drift_results:
            r["experiment"] = "Phi7C-Exp3"
        results.extend(drift_results)

    return results


def load_h5_reference() -> dict[str, Any]:
    """Load h=5 Chronos reference from data/backtest.json."""
    if not BACKTEST_JSON.exists():
        return {}
    bt = json.loads(BACKTEST_JSON.read_text())
    folds = bt.get("folds", [])
    ge30 = [f for f in folds if not f["sub_30_context"]]
    n = len(ge30)
    if n == 0:
        return {}
    mae_c = float(np.mean([np.mean(f["mae_chronos_per_h"]) for f in ge30]))
    mae_n = float(np.mean([np.mean(f["mae_naive_per_h"]) for f in ge30]))
    pct = (mae_n - mae_c) / mae_n if mae_n > 0 else 0.0
    return {
        "horizon": 5,
        "mae_chronos_ge30": round(mae_c, 2),
        "mae_naive_ge30": round(mae_n, 2),
        "pct_chronos_vs_naive": round(pct * 100, 2),
    }
