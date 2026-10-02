"""scripts/analysis_calibrated_floor.py -- brief item 6: do better-calibrated learners lower the
direction pipeline's detection floor? Pre-registered in docs/adr/045 BEFORE any run.

#1992 (scripts/analysis_pipeline_sensitivity.py) found that on COMEX the pipeline as used (plain
logistic regression, accuracy Diebold-Mariano test) detects a planted signal reliably only at
q = 0.20 (oracle ~60%), that the test alone could see q = 0.10, and that a probability-based (Brier)
test never fires because the logistic model's probabilities score worse than climatology. This
script re-measures the floor with calibration fitted INSIDE each walk-forward refit:

  logit              control: the #1992 learner, via the same diag.walk_forward
  logit_platt        logit score -> Platt (1-D logistic) fitted on a held-out calibration tail
  logit_isotonic     logit score -> isotonic regression on the calibration tail
  logit_temperature  logit score / T, T fitted by log-loss on the calibration tail
  ensemble_platt     mean of logit_strong_l2 and gbm_stumps probabilities -> Platt

Calibration split per refit (forward-only, no leakage): the eligible training rows (label known
before the block's first test day) are split chronologically. The last CAL_FRACTION (at least
MIN_CAL rows) fit the calibrator. The base model fits on the earlier rows whose label date is
before the first calibration day (embargo >= h). Imputation means come from the base-fit rows.

Same data, signal, seeds-first-20, block size and climatology baseline as #1992, so the control
reproduces #1992's cells. Long: run via analysis.yml (GitHub-hosted), never the laptop.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import subprocess
import sys
import time
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load(name: str) -> Any:
    """Loaded lazily: the analysis workflow lists shards before installing ML dependencies."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _diag() -> Any:
    return _load("analysis_direction_diagnosis")


def _sens() -> Any:
    return _load("analysis_pipeline_sensitivity")


# --- Frozen by ADR 045 --------------------------------------------------------
SEEDS = [42 + i for i in range(100)]  # the first 20 are #1992's seeds
GRID = (0.0, 0.05, 0.10, 0.15, 0.20)
LEARNERS = ("logit", "logit_platt", "logit_isotonic", "logit_temperature", "ensemble_platt")
CALIBRATED = LEARNERS[1:]
TESTS = ("p_accuracy_dm", "p_brier_dm")
CAL_FRACTION = 0.2
MIN_CAL = 50
DETECT_P = 0.05
FLOOR_RATE = 0.80
MAX_FALSE_POSITIVE_RATE = 0.10
FAMILY_SIZE = len(CALIBRATED) * len(TESTS)  # 8 (learner, test) cells
BONFERRONI_ALPHA = 0.05 / FAMILY_SIZE
ECE_BINS = 10
SHARDS = [f"{learner}_q{q}" for learner in LEARNERS for q in GRID]


def _sigmoid(z: Any) -> Any:
    import numpy as np

    return 1.0 / (1.0 + np.exp(-z))


def _logit_of(p: Any) -> Any:
    import numpy as np

    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def fit_temperature(z: Any, y: Any) -> float:
    """T > 0 minimising the log-loss of sigmoid(z / T); searched on log T in [-3, 3]."""
    import numpy as np
    from scipy.optimize import minimize_scalar

    def nll(log_t: float) -> float:
        p = np.clip(_sigmoid(z / math.exp(log_t)), 1e-9, 1 - 1e-9)
        return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))

    return math.exp(minimize_scalar(nll, bounds=(-3.0, 3.0), method="bounded").x)


def calibrate(method: str, z_cal: Any, y_cal: Any, z_test: Any) -> Any:
    """Map base-model scores to probabilities with a calibrator fitted on the tail only."""
    import numpy as np
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression

    if method == "platt":
        m = LogisticRegression(C=1e6, max_iter=1000).fit(z_cal.reshape(-1, 1), y_cal)
        return m.predict_proba(z_test.reshape(-1, 1))[:, 1]
    if method == "isotonic":
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(z_cal, y_cal)
        return np.asarray(iso.predict(z_test), dtype=float)
    if method == "temperature":
        return _sigmoid(z_test / fit_temperature(z_cal, y_cal))
    raise ValueError(method)


def split_calibration(elig: Any, as_of: Any, label_date: Any) -> tuple[Any, Any]:
    """Chronological split of eligible rows: (base-fit rows, calibration rows).
    Base-fit rows must have matured before the first calibration day (embargo >= h)."""
    n_cal = max(MIN_CAL, int(CAL_FRACTION * len(elig)))
    cal = elig[-n_cal:]
    fit = elig[:-n_cal]
    fit = fit[label_date[fit] < as_of[cal[0]]]
    return fit, cal


