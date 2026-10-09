"""scripts/calibrate_challenger_size.py -- fix and prove the wrongful-promotion size of each
challenger (ADR 072 Amendment 3, pre-registered 2026-10-09 before any challenger has 20 forward days).

Problem: at a true gain of exactly the 5% bar (the worst wrongful case) the rule's confidence sequence
promoted `p3_roll60` in 8-9% of resampled paths against its allowance alpha / family = 0.05 / 3 =
1.67%, because its daily difference is persistent beyond the rule's 4 Newey-West lags.

Procedure (frozen before running; the numbers below come out of it, none were chosen):
  1. CALIBRATE, per challenger, a variance inflation k (the rule's `variance_inflation`, today 1.5):
     the smallest value on a 0.25 grid, not below the current 1.5, for which the boundary
     wrongful-promotion rate is at most ALLOWANCE for EVERY calibration resampler of that challenger's own
     real 145-day daily series: circular blocks of 10, 20, 40, 60 days and a stationary bootstrap of
     mean block 20 (10,000 paths of 180 decision days each, seed 42).
  2. PROVE on resamplers NOT used in step 1: circular block 30 (seed 7), stationary mean block 40
     (seed 8), and a synthetic AR(1) series with the challenger's own standard deviation and
     autocorrelation 0.2, 0.3, 0.4, 0.5 (seed 9). Passing = the upper 95% Wilson bound of the boundary
     rate is at most ALLOWANCE + 1.0 point on every held-out resampler and the point estimate is at
     most ALLOWANCE. The cost is reported: power at a true 10% and 20% gain, before (k = 1.5) and after.
  3. If any challenger fails the proof, its k is NOT adopted and it stays held back.

The retrospective days are NOT evidence of any gain: every gain is set by hand (as in
scripts/simulate_promotion_v3.py, whose helpers this reuses).

Outputs: reports/challenger_size_calibration.json and .md. Run:
    python scripts/calibrate_challenger_size.py
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
import simulate_promotion_v3 as v3  # shares the loaders, block bootstrap and look helpers
from ml import promotion as pr

OUT_JSON = ROOT / "reports" / "challenger_size_calibration.json"
OUT_MD = ROOT / "reports" / "challenger_size_calibration.md"
SEED = 42
PATHS = 10_000
HORIZON = 180
ALLOWANCE = pr.RULE["alpha"] / pr.RULE["family_size"]  # 1.67% per challenger
K_BASE = float(pr.RULE["variance_inflation"])
K_GRID = tuple(np.round(np.arange(K_BASE, 6.01, 0.25), 2))
CAL_BLOCKS = (10, 20, 40, 60)
CAL_STATIONARY = 20
PROOF_BLOCK = 30
PROOF_STATIONARY = 40
AR_PHIS = (0.2, 0.3, 0.4, 0.5)
G_BOUNDARY = pr.RULE["min_gain"]  # true gain exactly at the bar: mean(e) = 0
NAMES = v3.NAMES


def stationary(rng: np.random.Generator, n_paths: int, length: int, t_len: int, mean_block: float):
    return v3.sp.stationary_indices(rng, n_paths, length, t_len, mean_block)


def e_from_idx(series: dict[str, Any], idx: np.ndarray, j: int, g: float) -> np.ndarray:
    return v3.paths_e(series, idx, j, g, 1.0)


def e_ar1(
    series: dict[str, Any], j: int, phi: float, g: float, rng: np.random.Generator
) -> np.ndarray:
    """Synthetic AR(1) e with the challenger's own sd (of the real e at the bar) and mean g - 0.05."""
    c, d = series["C"], series["D"]
    m_c = float(c.mean())
    e_real = d[:, j] - pr.RULE["min_gain"] * c
    sd = float(e_real.std())
    eps = rng.normal(0.0, sd * np.sqrt(1 - phi**2), (PATHS, HORIZON + 50))
    x = np.zeros_like(eps)
    for t in range(1, eps.shape[1]):
        x[:, t] = phi * x[:, t - 1] + eps[:, t]
    return x[:, 50:] + (g - pr.RULE["min_gain"]) * m_c


def rate(e: np.ndarray, k: float) -> float:
    rule = {**pr.RULE, "variance_inflation": float(k), "horizon_days": HORIZON}
    return float((v3.first_look_hit(e, rule, HORIZON) >= 0).mean())


def calibration_paths(series: dict[str, Any], j: int) -> dict[str, np.ndarray]:
    """Boundary (g = 5%) e-paths for each calibration resampler, built once and reused across k."""
    out: dict[str, np.ndarray] = {}
    t_len = len(series["C"])
    for b in CAL_BLOCKS:
        rng = np.random.default_rng(SEED)
        out[f"block{b}"] = e_from_idx(
            series, v3.block_indices(rng, PATHS, HORIZON, t_len, b), j, G_BOUNDARY
        )
    rng = np.random.default_rng(SEED)
    out[f"stationary{CAL_STATIONARY}"] = e_from_idx(
        series, stationary(rng, PATHS, HORIZON, t_len, CAL_STATIONARY), j, G_BOUNDARY
    )
    return out


