"""Phi7D: Non-bull subset diagnostic for drift_naive_span20 at h=20.

Subset rule: non-up folds — folds where the realised h=20 price change is <= 0
(price flat or down vs context last value). This is the simplest defensible
partition; it is transparent and requires no threshold tuning.

This is a DIAGNOSTIC ONLY run. No gate evaluation. No beats_naive boolean.
See spec.md and ADR 018.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.backtest import _MIN_CONTEXT_DAYS, load_ibja_series, yield_folds
from ml.experiments.drift_naive import forecast_drift_naive

RESULTS_PATH = ROOT / "data" / "experiments" / "phi7_results.json"
HORIZON = 20
SPAN = 20  # drift_naive_span20 — the one gate pass from Phi7C

# Full-set reference from Phi7C-Exp3 (phi7_results.json, h=20, span=20)
FULLSET_MAE_DRIFT = 721.57
FULLSET_MAE_FLAT = 760.94
FULLSET_PCT_IMPROVEMENT = 0.05174  # +5.17%
FULLSET_N_FOLDS = 129


def main() -> None:
    print("Phi7D: Non-bull subset diagnostic (drift_naive_span20 @ h=20)")
    print("=" * 65)
    print(f"Subset rule: non-up folds — realised h={HORIZON} change <= 0")
    print()

    ibja = load_ibja_series()
    print(f"IBJA series: {len(ibja)} rows ({ibja.index[0]} -> {ibja.index[-1]})")

    bull_mae_drift: list[float] = []
    bull_mae_flat: list[float] = []
    non_bull_mae_drift: list[float] = []
    non_bull_mae_flat: list[float] = []
    n_total_ge30 = 0

    for context, actuals in yield_folds(ibja, horizon=HORIZON, min_context=_MIN_CONTEXT_DAYS):
        if len(context) < 30:
            continue  # mirror the gate: >=30-context folds only

        n_total_ge30 += 1
        last_val = float(context.iloc[-1])
        realised_change = actuals[-1] - last_val  # h=20 price minus context last

        flat_fc = [last_val] * HORIZON
        drift_fc = forecast_drift_naive(context, HORIZON, SPAN)

        mae_drift_fold = float(np.mean([abs(drift_fc[h] - actuals[h]) for h in range(HORIZON)]))
        mae_flat_fold = float(np.mean([abs(flat_fc[h] - actuals[h]) for h in range(HORIZON)]))

        if realised_change <= 0:
            non_bull_mae_drift.append(mae_drift_fold)
            non_bull_mae_flat.append(mae_flat_fold)
        else:
            bull_mae_drift.append(mae_drift_fold)
            bull_mae_flat.append(mae_flat_fold)

    n_non_bull = len(non_bull_mae_drift)
    n_bull = len(bull_mae_drift)

    print(f"Total >=30-context folds: {n_total_ge30}  (full-set reference: {FULLSET_N_FOLDS})")
    print(f"Bull folds (realised change > 0): {n_bull}")
    print(f"Non-bull folds (realised change <= 0): {n_non_bull}")
    print()

    if n_non_bull == 0:
        print(
            "FINDING: No out-of-regime folds exist in the backtest window (2022-2026).\n"
            "ADR 018's predicted sign flip is untestable on current data — this itself\n"
            "confirms the confounding (dataset is essentially a single bull regime)."
        )
        result_entry = {
            "name": "drift_naive_span20_non_bull_subset",
            "experiment": "Phi7D-Exp4",
            "horizon": HORIZON,
            "subset_rule": "non_up_folds_realised_h20_change_lte_0",
            "diagnostic": True,
            "below_gate_power": True,
            "n_non_bull_folds": 0,
            "n_total_ge30_folds": n_total_ge30,
            "finding": "no_out_of_regime_folds",
            "fullset_reference": {
                "n_folds_ge30ctx": FULLSET_N_FOLDS,
                "mae_drift_span20": FULLSET_MAE_DRIFT,
                "mae_flat_naive": FULLSET_MAE_FLAT,
                "pct_improvement": FULLSET_PCT_IMPROVEMENT,
            },
            "subset_mae_drift_span20": None,
            "subset_mae_flat_naive": None,
            "subset_signed_improvement": None,
        }
        _append_result(result_entry)
        return

    mae_drift = float(np.mean(non_bull_mae_drift))
    mae_flat = float(np.mean(non_bull_mae_flat))
    signed_pct = (mae_flat - mae_drift) / mae_flat if mae_flat > 0 else 0.0
    sign_flipped = signed_pct < 0

    print(f"Non-bull subset (n={n_non_bull}):")
    print(f"  mae_drift_span20 : {mae_drift:.2f}")
    print(f"  mae_flat_naive   : {mae_flat:.2f}")
    print(f"  subset_signed_pct: {signed_pct * 100:+.2f}%")
    print("  (negative = drift LOSES = predicted sign flip)")
    print()
    print("Full-set reference (Phi7C-Exp3, all >=30-ctx folds):")
    print(
        f"  mae_drift={FULLSET_MAE_DRIFT}  mae_flat={FULLSET_MAE_FLAT}"
        f"  pct={FULLSET_PCT_IMPROVEMENT * 100:+.2f}%"
    )
    print()
    if sign_flipped:
        print("Sign flip OBSERVED: drift loses on non-bull folds (predicted by ADR 018).")
    else:
        print("Sign flip NOT observed: drift still wins on non-bull folds.")
    print()
    print("NOTE: n_folds below gate-power threshold — no gate verdict, no beats_naive.")

    result_entry = {
        "name": "drift_naive_span20_non_bull_subset",
        "experiment": "Phi7D-Exp4",
        "horizon": HORIZON,
        "subset_rule": "non_up_folds_realised_h20_change_lte_0",
        "diagnostic": True,
        "below_gate_power": True,
        "n_non_bull_folds": n_non_bull,
        "n_total_ge30_folds": n_total_ge30,
        "fullset_reference": {
            "n_folds_ge30ctx": FULLSET_N_FOLDS,
            "mae_drift_span20": FULLSET_MAE_DRIFT,
            "mae_flat_naive": FULLSET_MAE_FLAT,
            "pct_improvement": FULLSET_PCT_IMPROVEMENT,
        },
        "subset_mae_drift_span20": round(mae_drift, 2),
        "subset_mae_flat_naive": round(mae_flat, 2),
        "subset_signed_improvement": round(signed_pct, 5),
        "sign_flip_observed": sign_flipped,
    }
    _append_result(result_entry)


def _append_result(entry: dict) -> None:
    existing: list = json.loads(RESULTS_PATH.read_text()) if RESULTS_PATH.exists() else []
    existing.append(entry)
    RESULTS_PATH.write_text(json.dumps(existing, indent=2) + "\n")
    print(f"\nDiagnostic entry appended to {RESULTS_PATH}")
    print(f"Total entries in phi7_results.json: {len(existing)}")


if __name__ == "__main__":
    main()