def walk_forward_calibrated(d: dict[str, Any], learner: str, y: Any) -> dict[str, Any]:
    """diag.walk_forward's loop (same blocks, same eligible rows, same climatology) with a
    calibrator fitted inside each refit."""
    import numpy as np

    diag = _diag()
    X, as_of, ldate = d["X"], d["as_of"], d["label_date"]
    n = len(y)
    idx_test: list[int] = []
    p_val: list[float] = []
    p_clim: list[float] = []
    s = d["min_train"]
    while s < n:
        e = min(s + d["block"], n)
        elig = np.flatnonzero(ldate[:s] < as_of[s])
        if len(elig) < min(d["min_train"], 20) or len(set(y[elig])) < 2:
            s = e
            continue
        fit, cal = split_calibration(elig, as_of, ldate)
        if len(fit) < 20 or len(set(y[fit])) < 2 or len(set(y[cal])) < 2:
            s = e
            continue
        mu = np.nanmean(X[fit], axis=0)
        mu = np.where(np.isnan(mu), 0.0, mu)

        def imp(A: Any, mu: Any = mu) -> Any:
            return np.where(np.isnan(A), mu, A)

        Xf, Xc, Xt = imp(X[fit]), imp(X[cal]), imp(X[s:e])
        if learner == "ensemble_platt":
            ms = [diag.make_model("logit_strong_l2"), diag.make_model("gbm_stumps")]
            for m in ms:
                m.fit(Xf, y[fit])
            zc = _logit_of(np.mean([m.predict_proba(Xc)[:, 1] for m in ms], axis=0))
            zt = _logit_of(np.mean([m.predict_proba(Xt)[:, 1] for m in ms], axis=0))
            method = "platt"
        else:
            m = diag.make_model("logit")
            m.fit(Xf, y[fit])
            zc, zt = m.decision_function(Xc), m.decision_function(Xt)
            method = learner.removeprefix("logit_")
        p_te = calibrate(method, zc, y[cal], zt)
        idx_test.extend(range(s, e))
        p_val.extend(np.asarray(p_te, dtype=float).tolist())
        p_clim.extend([float(y[elig].mean())] * (e - s))
        s = e
    return {"idx": np.array(idx_test, dtype=int), "p": np.array(p_val), "p_clim": np.array(p_clim)}


def calibration_metrics(y: Any, p: Any, p_clim: Any) -> dict[str, float]:
    """Brier skill vs climatology and expected calibration error (equal-width bins)."""
    import numpy as np

    brier = float(np.mean((p - y) ** 2))
    brier_clim = float(np.mean((p_clim - y) ** 2))
    bins = np.minimum((p * ECE_BINS).astype(int), ECE_BINS - 1)
    ece = sum(
        abs(float(p[bins == b].mean()) - float(y[bins == b].mean())) * (bins == b).mean()
        for b in range(ECE_BINS)
        if (bins == b).any()
    )
    return {"brier_skill": 1 - brier / brier_clim, "ece": float(ece)}


def run_cell(learner: str, q: float, seeds: list[int] | None = None) -> dict[str, Any]:
    import numpy as np

    diag, sens = _diag(), _sens()
    d = diag.load("comex")
    sig = diag._signal(d, "linear")
    runs = []
    for sd in seeds or SEEDS:
        rng = np.random.default_rng(sd)
        take = rng.random(len(d["y"])) < q
        y_inj = np.where(take, sig, d["y"])
        if learner == "logit":
            wf = diag.walk_forward(d, "logit", y=y_inj)
        else:
            wf = walk_forward_calibrated(d, learner, y_inj)
        y_t = y_inj[wf["idx"]]
        runs.append(
            {
                "seed": sd,
                "n": len(wf["idx"]),
                **sens.tests(y_t, wf["p"], wf["p_clim"], d["horizon"]),
                **calibration_metrics(y_t, wf["p"], wf["p_clim"]),
            }
        )
    out: dict[str, Any] = {"learner": learner, "q": q, "oracle_accuracy_approx": 0.5 + q / 2}
    for t in TESTS:
        out[f"{t}_detected"] = [r[t] is not None and r[t] < DETECT_P for r in runs]
    for k in ("acc", "brier_skill", "ece"):
        out[f"mean_{k}"] = float(np.mean([r[k] for r in runs]))
    out["runs"] = runs
    print(learner, q, {t: sum(out[f"{t}_detected"]) for t in TESTS}, flush=True)
    return out


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (c - h, c + h)


def exact_sign_p(b: int, c: int) -> float:
    """One-sided exact McNemar: P(X >= b), X ~ Binomial(b + c, 1/2)."""
    m = b + c
    if m == 0:
        return 1.0
    return sum(math.comb(m, i) for i in range(b, m + 1)) / 2**m


