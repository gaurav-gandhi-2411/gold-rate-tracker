"""Run Phi7C Exp-3 (horizon sweep h=10,20) and append results to data/experiments/phi7_results.json.

This script loads the Chronos pipeline and runs walk-forward backtests at h=10 and h=20.
Expected runtime: ~3-5 minutes (Chronos inference on ~160 folds x 2 horizons).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.experiments.horizon_sweep import load_h5_reference, run_horizon_sweep

RESULTS_PATH = ROOT / "data" / "experiments" / "phi7_results.json"

_HORIZONS = [10, 20]


def main() -> None:
    print("Phi7C: Horizon sweep (h=10, h=20)")
    print("=" * 60)

    h5_ref = load_h5_reference()
    if h5_ref:
        print(
            f"\nh=5 reference (data/backtest.json, >=30-context folds):"
            f"  mae_chronos={h5_ref['mae_chronos_ge30']}  mae_naive={h5_ref['mae_naive_ge30']}"
            f"  gap={h5_ref['pct_chronos_vs_naive']:+.2f}%"
        )

    print("\nLoading IBJA + Chronos pipeline...")
    results = run_horizon_sweep(_HORIZONS)

    existing: list = json.loads(RESULTS_PATH.read_text()) if RESULTS_PATH.exists() else []
    existing.extend(results)
    RESULTS_PATH.write_text(json.dumps(existing, indent=2) + "\n")

    # Print horizon-gap curve
    print("\n\nHorizon-gap curve (Chronos vs flat-naive, >=30-context folds):")
    print(f"  h=5  (reference): gap={h5_ref.get('pct_chronos_vs_naive', 'N/A'):+.2f}%")
    for r in results:
        if r["name"].startswith("chronos_"):
            h = r["horizon"]
            pct = r["pct_improvement"]
            beats = r["beats_naive"]
            print(
                f"  h={h}: mae_variant={r['mae_variant']}  mae_naive={r['mae_naive']}"
                f"  gap={pct * 100:+.2f}%  p={r['wilcoxon_p']}  beats_naive={beats}"
            )

    print("\nDrift-naive results at extended horizons:")
    for r in results:
        if r["name"].startswith("drift_"):
            print(
                f"  {r['name']} h={r['horizon']}: "
                f"mae={r['mae_variant']}  naive={r['mae_naive']}"
                f"  gap={r['pct_improvement'] * 100:+.2f}%  beats={r['beats_naive']}"
            )

    print(f"\nResults written to {RESULTS_PATH} ({len(existing)} total entries).")


if __name__ == "__main__":
    main()
