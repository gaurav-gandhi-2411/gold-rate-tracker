"""scripts/simulate_promotion_v3.py -- operating characteristics of ADR 072 Amendment 2 (rule v3).

Deterministic (seed 42). Reads the committed RETROSPECTIVE per-day records ONLY for the variance,
autocorrelation and cross-correlation of the daily loss difference between the live model (P3) and
each challenger, and for the hold forecast's same-day error. Those retrospective days are NOT
evidence that any challenger gains anything: every simulated gain is set by hand.

Method. A circular FIXED-block bootstrap of whole days (block length BLOCK, the same resampled days
for all three challengers, so autocorrelation and cross-correlation are kept) on the real series
(C = champion error, D = C - challenger error, H = hold error |pm1 - pm0|). D is centred and a true
mean gain g x (champion mean error) is added. This is a different resampler from the stationary
bootstrap of scripts/simulate_sequential_promotion.py, which is the cross-check.

Compared on the same paths: rule v2 (horizon 180 calendar days ~ 128 decision days), rule v3
(horizon 180 DECISION days), rule v3 + a control variate (CUPED-style): e' = e - theta x (H - mu_H)
with theta, mu_H frozen from the retrospective record. Also reported: the control variate's variance
reduction (in sample and out of sample) and its size when the volatility regime changes.

Outputs: reports/promotion_v3_simulation.json and .md. Run:
    python scripts/simulate_promotion_v3.py
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
import simulate_sequential_promotion as sp
from ml import promotion as pr

OUT_JSON = ROOT / "reports" / "promotion_v3_simulation.json"
OUT_MD = ROOT / "reports" / "promotion_v3_simulation.md"
SEED = 42
PATHS = 10_000
BLOCK = 10  # fixed circular block: > 2x the 4 Newey-West lags; roll60 has lag 1-4 AC of 0.12-0.19
BLOCKS_SENS = (5, 20)
GAINS = (0.0, 0.05, 0.10, 0.20, 0.40)
SCALES = (0.7, 1.0, 1.3)  # volatility regime of the forward window relative to the record
HORIZON_V3 = 180  # decision days
NAMES = sp.CHALLENGERS


def block_indices(rng: np.random.Generator, n_paths: int, length: int, t_len: int, block: int):
    """Circular moving-block bootstrap: consecutive runs of ``block`` days from random starts."""
    n_blocks = -(-length // block)
    starts = rng.integers(0, t_len, (n_paths, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % t_len
    return idx.reshape(n_paths, -1)[:, :length]


def control_variate(series: dict[str, Any]) -> dict[str, Any]:
    """theta_j and mu_H from the retrospective record, plus the variance reduction measured in
    sample and out of sample (fit on the first half, applied to the second)."""
    c, d, h = series["C"], series["D"], series["H"]
    half = len(c) // 2
    out: dict[str, Any] = {"mu_H": float(h.mean()), "n": len(c), "per_challenger": {}}
    for j, name in enumerate(NAMES):
        e = (
            d[:, j] - 0.05 * c
        )  # e at gain 0 over the 5% bar; the slope does not depend on the level

        def slope(x: np.ndarray, y: np.ndarray) -> float:
            xc = x - x.mean()
            return float(xc @ (y - y.mean()) / (xc @ xc))

        th = slope(h, e)
        th_half = slope(h[:half], e[:half])
        resid_all = e - th * (h - h.mean())
        resid_oos = e[half:] - th_half * (h[half:] - h[:half].mean())
        out["per_challenger"][name] = {
            "theta": th,
            "var_reduction_in_sample": 1 - float(resid_all.var() / e.var()),
            "var_reduction_out_of_sample": 1 - float(resid_oos.var() / e[half:].var()),
            "theta_first_half": th_half,
        }
    return out


def paths_e(series, idx, j, g, scale, theta=None, mu_h=None, mode="additive"):
    """e (and e' with a control variate) on resampled days for challenger j at true gain g.

    mode "additive": the real centred D plus a constant g x (champion mean error), i.e. the gain
    is independent of the day's error. mode "proportional": the real centred D plus g x the day's
    own champion error, i.e. the challenger is better by the same share on every day, which adds
    the day-to-day variation of the champion's error to D (the more conservative construction)."""
    c, d, h = series["C"], series["D"], series["H"]
    dc = d - d.mean(0)
    m_c = float(c.mean())
    c_p, h_p = scale * c[idx], scale * h[idx]
    gain_term = g * m_c if mode == "additive" else g * c[idx]
    d_p = scale * (dc[idx, j] + gain_term)
    e = d_p - pr.RULE["min_gain"] * c_p
    if theta is not None:
        e = e - theta * (h_p - mu_h)
    return e


def first_look_hit(e: np.ndarray, rule: dict[str, Any], n_h: int) -> np.ndarray:
    return sp.first_true(np.nan_to_num(pr.cs_lower_bounds(e[:, :n_h], rule), nan=-1.0) > 0)


def summarise(first: np.ndarray, n_h: int) -> dict[str, Any]:
    prom = first >= 0
    lo, hi = sp.wilson(int(prom.sum()), len(first))
    return {
        "promoted_share": float(prom.mean()),
        "ci95": [lo, hi],
        "median_days_given_promoted": float(np.median(first[prom] + 1)) if prom.any() else None,
        "expected_days_to_decision": float(np.where(prom, first + 1, n_h).mean()),
        "promoted_by_day": {
            str(k): float(((first >= 0) & (first < k)).mean())
            for k in (40, 60, 90, 128, 180)
            if k <= n_h
        },
    }


def power_table(
    series, rule_v2, rule_v3, cv, block, rng_seed=SEED, with_cuped=True, mode="additive"
):
    n_v2, _ = sp.horizon_days(rule_v2)
    rng = np.random.default_rng(rng_seed)
    idx = block_indices(rng, PATHS, HORIZON_V3, len(series["C"]), block)
    out: dict[str, Any] = {"decision_days_v2_horizon": n_v2}
    for g in GAINS:
        row: dict[str, Any] = {}
        any3 = {
            "v2": np.zeros(PATHS, bool),
            "v3": np.zeros(PATHS, bool),
            "v3_cuped": np.zeros(PATHS, bool),
        }
        for j, name in enumerate(NAMES):
            e = paths_e(series, idx, j, g, 1.0, mode=mode)
            f2, f3 = first_look_hit(e, rule_v2, n_v2), first_look_hit(e, rule_v3, HORIZON_V3)
            row[name] = {"v2": summarise(f2, n_v2), "v3": summarise(f3, HORIZON_V3)}
            any3["v2"] |= f2 >= 0
            any3["v3"] |= f3 >= 0
            if with_cuped:
                th = cv["per_challenger"][name]["theta"]
                ec = paths_e(series, idx, j, g, 1.0, th, cv["mu_H"], mode=mode)
                fc = first_look_hit(ec, rule_v3, HORIZON_V3)
                row[name]["v3_cuped"] = summarise(fc, HORIZON_V3)
                any3["v3_cuped"] |= fc >= 0
        row["any_of_three"] = {
            k: float(v.mean()) for k, v in any3.items() if v.any() or k != "v3_cuped"
        }
        out[f"{round(g * 100)}%"] = row
    return out


def horizon_options(series, rule_v3):
    """What a longer horizon would buy at a true 10% and 15% gain (the cost: a slower decision and
    a later retirement). Both gain constructions; per challenger; the rule is otherwise unchanged."""
    out: dict[str, Any] = {}
    for h in (180, 270, 360):
        rng = np.random.default_rng(SEED)
        idx = block_indices(rng, PATHS, h, len(series["C"]), BLOCK)
        for mode in ("additive", "proportional"):
            for g in (0.10, 0.15):
                cell = {}
                for j, n in enumerate(NAMES):
                    e = paths_e(series, idx, j, g, 1.0, mode=mode)
                    cell[n] = float((first_look_hit(e, rule_v3, h) >= 0).mean())
                out[f"horizon={h}|{mode}|gain={round(g * 100)}%"] = cell
    return out


def regime_size(series, rule_v3, cv):
    """Promotion share at the 5% boundary (g = 0.05, the worst wrongful case) and at 0% gain when
    the forward window's volatility differs from the record's by ``scale``."""
    rng = np.random.default_rng(SEED)
    idx = block_indices(rng, PATHS, HORIZON_V3, len(series["C"]), BLOCK)
    out: dict[str, Any] = {}
    for scale in SCALES:
        for g in (0.0, 0.05):
            cell: dict[str, Any] = {}
            for j, name in enumerate(NAMES):
                th = cv["per_challenger"][name]["theta"]
                plain = first_look_hit(paths_e(series, idx, j, g, scale), rule_v3, HORIZON_V3)
                cup = first_look_hit(
                    paths_e(series, idx, j, g, scale, th, cv["mu_H"]), rule_v3, HORIZON_V3
                )
                cell[name] = {
                    "plain": float((plain >= 0).mean()),
                    "cuped": float((cup >= 0).mean()),
                }
            out[f"scale={scale}|gain={round(g * 100)}%"] = cell
    return out


def build() -> dict[str, Any]:
    series = sp.load_series()
    rule_v2 = dict(pr.RULE)  # the live frozen rule: used for the v2 column (calendar horizon)
    rule_v3 = {**pr.RULE, "horizon_days": HORIZON_V3}
    cv = control_variate(series)
    res: dict[str, Any] = {
        "seed": SEED,
        "paths": PATHS,
        "block": BLOCK,
        "record_days": len(series["C"]),
        "record_span": [series["days"][0], series["days"][-1]],
        "series_scale": {
            name: {
                "sd_e_over_mean_champion_error": float(
                    (series["D"][:, j] - 0.05 * series["C"]).std() / series["C"].mean()
                ),
                "mean_gain_in_record": float(series["D"][:, j].mean() / series["C"].mean()),
            }
            for j, name in enumerate(NAMES)
        },
        "control_variate": cv,
        "power_primary": power_table(series, rule_v2, rule_v3, cv, BLOCK),
        "power_proportional_gain": power_table(
            series, rule_v2, rule_v3, cv, BLOCK, mode="proportional"
        ),
        "horizon_options": horizon_options(series, rule_v3),
        "power_block_sensitivity": {
            str(b): power_table(series, rule_v2, rule_v3, cv, b, with_cuped=False)
            for b in BLOCKS_SENS
        },
        "regime_size": regime_size(series, rule_v3, cv),
    }
    return res


def pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.1f}%"


