"""Reproduce the promoted next-fix model's published numbers from scratch, and test its USD/INR leg.

A. ``reproduction``: rebuild every out-of-sample fold with ml.nextfix (empty track record, fresh
   macro cache) and compare with the committed data/nextfix_oos.json fold by fold, then score.
B. ``independent_ridge``: a separate implementation of the ridge component (no ml.nextfix model
   code) on the same pairs; its folds must match the production ridge to numerical precision.
C. ``usdinr_lagged``: the same model with gold at D's COMEX settlement but USD/INR from the
   previous day, an input that is known at the decision moment under every clock convention
   (the audit_nextfix_leak.py finding: the repo's conservative INR=X clock says D's bar is not).

    python scripts/analysis_nextfix_reproduce.py [--out reports/model_audit_2026-10/reproduction.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml import nextfix as nf


def rebuild(pairs: pd.DataFrame) -> list[dict]:
    """Every out-of-sample fold from scratch (nothing read from or written to the OOS file)."""
    return nf.update_oos(pairs, [])


def compare_folds(new: list[dict], old: list[dict]) -> dict:
    """Fold-by-fold agreement between a rebuild and the committed record, on shared days."""
    a = {f["d0"]: f for f in new}
    b = {f["d0"]: f for f in old}
    common = sorted(set(a) & set(b))
    d_ret = np.array([abs(a[k]["ret"] - b[k]["ret"]) for k in common])
    d_p = np.array([abs(a[k]["p_up"] - b[k]["p_up"]) for k in common])
    return {
        "n_common": len(common),
        "only_in_rebuild": sorted(set(a) - set(b)),
        "only_in_committed": sorted(set(b) - set(a)),
        "max_abs_diff_ret": float(d_ret.max()) if len(d_ret) else None,
        "max_abs_diff_p_up": float(d_p.max()) if len(d_p) else None,
        "n_direction_flips": int(sum((a[k]["p_up"] > 0.5) != (b[k]["p_up"] > 0.5) for k in common)),
    }


def independent_ridge_folds(pairs: pd.DataFrame, folds: list[dict]) -> dict:
    """Closed-form ridge with the same alpha grid and leave-one-out selection, written here from
    the formula (no sklearn RidgeCV, no ml.nextfix model code): compare with production ridge."""
    resolved = pairs[pairs["d1"].notna()].reset_index(drop=True)
    rows = []
    for i in range(nf.MIN_TRAIN, len(resolved)):
        row = resolved.iloc[i]
        train = resolved[resolved["d1"] <= row["d0"]]
        if len(train) < nf.MIN_TRAIN:
            continue
        x = train[nf.FEATURES].to_numpy(float)
        y = train["y"].to_numpy(float)
        xm, ym = x.mean(0), y.mean()
        xc, yc = x - xm, y - ym
        best = None
        for alpha in np.logspace(-6, 0, 13):
            # leave-one-out error of ridge: e_i / (1 - h_ii), h from the hat matrix
            h_mat = xc @ np.linalg.solve(xc.T @ xc + alpha * np.eye(xc.shape[1]), xc.T)
            resid = yc - h_mat @ yc
            loo = float(np.mean((resid / (1.0 - np.diag(h_mat) - 1.0 / len(y))) ** 2))
            if best is None or loo < best[0]:
                best = (loo, alpha)
        beta = np.linalg.solve(xc.T @ xc + best[1] * np.eye(xc.shape[1]), xc.T @ yc)
        pred = float(ym + (row[nf.FEATURES].to_numpy(float) - xm) @ beta)
        rows.append({"d0": row["d0"].strftime("%Y-%m-%d"), "ridge_indep": pred})
    ind = pd.DataFrame(rows)
    # production ridge on the same fold, recomputed with sklearn RidgeCV for an apples-to-apples diff
    from sklearn.linear_model import RidgeCV

    diffs = []
    for r in rows:
        d0 = pd.Timestamp(r["d0"])
        train = resolved[resolved["d1"] <= d0]
        row = resolved[resolved["d0"] == d0].iloc[0]
        prod = float(
            RidgeCV(alphas=np.logspace(-6, 0, 13))
            .fit(train[nf.FEATURES].to_numpy(float), train["y"].to_numpy(float))
            .predict(row[nf.FEATURES].to_numpy(float)[None, :])[0]
        )
        diffs.append(abs(prod - r["ridge_indep"]))
    return {
        "n": len(ind),
        "max_abs_diff_vs_sklearn_ridgecv": float(max(diffs)) if diffs else None,
        "note": "RidgeCV's default (gcv) selection can differ from explicit LOO at ties",
    }


def lagged_inr_pairs(macro: pd.DataFrame, full: pd.DataFrame) -> pd.DataFrame:
    """Pairs whose world series is gold(D) x usd_inr(D-1): no same-day INR=X bar."""
    m = macro.copy()
    m["usd_inr"] = m["usd_inr"].shift(1)
    return nf.build_pairs(full.dropna(subset=["pm"]).reset_index(drop=True), nf.global_series(m))


def score(folds: list[dict]) -> dict:
    ev = nf.evaluate(folds)
    keep = [
        "n",
        "mae_model",
        "mae_flat",
        "mae_change_pct",
        "mae_change_ci95",
        "dm_p",
        "wilcoxon_p",
        "range_coverage",
        "range_coverage_ci95",
        "range_mean_width",
    ]
    out = {k: ev.get(k) for k in keep}
    d = ev["direction"]
    out["direction_accuracy"] = d.get("accuracy")
    out["always_up_accuracy"] = d.get("always_up_accuracy", d.get("naive_accuracy"))
    out["direction_gate"] = ev["direction_gate"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/model_audit_2026-10/reproduction.json")
    args = ap.parse_args()
    from ml.macro import load_macro_features

    macro = load_macro_features()
    full = nf.load_ibja_full()
    pairs = nf.build_pairs(
        full.dropna(subset=["pm"]).reset_index(drop=True), nf.global_series(macro)
    )
    committed = nf.load_oos()
    rebuilt = rebuild(pairs)
    lagged = rebuild(lagged_inr_pairs(macro, full))
    out = {
        "committed_record": score(committed),
        "reproduction": {
            "vs_committed": compare_folds(rebuilt, committed),
            "scored": score(rebuilt),
        },
        "independent_ridge": independent_ridge_folds(pairs, committed),
        "usdinr_lagged": {
            "vs_committed": compare_folds(lagged, committed),
            "scored": score(lagged),
        },
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1, default=str) + "\n")
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
