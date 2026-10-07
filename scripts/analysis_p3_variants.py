"""Pre-registered comparison of the P3 slope variants against the live P3 forecast (ADR 071).

Rules, thresholds and the decision rule are frozen in
docs/adr/071-p3-slope-variants-preregistration.md. Read-only over data/nextfix_p3_oos.json (P3) and
data/nextfix_p3_variants_oos.json (V1 ``p3_roll60``, V2 ``p3_monday``).

    python scripts/analysis_p3_variants.py [--out reports/model_audit_2026-10/p3_variants.json]

Forward = ``retro`` false (decision day on or after 2026-10-05). The retrospective block is
EXPLORATORY (the variants were designed after the 143-day diagnostics were seen) and never enters
the decision.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml import nextfix as nf

SEED = 42
N_MIN_FORWARD = 40  # the first scored read (about 8 weeks)
ALPHA_ONE_SIDED = 0.05 / 2  # two variants
MIN_IMPROVEMENT_PCT = 2.0
DM_LAGS = 4
VARIANTS = ("p3_roll60", "p3_monday")


def one_sided_dm_p_better(err_v: np.ndarray, err_b: np.ndarray, lags: int = DM_LAGS) -> float:
    """One-sided Diebold-Mariano p that V has LOWER mean absolute error than the base (Newey-West)."""
    from scipy.stats import norm

    d = err_v - err_b
    n = len(d)
    if n < 3:
        return 1.0
    dc = d - d.mean()
    var = float(dc @ dc) / n
    for lag in range(1, min(lags, n - 1) + 1):
        var += 2.0 * (1.0 - lag / (lags + 1)) * float(dc[lag:] @ dc[:-lag]) / n
    if var <= 0:
        return 0.0 if d.mean() < 0 else 1.0
    return float(norm.cdf(d.mean() / math.sqrt(var / n)))


def _errs(folds: list[dict]) -> np.ndarray:
    return np.array([abs(f["pm1"] - f["pm0"] * math.exp(f["ret"])) for f in folds])


def calibration_slope(folds: list[dict]) -> float | None:
    y = np.array([f["y"] for f in folds])
    r = np.array([f["ret"] for f in folds])
    return float(r @ y / (r @ r)) if len(folds) and (r @ r) > 0 else None


def _mcnemar_p(only_a: int, only_b: int) -> float:
    from scipy.stats import binomtest

    n = only_a + only_b
    return 1.0 if n == 0 else float(binomtest(only_a, n, 0.5).pvalue)


def compare_folds(live: list[dict], var: list[dict]) -> dict:
    """V against the live model on the days both have, as pre-registered."""
    by_day = {f["d0"]: f for f in var}
    pairs = [(a, by_day[a["d0"]]) for a in live if a["d0"] in by_day]
    n = len(pairs)
    if n == 0:
        return {"n": 0}
    a_f = [p[0] for p in pairs]
    v_f = [p[1] for p in pairs]
    err_a, err_v = _errs(a_f), _errs(v_f)
    pm0 = np.array([f["pm0"] for f in a_f])
    pm1 = np.array([f["pm1"] for f in a_f])
    moved = pm1 != pm0
    up = pm1 > pm0
    hit_a = (np.array([f["p_up"] for f in a_f]) > 0.5) == up
    hit_v = (np.array([f["p_up"] for f in v_f]) > 0.5) == up
    only_v = int((hit_v & ~hit_a & moved).sum())
    only_a = int((~hit_v & hit_a & moved).sum())

    def rel(ix: np.ndarray) -> float:
        return 100.0 * (err_v[ix].mean() / err_a[ix].mean() - 1.0)

    d = err_v - err_a
    dc = d - d.mean()
    denom = float(dc @ dc)
    s = sum(
        (1.0 - k / (DM_LAGS + 1)) * float(dc[k:] @ dc[:-k]) / denom
        for k in range(1, min(DM_LAGS, n - 1) + 1)
        if denom > 0
    )
    p = one_sided_dm_p_better(err_v, err_a)
    change = rel(np.arange(n))
    mc = _mcnemar_p(only_v, only_a)
    acc_v = float(hit_v[moved].mean()) if moved.any() else None
    acc_a = float(hit_a[moved].mean()) if moved.any() else None
    dir_ok = acc_v is None or acc_a is None or acc_v >= acc_a or mc >= ALPHA_ONE_SIDED
    return {
        "n": n,
        "n_eff": round(n / max(1.0, 1.0 + 2.0 * s), 1) if denom > 0 else float(n),
        "mae_variant": round(float(err_v.mean()), 2),
        "mae_live": round(float(err_a.mean()), 2),
        "change_pct_variant_vs_live": round(change, 2),
        "change_pct_ci95": [round(v, 2) for v in nf.block_bootstrap_ci(n, rel, seed=SEED)],
        "dm_p_one_sided_variant_better": p,
        "direction_acc_variant": None if acc_v is None else round(acc_v, 4),
        "direction_acc_live": None if acc_a is None else round(acc_a, 4),
        "mcnemar_p": mc,
        "calibration_slope_variant": calibration_slope(v_f),
        "calibration_slope_live": calibration_slope(a_f),
        "clearly_beats": bool(
            change <= -MIN_IMPROVEMENT_PCT and p < ALPHA_ONE_SIDED and dir_ok and n >= N_MIN_FORWARD
        ),
    }


def monday_stratum(live: list[dict], var: list[dict]) -> dict:
    import pandas as pd

    mondays = {f["d0"] for f in live if pd.Timestamp(f["d0"]).dayofweek == 0}
    res = compare_folds(
        [f for f in live if f["d0"] in mondays], [f for f in var if f["d0"] in mondays]
    )
    res.pop("clearly_beats", None)
    return res


def decide(results: dict[str, dict]) -> str:
    winners = [v for v in VARIANTS if results.get(v, {}).get("clearly_beats")]
    if not winners:
        return "P3 stays: no variant clearly beats it (or n_forward < 40)"
    best = min(winners, key=lambda v: results[v]["mae_variant"])
    return f"recommend {best} to GG for promotion (clearly beats P3)"


def summarise(live: list[dict], variants: dict[str, list[dict]]) -> dict:
    out = {}
    for name in VARIANTS:
        res = compare_folds(live, variants.get(name, []))
        res["monday_stratum"] = monday_stratum(live, variants.get(name, []))
        out[name] = res
    return {"results": out, "decision": decide(out)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/model_audit_2026-10/p3_variants.json")
    args = ap.parse_args()
    live = nf.load_oos(nf.P3_OOS_PATH)
    try:
        variants = json.loads(nf.P3_VARIANTS_PATH.read_text()).get("variants", {})
    except (OSError, ValueError):
        variants = {}
    fwd = [f for f in live if f.get("retro") is False]
    fwd_v = {k: [f for f in v if f.get("retro") is False] for k, v in variants.items()}
    out = {
        "preregistration": "docs/adr/071-p3-slope-variants-preregistration.md",
        "n_forward": len(fwd),
        "forward": summarise(fwd, fwd_v)
        if len(fwd) >= N_MIN_FORWARD
        else {
            "scored": False,
            "reason": f"n_forward {len(fwd)} < {N_MIN_FORWARD}",
        },
        "retrospective_EXPLORATORY": summarise(
            [f for f in live if f.get("retro") is True],
            {k: [f for f in v if f.get("retro") is True] for k, v in variants.items()},
        ),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1, default=float) + "\n")
    print(json.dumps(out, indent=1, default=float))


if __name__ == "__main__":
    main()
