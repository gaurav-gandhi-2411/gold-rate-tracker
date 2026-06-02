"""Walk-forward backtest at h=5 comparing Chronos-Bolt-Tiny against naive_5d.

Usage (from repo root):
    python -m ml.backtest --run      # run backtest, write data/backtest.json
    python -m ml.backtest --report   # print headline from data/backtest.json

Walk-forward protocol (expanding window, no leakage):
  - Minimum context: 8 rows (Chronos minimum, same as _MIN_CONTEXT_DAYS).
  - Step: 1 day forward per fold.
  - Horizon: h=1..5 calendar days.
  - Fold included only when all 5 actuals exist.
  - Naive baseline: flat hold at context's last value for all 5 steps.
  - Pipeline loaded once outside the fold loop.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from ml.chronos_forecast import (
    _MIN_CONTEXT_DAYS,
    CHRONOS_BOLT_TINY_REVISION,
    forecast_ibja,
    load_chronos_pipeline,
)
from ml.metrics import (
    compute_decision_accuracy_h5,
    compute_dir_acc_h5,
    compute_mae_per_horizon,
    compute_peak_timing_error,
    compute_pi_coverage,
    compute_wilcoxon_p,
)

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
IBJA_PARQUET = DATA_DIR / "ibja_rates.parquet"
BACKTEST_JSON = DATA_DIR / "backtest.json"

_HORIZON = 5
logger = logging.getLogger(__name__)


def load_ibja_series(parquet_path: Path = IBJA_PARQUET) -> pd.Series:
    """Load IBJA-916-PM daily series as INR/g, sorted ascending, non-null."""
    df = pd.read_parquet(parquet_path)
    df = df.sort_values("date").dropna(subset=["pm_916"])
    return df.set_index("date")["pm_916"] / 10.0


def yield_folds(
    ibja_series: pd.Series,
    horizon: int = _HORIZON,
    min_context: int = _MIN_CONTEXT_DAYS,
):
    """Yield (context, actuals) fold pairs using the canonical walk-forward split.

    Same fold boundaries as run_backtest() — use for paired-fold experiments
    (Wilcoxon requires identical fold indices across variants).

    Yields
    ------
    context : pd.Series — rows strictly before the forecast window.
    actuals : list[float] — the next ``horizon`` actual values.
    """
    n = len(ibja_series)
    for context_end_idx in range(min_context - 1, n - horizon):
        context = ibja_series.iloc[: context_end_idx + 1]
        actuals_slice = ibja_series.iloc[context_end_idx + 1 : context_end_idx + 1 + horizon]
        if len(actuals_slice) < horizon:
            break
        assert (
            context.index[-1] < actuals_slice.index[0]
        ), f"leakage: context ends {context.index[-1]}, actuals start {actuals_slice.index[0]}"
        yield context, actuals_slice.values.tolist()


def run_backtest(
    ibja_series: pd.Series,
    pipeline,
    horizon: int = _HORIZON,
    min_context: int = _MIN_CONTEXT_DAYS,
) -> dict:
    """Execute walk-forward backtest. Returns result dict; caller writes to disk.

    No leakage: each fold's context window strictly precedes its actuals window.
    Load the pipeline once before calling — it is expensive to load per-fold.

    Parameters
    ----------
    ibja_series : date-indexed daily Series, values in INR/g.
    pipeline    : ChronosBoltPipeline (pre-loaded).
    horizon     : forecast steps per fold (default 5).
    min_context : minimum rows required for first fold context (default 8).
    """
    n = len(ibja_series)
    folds: list[dict] = []

    for context_end_idx in range(min_context - 1, n - horizon):
        context = ibja_series.iloc[: context_end_idx + 1]
        actuals_slice = ibja_series.iloc[context_end_idx + 1 : context_end_idx + 1 + horizon]

        if len(actuals_slice) < horizon:
            break

        # Leakage invariant: last context date strictly before first actuals date.
        assert (
            context.index[-1] < actuals_slice.index[0]
        ), f"leakage: context ends {context.index[-1]}, actuals start {actuals_slice.index[0]}"

        actuals = actuals_slice.values.tolist()
        context_last = float(context.iloc[-1])
        naive = [context_last] * horizon

        try:
            fc_df = forecast_ibja(pipeline, context, horizon=horizon)
        except Exception as exc:
            logger.warning("backtest fold %d forecast failed: %s", len(folds), exc)
            continue

        p10 = fc_df["p10"].tolist()
        p50 = fc_df["p50"].tolist()
        p90 = fc_df["p90"].tolist()

        mae_chronos_per_h = [round(abs(p50[h] - actuals[h]), 2) for h in range(horizon)]
        mae_naive_per_h = [round(abs(naive[h] - actuals[h]), 2) for h in range(horizon)]
        in_pi_80 = [p10[h] <= actuals[h] <= p90[h] for h in range(horizon)]

        folds.append(
            {
                "fold_id": len(folds),
                "context_end_date": str(context.index[-1]),
                "context_size": len(context),
                "actuals": [round(v, 2) for v in actuals],
                "chronos_p10": [round(v, 2) for v in p10],
                "chronos_p50": [round(v, 2) for v in p50],
                "chronos_p90": [round(v, 2) for v in p90],
                "naive": [round(v, 2) for v in naive],
                "mae_chronos_per_h": mae_chronos_per_h,
                "mae_naive_per_h": mae_naive_per_h,
                "in_pi_80": in_pi_80,
                "sub_30_context": len(context) < 30,
            }
        )

    if not folds:
        raise RuntimeError(
            f"No valid backtest folds — need at least {min_context + horizon} IBJA rows, got {n}."
        )

    # --- Aggregate ---
    actuals_m = np.array([f["actuals"] for f in folds])  # (n_folds, horizon)
    p50_m = np.array([f["chronos_p50"] for f in folds])  # (n_folds, horizon)
    p10_m = np.array([f["chronos_p10"] for f in folds])  # (n_folds, horizon)
    p90_m = np.array([f["chronos_p90"] for f in folds])  # (n_folds, horizon)
    naive_m = np.array([f["naive"] for f in folds])  # (n_folds, horizon)
    context_lasts = np.array([f["naive"][0] for f in folds])  # (n_folds,)
    mae_c_per_fold = np.array([f["mae_chronos_per_h"] for f in folds]).mean(axis=1)
    mae_n_per_fold = np.array([f["mae_naive_per_h"] for f in folds]).mean(axis=1)

    mae_chronos_ph = compute_mae_per_horizon(actuals_m, p50_m)
    mae_naive_ph = compute_mae_per_horizon(actuals_m, naive_m)
    mae_5d_avg_chronos = float(np.mean(mae_chronos_ph))
    mae_5d_avg_naive = float(np.mean(mae_naive_ph))

    dir_acc = compute_dir_acc_h5(context_lasts, p50_m[:, -1], actuals_m[:, -1])
    pi_cov = compute_pi_coverage(actuals_m, p10_m, p90_m)
    decision_acc = compute_decision_accuracy_h5(context_lasts, p50_m, actuals_m)
    peak_err = compute_peak_timing_error(p50_m, actuals_m)

    paired_diffs = (mae_c_per_fold - mae_n_per_fold).tolist()
    paired_diff_median = round(float(np.median(paired_diffs)), 2)
    n_folds = len(folds)
    wilcoxon_p = compute_wilcoxon_p(paired_diffs) if n_folds >= 6 else None

    return {
        "backtest_run_at": pd.Timestamp.utcnow().isoformat(),
        "n_folds": n_folds,
        "n_folds_sub_30_context": int(np.sum([f["sub_30_context"] for f in folds])),
        "horizon": horizon,
        "model_version": f"amazon/chronos-bolt-tiny@{CHRONOS_BOLT_TINY_REVISION[:8]}",
        "mae_5d_avg_chronos": round(mae_5d_avg_chronos, 2),
        "mae_5d_avg_naive": round(mae_5d_avg_naive, 2),
        "mae_chronos_per_h": [round(v, 2) for v in mae_chronos_ph],
        "mae_naive_per_h": [round(v, 2) for v in mae_naive_ph],
        "dir_acc_5d_chronos": round(dir_acc, 4),
        "dir_acc_5d_naive": 0.5,
        "pi_coverage_80_per_h": [round(v, 4) for v in pi_cov],
        "pi_coverage_80_5d_avg": round(float(np.mean(pi_cov)), 4),
        "decision_acc": decision_acc,
        "peak_timing_err_days_median": peak_err,
        "paired_diff_median": paired_diff_median,
        "wilcoxon_signed_rank_p": wilcoxon_p,
        "insufficient_evidence": n_folds < 6,
        "folds": folds,
    }


def _print_report(result: dict) -> None:
    n = result["n_folds"]
    mc = result["mae_5d_avg_chronos"]
    mn = result["mae_5d_avg_naive"]
    pct = (mn - mc) / mn * 100 if mn > 0 else 0.0
    direction = "better" if mc < mn else "worse"
    mph_c = result["mae_chronos_per_h"]
    mph_n = result["mae_naive_per_h"]
    da = result["dir_acc_5d_chronos"]
    pi = result["pi_coverage_80_5d_avg"]
    da_obj = result["decision_acc"]
    prec = da_obj["precision"]
    rec = da_obj["recall"]
    wp = result["wilcoxon_signed_rank_p"]
    insuf = result.get("insufficient_evidence", False)

    print(f"\nChronos vs naive (h=5 walk-forward, n folds = {n}):")
    print(
        f"  MAE 5d avg:  Chronos Rs.{mc:.1f}  Naive Rs.{mn:.1f}  (Chronos {abs(pct):.1f}% {direction})"
    )
    h_str = "  ".join(f"h{i + 1}=Rs.{mph_c[i]:.0f}/Rs.{mph_n[i]:.0f}" for i in range(len(mph_c)))
    print(f"  Per-horizon (chronos/naive): {h_str}")
    print(f"  Direction acc (h=5): Chronos {da * 100:.1f}%  Naive 50.0%")
    print(f"  PI 80 coverage (avg): {pi * 100:.1f}%  (target 80%)")
    prec_s = f"{prec * 100:.1f}%" if prec is not None else "N/A"
    rec_s = f"{rec * 100:.1f}%" if rec is not None else "N/A"
    print(f"  Decision precision: {prec_s}  Recall: {rec_s}")
    if insuf:
        print(
            f"  NOTE: n={n} folds — insufficient_evidence: true (sub-30 context, directional only)"
        )
    if wp is not None:
        print(f"  Paired Wilcoxon p: {wp:.4f}")
    else:
        print(f"  Paired Wilcoxon p: null (n={n}; scipy unavailable or n<6)")
    print()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Walk-forward h=5 backtest (Chronos vs naive)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run", action="store_true", help="Run backtest, write data/backtest.json")
    group.add_argument(
        "--report", action="store_true", help="Print headline from data/backtest.json"
    )
    args = parser.parse_args()

    if args.report:
        if not BACKTEST_JSON.exists():
            print("data/backtest.json not found — run --run first.")
            raise SystemExit(1)
        result = json.loads(BACKTEST_JSON.read_text())
        _print_report(result)
        raise SystemExit(0)

    # --run path
    if not IBJA_PARQUET.exists():
        print(f"IBJA parquet not found at {IBJA_PARQUET}")
        raise SystemExit(1)

    ibja_series = load_ibja_series()
    n = len(ibja_series)
    print(f"IBJA series: {n} rows  ({ibja_series.index[0]} to {ibja_series.index[-1]})")

    print("Loading Chronos pipeline...")
    pipeline = load_chronos_pipeline()
    print("Pipeline loaded. Running walk-forward folds...")

    result = run_backtest(ibja_series, pipeline)
    _print_report(result)

    DATA_DIR.mkdir(exist_ok=True)
    BACKTEST_JSON.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Backtest written to {BACKTEST_JSON} ({result['n_folds']} folds).")


if __name__ == "__main__":
    main()
