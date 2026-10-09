"""scripts/calibrate_challenger_size_round2.py -- round 2 of the wrongful-promotion size calibration
(ADR 072 Amendment 5, pre-registered 2026-10-09 BEFORE this was run) for the two challengers held back
after round 1: ``ensemble`` and ``p3_monday``.

Procedure (frozen in Amendment 5; nothing below was tuned after seeing a result):
  1. CALIBRATE, per challenger, the variance inflation k: the smallest value on a 0.25 grid from 1.5 up
     to 8.0 for which the boundary wrongful-promotion rate (true gain exactly the 5% bar) is at most the
     allowance 0.05 / 3 = 1.67% on EVERY calibration resampler: circular blocks of 10, 20, 30, 40, 60
     days; a stationary bootstrap of mean block 20; synthetic AR(1) series (the challenger's own sd)
     with autocorrelation 0.2, 0.3, 0.4, 0.5. Seed 42, 10,000 paths of 180 decision days.
  2. PROVE on resamplers not used above: circular block 25 (seed 11); stationary mean block 30
     (seed 12); synthetic AR(1) 0.25, 0.35, 0.45 (seed 13). Pass = point rate <= allowance AND upper 95%
     Wilson bound <= allowance + 1 point on every one.
  3. A challenger that fails stays held back. The cost (power at a true 10% and 20% gain, before and
     after) is reported either way.

Outputs: reports/challenger_size_calibration_round2.json and .md. Run:
    python scripts/calibrate_challenger_size_round2.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import calibrate_challenger_size as c1  # round-1 helpers: resamplers, rate(), paths
import simulate_promotion_v3 as v3
from ml import promotion as pr

OUT_JSON = ROOT / "reports" / "challenger_size_calibration_round2.json"
OUT_MD = ROOT / "reports" / "challenger_size_calibration_round2.md"
SEED = c1.SEED
PATHS = c1.PATHS
HORIZON = c1.HORIZON
ALLOWANCE = c1.ALLOWANCE
K_BASE = c1.K_BASE
K_GRID = tuple(np.round(np.arange(K_BASE, 8.01, 0.25), 2))
CAL_BLOCKS = (10, 20, 30, 40, 60)
CAL_STATIONARY = 20
CAL_PHIS = (0.2, 0.3, 0.4, 0.5)
HELD_BLOCK, HELD_BLOCK_SEED = 25, 11
HELD_STAT, HELD_STAT_SEED = 30, 12
HELD_PHIS, HELD_PHI_SEED = (0.25, 0.35, 0.45), 13
CHALLENGERS = ("ensemble", "p3_monday")
G_BOUNDARY = c1.G_BOUNDARY


def calibration_paths(series: dict[str, Any], j: int) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    t_len = len(series["C"])
    for b in CAL_BLOCKS:
        rng = np.random.default_rng(SEED)
        idx = v3.block_indices(rng, PATHS, HORIZON, t_len, b)
        out[f"block{b}"] = c1.e_from_idx(series, idx, j, G_BOUNDARY)
    rng = np.random.default_rng(SEED)
    out[f"stationary{CAL_STATIONARY}"] = c1.e_from_idx(
        series, c1.stationary(rng, PATHS, HORIZON, t_len, CAL_STATIONARY), j, G_BOUNDARY
    )
    for phi in CAL_PHIS:
        out[f"ar1_phi{phi}"] = c1.e_ar1(series, j, phi, G_BOUNDARY, np.random.default_rng(SEED))
    return out


def calibrate(series: dict[str, Any], j: int) -> dict[str, Any]:
    paths = calibration_paths(series, j)
    table: dict[str, dict[str, float]] = {}
    chosen = None
    for k in K_GRID:
        rates = {name: c1.rate(e, k) for name, e in paths.items()}
        table[f"{k:.2f}"] = rates
        if max(rates.values()) <= ALLOWANCE:
            chosen = float(k)
            break
    return {"k": chosen, "boundary_rate_by_k": table}


def proof_and_cost(series: dict[str, Any], j: int, k: float | None) -> dict[str, Any]:
    out: dict[str, Any] = {"k_tested": k, "held_out": {}, "power": {}}
    if k is None:
        return out
    t_len = len(series["C"])
    held: dict[str, np.ndarray] = {
        f"block{HELD_BLOCK}_seed{HELD_BLOCK_SEED}": c1.e_from_idx(
            series,
            v3.block_indices(
                np.random.default_rng(HELD_BLOCK_SEED), PATHS, HORIZON, t_len, HELD_BLOCK
            ),
            j,
            G_BOUNDARY,
        ),
        f"stationary{HELD_STAT}_seed{HELD_STAT_SEED}": c1.e_from_idx(
            series,
            c1.stationary(np.random.default_rng(HELD_STAT_SEED), PATHS, HORIZON, t_len, HELD_STAT),
            j,
            G_BOUNDARY,
        ),
    }
    for phi in HELD_PHIS:
        held[f"ar1_phi{phi}_seed{HELD_PHI_SEED}"] = c1.e_ar1(
            series, j, phi, G_BOUNDARY, np.random.default_rng(HELD_PHI_SEED)
        )
    ok = True
    for name, e in held.items():
        r = c1.rate(e, k)
        hi = v3.sp.wilson(round(r * PATHS), PATHS)[1]
        passed = r <= ALLOWANCE and hi <= ALLOWANCE + 0.01
        ok &= passed
        out["held_out"][name] = {
            "rate": r,
            "upper95": hi,
            "rate_at_base_k": c1.rate(e, K_BASE),
            "passes": passed,
        }
    out["proof_passes"] = bool(ok)
    idx = v3.block_indices(np.random.default_rng(SEED), PATHS, HORIZON, t_len, 10)
    for g in (0.10, 0.20):
        e = c1.e_from_idx(series, idx, j, g)
        out["power"][f"{round(g * 100)}%"] = {
            "before_k": c1.rate(e, K_BASE),
            "after_k": c1.rate(e, k),
        }
    return out


def build() -> dict[str, Any]:
    series = v3.sp.load_series()
    res: dict[str, Any] = {
        "seed": SEED,
        "paths": PATHS,
        "horizon_decision_days": HORIZON,
        "allowance": ALLOWANCE,
        "k_base": K_BASE,
        "rule_sha256": pr.rule_sha256(),
        "record_days": len(series["C"]),
        "challengers": {},
    }
    for name in CHALLENGERS:
        j = v3.NAMES.index(name)
        cal = calibrate(series, j)
        res["challengers"][name] = {**cal, **proof_and_cost(series, j, cal["k"])}
    return res


def pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.2f}%"


def render(r: dict[str, Any]) -> str:
    lines = [
        "# Challenger size calibration, round 2 (ADR 072 Amendment 5)",
        "",
        f"Seed {r['seed']}, {r['paths']:,} paths of {r['horizon_decision_days']} decision days per cell, "
        f"real record of {r['record_days']} days, allowance {pct(r['allowance'])}. All gains are set by hand "
        "(VERIFIED: this script). Procedure: see the module docstring (pre-registered before running).",
        "",
        "## Result",
        "| Challenger | calibrated k (base 1.5) | proof on held-out resamplers | power at true 10% | at 20% |",
        "|---|---|---|---|---|",
    ]
    for n, v in r["challengers"].items():
        pw = v.get("power", {})
        p10, p20 = pw.get("10%"), pw.get("20%")
        verdict = (
            "PASS" if v.get("proof_passes") else ("FAIL" if v["k"] else "no k found up to 8.0")
        )
        lines.append(
            f"| {n} | {v['k']} | {verdict} | "
            + (f"{pct(p10['before_k'])} -> {pct(p10['after_k'])}" if p10 else "-")
            + " | "
            + (f"{pct(p20['before_k'])} -> {pct(p20['after_k'])}" if p20 else "-")
            + " |"
        )
    for n, v in r["challengers"].items():
        lines += ["", f"## {n}: boundary rate by k (calibration resamplers)", ""]
        cols = list(next(iter(v["boundary_rate_by_k"].values())).keys())
        lines += ["| k | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
        for k, rates in v["boundary_rate_by_k"].items():
            lines.append(f"| {k} | " + " | ".join(pct(rates[c]) for c in cols) + " |")
        if v.get("held_out"):
            lines += [
                "",
                f"### {n}: held-out proof at k = {v['k']}",
                "| Resampler | rate | upper 95% | rate at base k | passes |",
                "|---|---|---|---|---|",
            ]
            for name, h in v["held_out"].items():
                lines.append(
                    f"| {name} | {pct(h['rate'])} | {pct(h['upper95'])} | {pct(h['rate_at_base_k'])} | {h['passes']} |"
                )
    return "\n".join(lines) + "\n"


def main() -> int:
    res = build()
    OUT_JSON.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8", newline="\n")
    OUT_MD.write_text(render(res), encoding="utf-8", newline="\n")
    print(OUT_MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