def verdict(cells: list[dict[str, Any]]) -> dict[str, Any]:
    """The pre-registered decision rule of ADR 045."""
    by = {(c["learner"], c["q"]): c for c in cells}

    def rate(learner: str, t: str, q: float) -> float | None:
        c = by.get((learner, q))
        return None if c is None else sum(c[f"{t}_detected"]) / len(c[f"{t}_detected"])

    def floor(learner: str, t: str) -> float | None:
        return next((q for q in GRID if q > 0 and (rate(learner, t, q) or 0.0) >= FLOOR_RATE), None)

    control_floor = floor("logit", "p_accuracy_dm")
    table: dict[str, Any] = {}
    pvals: list[tuple[str, float]] = []
    for learner in LEARNERS:
        for t in TESTS:
            key = f"{learner}/{t}"
            fq = floor(learner, t)
            fp = by.get((learner, 0.0))
            fp_k = sum(fp[f"{t}_detected"]) if fp else None
            fp_n = len(fp[f"{t}_detected"]) if fp else 0
            row: dict[str, Any] = {
                "floor_q": fq,
                "floor_oracle_accuracy": None if fq is None else 0.5 + fq / 2,
                "false_positives_q0": fp_k,
                "false_positive_rate_q0": None if fp is None else fp_k / fp_n,
                "detection_by_q": {},
            }
            for q in GRID:
                c = by.get((learner, q))
                if c is None:
                    continue
                k, n = sum(c[f"{t}_detected"]), len(c[f"{t}_detected"])
                row["detection_by_q"][str(q)] = {"k": k, "n": n, "wilson95": wilson(k, n)}
            if learner != "logit" and fq is not None and ("logit", fq) in by:
                mine = by[(learner, fq)][f"{t}_detected"]
                ctrl = by[("logit", fq)]["p_accuracy_dm_detected"]
                b = sum(1 for a, z in zip(mine, ctrl, strict=True) if a and not z)
                cc = sum(1 for a, z in zip(mine, ctrl, strict=True) if z and not a)
                row["paired_vs_control_at_floor"] = {"b": b, "c": cc, "p": exact_sign_p(b, cc)}
                pvals.append((key, exact_sign_p(b, cc)))
            table[key] = row
    ranked = sorted(pvals, key=lambda kv: kv[1])
    bh_pass = set()
    for i, (_key, p) in enumerate(ranked, start=1):
        if p <= 0.05 * i / FAMILY_SIZE:
            bh_pass = {k for k, _ in ranked[:i]}
    lowered = []
    for learner in CALIBRATED:
        for t in TESTS:
            row = table[f"{learner}/{t}"]
            paired = row.get("paired_vs_control_at_floor")
            if (
                row["floor_q"] is not None
                and control_floor is not None
                and row["floor_q"] < control_floor
                and row["false_positive_rate_q0"] is not None
                and row["false_positive_rate_q0"] <= MAX_FALSE_POSITIVE_RATE
                and paired is not None
                and paired["p"] < BONFERRONI_ALPHA
            ):
                lowered.append(f"{learner}/{t}")
            row["bh_significant"] = f"{learner}/{t}" in bh_pass
    return {
        "control_floor_q": control_floor,
        "floor_lowered": bool(lowered),
        "cells_that_lower_the_floor": lowered,
        "bonferroni_alpha": BONFERRONI_ALPHA,
        "table": table,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-shards", action="store_true")
    ap.add_argument("--shard")
    ap.add_argument("--aggregate", type=Path)
    ap.add_argument("--out", type=Path, default=Path("out"))
    args = ap.parse_args()
    warnings.filterwarnings("ignore")
    if args.list_shards:
        print(json.dumps(SHARDS))
        return 0
    if args.shard:
        learner, qs = args.shard.rsplit("_q", 1)
        t0 = time.time()
        res = run_cell(learner, float(qs))
        res["seconds"] = round(time.time() - t0, 1)
        res["git_sha"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
        ).stdout.strip()
        res["generated_at_utc"] = datetime.now(UTC).isoformat()
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / f"{args.shard}.json").write_text(json.dumps(res) + "\n", encoding="utf-8")
        return 0
    if args.aggregate:
        cells, missing = [], []
        for k in SHARDS:
            p = args.aggregate / f"{k}.json"
            if p.exists():
                cells.append(json.loads(p.read_text(encoding="utf-8")))
            else:
                missing.append(k)
        summary = {
            "verdict": verdict(cells),
            "cells": [{k: v for k, v in c.items() if k != "runs"} for c in cells],
            "missing": missing,
        }
        args.out.write_text(json.dumps({**summary, "runs": cells}) + "\n", encoding="utf-8")
        print(json.dumps(summary["verdict"], indent=1))
        return 1 if missing else 0
    ap.error("need --list-shards, --shard or --aggregate")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
