"""Runner script for Phi10A driver-decomp experiment.

Usage (from repo root):
    conda run -n base python scripts/run_phi10a.py

Writes results to data/experiments/phi10a_driver_decomp.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from ml.backtest import load_ibja_series
from ml.experiments.driver_decomp import load_macro_series, run_driver_decomp_experiment

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ibja = load_ibja_series()
    gold_usd, usd_inr = load_macro_series()
    results = run_driver_decomp_experiment(ibja, gold_usd, usd_inr)

    out = ROOT / "data" / "experiments" / "phi10a_driver_decomp.json"
    out.write_text(json.dumps(results, indent=2) + "\n")
    print(f"Written {len(results)} variants to {out}")
    for r in results:
        pct = r.get("pct_improvement") or 0.0
        pct_display = pct * 100
        print(
            f"  {r['name']}: MAE {r['mae_variant']:.1f} vs naive {r['mae_naive']:.1f} "
            f"({pct_display:+.1f}%) p={r['wilcoxon_p']} beats_naive={r['beats_naive']}"
        )


if __name__ == "__main__":
    main()
