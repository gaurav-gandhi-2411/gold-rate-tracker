"""Run Phi7A Exp-1 (drift-naive) and append 3 results to data/experiments/phi7_results.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.backtest import load_ibja_series
from ml.experiments.drift_naive import run_drift_naive_experiment

RESULTS_PATH = ROOT / "data" / "experiments" / "phi7_results.json"


def main() -> None:
    print("Loading IBJA series...")
    ibja = load_ibja_series()
    print(f"  {len(ibja)} rows  ({ibja.index[0]} to {ibja.index[-1]})")

    print("\nRunning Exp-1: drift_naive (spans=[5, 10, 20], h=5)...")
    results = run_drift_naive_experiment(ibja)

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: list = json.loads(RESULTS_PATH.read_text()) if RESULTS_PATH.exists() else []
    existing.extend(results)
    RESULTS_PATH.write_text(json.dumps(existing, indent=2) + "\n")

    print("\nExp-1 results (Phi7A gate: >=2% improvement, p<0.05, >=30 folds):")
    for r in results:
        status = "BEATS NAIVE" if r["beats_naive"] else "does not beat naive"
        print(
            f"  {r['name']}: mae_variant={r['mae_variant']:.2f}  mae_naive={r['mae_naive']:.2f}"
            f"  pct={r['pct_improvement'] * 100:+.2f}%  p={r['wilcoxon_p']}  "
            f"n_ge30ctx={r['n_folds_ge30ctx']}  => {status}"
        )

    print(f"\nResults written to {RESULTS_PATH} ({len(existing)} total entries).")


if __name__ == "__main__":
    main()
