"""Pre-registered comparison of the promoted next-fix model against one-line parity rules (ADR 067).

Rules, test, thresholds and decision rule are frozen in
docs/adr/067-nextfix-parity-baseline-preregistration.md. Read-only: scores the committed
out-of-sample record (data/nextfix_oos.json) against rules computed from the same pairs.

    python scripts/analysis_nextfix_parity.py [--out reports/model_audit_2026-10/parity.json]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml import nextfix as nf

SEED = 42  # block bootstrap seed
ALPHA = 0.05
N_RULES = 3
MIN_IMPROVEMENT_PCT = 2.0  # champion-challenger margin (rule 41)
FORWARD_FROM = "2026-10-01"
DM_LAGS = 4


def effective_n(d: np.ndarray, lags: int = DM_LAGS) -> float:
    """n / (1 + 2 * sum of Bartlett-weighted autocorrelations of the paired difference)."""
    n = len(d)
    dc = d - d.mean()
    denom = float(dc @ dc)
    if n < lags + 2 or denom <= 0:
        return float(n)
    s = sum((1.0 - k / (lags + 1)) * float(dc[k:] @ dc[:-k]) / denom for k in range(1, lags + 1))
    return float(n / max(1.0, 1.0 + 2.0 * s))


def bh_adjust(p: list[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values."""
    m = len(p)
    order = np.argsort(p)
    out = np.empty(m)
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, p[i] * m / rank)
        out[i] = prev
    return [float(v) for v in out]


def mcnemar_exact_p(only_a: int, only_b: int) -> float:
    """Two-sided exact McNemar from the discordant counts."""
    from scipy.stats import binomtest

    n = only_a + only_b
    return 1.0 if n == 0 else float(binomtest(only_a, n, 0.5).pvalue)


def rule_forecasts(pairs: pd.DataFrame) -> pd.DataFrame:
    """Point forecasts of ln(pm1/pm0) from P1, P2, P3 for every row of ``pairs``.

    P3's slope for row i uses only pairs whose target fix was on or before the row's decision day.
    """
    out = pd.DataFrame({"d0": pairs["d0"], "P1": pairs["x_glob"], "P2": pairs["bdev"]})
    b = []
    for _, row in pairs.iterrows():
        tr = pairs[pairs["d1"].notna() & (pairs["d1"] <= row["d0"])]
        if len(tr) < nf.MIN_TRAIN:
            b.append(np.nan)
            continue
        x, y = tr["x_glob"].to_numpy(float), tr["y"].to_numpy(float)
        b.append(float(x @ y / (x @ x)))
    out["P3_b"] = b
    out["P3"] = out["P3_b"] * pairs["x_glob"]
    return out


def compare(folds: pd.DataFrame, rule: str) -> dict:
    """E (the recorded ensemble) vs ``rule`` on the folds, as pre-registered."""

    pm0 = folds["pm0"].to_numpy(float)
    pm1 = folds["pm1"].to_numpy(float)
    err_e = np.abs(pm1 - pm0 * np.exp(folds["ret"].to_numpy(float)))
    err_r = np.abs(pm1 - pm0 * np.exp(folds[rule].to_numpy(float)))
    d = err_e - err_r
    n = len(folds)

    def rel(ix: np.ndarray) -> float:
        return 100.0 * (err_e[ix].mean() / err_r[ix].mean() - 1.0)

    moved = pm1 != pm0
    up = pm1 > pm0
    hit_e = (folds["p_up"].to_numpy(float) > 0.5) == up
    hit_r = (folds[rule].to_numpy(float) > 0) == up
    only_e = int(np.sum(hit_e & ~hit_r & moved))
    only_r = int(np.sum(~hit_e & hit_r & moved))
    return {
        "rule": rule,
        "n": n,
        "n_eff": round(effective_n(d), 1),
        "mae_ensemble": round(float(err_e.mean()), 2),
        "mae_rule": round(float(err_r.mean()), 2),
        "change_pct_ensemble_vs_rule": round(rel(np.arange(n)), 2),
        "change_pct_ci95": [round(v, 2) for v in nf.block_bootstrap_ci(n, rel, seed=SEED)],
        "dm_p_two_sided": nf.diebold_mariano_p(err_e, err_r, DM_LAGS),
        "direction_n_moved": int(moved.sum()),
        "direction_acc_ensemble": round(float(hit_e[moved].mean()), 4) if moved.any() else None,
        "direction_acc_rule": round(float(hit_r[moved].mean()), 4) if moved.any() else None,
        "mcnemar_only_ensemble": only_e,
        "mcnemar_only_rule": only_r,
        "mcnemar_p": mcnemar_exact_p(only_e, only_r),
    }