def render(r: dict[str, Any]) -> str:
    L = [
        "# Promotion rule v3: simulation (ADR 072, Amendment 2)",
        "",
        f"Seed {r['seed']}, {r['paths']:,} paths per cell, circular block bootstrap (block "
        f"{r['block']}) of the {r['record_days']} real days {r['record_span'][0]} to "
        f"{r['record_span'][1]}. The gain in every cell is set by hand (VERIFIED: this script); the "
        "retrospective days are used only for noise, autocorrelation and the control variate.",
        "",
        "## Noise in the real record",
        "| Challenger | sd of daily e / champion mean error | record's own mean gain (NOT evidence) |",
        "|---|---|---|",
    ]
    for n, v in r["series_scale"].items():
        L.append(
            f"| {n} | {v['sd_e_over_mean_champion_error']:.3f} | {pct(v['mean_gain_in_record'])} |"
        )
    L += ["", "## Chance of being promoted within the horizon (per challenger)", ""]
    L += [
        "| True gain | Challenger | v2 (128 decision days) | v3 (180 decision days) | v3 + control variate |",
        "|---|---|---|---|---|",
    ]
    for g, row in r["power_primary"].items():
        if not g.endswith("%"):
            continue
        for n in NAMES:
            c = row[n]
            L.append(
                f"| {g} | {n} | {pct(c['v2']['promoted_share'])} | {pct(c['v3']['promoted_share'])} | "
                f"{pct(c['v3_cuped']['promoted_share'])} |"
            )
        a = row["any_of_three"]
        L.append(
            f"| {g} | any of three | {pct(a['v2'])} | {pct(a['v3'])} | {pct(a.get('v3_cuped'))} |"
        )
    L += [
        "",
        "## Same, when the gain is proportional to each day's champion error (more conservative)",
        "",
        "| True gain | Challenger | v2 (128 decision days) | v3 (180 decision days) | v3 + control variate |",
        "|---|---|---|---|---|",
    ]
    for g, row in r["power_proportional_gain"].items():
        if not g.endswith("%") or g == "0%":
            continue
        for n in NAMES:
            c = row[n]
            L.append(
                f"| {g} | {n} | {pct(c['v2']['promoted_share'])} | {pct(c['v3']['promoted_share'])} | "
                f"{pct(c['v3_cuped']['promoted_share'])} |"
            )
        a = row["any_of_three"]
        L.append(
            f"| {g} | any of three | {pct(a['v2'])} | {pct(a['v3'])} | {pct(a.get('v3_cuped'))} |"
        )
    L += [
        "",
        "## Longer horizons (the option; its cost is that decisions come later)",
        "| Horizon (decision days) | Gain construction | True gain | ensemble | p3_roll60 | p3_monday |",
        "|---|---|---|---|---|---|",
    ]
    for k, cell in r["horizon_options"].items():
        h, mode, g = k.split("|")
        L.append(
            f"| {h.split('=')[1]} | {mode} | {g.split('=')[1]} | "
            + " | ".join(pct(cell[n]) for n in NAMES)
            + " |"
        )
    L += [
        "",
        "## Median decision day, given promoted (v3)",
        "| True gain | Challenger | median day | by day 60 | by day 90 | by day 180 |",
        "|---|---|---|---|---|---|",
    ]
    for g, row in r["power_primary"].items():
        if not g.endswith("%") or g in ("0%",):
            continue
        for n in NAMES:
            c = row[n]["v3"]
            md = c["median_days_given_promoted"]
            L.append(
                f"| {g} | {n} | {'-' if md is None else int(md)} | {pct(c['promoted_by_day']['60'])} | "
                f"{pct(c['promoted_by_day']['90'])} | {pct(c['promoted_by_day']['180'])} |"
            )
    L += ["", "## Block-length sensitivity (v3 only, any challenger, per challenger share)"]
    L += ["| Block | True gain | ensemble | p3_roll60 | p3_monday |", "|---|---|---|---|---|"]
    for b, tab in r["power_block_sensitivity"].items():
        for g, row in tab.items():
            if g.endswith("%"):
                L.append(
                    f"| {b} | {g} | "
                    + " | ".join(pct(row[n]["v3"]["promoted_share"]) for n in NAMES)
                    + " |"
                )
    cv = r["control_variate"]
    L += [
        "",
        "## Control variate (hold error, same day)",
        f"mu_H = {cv['mu_H']:.2f} Rs./g (frozen), n = {cv['n']}.",
        "",
    ]
    L += [
        "| Challenger | theta | variance cut, in sample | variance cut, out of sample |",
        "|---|---|---|---|",
    ]
    for n, v in cv["per_challenger"].items():
        L.append(
            f"| {n} | {v['theta']:.4f} | {pct(v['var_reduction_in_sample'])} | {pct(v['var_reduction_out_of_sample'])} |"
        )
    L += [
        "",
        "## Wrongful promotion when the volatility regime shifts (v3, per challenger)",
        "| Regime x gain | Challenger | plain | with control variate |",
        "|---|---|---|---|",
    ]
    for k, cell in r["regime_size"].items():
        for n in NAMES:
            L.append(f"| {k} | {n} | {pct(cell[n]['plain'])} | {pct(cell[n]['cuped'])} |")
    return "\n".join(L) + "\n"


def main() -> int:
    res = build()
    OUT_JSON.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8", newline="\n")
    OUT_MD.write_text(render(res), encoding="utf-8", newline="\n")
    print(OUT_MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
