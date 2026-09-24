"""scripts/measure_preregistration_embargo.py — provenance for ADR 038
amendment A1: scores the frozen config J on the live h2 dataset three ways
and writes reports/preregistration_embargo_a1.json.

  1. original protocol (no embargo, every fold)        — the leaky number
  2. embargo on label_date_h2, every fold               — the honest number
  3. amended live arm (embargo, post-2026-09-23 only)   — the confirmatory test

Shadow/diagnostic only. Run: python scripts/measure_preregistration_embargo.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import warnings
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

# Run as `python scripts/<name>.py` from the repo root (weekly-backtest.yml does):
# sys.path[0] is then scripts/, so the repo root must be added for `import ml`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.direction.config_sweep import run_config_sweep
from ml.direction.dataset import build_dataset
from ml.direction.preregistration import (
    EMBARGO_LABEL_DATE_COL,
    PREREGISTERED_CONFIG,
    run_live_arm,
    score_config,
)

OUTPUT_PATH = Path("reports/preregistration_embargo_a1.json")


def _score(dataset: pd.DataFrame, embargo: bool) -> dict:
    cfg = PREREGISTERED_CONFIG
    result = run_config_sweep(
        dataset,
        feature_cols=cfg["feature_cols"],
        label_col=cfg["label_col"],
        model=cfg["model"],
        class_weight=cfg["class_weight"],
        calibrate_gbm=cfg["calibrate_gbm"],
        min_train_size=cfg["min_train_size"],
        return_raw=True,
        embargo_label_date_col=EMBARGO_LABEL_DATE_COL if embargo else None,
    )
    raw = result["raw"]
    scored = score_config(raw["y_true"], raw["y_prob"], horizon=cfg["horizon"])
    scored["first_as_of"] = raw["as_of_date"][0] if raw["as_of_date"] else None
    scored["last_as_of"] = raw["as_of_date"][-1] if raw["as_of_date"] else None
    if embargo:
        scored["embargo_violations"] = sum(
            1
            for as_of, max_label in zip(raw["as_of_date"], raw["train_max_label_date"], strict=True)
            if max_label is None or max_label >= as_of
        )
    return scored


def main() -> int:
    warnings.filterwarnings("ignore")
    # A1 provenance was measured on the pre-G2 labels, which bridge holes in the
    # IBJA record; kept on them so reports/preregistration_embargo_a1.json stays
    # reproducible. Item 3 now runs the v2 live arm (ADR 042) on this dataset.
    dataset = build_dataset(require_consecutive=False)
    labelled = dataset[dataset[PREREGISTERED_CONFIG["label_col"]].notna()]
    live = run_live_arm(dataset)
    out = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        ).stdout.strip(),
        "dataset": {
            "rows_with_h2_label": len(labelled),
            "as_of_min": str(labelled["as_of_date"].min()),
            "as_of_max": str(labelled["as_of_date"].max()),
        },
        "original_no_embargo_all_folds": _score(dataset, embargo=False),
        "embargo_all_folds": _score(dataset, embargo=True),
        "amended_live_arm": {
            k: live[k] for k in ("n", "effective_n", "p_value", "accuracy", "scored_as_of_dates")
        },
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
