"""scripts/analysis_macro_horizons.py -- item 8 (ADR 053): does CFTC positioning or the
US real-yield level/change add anything at longer horizons (1 week / 1 month / 3 months)?

Frozen by docs/adr/053-macro-drivers-longer-horizons.md BEFORE any COT/real-yield/price
data for this analysis is touched. Shards (analysis.yml contract: --list-shards /
--shard KEY --out DIR / --aggregate DIR --out F):

  h{5,21,63}_direction_{logit,gbm_stumps}      confirmatory family: direction vs always-up
  h{5,21,63}_return_{ridge,gbm_stumps_reg}     confirmatory family: log return vs no-change
  h{5,21,63}_injection                         detection-floor calibration (not confirmatory)

Family size for multiplicity correction = 3 horizons x 2 targets x 2 models = 12
(ADR 053's pre-registered family). Data: CFTC Disaggregated COT (COMEX gold, 088691)
from its empirically-confirmed start 2006-06-13; Treasury 10-year real yield from
2003-01-02; COMEX GC=F (ml.direction.comex_daily, same roll-adjustment as the rest of
this project). Every feature is release-lag-aligned to its decision date via
ml.macro_drivers (no-look-ahead by construction, not just by convention).

Walk-forward: expanding window, refit every 21 trading days (BLOCK), a refit at test-row
s trains only on rows whose label matured (label_date_h{H} < as_of[s]) -- embargo >=
horizon by construction, reusing ml.direction.comex_daily's label_date_hN columns and
scripts/analysis_direction_diagnosis's walk_forward/_impute/_dm exactly. MIN_TRAIN=500
trading days (~2 years) before any row is scored, so the pipeline's own signal-injection
and detection-floor logic (ADR 040/045 precedent) has already been exercised on data
this thin elsewhere in the project.

Two design choices NOT fully pinned by the item-8 brief, resolved here and stated
plainly (rule 73 -- ambiguity resolved, not silently picked):
  1. "models (logistic ...; gbm_stumps ...)" describes one model-complexity pair used
     for BOTH targets. For the continuous return target this is implemented as the
     literal regression analogs of the same two model families at the same
     hyperparameters: `ridge` (Ridge alpha=1.0 after standard scaling -- L2, C=1.0's
     regression counterpart) and `gbm_stumps_reg` (LGBMRegressor, identical
     depth-1/50-estimator/0.05-lr hyperparameters to diag.make_model("gbm_stumps")).
  2. "squared error vs no-change" -- two no-change baselines are reported
     (zero-return AND historical-mean-drift, per the brief's "report both"); the
     PRIMARY confirmatory comparison (the one that enters the Bonferroni/BH family) is
     zero-return, the standard random-walk null for a price series and the more
     conservative of the two (historical-mean-drift already "knows" gold's realised
     drift over the training window, so a model beating it is stronger evidence, but
     zero-return is the null this brief's "no-change" most naturally names).

Success (both required): (a) the primary p-value survives Bonferroni across the 12-cell
family; (b) the edge (model loss < baseline loss) has the SAME SIGN in both halves,
2006-01-01..2015-12-31 and 2016-01-01..2026-09-24 (not independently significant -- just
non-reversed, per the pre-registration). The detection-floor injection shards are
NOT part of this family; they calibrate what the pipeline can and cannot see, exactly
as ADR 040 D3 / ADR 045 did, and are reported "positive or negative, plainly" alongside.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
import warnings
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SEED = 42
HORIZONS: tuple[int, ...] = (5, 21, 63)  # 1 week, 1 month, 3 months (trading days)
DIRECTION_MODELS: tuple[str, ...] = ("logit", "gbm_stumps")
RETURN_MODELS: tuple[str, ...] = ("ridge", "gbm_stumps_reg")
MIN_TRAIN = 500  # ~2 trading years before any row is scored
BLOCK = 21  # refit cadence
SPLIT_DATE = "2016-01-01"  # half A: as_of < this; half B: as_of >= this
INJECTION_GRID: tuple[float, ...] = (0.0, 0.05, 0.10, 0.15, 0.20)
INJECTION_SEEDS: list[int] = [SEED + i for i in range(20)]

# Frozen fetch window (ADR 053). COT's own start (2006-06-13) is the binding
# constraint; price history is fetched a few months earlier purely so the 63-trading-
# day momentum control feature is non-NaN at the COT start, not to extend the scored
# window. Real yields are fetched from Treasury's own history floor.
BUILD_PRICE_START = "2006-01-01"
COT_DATA_START = date(2006, 6, 13)
REAL_YIELD_FETCH_START = date(2003, 1, 2)
MACRO_HORIZONS_END = "2026-09-24"  # frozen "today" at registration time

FEATURES: list[str] = [
    "cot_net_pct_oi",
    "cot_net_pct_oi_chg_4w",
    "cot_net_pct_oi_z",
    "real_yield_10y",
    "real_yield_10y_chg_4w",
    "mom_21",
    "mom_63",
]

SHARDS: list[str] = [
    *[f"h{h}_direction_{m}" for h in HORIZONS for m in DIRECTION_MODELS],
    *[f"h{h}_return_{m}" for h in HORIZONS for m in RETURN_MODELS],
    *[f"h{h}_injection" for h in HORIZONS],
]
FAMILY_KEYS: list[str] = [
    *[f"h{h}_direction_{m}" for h in HORIZONS for m in DIRECTION_MODELS],
    *[f"h{h}_return_{m}" for h in HORIZONS for m in RETURN_MODELS],
]


def _diag() -> Any:
    """Loaded lazily: the analysis workflow lists shards before installing ML deps."""
    if "analysis_direction_diagnosis" in sys.modules:
        return sys.modules["analysis_direction_diagnosis"]
    spec = importlib.util.spec_from_file_location(
        "analysis_direction_diagnosis", ROOT / "scripts" / "analysis_direction_diagnosis.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["analysis_direction_diagnosis"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Dataset: price (ml.direction.comex_daily) + COT + real yield, release-lag aligned
# ---------------------------------------------------------------------------

_FRAME_CACHE: Any = None


def _build_frame() -> Any:
    """One row per COMEX trading day, 2006-01-01.. MACRO_HORIZONS_END: the existing
    label_binary_h{H}/label_date_h{H}/next_pm916_h{H} columns for every horizon
    (reused verbatim from ml.direction.comex_daily -- same roll-adjustment, same
    embargo-ready label_date construction), plus the 7 FEATURES columns, release-lag
    aligned via ml.macro_drivers so no feature can ever be seen before its real-world
    publication date."""
    import numpy as np
    from ml import macro_drivers as md
    from ml.direction.comex_daily import build_comex_dataset

    df = build_comex_dataset(
        start=BUILD_PRICE_START, end=MACRO_HORIZONS_END, extra_horizons=HORIZONS
    )

    cot = md.add_cot_features(
        md.fetch_cot_disaggregated(start=COT_DATA_START, end=date.fromisoformat(MACRO_HORIZONS_END))
    )
    ry = md.add_real_yield_features(
        md.fetch_real_yield(
            start=REAL_YIELD_FETCH_START, end=date.fromisoformat(MACRO_HORIZONS_END)
        )
    )
    as_of = df["as_of_date"]
    cot_aligned = md.align_feature_to_decision_dates(
        cot, ["cot_net_pct_oi", "cot_net_pct_oi_chg_4w", "cot_net_pct_oi_z"], as_of
    )
    ry_aligned = md.align_feature_to_decision_dates(
        ry, ["real_yield_10y", "real_yield_10y_chg_4w"], as_of
    )
    for c in ["cot_net_pct_oi", "cot_net_pct_oi_chg_4w", "cot_net_pct_oi_z"]:
        df[c] = cot_aligned[c].to_numpy()
    for c in ["real_yield_10y", "real_yield_10y_chg_4w"]:
        df[c] = ry_aligned[c].to_numpy()

    # Momentum control features: both already T-1 (current_pm916 IS the T-1 price;
    # ml.direction.comex_daily's docstring), so a same-row shift introduces no leak.
    df["mom_21"] = df["current_pm916"] / df["current_pm916"].shift(21) - 1.0
    df["mom_63"] = df["current_pm916"] / df["current_pm916"].shift(63) - 1.0

    for h in HORIZONS:
        next_p = df[f"next_pm916_h{h}"].astype(float)
        cur = df["current_pm916"].astype(float)
        df[f"_log_return_h{h}"] = np.where(next_p.notna(), np.log(next_p / cur), np.nan)

    return df


def _frame() -> Any:
    global _FRAME_CACHE
    if _FRAME_CACHE is None:
        _FRAME_CACHE = _build_frame()
    return _FRAME_CACHE


def load_dataset(horizon: int, target: str) -> dict[str, Any]:
    import numpy as np

    df = _frame()
    label_col = f"label_binary_h{horizon}"
    ldate_col = f"label_date_h{horizon}"
    sub = df[df[label_col].notna()].reset_index(drop=True)
    if target == "direction":
        y = sub[label_col].to_numpy(dtype=int)
    elif target == "return":
        y = sub[f"_log_return_h{horizon}"].to_numpy(dtype=float)
    else:
        raise ValueError(target)
    X = sub[FEATURES].to_numpy(dtype=float)
    return {
        "X": X,
        "y": y,
        "as_of": sub["as_of_date"].astype(str).to_numpy(),
        "label_date": np.array([str(v) if v is not None else "9999" for v in sub[ldate_col]]),
        "horizon": horizon,
        "features": FEATURES,
        "min_train": MIN_TRAIN,
        "block": BLOCK,
    }


# ---------------------------------------------------------------------------
# Return-target models + walk-forward (the direction target reuses diag's exactly)
# ---------------------------------------------------------------------------


def make_return_model(name: str):  # type: ignore[no-untyped-def]
    from lightgbm import LGBMRegressor
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if name == "ridge":
        return make_pipeline(StandardScaler(), Ridge(alpha=1.0))
    if name == "gbm_stumps_reg":
        return LGBMRegressor(
            n_estimators=50,
            max_depth=1,
            num_leaves=2,
            learning_rate=0.05,
            random_state=SEED,
            verbose=-1,
            n_jobs=1,
        )
    raise ValueError(name)


def walk_forward_return(d: dict[str, Any], model_name: str, y: Any = None) -> dict[str, Any]:
    """Mirrors diag.walk_forward's loop exactly (same blocks, same embargo-eligible
    rows, same imputation) for a continuous target. p_clim is the historical-mean-
    drift baseline (the training fold's own mean return); the zero-return baseline
    needs no fitting and is computed directly from y at score time."""
    import numpy as np

    diag = _diag()
    X, as_of, ldate = d["X"], d["as_of"], d["label_date"]
    y = d["y"] if y is None else y
    n = len(y)
    s = d["min_train"]
    idx_test: list[int] = []
    p_val: list[float] = []
    p_clim: list[float] = []
    while s < n:
        e = min(s + d["block"], n)
        elig = np.flatnonzero(ldate[:s] < as_of[s])
        if len(elig) < min(d["min_train"], 20):
            s = e
            continue
        Xtr, Xte = diag._impute(X[elig], X[s:e])
        ytr = y[elig]
        m = make_return_model(model_name)
        m.fit(Xtr, ytr)
        p_te = np.asarray(m.predict(Xte), dtype=float)
        idx_test.extend(range(s, e))
        p_val.extend(p_te.tolist())
        p_clim.extend([float(np.mean(ytr))] * (e - s))
        s = e
    return {"idx": np.array(idx_test, dtype=int), "p": np.array(p_val), "p_clim": np.array(p_clim)}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_direction(d: dict[str, Any], wf: dict[str, Any], y: Any = None) -> dict[str, Any]:
    """Confirmatory test: model's 0/1 loss vs the literal always-up baseline
    (gold's long-run drift makes this the right baseline -- ADR 038/040/044
    precedent), one-sided HAC Diebold-Mariano, lag = horizon-1."""

    diag = _diag()
    y = (d["y"] if y is None else y)[wf["idx"]]
    p = wf["p"]
    wrong_model = ((p >= 0.5).astype(int) != y).astype(float)
    wrong_always_up = (1.0 - y).astype(float)
    dm = diag._dm(wrong_model, wrong_always_up, d["horizon"])
    return {
        "n": len(y),
        "acc_model": float(1 - wrong_model.mean()),
        "acc_always_up": float(1 - wrong_always_up.mean()),
        "p_primary": dm["p_one_sided"],
        "effective_n_primary": dm["effective_n"],
        "loss_model": wrong_model,
        "loss_baseline": wrong_always_up,
    }


def score_return(d: dict[str, Any], wf: dict[str, Any], y: Any = None) -> dict[str, Any]:
    """Confirmatory test: model's squared error vs the zero-return (PRIMARY) and
    historical-mean-drift (secondary, reported) baselines, one-sided HAC DM."""

    diag = _diag()
    y = (d["y"] if y is None else y)[wf["idx"]]
    p, pc = wf["p"], wf["p_clim"]
    se_model = (p - y) ** 2
    se_zero = y**2
    se_drift = (pc - y) ** 2
    dm_zero = diag._dm(se_model, se_zero, d["horizon"])
    dm_drift = diag._dm(se_model, se_drift, d["horizon"])
    return {
        "n": len(y),
        "mse_model": float(se_model.mean()),
        "mse_zero_return": float(se_zero.mean()),
        "mse_historical_mean_drift": float(se_drift.mean()),
        "p_primary": dm_zero["p_one_sided"],
        "effective_n_primary": dm_zero["effective_n"],
        "p_vs_historical_mean_drift": dm_drift["p_one_sided"],
        "effective_n_vs_historical_mean_drift": dm_drift["effective_n"],
        "loss_model": se_model,
        "loss_baseline": se_zero,
    }


def half_signs(
    d: dict[str, Any], wf: dict[str, Any], loss_model: Any, loss_baseline: Any
) -> dict[str, Any]:
    """Same-sign-in-both-halves replication check (not independently significant --
    just non-reversed), per ADR 053's success rule."""
    import numpy as np

    as_of_scored = d["as_of"][wf["idx"]]

    def half(mask: Any) -> dict[str, Any]:
        n = int(mask.sum())
        if n < 30:
            return {"n": n, "mean_diff": None, "better": None}
        diff = float(np.mean(loss_model[mask] - loss_baseline[mask]))
        return {"n": n, "mean_diff": diff, "better": diff < 0}

    mask_a = as_of_scored < SPLIT_DATE
    return {"half_2006_2015": half(mask_a), "half_2016_2026": half(~mask_a)}


# ---------------------------------------------------------------------------
# Shards
# ---------------------------------------------------------------------------


def shard_model(horizon: int, target: str, model_name: str) -> dict[str, Any]:
    diag = _diag()
    d = load_dataset(horizon, target)
    if target == "direction":
        wf = diag.walk_forward(d, model_name)
        main = score_direction(d, wf)
    else:
        wf = walk_forward_return(d, model_name)
        main = score_return(d, wf)
    halves = half_signs(d, wf, main.pop("loss_model"), main.pop("loss_baseline"))
    out: dict[str, Any] = {
        "horizon": horizon,
        "target": target,
        "model": model_name,
        **main,
        **halves,
    }
    print(
        horizon,
        target,
        model_name,
        {k: v for k, v in out.items() if not k.startswith("half")},
        flush=True,
    )
    return out


def _injection_signal(d: dict[str, Any]) -> Any:
    """A deterministic function of the row's own (already T-1) momentum feature --
    same construction pattern as analysis_direction_diagnosis._signal."""
    import numpy as np

    idx = d["features"].index("mom_21")
    v = d["X"][:, idx]
    v = np.where(np.isnan(v), np.nanmedian(v), v)
    return (v > np.median(v)).astype(int)


def shard_injection(horizon: int) -> dict[str, Any]:
    """Detection-floor calibration, NOT part of the confirmatory family (see module
    docstring): reuses diag.walk_forward/diag.score exactly, so detection is measured
    against the walk-forward climatology/majority baseline, consistent with the ADR
    040 D3 / ADR 045 floor-measurement precedent this extends to longer horizons."""
    import numpy as np

    diag = _diag()
    d = load_dataset(horizon, "direction")
    sig = _injection_signal(d)
    out: dict[str, Any] = {"horizon": horizon, "results": {}}
    for q in INJECTION_GRID:
        for m in DIRECTION_MODELS:
            rows = []
            for sd in INJECTION_SEEDS:
                rng = np.random.default_rng(sd)
                take = rng.random(len(d["y"])) < q
                y_inj = np.where(take, sig, d["y"])
                wf = diag.walk_forward(d, m, y=y_inj)
                sc = diag.score(d, wf, y=y_inj)
                rows.append(
                    {
                        "seed": sd,
                        "val_acc": sc["val_acc"],
                        "val_majority_acc": sc["val_majority_acc"],
                        "p": sc["p_acc_vs_majority"],
                    }
                )
            det = float(np.mean([r["p"] is not None and r["p"] < 0.05 for r in rows]))
            out["results"][f"q={q}/{m}"] = {"q": q, "model": m, "detection_rate": det, "runs": rows}
            print("inject", horizon, q, m, "detection", det, flush=True)
    floor: dict[str, Any] = {}
    for m in DIRECTION_MODELS:
        fq = next(
            (
                q
                for q in INJECTION_GRID
                if q > 0 and out["results"][f"q={q}/{m}"]["detection_rate"] >= 0.8
            ),
            None,
        )
        floor[m] = {
            "floor_q": fq,
            "floor_oracle_accuracy": None if fq is None else 0.5 + fq / 2,
            "false_positive_rate_q0": out["results"][f"q=0.0/{m}"]["detection_rate"],
        }
    out["floor"] = floor
    return out


def run_shard(key: str) -> dict[str, Any]:
    warnings.filterwarnings("ignore")
    t0 = time.time()
    if key.endswith("_injection"):
        horizon = int(key.removeprefix("h").removesuffix("_injection"))
        r = shard_injection(horizon)
    else:
        h_part, target, model_name = key.split("_", 2)
        r = shard_model(int(h_part.removeprefix("h")), target, model_name)
    r["shard"] = key
    r["seconds"] = round(time.time() - t0, 1)
    r["git_sha"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout.strip()
    r["generated_at_utc"] = datetime.now(UTC).isoformat()
    return r


# ---------------------------------------------------------------------------
# Aggregate: multiplicity correction + success rule over the 12-cell family
# ---------------------------------------------------------------------------


def build_verdict(cells: list[dict[str, Any]]) -> dict[str, Any]:
    from ml.direction.stats_corrections import benjamini_hochberg, bonferroni

    ordered = [
        c
        for key in FAMILY_KEYS
        for c in cells
        if f"h{c['horizon']}_{c['target']}_{c['model']}" == key
    ]
    ps = [c["p_primary"] for c in ordered]
    bonf = bonferroni(ps, alpha=0.05)
    bh = benjamini_hochberg(ps, alpha=0.05)
    table: dict[str, Any] = {}
    for i, c in enumerate(ordered):
        key = f"h{c['horizon']}_{c['target']}_{c['model']}"
        a, b = c["half_2006_2015"]["better"], c["half_2016_2026"]["better"]
        replicates = bool(a) and bool(b)
        table[key] = {
            "p_primary": c["p_primary"],
            "effective_n_primary": c["effective_n_primary"],
            "n": c["n"],
            "bonferroni_significant": bonf["significant"][i],
            "bh_significant": bh["significant"][i],
            "half_2006_2015": c["half_2006_2015"],
            "half_2016_2026": c["half_2016_2026"],
            "replicates_both_halves": replicates,
            "success": bool(bonf["significant"][i] and replicates),
        }
    return {
        "family_size": len(ordered),
        "bonferroni_alpha": bonf["threshold"],
        "table": table,
        "any_success": any(v["success"] for v in table.values()),
    }


def build_floor_summary(cells: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for c in cells:
        out[f"h{c['horizon']}"] = c["floor"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-shards", action="store_true")
    ap.add_argument("--shard")
    ap.add_argument("--aggregate", type=Path)
    ap.add_argument("--out", type=Path, default=Path("out"))
    args = ap.parse_args()
    if args.list_shards:
        print(json.dumps(SHARDS))
        return 0
    if args.shard:
        res = run_shard(args.shard)
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / f"{args.shard}.json").write_text(
            json.dumps(res, default=str) + "\n", encoding="utf-8"
        )
        return 0
    if args.aggregate:
        got: dict[str, dict[str, Any]] = {}
        for k in SHARDS:
            p = args.aggregate / f"{k}.json"
            if p.exists():
                got[k] = json.loads(p.read_text(encoding="utf-8"))
        missing = [k for k in SHARDS if k not in got]
        model_cells = [v for k, v in got.items() if not k.endswith("_injection")]
        injection_cells = [v for k, v in got.items() if k.endswith("_injection")]
        report = {
            "shards": got,
            "missing": missing,
            "verdict": build_verdict(model_cells) if model_cells else None,
            "detection_floor": build_floor_summary(injection_cells) if injection_cells else None,
        }
        args.out.write_text(json.dumps(report, default=str) + "\n", encoding="utf-8")
        print("missing:", missing)
        if not missing and report["verdict"] is not None:
            print(json.dumps(report["verdict"], indent=1, default=str))
        return 1 if missing else 0
    ap.error("need --list-shards, --shard or --aggregate")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
