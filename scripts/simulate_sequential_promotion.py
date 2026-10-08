"""scripts/simulate_sequential_promotion.py -- operating characteristics of ADR 072 Amendment 1.

Deterministic (seed 42). Reads the committed RETROSPECTIVE per-day records
(data/nextfix_p3_oos.json, data/nextfix_oos.json, data/nextfix_p3_variants_oos.json) ONLY to
estimate the variance, autocorrelation and cross-correlation of the daily loss difference between
the live model (P3) and each challenger. Those retrospective folds are NOT evidence that any
challenger gains anything: the gain in every simulated scenario is set by hand, not read from them.

Method. Resample whole days with a circular stationary bootstrap (mean block length BLOCK, so the
real autocorrelation is kept), the SAME resampled days for all three challengers (so their
cross-correlation is kept), centre the real loss difference, then add a true mean gain of 0, 5, 10,
20, 40% of the champion's mean error. Per path the new rule (ml.promotion, version 2) looks every
forward day from day 20; the old rule (version 1: fixed n, one-sided Diebold-Mariano) is evaluated
on the same paths every day, which is how the code applies it (a daily run). Gates (coverage,
direction) only ever block a promotion, so ignoring them here can only overstate promotions.

Outputs: reports/sequential_promotion_simulation.json and .md. Run:
    python scripts/simulate_sequential_promotion.py
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ml import promotion as pr

DATA = ROOT / "data"
OUT_JSON = ROOT / "reports" / "sequential_promotion_simulation.json"
OUT_MD = ROOT / "reports" / "sequential_promotion_simulation.md"

SEED = 42
PATHS = 10_000  # Monte-Carlo paths per scenario: a 1.7% rate then has a 95% half-width of ~0.25%
BLOCK = (
    6  # mean stationary-bootstrap block length: > the 4 Newey-West lags, so lag-4 dependence stays
)
GAINS = (0.0, 0.05, 0.10, 0.20, 0.40)  # true gain as a fraction of the champion's mean error
OLD_HORIZON = 600  # forward days the old rule is followed for (about 2.3 years)
CHUNK = 1000
CHALLENGERS = ("ensemble", "p3_roll60", "p3_monday")
# (iid-variance floor, inflation) pairs for the size check; the frozen rule is (True, 1.5)
VARIANTS = ((False, 1.0), (False, 1.5), (True, 1.0), (True, 1.25), (True, 1.5), (True, 2.0))


# --- inputs --------------------------------------------------------------------------------------
def _loss(f: dict) -> float:
    return abs(f["pm1"] - f["pm0"] * math.exp(f["ret"]))


def load_series() -> dict[str, Any]:
    """Aligned per-day losses: champion C, differences D = C - challenger, hold losses H."""
    p3 = json.loads((DATA / "nextfix_p3_oos.json").read_text(encoding="utf-8"))["folds"]
    ens = json.loads((DATA / "nextfix_oos.json").read_text(encoding="utf-8"))["folds"]
    var = json.loads((DATA / "nextfix_p3_variants_oos.json").read_text(encoding="utf-8"))
    recs = {"p3": p3, "ensemble": ens, **var["variants"]}
    by_day = {k: {f["d0"]: f for f in v} for k, v in recs.items()}
    days = sorted(set.intersection(*(set(m) for m in by_day.values())))
    champ = np.array([_loss(by_day["p3"][d]) for d in days])
    diffs = np.stack(
        [champ - np.array([_loss(by_day[c][d]) for d in days]) for c in CHALLENGERS], 1
    )
    hold = np.array([abs(by_day["p3"][d]["pm1"] - by_day["p3"][d]["pm0"]) for d in days])
    forward = {k: sum(1 for f in v if f.get("retro") is False) for k, v in recs.items()}
    return {
        "days": days,
        "C": champ,
        "D": diffs,
        "H": hold,
        "forward_folds_retro_false": forward,
        "folds_total": {k: len(v) for k, v in recs.items()},
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def horizon_days(rule: dict[str, Any]) -> tuple[int, np.ndarray]:
    """Weekday decision days inside the horizon: counted from the first registration date, one
    per Mon-Fri calendar day for ``horizon_days`` calendar days (public holidays ignored, so the
    count is an upper bound). Returns (count, calendar-day offset of each decision day)."""
    start = date.fromisoformat(rule["common_start"])
    offs = [
        k for k in range(int(rule["horizon_days"])) if (start + timedelta(days=k)).weekday() < 5
    ]
    return len(offs), np.array(offs)


# --- paths ---------------------------------------------------------------------------------------
def stationary_indices(
    rng: np.random.Generator, n_paths: int, length: int, t_len: int, block: float
) -> np.ndarray:
    """Circular stationary bootstrap (Politis & Romano 1994): continue the block with probability
    1 - 1/block, else jump to a uniformly random day."""
    idx = np.empty((n_paths, length), dtype=np.int64)
    idx[:, 0] = rng.integers(0, t_len, n_paths)
    jump = rng.random((n_paths, length)) < 1.0 / block
    fresh = rng.integers(0, t_len, (n_paths, length))
    for t in range(1, length):
        idx[:, t] = np.where(jump[:, t], fresh[:, t], (idx[:, t - 1] + 1) % t_len)
    return idx


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval for a Monte-Carlo proportion."""
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def first_true(mask: np.ndarray) -> np.ndarray:
    """Index of the first True along the last axis, or -1 when none."""
    return np.where(mask.any(axis=-1), mask.argmax(axis=-1), -1)