def clearly_beats(res: dict) -> bool:
    """The pre-registered "E clearly beats R" test."""
    acc_e, acc_r = res["direction_acc_ensemble"], res["direction_acc_rule"]
    dir_not_worse = (
        acc_e is None or acc_r is None or acc_e >= acc_r or res["mcnemar_p"] >= ALPHA / N_RULES
    )
    return bool(
        res["change_pct_ensemble_vs_rule"] <= -MIN_IMPROVEMENT_PCT
        and res["dm_p_two_sided"] < ALPHA / N_RULES
        and dir_not_worse
    )


def decision(results: list[dict]) -> str:
    """Pre-registered decision rule over P1, P2, P3 (simplicity order)."""
    not_beaten = [r["rule"] for r in results if not r["clearly_beats"]]
    if not not_beaten:
        return "keep the ensemble: it clearly beats P1, P2 and P3"
    return f"recommend the simplest rule the ensemble does not clearly beat: {not_beaten[0]}"


def summarise(folds: pd.DataFrame) -> dict:
    res = [compare(folds, r) for r in ("P1", "P2", "P3")]
    bh = bh_adjust([r["dm_p_two_sided"] for r in res])
    for r, p_bh in zip(res, bh, strict=True):
        r["dm_p_bh"] = p_bh
        r["dm_p_bonferroni"] = min(1.0, r["dm_p_two_sided"] * N_RULES)
        r["clearly_beats"] = clearly_beats(r)
    return {"results": res, "decision": decision(res)}


def stratum_gap(folds: pd.DataFrame) -> pd.Series:
    """Calendar days between pm0's and pm1's IBJA dates (1 = consecutive weekdays)."""
    return (pd.to_datetime(folds["d1"]) - pd.to_datetime(folds["d0"])).dt.days


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/model_audit_2026-10/parity.json")
    args = ap.parse_args()

    macro = None
    try:
        from ml.macro import load_macro_features

        macro = load_macro_features()
    except Exception as exc:
        print(f"macro cache unavailable ({exc}); using the label seed only", file=sys.stderr)
    full = nf.load_ibja_full()
    glob = nf.global_series(macro)
    pairs = nf.build_pairs(full.dropna(subset=["pm"]).reset_index(drop=True), glob)
    rules = rule_forecasts(pairs)
    pairs = pairs.assign(d0s=pairs["d0"].dt.strftime("%Y-%m-%d"))
    folds = pd.DataFrame(nf.load_oos())
    m = folds.merge(
        rules.assign(d0s=rules["d0"].dt.strftime("%Y-%m-%d")), left_on="d0", right_on="d0s"
    )
    m = m.drop(columns=["d0_y"]).rename(columns={"d0_x": "d0"}).dropna(subset=["P1", "P2", "P3"])
    out = {
        "preregistration": "docs/adr/067-nextfix-parity-baseline-preregistration.md",
        "retrospective": {
            "first_d0": m["d0"].iloc[0],
            "last_d0": m["d0"].iloc[-1],
            **summarise(m),
        },
        "forward": None,
    }
    fwd = m[m["d0"] >= FORWARD_FROM]
    out["forward"] = (
        {"n": len(fwd), **summarise(fwd)} if len(fwd) >= 20 else {"n": len(fwd), "scored": False}
    )
    gap = stratum_gap(m)
    out["strata_gap_days"] = {
        "consecutive_1_day": int((gap == 1).sum()),
        "multi_day_gap": int((gap > 1).sum()),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1, default=float) + "\n")
    print(json.dumps(out, indent=1, default=float))
    if math.isnan(out["retrospective"]["results"][0]["mae_rule"]):
        raise SystemExit("NaN result: refusing to publish")


if __name__ == "__main__":
    main()
