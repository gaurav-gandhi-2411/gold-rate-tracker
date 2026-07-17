"""Experiment: does enriching ml/direction's feature set or reframing its target move
the walk-forward result above the always-up base rate?

Two independent, non-invasive tests against the EXISTING, unmodified harness
(ml.direction.evaluate.run_walk_forward) and the EXISTING, unmodified gate
(ml.direction.gate.decide_direction_signal) — no tolerance is loosened, no new gate is
built:

  1. Momentum enrichment: FEATURE_COLS (production) vs FEATURE_COLS + row-to-row
     momentum/volatility features derived from the snapshot series itself (1-step %
     change and rolling volatility of gold_usd, usd_inr, dxy, us_10y_yield, vix,
     crude_wti, tips, sensex — computed by diffing consecutive PIT snapshot rows, so no
     new external dependency is introduced into the eval-direction.yml pipeline, which
     reads only snapshots.parquet + ibja_rates.parquet).
  2. Relative-cheapness reframe: instead of "will price rise vs today", label = "will
     next price be above vs below its own trailing K-observation rolling mean"
     (mean-reversion framing). Same production FEATURE_COLS, different label.

This is a read-only diagnostic script — it does not write to data/direction_baseline.json
and does not alter ml/direction/dataset.py's production FEATURE_COLS. Findings are
reported to stdout; a production change is only warranted if a variant clears the
existing probability gate.

Usage:
    python -m ml.experiments.direction_enrichment
"""

from __future__ import annotations

import bisect

import numpy as np
import pandas as pd

from ml.direction.dataset import FEATURE_COLS, IBJA_PARQUET, SNAPSHOTS_PARQUET, build_dataset
from ml.direction.evaluate import MIN_TRAIN_SIZE, run_walk_forward
from ml.direction.gate import decide_direction_signal

# Raw levels already in FEATURE_COLS that we derive 1-step momentum/vol from.
_MOMENTUM_BASE_COLS: list[str] = [
    "gold_usd",
    "usd_inr",
    "dxy",
    "us_10y_yield",
    "vix",
    "crude_wti",
    "tips",
    "sensex",
]
_VOL_WINDOW = 5

MOMENTUM_FEATURE_COLS: list[str] = [f"{c}_chg1" for c in _MOMENTUM_BASE_COLS] + [
    "gold_usd_vol5"
]


def add_momentum_features(dataset: pd.DataFrame) -> pd.DataFrame:
    """Add row-to-row (snapshot-to-snapshot) momentum + volatility columns.

    Computed by diffing consecutive rows of the already-built, as_of_date-sorted
    dataset — no new data source, no leakage (each row only uses its own value and
    prior rows' values).
    """
    out = dataset.sort_values("as_of_date").reset_index(drop=True).copy()
    for col in _MOMENTUM_BASE_COLS:
        out[f"{col}_chg1"] = out[col].pct_change(1)
    log_ret = np.log(out["gold_usd"] / out["gold_usd"].shift(1))
    out["gold_usd_vol5"] = log_ret.rolling(_VOL_WINDOW, min_periods=2).std()
    return out