# --- the two rules on a batch of paths -----------------------------------------------------------
def new_rule_first(e: np.ndarray, rule: dict[str, Any]) -> np.ndarray:
    """Index (0-based day) of the first look whose lower bound is > 0, else -1."""
    lb = pr.cs_lower_bounds(e, rule)
    return first_true(np.nan_to_num(lb, nan=-1.0) > 0)


def old_rule_first(c: np.ndarray, d: np.ndarray) -> np.ndarray:
    """Version 1 applied each day: n >= max(40, n_required), mean error >= 5% below the
    champion's, one-sided HAC Diebold-Mariano p <= 0.05 (a single challenger: BH = 0.05)."""
    from scipy.stats import norm

    n = np.arange(1, d.shape[-1] + 1)
    mean_d = np.cumsum(d, -1) / n
    mean_c = np.cumsum(c, -1) / n
    var = pr.running_long_run_var(d, 4)
    with np.errstate(invalid="ignore", divide="ignore"):
        z = norm.ppf(0.95) + norm.ppf(0.80)
        n_req = np.maximum(40, np.ceil(z * z * var / (0.05 * mean_c) ** 2))
        stat = mean_d / np.sqrt(var / n)
        ok = (n >= n_req) & (mean_d / mean_c >= 0.05) & (stat >= norm.ppf(0.95)) & (var > 0)
    return first_true(np.nan_to_num(ok, nan=0).astype(bool))


# --- summaries -----------------------------------------------------------------------------------
def summarise_new(first: np.ndarray, n_h: int, cal: np.ndarray) -> dict[str, Any]:
    paths = len(first)
    prom = first >= 0
    k = int(prom.sum())
    lo, hi = wilson(k, paths)
    days = np.where(prom, first + 1, n_h)  # retired paths are decided (retired) at the horizon
    cal_days = np.where(prom, cal[np.clip(first, 0, n_h - 1)] + 1, cal[-1] + 1)
    return {
        "promoted_share": k / paths,
        "promoted_ci95": [lo, hi],
        "retired_share": 1 - k / paths,
        "expected_days_to_decision": float(days.mean()),
        "median_days_to_decision": float(np.median(days)),
        "median_days_given_promoted": float(np.median(first[prom] + 1)) if k else None,
        "expected_calendar_days_to_decision": float(cal_days.mean()),
        "median_calendar_days_to_decision": float(np.median(cal_days)),
    }


def summarise_old(first: np.ndarray, n_h: int) -> dict[str, Any]:
    paths = len(first)
    in_h = (first >= 0) & (first < n_h)
    anywhere = first >= 0
    lo, hi = wilson(int(in_h.sum()), paths)
    return {
        "promoted_within_horizon_share": float(in_h.mean()),
        "promoted_within_horizon_ci95": [lo, hi],
        "promoted_within_600_days_share": float(anywhere.mean()),
        "median_days_given_promoted_within_600": (
            float(np.median(first[anywhere] + 1)) if anywhere.any() else None
        ),
        "never_decided_within_600_share": float(1 - anywhere.mean()),
    }