def calibrate(series: dict[str, Any], j: int) -> dict[str, Any]:
    paths = calibration_paths(series, j)
    table: dict[str, dict[str, float]] = {}
    chosen = None
    for k in K_GRID:
        rates = {name: rate(e, k) for name, e in paths.items()}
        table[f"{k:.2f}"] = rates
        if chosen is None and max(rates.values()) <= ALLOWANCE:
            chosen = float(k)
            break  # smallest k that passes; larger values are not needed
    return {"k": chosen, "boundary_rate_by_k": table}


def proof_and_cost(series: dict[str, Any], j: int, k: float | None) -> dict[str, Any]:
    t_len = len(series["C"])
    out: dict[str, Any] = {"k_tested": k, "held_out": {}, "power": {}}
    if k is None:
        return out
    held: dict[str, np.ndarray] = {}
    held[f"block{PROOF_BLOCK}_seed7"] = e_from_idx(
        series,
        v3.block_indices(np.random.default_rng(7), PATHS, HORIZON, t_len, PROOF_BLOCK),
        j,
        G_BOUNDARY,
    )
    held[f"stationary{PROOF_STATIONARY}_seed8"] = e_from_idx(
        series,
        stationary(np.random.default_rng(8), PATHS, HORIZON, t_len, PROOF_STATIONARY),
        j,
        G_BOUNDARY,
    )
    for phi in AR_PHIS:
        held[f"ar1_phi{phi}_seed9"] = e_ar1(series, j, phi, G_BOUNDARY, np.random.default_rng(9))
    ok = True
    for name, e in held.items():
        r = rate(e, k)
        hi = v3.sp.wilson(round(r * PATHS), PATHS)[1]
        passed = r <= ALLOWANCE and hi <= ALLOWANCE + 0.01
        ok &= passed
        out["held_out"][name] = {
            "rate": r,
            "upper95": hi,
            "rate_at_base_k": rate(e, K_BASE),
            "passes": passed,
        }
    out["proof_passes"] = bool(ok)
    # cost: power at a true 10% and 20% gain on the calibration-style block-10 resampler, before / after
    idx = v3.block_indices(np.random.default_rng(SEED), PATHS, HORIZON, t_len, 10)
    for g in (0.10, 0.20):
        e = e_from_idx(series, idx, j, g)
        out["power"][f"{round(g * 100)}%"] = {"before_k": rate(e, K_BASE), "after_k": rate(e, k)}
    return out


def build() -> dict[str, Any]:
    series = v3.sp.load_series()
    res: dict[str, Any] = {
        "seed": SEED,
        "paths": PATHS,
        "horizon_decision_days": HORIZON,
        "allowance": ALLOWANCE,
        "k_base": K_BASE,
        "k_grid": [float(k) for k in K_GRID],
        "record_days": len(series["C"]),
        "challengers": {},
    }
    for j, name in enumerate(NAMES):
        cal = calibrate(series, j)
        # a challenger already inside its allowance at the base k keeps the base k (grid starts at it)
        res["challengers"][name] = {**cal, **proof_and_cost(series, j, cal["k"])}
    return res


def pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.2f}%"


def render(r: dict[str, Any]) -> str:
    L = [
        "# Challenger size calibration (ADR 072 Amendment 3)",
        "",
        f"Seed {r['seed']}, {r['paths']:,} paths of {r['horizon_decision_days']} decision days per cell, "
        f"real record of {r['record_days']} days. Allowance per challenger {pct(r['allowance'])}. "
        "All gains are set by hand (VERIFIED: this script). Procedure: see the module docstring.",
        "",
        "## Result",
        "| Challenger | calibrated k (base 1.5) | proof on held-out resamplers | power at true 10% (before -> after) | power at 20% (before -> after) |",
        "|---|---|---|---|---|",
    ]
    for n, v in r["challengers"].items():
        pw = v.get("power", {})
        p10, p20 = pw.get("10%"), pw.get("20%")
        L.append(
            f"| {n} | {v['k']} | {'PASS' if v.get('proof_passes') else ('FAIL' if v['k'] else 'no k found')} | "
            + (f"{pct(p10['before_k'])} -> {pct(p10['after_k'])}" if p10 else "-")
            + " | "
            + (f"{pct(p20['before_k'])} -> {pct(p20['after_k'])}" if p20 else "-")
            + " |"
        )
    for n, v in r["challengers"].items():
        L += ["", f"## {n}: boundary wrongful-promotion rate by k (calibration resamplers)", ""]
        cols = list(next(iter(v["boundary_rate_by_k"].values())).keys())
        L += ["| k | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
        for k, rates in v["boundary_rate_by_k"].items():
            L.append(f"| {k} | " + " | ".join(pct(rates[c]) for c in cols) + " |")
        if v.get("held_out"):
            L += [
                "",
                f"### {n}: held-out proof at k = {v['k']}",
                "| Resampler | rate | upper 95% | rate at base k | passes |",
                "|---|---|---|---|---|",
            ]
            for name, h in v["held_out"].items():
                L.append(
                    f"| {name} | {pct(h['rate'])} | {pct(h['upper95'])} | {pct(h['rate_at_base_k'])} | {h['passes']} |"
                )
    return "\n".join(L) + "\n"


def main() -> int:
    res = build()
    OUT_JSON.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8", newline="\n")
    OUT_MD.write_text(render(res), encoding="utf-8", newline="\n")
    print(OUT_MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