def add_relative_cheapness_label(
    dataset: pd.DataFrame,
    k: int,
    ibja_path=IBJA_PARQUET,
) -> pd.DataFrame:
    """Add label_binary_cheap_h{1,2}_k{k}: is the h-step-ahead price above the trailing
    K-observation rolling mean of IBJA pm_916 as of today (mean-reversion framing),
    rather than above today's price (drift framing)?

    Leak-free: the rolling mean uses only IBJA observations at/before as_of_date's own
    current_pm916 reading (ibja_pm_916_asof_date), the same PIT anchor build_dataset()
    already uses for the drift label.
    """
    ibja = pd.read_parquet(ibja_path)
    ibja["date"] = ibja["date"].astype(str)
    ibja_sorted = ibja.sort_values("date").reset_index(drop=True)
    ibja_dates: list[str] = ibja_sorted["date"].tolist()
    ibja_pm916: list[float] = ibja_sorted["pm_916"].tolist()

    out = dataset.copy()
    for h, next_col in (("h1", "next_pm916_h1"), ("h2", "next_pm916_h2")):
        labels: list[float | None] = []
        for _, row in out.iterrows():
            ibja_asof = str(row["ibja_pm_916_asof_date"])
            next_val = row[next_col]
            if pd.isna(next_val):
                labels.append(None)
                continue
            idx = bisect.bisect_right(ibja_dates, ibja_asof) - 1
            if idx < 0:
                labels.append(None)
                continue
            window = ibja_pm916[max(0, idx - k + 1) : idx + 1]
            window = [v for v in window if not pd.isna(v)]
            if len(window) < max(3, k // 2):
                labels.append(None)
                continue
            roll_mean = float(np.mean(window))
            labels.append(float(next_val > roll_mean))
        out[f"label_binary_cheap_{h}_k{k}"] = labels
    return out


def _report(label: str, result: dict) -> None:
    log = result["logistic_metrics"]
    gate = decide_direction_signal(result)
    print(f"  [{label}]")
    print(
        f"    n={result['n_test_folds']}  base_rate={result['always_up_baseline_accuracy']:.4f}"
        f"  acc={log['accuracy']:.4f}  brier={log['brier']:.4f}"
        f"  (baseline brier={log['always_up_brier']:.4f})  ece={log['ece']:.4f}"
        f"  p={log['p_value']:.4f}  sig={log['significant_at_05']}"
    )
    print(f"    probability_gate: ship={gate['ship']} - {gate['reason']}")


def run_momentum_experiment(dataset: pd.DataFrame) -> None:
    print("=== Experiment 1: momentum/volatility feature enrichment ===")
    enriched = add_momentum_features(dataset)
    combined_cols = FEATURE_COLS + MOMENTUM_FEATURE_COLS
    for hkey, label_col in (("h1", "label_binary_h1"), ("h2", "label_binary_h2")):
        print(f" -- horizon {hkey} --")
        baseline_result = run_walk_forward(
            enriched, feature_cols=FEATURE_COLS, min_train_size=MIN_TRAIN_SIZE, label_col=label_col
        )
        _report(f"{hkey} baseline (production FEATURE_COLS)", baseline_result)
        candidate_result = run_walk_forward(
            enriched, feature_cols=combined_cols, min_train_size=MIN_TRAIN_SIZE, label_col=label_col
        )
        _report(f"{hkey} + momentum/vol ({len(MOMENTUM_FEATURE_COLS)} new cols)", candidate_result)
        print()


def run_relative_cheapness_experiment(dataset: pd.DataFrame) -> None:
    print("=== Experiment 2: relative-cheapness reframed target ===")
    for k in (5, 10, 20):
        enriched = add_relative_cheapness_label(dataset, k=k)
        for hkey in ("h1", "h2"):
            label_col = f"label_binary_cheap_{hkey}_k{k}"
            sub = enriched[enriched[label_col].notna()].reset_index(drop=True)
            if len(sub) < MIN_TRAIN_SIZE + 5:
                print(f" -- horizon {hkey}, k={k}: insufficient rows ({len(sub)}), skipping --")
                continue
            print(f" -- horizon {hkey}, k={k} (n_labelled={len(sub)}) --")
            result = run_walk_forward(
                sub, feature_cols=FEATURE_COLS, min_train_size=MIN_TRAIN_SIZE, label_col=label_col
            )
            _report(f"{hkey} cheap-vs-{k}obs-rolling-mean", result)
            print()


def main() -> None:
    dataset = build_dataset(verbose=False)
    print(f"Dataset: {len(dataset)} rows, {dataset['as_of_date'].min()} to {dataset['as_of_date'].max()}\n")
    run_momentum_experiment(dataset)
    run_relative_cheapness_experiment(dataset)


if __name__ == "__main__":
    main()
