"""Run Phi7B Exp-2 (premium-carry) and append result to data/experiments/phi7_results.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.backtest import load_ibja_series
from ml.experiments.premium_carry import load_macro_series, run_premium_carry_experiment

RESULTS_PATH = ROOT / "data" / "experiments" / "phi7_results.json"


def main() -> None:
    print("Loading IBJA series...")
    ibja = load_ibja_series()
    print(f"  {len(ibja)} rows  ({ibja.index[0]} -> {ibja.index[-1]})")

    print("Loading macro series (gold_usd, usd_inr)...")
    gold_usd, usd_inr = load_macro_series()
    print(
        f"  Macro: {len(gold_usd)} rows"
        f"  ({gold_usd.index[0].date()} -> {gold_usd.index[-1].date()})"
    )

    print("\nRunning Exp-2: premium_carry (flat carry, h=5)...")
    result = run_premium_carry_experiment(ibja, gold_usd, usd_inr)

    existing: list = json.loads(RESULTS_PATH.read_text()) if RESULTS_PATH.exists() else []
    existing.append(result)
    RESULTS_PATH.write_text(json.dumps(existing, indent=2) + "\n")

    print("\nExp-2 result (Phi7B gate: >=2% improvement, p<0.05, >=30 folds):")
    if result["mae_variant"] is not None:
        status = "BEATS NAIVE" if result["beats_naive"] else "does not beat naive"
        print(
            f"  premium_carry_flat: mae_variant={result['mae_variant']:.2f}"
            f"  mae_naive={result['mae_naive']:.2f}"
            f"  pct={result['pct_improvement'] * 100:+.2f}%"
            f"  p={result['wilcoxon_p']}"
            f"  n_ge30ctx={result['n_folds_ge30ctx']} -> {status}"
        )
        print(f"  Skipped (no macro): {result['params']['n_folds_skipped_no_macro']}")
        print(f"  Skipped (sub-30 context): {result['params']['n_folds_skipped_sub30']}")
    else:
        print(f"  ERROR: {result.get('note')}")

    print(f"\nResult appended to {RESULTS_PATH} ({len(existing)} total entries).")


if __name__ == "__main__":
    main()
