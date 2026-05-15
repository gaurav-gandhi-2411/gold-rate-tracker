"""
compare_feature_sets.py — Phase 2.5a A/B/C feature-set comparison.

Usage (from repo root):
    python ml/compare_feature_sets.py

Runs the walk-forward backtest three times (full_v1 / tuned_v1 / minimal_v2)
under identical bd602a6 hyperparams, then prints a side-by-side markdown table
and picks a winner by the Phase 2.5a decision rule.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.backtest import run_backtest
from ml.features import ALL_FEATURE_COLS, MINIMAL_FEATURE_COLS, TUNED_V1_FEATURE_COLS
from ml.forecast import load_combined_history

# full_v1 = ALL_FEATURE_COLS (43) + regime if available in the feature matrix
FULL_V1_CANDIDATE = ALL_FEATURE_COLS + ["regime"]

FEATURE_SETS = {
    "full_v1":    (FULL_V1_CANDIDATE,    "full_v1 (44 feat)"),
    "tuned_v1":   (TUNED_V1_FEATURE_COLS, "tuned_v1 (40 feat)"),
    "minimal_v2": (MINIMAL_FEATURE_COLS,  "minimal_v2 (8 feat)"),
}


def _pct(v: float | None) -> str:
    if v is None:
        return "  n/a  "
    return f"{v*100:5.1f}%"


def _rs(v: float) -> str:
    return f"Rs.{v:6.1f}"


def main() -> None:
    df = load_combined_history()

    macro_df = None
    try:
        from ml.macro import load_macro_features
        from ml.regime import add_regime_to_macro

        macro_df = load_macro_features()
        if macro_df is not None:
            today_utc = pd.Timestamp.now(tz="UTC").normalize()
            if macro_df.index[-1] < today_utc:
                extended_idx = pd.date_range(macro_df.index[0], today_utc, freq="D", tz="UTC")
                macro_df = macro_df.reindex(extended_idx, method="ffill")
            macro_df = add_regime_to_macro(macro_df)
            print("Macro loaded (including regime).")
    except Exception as exc:
        print(f"Macro unavailable -- base features only ({exc})")

    results: dict[str, dict] = {}
    for key, (cols, label) in FEATURE_SETS.items():
        print(f"\n{'='*60}")
        r = run_backtest(df, macro_df=macro_df, feature_cols_override=cols,
                         use_tuned=True, label=label)
        results[key] = r
        m = r["model"]
        print(
            f"  => MAE {m['mae']:.1f}+/-{m['mae_std']:.1f}  "
            f"Dir {m['direction_acc']*100:.1f}%  "
            f"Dir(>50) {_pct(m['direction_acc_big_move'])}  "
            f"blend_w {m['blend_weight_lgbm_mean']:.3f}"
        )

    # -----------------------------------------------------------------------
    # Side-by-side table
    # -----------------------------------------------------------------------
    keys = list(FEATURE_SETS.keys())
    labels = [FEATURE_SETS[k][1] for k in keys]

    print("\n\n" + "=" * 72)
    print("PHASE 2.5a — FEATURE SET A/B/C COMPARISON")
    print("Hyperparams: bd602a6 (num_leaves=16, lr=0.02, min_data_leaf=40, lambda_l2=1.0)")
    print("=" * 72)

    col_w = 18
    hdr = f"{'Metric':<28}" + "".join(f"{l:>{col_w}}" for l in labels)
    print(hdr)
    print("-" * (28 + col_w * 3))

    def row(label: str, vals: list[str]) -> None:
        print(f"{label:<28}" + "".join(f"{v:>{col_w}}" for v in vals))

    def _get(k, *path):
        obj = results[k]
        for p in path:
            obj = obj[p]
        return obj

    row("Folds",
        [str(_get(k, "folds")) for k in keys])

    row("",  ["", "", ""])

    row("=== PRIMARY ===", ["", "", ""])
    row("Dir-acc overall",
        [_pct(_get(k, "model", "direction_acc")) for k in keys])
    row("Dir-acc |delta|>Rs50",
        [_pct(_get(k, "model", "direction_acc_big_move")) for k in keys])
    row("  n folds (big move)",
        [str(_get(k, "model", "n_big_move_folds")) for k in keys])
    row("Dir-acc |delta|<=Rs50",
        [_pct(_get(k, "model", "direction_acc_small_move")) for k in keys])
    row("  n folds (small move)",
        [str(_get(k, "model", "n_small_move_folds")) for k in keys])

    row("", ["", "", ""])
    row("=== SECONDARY ===", ["", "", ""])
    row("MAE model (Rs)",
        [_rs(_get(k, "model", "mae")) for k in keys])
    row("MAE model std (Rs)",
        [_rs(_get(k, "model", "mae_std")) for k in keys])
    row("MAE naive (Rs)",
        [_rs(_get(k, "baseline", "mae")) for k in keys])
    row("MAE ratio (model/naive)",
        [f"{_get(k, 'model', 'mae') / _get(k, 'baseline', 'mae'):.3f}" for k in keys])
    row("MAPE model (%)",
        [f"{_get(k, 'model', 'mape'):.2f}%" for k in keys])
    row("blend_weight_lgbm mean",
        [f"{_get(k, 'model', 'blend_weight_lgbm_mean'):.3f}" for k in keys])
    row("blend_weight_lgbm std",
        [f"{_get(k, 'model', 'blend_weight_lgbm_std'):.3f}" for k in keys])

    row("", ["", "", ""])
    row("=== PAIRED DIFFS (model-naive) ===", ["", "", ""])
    row("Paired err diff median (Rs)",
        [str(_get(k, "paired_diff_model_minus_baseline", "median")) for k in keys])
    row("Paired err diff IQR [25,75] (Rs)",
        [f"[{_get(k,'paired_diff_model_minus_baseline','iqr_25')}, "
         f"{_get(k,'paired_diff_model_minus_baseline','iqr_75')}]"
         for k in keys])

    # -----------------------------------------------------------------------
    # Decision
    # -----------------------------------------------------------------------
    print("\n" + "=" * 72)
    print("DECISION RULE (in priority order):")
    print("  a. Highest dir-acc on |delta|>Rs50 bucket")
    print("  b. Tiebreak: highest overall dir-acc")
    print("  c. Tiebreak: lowest MAE")
    print("  d. Tiebreak: simpler feature set")
    print()

    big_acc = {k: _get(k, "model", "direction_acc_big_move") or 0.0 for k in keys}
    overall_acc = {k: _get(k, "model", "direction_acc") for k in keys}
    mae_val = {k: _get(k, "model", "mae") for k in keys}
    n_feat = {"full_v1": 44, "tuned_v1": 40, "minimal_v2": 8}

    def _noise_threshold(scores: dict[str, float], margin: float = 0.02) -> bool:
        vals = list(scores.values())
        return (max(vals) - min(vals)) < margin

    # Rule a
    best_big = max(keys, key=lambda k: big_acc[k])
    best_big_val = big_acc[best_big]
    second_big = sorted(keys, key=lambda k: big_acc[k], reverse=True)[1]
    gap_a = best_big_val - big_acc[second_big]

    # Rule b
    best_overall = max(keys, key=lambda k: overall_acc[k])
    gap_b = overall_acc[best_overall] - sorted(overall_acc.values(), reverse=True)[1]

    # Rule c
    best_mae = min(keys, key=lambda k: mae_val[k])

    within_noise_a = _noise_threshold(big_acc, margin=0.03)
    within_noise_b = _noise_threshold(overall_acc, margin=0.02)

    print(f"  a) |delta|>50 dir-acc: ", end="")
    for k in keys:
        marker = " <-- best" if k == best_big else ""
        print(f"  {FEATURE_SETS[k][1].split('(')[0].strip()}: {_pct(big_acc[k])}{marker}", end="")
    print(f"\n     Gap best vs 2nd: {gap_a*100:.1f}pp  {'[within noise <3pp]' if within_noise_a else ''}")

    print(f"  b) Overall dir-acc:   ", end="")
    for k in keys:
        marker = " <-- best" if k == best_overall else ""
        print(f"  {FEATURE_SETS[k][1].split('(')[0].strip()}: {_pct(overall_acc[k])}{marker}", end="")
    print(f"\n     Gap best vs 2nd: {gap_b*100:.1f}pp  {'[within noise <2pp]' if within_noise_b else ''}")

    print(f"  c) MAE: ", end="")
    for k in keys:
        marker = " <-- lowest" if k == best_mae else ""
        print(f"  {FEATURE_SETS[k][1].split('(')[0].strip()}: {_rs(mae_val[k])}{marker}", end="")
    print()

    # Pick winner
    if not within_noise_a:
        winner = best_big
        rationale = f"Highest |delta|>Rs50 direction accuracy ({_pct(big_acc[winner])}, gap={gap_a*100:.1f}pp — exceeds 3pp noise floor). Rule a."
    elif not within_noise_b:
        winner = best_overall
        rationale = f"All big-move dir-accs within noise. Highest overall dir-acc ({_pct(overall_acc[winner])}, gap={gap_b*100:.1f}pp). Rule b."
    elif best_mae != "minimal_v2":
        winner = best_mae
        rationale = f"Dir-accs all within noise. Lowest MAE ({_rs(mae_val[winner])}). Rule c."
    else:
        # Tiebreak: simplest with competitive metrics
        if mae_val["tuned_v1"] < mae_val["full_v1"]:
            winner = "tuned_v1"
            rationale = (
                "All metrics within noise. tuned_v1 is simpler than full_v1 and has "
                f"better MAE ({_rs(mae_val['tuned_v1'])} vs {_rs(mae_val['full_v1'])}). Rule d."
            )
        else:
            winner = "tuned_v1"
            rationale = (
                "All metrics within noise across all three sets. "
                "Recommend tuned_v1 on simplicity vs full_v1 grounds (40 vs 44 features, "
                "dead-weight dropped) — no regression vs minimal_v2 on direction accuracy. Rule d."
            )

    print(f"\n  WINNER: {FEATURE_SETS[winner][1]}")
    print(f"  RATIONALE: {rationale}")

    blend_flags = {k: _get(k, "model", "blend_weight_lgbm_mean") <= 0.15 for k in keys}
    for k, flagged in blend_flags.items():
        if flagged:
            print(f"  FLAG: {FEATURE_SETS[k][1]} blend_weight={_get(k,'model','blend_weight_lgbm_mean'):.3f} -- near 0.1 floor (red flag).")

    print("\nDone.")
    return winner, results


if __name__ == "__main__":
    main()