def run_promotion(series: dict[str, Any], rule: dict[str, Any], rng: np.random.Generator) -> dict:
    n_h, cal = horizon_days(rule)
    c_all, d_all = series["C"], series["D"]
    m_c, t_len = float(c_all.mean()), len(c_all)
    d_centred = d_all - d_all.mean(0)
    # one set of resampled days per chunk, reused for every gain and both rules (paired scenarios)
    new_first = {g: {c: [] for c in CHALLENGERS} for g in GAINS}
    old_first = {g: {c: [] for c in CHALLENGERS} for g in GAINS}
    joint = {g: [] for g in GAINS}
    for _ in range(PATHS // CHUNK):
        idx = stationary_indices(rng, CHUNK, OLD_HORIZON, t_len, BLOCK)
        c_p = c_all[idx]
        for g in GAINS:
            hit_any = np.zeros(CHUNK, dtype=bool)
            for j, name in enumerate(CHALLENGERS):
                d_p = d_centred[idx, j] + g * m_c  # true mean difference = g x champion MAE
                e_p = d_p - rule["min_gain"] * c_p  # e = 0.95 c - b with b = c - d
                f_new = new_rule_first(e_p[:, :n_h], rule)
                new_first[g][name].append(f_new)
                old_first[g][name].append(old_rule_first(c_p, d_p))
                hit_any |= f_new >= 0
            joint[g].append(hit_any)
    out: dict[str, Any] = {"decision_days_in_horizon": n_h, "gains": {}}
    for g in GAINS:
        row: dict[str, Any] = {"new_rule": {}, "old_rule": {}}
        for name in CHALLENGERS:
            row["new_rule"][name] = summarise_new(np.concatenate(new_first[g][name]), n_h, cal)
            row["old_rule"][name] = summarise_old(np.concatenate(old_first[g][name]), n_h)
        hits = np.concatenate(joint[g])
        row["any_of_three_promoted_share"] = float(hits.mean())
        row["any_of_three_promoted_ci95"] = list(wilson(int(hits.sum()), len(hits)))
        out["gains"][f"{round(g * 100)}%"] = row
    return out


def size_by_inflation(
    series: dict[str, Any], rule: dict[str, Any], rng: np.random.Generator
) -> list:
    """False-promotion rate at the BOUNDARY (true gain exactly min_gain) and at 0% gain for each
    variance-inflation factor: how anti-conservative the asymptotic sequence is at small n."""
    n_h, _ = horizon_days(rule)
    c_all, d_all = series["C"], series["D"]
    m_c, t_len = float(c_all.mean()), len(c_all)
    d_centred = d_all - d_all.mean(0)
    rows = []
    idx_chunks = [stationary_indices(rng, CHUNK, n_h, t_len, BLOCK) for _ in range(PATHS // CHUNK)]
    for floor, infl in VARIANTS:
        r = {**rule, "variance_inflation": infl, "variance_floor_iid": floor}
        for gain in (rule["min_gain"], 0.0):
            hits = {c: [] for c in CHALLENGERS}
            anyhit = []
            for idx in idx_chunks:
                c_p = c_all[idx]
                joint = np.zeros(len(idx), dtype=bool)
                for j, name in enumerate(CHALLENGERS):
                    e_p = d_centred[idx, j] + gain * m_c - rule["min_gain"] * c_p
                    h = new_rule_first(e_p, r) >= 0
                    hits[name].append(h)
                    joint |= h
                anyhit.append(joint)
            row: dict[str, Any] = {
                "variance_floor_iid": floor,
                "variance_inflation": infl,
                "true_gain": gain,
                "rate": {},
            }
            for name in CHALLENGERS:
                h = np.concatenate(hits[name])
                row["rate"][name] = [float(h.mean()), *wilson(int(h.sum()), len(h))]
            a = np.concatenate(anyhit)
            row["rate"]["any_of_three"] = [float(a.mean()), *wilson(int(a.sum()), len(a))]
            rows.append(row)
    return rows


# --- demotion monitor ----------------------------------------------------------------------------
def _rolling_error_rule(dm: np.ndarray, window: int = 40, min_folds: int = 30) -> np.ndarray:
    """ml.demotion's error rule (last 40 folds, one-sided HAC p < 0.05 and model worse, 3 checks in
    a row) per path: index of the first day the demotion fires, else -1. Vectorised over paths."""
    from scipy.stats import norm

    paths, t_len = dm.shape
    breach = np.zeros((paths, t_len), dtype=bool)
    for t in range(min_folds, t_len + 1):
        w = dm[:, max(0, t - window) : t]
        n = w.shape[1]
        mean = w.mean(1)
        dc = w - mean[:, None]
        var = (dc * dc).sum(1) / n
        for lag in range(1, min(4, n - 1) + 1):
            var += 2.0 * (1.0 - lag / 5.0) * (dc[:, lag:] * dc[:, :-lag]).sum(1) / n
        with np.errstate(invalid="ignore", divide="ignore"):
            p = 1.0 - norm.cdf(mean / np.sqrt(var / n))
        breach[:, t - 1] = (mean > 0) & (p < 0.05) & (var > 0)
    fired = breach.copy()
    fired[:, 2:] = breach[:, 2:] & breach[:, 1:-1] & breach[:, :-2]
    fired[:, :2] = False
    return first_true(fired)


def run_demotion(series: dict[str, Any], rule: dict[str, Any], rng: np.random.Generator) -> dict:
    """Does the sequential bound detect a model worse than holding sooner than the live monitor?

    dm = model error - hold error per day (P3's retrospective series, centred). Scenarios: the model
    is worse than holding by w x mean hold error from day 1, and a change point (good for 60 days at
    -20%, then worse by w). The sequential monitor: lower bound of mean(dm) > 0, level 0.05, from
    day 20, no persistence rule needed. NOT wired anywhere: ml/demotion.py is unchanged.
    """
    n_h, _ = horizon_days(rule)
    dm_real = series["C"] - series["H"]  # P3 error minus hold error (negative = better than hold)
    m_h = float(series["H"].mean())
    centred = dm_real - dm_real.mean()
    t_len = len(centred)
    seq_rule = {**rule, "family_size": 1}  # one monitor per model: the full alpha
    out: dict[str, Any] = {"hold_mean_error": m_h, "horizon_days": n_h, "scenarios": {}}
    scen = {
        "model better than holding by 20% (false-alarm check)": (-0.2, None),
        "model equal to holding (boundary)": (0.0, None),
        "model worse by 10% from day 1": (0.1, None),
        "model worse by 20% from day 1": (0.2, None),
        "model worse by 40% from day 1": (0.4, None),
        "good (-20%) for 60 days, then worse by 20%": (-0.2, (60, 0.2)),
    }
    idx = stationary_indices(rng, PATHS, n_h, t_len, BLOCK)
    for label, (w, change) in scen.items():
        mean = np.full(n_h, w * m_h)
        if change:
            mean[change[0] :] = change[1] * m_h
        dm = centred[idx] + mean
        cur = _rolling_error_rule(dm)
        seq = new_rule_first(dm, seq_rule)
        start = change[0] if change else 0
        rows = {}
        for name, first in (("current_rolling_40_persist_3", cur), ("sequential_bound", seq)):
            fired = first >= 0
            # after a change point only alarms raised AFTER it are detections; earlier ones are false
            early = fired & (first < start)
            late = fired & (first >= start)
            rows[name] = {
                "fired_share": float(fired.mean()),
                "fired_ci95": list(wilson(int(fired.sum()), len(fired))),
                "fired_before_change_share": float(early.mean()) if change else None,
                "median_days_to_fire": float(np.median(first[fired] + 1)) if fired.any() else None,
                "median_days_after_change": (
                    float(np.median(first[late] + 1 - start)) if change and late.any() else None
                ),
            }
        out["scenarios"][label] = rows
    return out


# --- report --------------------------------------------------------------------------------------
def build() -> dict[str, Any]:
    rng = np.random.default_rng(SEED)
    series = load_series()
    rule = pr.RULE
    d = series["D"]
    c = series["C"]
    acf = {}
    for j, name in enumerate(CHALLENGERS):
        e = d[:, j] - rule["min_gain"] * c
        ec = e - e.mean()
        acf[name] = {
            "sd_over_champion_mae": float(ec.std() / c.mean()),
            "acf_lag1_to_4": [float(ec[k:] @ ec[:-k] / (ec @ ec)) for k in range(1, 5)],
            "long_run_sd_over_champion_mae": float(
                math.sqrt(pr.running_long_run_var(e, 4)[-1]) / c.mean()
            ),
            "retrospective_gain_NOT_EVIDENCE": float(d[:, j].mean() / c.mean()),
        }
    n_h, _ = horizon_days(rule)
    return {
        "script": "scripts/simulate_sequential_promotion.py",
        "seed": SEED,
        "paths": PATHS,
        "mean_block_length": BLOCK,
        "rule_sha256": pr.rule_sha256(rule),
        "inputs_sha256": {
            n: sha256(DATA / n)
            for n in ("nextfix_p3_oos.json", "nextfix_oos.json", "nextfix_p3_variants_oos.json")
        },
        "retro_days_shared": len(series["days"]),
        "retro_first_day": series["days"][0],
        "retro_last_day": series["days"][-1],
        "forward_folds_with_retro_false": series["forward_folds_retro_false"],
        "folds_total": series["folds_total"],
        "champion_mean_error_retro": float(c.mean()),
        "real_series": acf,
        "decision_days_in_horizon": n_h,
        "per_challenger_level": pr.cs_level(rule),
        "promotion": run_promotion(series, rule, rng),
        "size_by_variance_inflation": size_by_inflation(series, rule, rng),
        "demotion": run_demotion(series, rule, rng),
    }


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def render(r: dict[str, Any]) -> str:
    n_h = r["decision_days_in_horizon"]
    L = [
        "# Sequential promotion rule: simulation (ADR 072 Amendment 1)",
        "",
        f"Seed {r['seed']}, {r['paths']} paths per scenario, stationary bootstrap (mean block "
        f"{r['mean_block_length']}) of the CENTRED retrospective loss differences between P3 and "
        f"each challenger ({r['retro_days_shared']} shared days, {r['retro_first_day']} to "
        f"{r['retro_last_day']}). Those retrospective folds give variance and autocorrelation only; "
        "they are NOT evidence of any gain, and the gain in each row is set by hand. The 145 folds "
        "are not consecutive trading days, so real daily autocorrelation may differ. Rule hash "
        f"`{r['rule_sha256'][:12]}`. Horizon: {n_h} weekday decision days in 180 calendar days "
        "(holidays ignored). Per-challenger level alpha/3 = "
        f"{r['per_challenger_level']:.4f}; family-wise alpha 0.05.",
        "",
        "Forward folds with retro false at amendment time: "
        + ", ".join(f"{k} {v}" for k, v in r["forward_folds_with_retro_false"].items())
        + ".",
        "",
    ]
    for name in CHALLENGERS:
        s = r["real_series"][name]
        L.append(
            f"- {name}: sd of daily e = {s['sd_over_champion_mae']:.3f} x champion MAE, long-run sd "
            f"{s['long_run_sd_over_champion_mae']:.3f}, autocorrelation lags 1-4 "
            + ", ".join(f"{a:+.2f}" for a in s["acf_lag1_to_4"])
        )
    L += ["", "## New rule (confidence sequence), per challenger", ""]
    hdr = (
        "| Challenger | True gain | Promoted <=180d | 95% MC interval | Retired | "
        "Mean days to decision | Median days | Median days if promoted |"
    )
    L += [hdr, "|---|---|---|---|---|---|---|---|"]
    for g, row in r["promotion"]["gains"].items():
        for name in CHALLENGERS:
            n = row["new_rule"][name]
            lo, hi = n["promoted_ci95"]
            mp = n["median_days_given_promoted"]
            L.append(
                f"| {name} | {g} | {_pct(n['promoted_share'])} | {_pct(lo)} to {_pct(hi)} | "
                f"{_pct(n['retired_share'])} | {n['expected_days_to_decision']:.1f} | "
                f"{n['median_days_to_decision']:.0f} | {'n/a' if mp is None else f'{mp:.0f}'} |"
            )
    L += [
        "",
        "Days are forward decision days (a retired path counts as decided at the horizon). "
        "At 5% true gain the rule is at its boundary: that row is the size of the test. "
        "At 0% true gain the row is the wrongful-promotion rate.",
        "",
        "Any of the three promoted (same days for all three, family-wise):",
        "",
        "| True gain | Share | 95% MC interval |",
        "|---|---|---|",
    ]
    for g, row in r["promotion"]["gains"].items():
        lo, hi = row["any_of_three_promoted_ci95"]
        L.append(f"| {g} | {_pct(row['any_of_three_promoted_share'])} | {_pct(lo)} to {_pct(hi)} |")
    L += [
        "",
        "## Old rule (version 1, fixed n), same paths",
        "",
        "| Challenger | True gain | Promoted <=180d | 95% MC interval | Promoted <=600 decision "
        "days | Median days if promoted (<=600) | Not decided in 600 days |",
        "|---|---|---|---|---|---|---|",
    ]
    for g, row in r["promotion"]["gains"].items():
        for name in CHALLENGERS:
            o = row["old_rule"][name]
            lo, hi = o["promoted_within_horizon_ci95"]
            md = o["median_days_given_promoted_within_600"]
            L.append(
                f"| {name} | {g} | {_pct(o['promoted_within_horizon_share'])} | "
                f"{_pct(lo)} to {_pct(hi)} | {_pct(o['promoted_within_600_days_share'])} | "
                f"{'n/a' if md is None else f'{md:.0f}'} | {_pct(o['never_decided_within_600_share'])} |"
            )
    L += [
        "",
        "## Is the asymptotic sequence anti-conservative at small n? (variance inflation)",
        "",
        "False-promotion rate within the horizon, by variance plug-in (floor = also at least the plain "
        "sample variance; x = inflation factor). The frozen rule is marked.",
        "",
        "| Variance used | True gain | ensemble | p3_roll60 | p3_monday | any of three |",
        "|---|---|---|---|---|---|",
    ]
    for row in r["size_by_variance_inflation"]:
        cells = [
            f"{_pct(v[0])} ({_pct(v[1])} to {_pct(v[2])})"
            for v in (row["rate"][c] for c in (*CHALLENGERS, "any_of_three"))
        ]
        frozen = (
            " (frozen)"
            if (row["variance_floor_iid"], row["variance_inflation"])
            == (pr.RULE["variance_floor_iid"], pr.RULE["variance_inflation"])
            else ""
        )
        L.append(
            f"| {'max(NW, iid)' if row['variance_floor_iid'] else 'NW'} x {row['variance_inflation']}{frozen} | {_pct(row['true_gain'])} | "
            + " | ".join(cells)
            + " |"
        )
    dm = r["demotion"]
    L += [
        "",
        "## Demotion monitor: would the same bound detect a failing live model sooner?",
        "",
        "P3's centred retrospective (model error minus hold error) series, same bootstrap. Current = "
        "ml/demotion.py error rule (last 40 days, one-sided HAC p < 0.05, 3 checks in a row). "
        "Sequential = lower confidence bound of (model - hold) above 0 at level 0.05 from day 20. "
        f"{dm['horizon_days']} days. ml/demotion.py is NOT changed by this work.",
        "",
        "| Scenario | Rule | Fired | Fired before change | Median days to fire | Median days after change |",
        "|---|---|---|---|---|---|",
    ]
    for label, rows in dm["scenarios"].items():
        for name, v in rows.items():
            md = v["median_days_to_fire"]
            ma = v["median_days_after_change"]
            L.append(
                f"| {label} | {name} | {_pct(v['fired_share'])} | {_pct(v['fired_before_change_share'])}"
                f" | {'n/a' if md is None else f'{md:.0f}'} | {'n/a' if ma is None else f'{ma:.0f}'} |"
            )
    return "\n".join(L) + "\n"


def main() -> int:
    report = build()
    OUT_JSON.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8", newline="\n")
    OUT_MD.write_text(render(report), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
