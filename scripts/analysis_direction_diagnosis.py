"""scripts/analysis_direction_diagnosis.py — why is gold direction noise? (GG item D)

Shards (analysis.yml contract: --list-shards / --shard KEY --out DIR /
--aggregate DIR --out F):

  capacity_comex, capacity_inr   D1 train-vs-validation fit + D6 capacity sweep
  curve_comex                    D2 learning curves (500/1000/2000/all train days)
  inject_comex_linear,           D3 signal injection: plant a known-strength signal
  inject_comex_nonlinear,           into the real labels and measure how often the
  inject_inr_linear                 pipeline detects it
  series                         D4 predictability ceiling of the series itself
  permute_comex, permute_inr     D5 out-of-sample permutation importance by family,
                                    shuffled within 63-day windows (a whole-period
                                    shuffle mixes price levels from different years
                                    and measures regime fragility, not signal)
  family_comex, family_inr       D5 complement: a model trained on ONE family only,
                                    tested against climatology
  inject_comex_linear_stationary D3 repeat with only stationary features (tests
                                    whether the level features block recovery)
  inject_inr_level               D3 on INR with a signal the level-only INR
                                    features can express

Datasets: COMEX daily (ml.direction.comex_daily, target = next trading day up,
~3,400 days, 2013-2026) and INR (ml.direction.dataset, IBJA 22K, target = up in
2 IBJA days, ~180 labelled days). Every walk-forward is expanding-window with
an embargo: a model refitted at block start s trains only on rows whose label
date is strictly before as_of[s], then predicts the next BLOCK rows. Block
refitting (not per-day) keeps high-capacity models affordable; it can only
make predictions staler, never leakier.

Significance everywhere: one-sided HAC Diebold-Mariano (lag h-1) of the model's
loss vs the per-fold majority-class (climatology) baseline; Brier and 0/1 loss.
Shadow research only: nothing here touches the gate or any user-facing file.
"""

from __future__ import annotations

import argparse
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

SEED = 42
COMEX_END = "2026-09-23"
COMEX_MIN_TRAIN = 250
INR_MIN_TRAIN = 20
COMEX_BLOCK = 21  # ~one trading month per refit
INR_BLOCK = 1  # the INR set is small enough to refit every day

SHARDS = [
    "capacity_comex",
    "capacity_inr",
    "curve_comex",
    "inject_comex_linear",
    "inject_comex_nonlinear",
    "inject_inr_linear",
    "series",
    "permute_comex",
    "permute_inr",
    "family_comex",
    "family_inr",
    "inject_comex_linear_stationary",
    "inject_inr_level",
]
# Stationary (scale-free) COMEX features: returns, volatility, distance from a
# moving average, and calendar. Everything else is a price or macro LEVEL.
COMEX_STATIONARY = [
    "gc_return_1d",
    "gc_return_5d",
    "gc_vol_5d",
    "gc_ma20_dist",
    "dow",
    "dom",
    "month",
]
PERMUTE_WINDOW = 63  # shuffle within ~3-month windows so levels stay in-regime

COMEX_FAMILIES: dict[str, list[str]] = {
    "price_history": ["gold_usd_lag1", "gc_return_1d", "gc_return_5d", "gc_vol_5d", "gc_ma20_dist"],
    "macro": [
        "usd_inr_lag1",
        "us_10y_yield_lag1",
        "dxy_lag1",
        "sensex_lag1",
        "vix_lag1",
        "crude_wti_lag1",
        "tips_lag1",
    ],
    "india_vix": ["india_vix_lag1"],
    "calendar": [
        "dow",
        "dom",
        "month",
        "is_festival_window",
        "days_to_next_festival",
        "is_wedding_season",
        "is_budget_window",
        "is_duty_event_recent",
        "days_since_duty_event",
    ],
}
INR_FAMILIES: dict[str, list[str]] = {
    "price_levels": ["gold_usd", "ibja_pm_916", "ibja_am_916", "tanishq_22k"],
    "macro": ["usd_inr", "us_10y_yield", "dxy", "sensex", "vix", "crude_wti", "tips"],
    "calendar": [
        "dow",
        "dom",
        "month",
        "is_festival_window",
        "days_to_next_festival",
        "duty_change_active",
        "days_since_last_duty_change",
    ],
}


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def load(kind: str) -> dict[str, Any]:
    """Returns {X, y, as_of, label_date, horizon, features, families}."""
    import numpy as np

    if kind == "comex":
        from ml.direction.comex_daily import COMEX_FEATURE_COLS, build_comex_dataset

        df = build_comex_dataset(end=COMEX_END, extra_horizons=(1,))
        df = df[df["label_binary_h1"].notna()].reset_index(drop=True)
        feats, fams, h, label, ldate = (
            COMEX_FEATURE_COLS,
            COMEX_FAMILIES,
            1,
            "label_binary_h1",
            "label_date_h1",
        )
    else:
        from ml.direction.dataset import FEATURE_COLS, build_dataset

        df = build_dataset()
        df = df[df["label_binary_h2"].notna()].sort_values("as_of_date").reset_index(drop=True)
        feats, fams, h, label, ldate = (
            FEATURE_COLS,
            INR_FAMILIES,
            2,
            "label_binary_h2",
            "label_date_h2",
        )
    X = df[feats].to_numpy(dtype=float)
    return {
        "X": X,
        "y": df[label].to_numpy(dtype=int),
        "as_of": df["as_of_date"].astype(str).to_numpy(),
        "label_date": np.array([str(v) if v is not None else "9999" for v in df[ldate]]),
        "horizon": h,
        "features": list(feats),
        "families": fams,
        "min_train": COMEX_MIN_TRAIN if kind == "comex" else INR_MIN_TRAIN,
        "block": COMEX_BLOCK if kind == "comex" else INR_BLOCK,
        "extra": df,
    }


# ---------------------------------------------------------------------------
# Models (D6 capacity ladder, simplest first)
# ---------------------------------------------------------------------------


def make_model(name: str):  # type: ignore[no-untyped-def]
    from lightgbm import LGBMClassifier
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if name == "logit_strong_l2":
        return make_pipeline(StandardScaler(), LogisticRegression(C=0.01, max_iter=2000))
    if name == "logit":
        return make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000))
    if name == "gbm_stumps":
        return LGBMClassifier(
            n_estimators=50,
            max_depth=1,
            num_leaves=2,
            learning_rate=0.05,
            random_state=SEED,
            verbose=-1,
            n_jobs=1,
        )
    if name == "gbm_small":
        return LGBMClassifier(
            n_estimators=100,
            num_leaves=7,
            learning_rate=0.05,
            random_state=SEED,
            verbose=-1,
            n_jobs=1,
        )
    if name == "gbm_default":
        return LGBMClassifier(n_estimators=100, random_state=SEED, verbose=-1, n_jobs=1)
    if name == "gbm_large":
        return LGBMClassifier(
            n_estimators=500,
            num_leaves=127,
            min_child_samples=5,
            learning_rate=0.05,
            random_state=SEED,
            verbose=-1,
            n_jobs=1,
        )
    if name == "random_forest":
        return RandomForestClassifier(
            n_estimators=300, min_samples_leaf=1, random_state=SEED, n_jobs=1
        )
    raise ValueError(name)


CAPACITY_LADDER = [
    "logit_strong_l2",
    "logit",
    "gbm_stumps",
    "gbm_small",
    "gbm_default",
    "gbm_large",
    "random_forest",
]


# ---------------------------------------------------------------------------
# Walk-forward with embargo, block refits
# ---------------------------------------------------------------------------


def _impute(X_train, X_test):  # type: ignore[no-untyped-def]
    import numpy as np

    mu = np.nanmean(X_train, axis=0)
    mu = np.where(np.isnan(mu), 0.0, mu)
    Xtr = np.where(np.isnan(X_train), mu, X_train)
    Xte = np.where(np.isnan(X_test), mu, X_test)
    return Xtr, Xte


def walk_forward(
    d: dict[str, Any],
    model_name: str,
    y: Any = None,
    test_start: int | None = None,
    train_window: int | None = None,
    permute: dict[str, Any] | None = None,
    columns: list[int] | None = None,
) -> dict[str, Any]:
    """Expanding (or trailing `train_window`) walk-forward. Returns per-fold
    arrays for validation plus the average in-sample (training) metrics."""
    import numpy as np

    X, as_of, ldate = d["X"], d["as_of"], d["label_date"]
    if columns is not None:
        X = X[:, columns]
        permute = {k: v[:, columns] for k, v in (permute or {}).items()} or None
    y = d["y"] if y is None else y
    n = len(y)
    start = d["min_train"] if test_start is None else test_start
    idx_test, p_val, p_clim = [], [], []
    p_perm: dict[str, list[float]] = {k: [] for k in (permute or {})}
    train_brier, train_acc, train_w = [], [], []
    s = start
    while s < n:
        e = min(s + d["block"], n)
        elig = np.flatnonzero(ldate[:s] < as_of[s])
        if train_window is not None:
            elig = elig[-train_window:]
        if len(elig) < min(d["min_train"], 20) or len(set(y[elig])) < 2:
            s = e
            continue
        Xtr, Xte = _impute(X[elig], X[s:e])
        ytr = y[elig]
        if model_name == "majority":
            p_tr = np.full(len(elig), ytr.mean())
            p_te = np.full(e - s, ytr.mean())
        else:
            m = make_model(model_name)
            m.fit(Xtr, ytr)
            p_tr = m.predict_proba(Xtr)[:, 1]
            p_te = m.predict_proba(Xte)[:, 1]
            for fam, Xperm in (permute or {}).items():
                _, Xp = _impute(X[elig], Xperm[s:e])
                p_perm[fam].extend(m.predict_proba(Xp)[:, 1].tolist())
        train_brier.append(float(np.mean((p_tr - ytr) ** 2)))
        train_acc.append(float(np.mean((p_tr >= 0.5) == ytr)))
        train_w.append(e - s)
        idx_test.extend(range(s, e))
        p_val.extend(p_te.tolist())
        p_clim.extend([float(ytr.mean())] * (e - s))
        s = e
    w = np.array(train_w, dtype=float)
    return {
        "idx": np.array(idx_test, dtype=int),
        "p": np.array(p_val),
        "p_clim": np.array(p_clim),
        "p_perm": {k: np.array(v) for k, v in p_perm.items()},
        "train_brier": float(np.average(train_brier, weights=w)) if len(w) else None,
        "train_acc": float(np.average(train_acc, weights=w)) if len(w) else None,
    }


def _dm(loss_model, loss_base, horizon: int) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    from ml.direction.evaluate_reframed import diebold_mariano_test

    r = diebold_mariano_test(list(loss_model), list(loss_base), horizon, alternative="less")
    return {"p_one_sided": r["p_value"], "effective_n": r["effective_n"]}


def score(d: dict[str, Any], wf: dict[str, Any], y: Any = None) -> dict[str, Any]:
    import numpy as np

    y = (d["y"] if y is None else y)[wf["idx"]]
    p, pc = wf["p"], wf["p_clim"]
    if len(y) == 0:
        return {"n": 0}
    brier = float(np.mean((p - y) ** 2))
    brier_clim = float(np.mean((pc - y) ** 2))
    wrong = ((p >= 0.5).astype(int) != y).astype(float)
    wrong_maj = ((pc >= 0.5).astype(int) != y).astype(float)
    dm_acc = _dm(wrong, wrong_maj, d["horizon"])
    dm_brier = _dm((p - y) ** 2, (pc - y) ** 2, d["horizon"])
    return {
        "n": len(y),
        "val_brier": brier,
        "val_brier_climatology": brier_clim,
        "val_bss": 1 - brier / brier_clim if brier_clim > 0 else None,
        "val_acc": float(1 - wrong.mean()),
        "val_majority_acc": float(1 - wrong_maj.mean()),
        "train_brier": wf["train_brier"],
        "train_acc": wf["train_acc"],
        "p_acc_vs_majority": dm_acc["p_one_sided"],
        "effective_n_acc": dm_acc["effective_n"],
        "p_brier_vs_climatology": dm_brier["p_one_sided"],
    }


# ---------------------------------------------------------------------------
# Shards
# ---------------------------------------------------------------------------


def shard_capacity(kind: str) -> dict[str, Any]:
    d = load(kind)
    out: dict[str, Any] = {"dataset": kind, "rows": len(d["y"]), "models": {}}
    for name in ["majority", *CAPACITY_LADDER]:
        t0 = time.time()
        wf = walk_forward(d, name)
        out["models"][name] = score(d, wf) | {"seconds": round(time.time() - t0, 1)}
        print(kind, name, out["models"][name], flush=True)
    return out


def shard_curve() -> dict[str, Any]:
    """D2: fixed test period (the last 1,000 COMEX days), training window
    limited to the most recent 500 / 1,000 / 2,000 / all eligible days."""
    d = load("comex")
    n = len(d["y"])
    test_start = n - 1000
    out: dict[str, Any] = {
        "dataset": "comex",
        "test_first_as_of": str(d["as_of"][test_start]),
        "curves": {},
    }
    for name in ["logit", "gbm_small", "gbm_default"]:
        out["curves"][name] = {}
        for win in [500, 1000, 2000, None]:
            wf = walk_forward(d, name, test_start=test_start, train_window=win)
            out["curves"][name][str(win or "all")] = score(d, wf)
            print("curve", name, win, out["curves"][name][str(win or "all")], flush=True)
    return out


def _signal(d: dict[str, Any], kind: str):  # type: ignore[no-untyped-def]
    """A deterministic function of the row's own (already T-1) features."""
    import numpy as np

    X = d["X"]
    f = d["features"]

    def col(name: str):  # type: ignore[no-untyped-def]
        v = X[:, f.index(name)]
        return np.where(np.isnan(v), np.nanmedian(v), v)

    if kind == "level":
        v = col("usd_inr")
        return (v > np.median(v)).astype(int)
    if kind == "linear":
        v = col("gc_return_1d") if "gc_return_1d" in f else col("usd_inr")
        if "gc_return_1d" not in f:
            v = np.r_[0.0, np.diff(v)]  # INR set has only levels: use the change
        return (v > np.median(v)).astype(int)
    # nonlinear: an interaction a single linear model cannot represent
    a, b = col("gc_ma20_dist"), col("vix_lag1")
    return ((a > np.median(a)) ^ (b > np.median(b))).astype(int)


def shard_inject(kind: str, signal: str, stationary: bool = False) -> dict[str, Any]:
    """D3: with probability q a day's label is replaced by the planted signal.
    An oracle that knows the signal reaches ~0.5 + q/2 accuracy on a coin-flip
    base, so q sets the size of the only real edge in the data."""
    import numpy as np

    d = load(kind)
    sig = _signal(d, signal)
    qs = [0.0, 0.02, 0.05, 0.1, 0.2, 0.4] if kind == "comex" else [0.0, 0.1, 0.2, 0.4, 0.6]
    seeds = [SEED + i for i in range(5)]
    models = ["logit", "gbm_small"]
    cols = [d["features"].index(c) for c in COMEX_STATIONARY] if stationary else None
    out: dict[str, Any] = {
        "dataset": kind,
        "signal": signal,
        "stationary_only": stationary,
        "results": {},
    }
    for q in qs:
        for m in models:
            rows = []
            for sd in seeds:
                rng = np.random.default_rng(sd)
                take = rng.random(len(d["y"])) < q
                y_inj = np.where(take, sig, d["y"])
                wf = walk_forward(d, m, y=y_inj, columns=cols)
                sc = score(d, wf, y=y_inj)
                rows.append(
                    {
                        "seed": sd,
                        "val_acc": sc["val_acc"],
                        "val_majority_acc": sc["val_majority_acc"],
                        "p": sc["p_acc_vs_majority"],
                        "val_bss": sc["val_bss"],
                    }
                )
            det = float(np.mean([r["p"] is not None and r["p"] < 0.05 for r in rows]))
            out["results"][f"q={q}/{m}"] = {"q": q, "model": m, "detection_rate": det, "runs": rows}
            print(kind, signal, q, m, "detection", det, flush=True)
    return out


def shard_series() -> dict[str, Any]:
    """D4: autocorrelation, Ljung-Box, Lo-MacKinlay variance ratios, by regime."""
    import numpy as np
    import pandas as pd
    from scipy.stats import chi2, norm

    def stats(r: np.ndarray) -> dict[str, Any]:
        r = r[np.isfinite(r)]
        n = len(r)
        if n < 60:
            return {"n": n}
        c = r - r.mean()
        v = float(np.mean(c**2))
        acf = [float(np.sum(c[k:] * c[:-k]) / n / v) for k in range(1, 11)]
        q = n * (n + 2) * sum(a**2 / (n - k) for k, a in enumerate(acf, start=1))
        vr = {}
        for qq in (2, 5, 10, 20):
            # Lo-MacKinlay VR(q) with the heteroskedasticity-robust z statistic.
            sums = np.convolve(r, np.ones(qq), mode="valid")
            m = r.mean()
            var_q = float(np.sum((sums - qq * m) ** 2)) / (qq * (n - qq + 1) * (1 - qq / n))
            var_1 = float(np.sum((r - m) ** 2)) / (n - 1)
            ratio = var_q / var_1
            delta = [
                float(np.sum((c[j:] ** 2) * (c[:-j] ** 2)) / (np.sum(c**2) ** 2))
                for j in range(1, qq)
            ]
            theta = sum((2 * (qq - j) / qq) ** 2 * delta[j - 1] for j in range(1, qq))
            z = (ratio - 1) / math.sqrt(theta) if theta > 0 else float("nan")
            vr[str(qq)] = {
                "vr": ratio,
                "z_robust": z,
                "p_two_sided": float(2 * (1 - norm.cdf(abs(z)))),
            }
        rho1 = acf[0]
        return {
            "n": n,
            "acf_1_to_10": acf,
            "ljung_box_q10": q,
            "ljung_box_p": float(1 - chi2.cdf(q, 10)),
            "variance_ratio": vr,
            # Sign-prediction accuracy an AR(1) with this lag-1 autocorrelation
            # would allow (Gaussian): 0.5 + arcsin(rho)/pi.
            "ar1_direction_ceiling": 0.5 + math.asin(max(-1.0, min(1.0, rho1))) / math.pi,
        }

    series: dict[str, pd.Series] = {}
    d = load("comex")
    df = d["extra"]
    r = np.log(df["next_pm916_h1"].astype(float) / df["current_pm916"].astype(float))
    series["comex_gcf_daily"] = pd.Series(r.to_numpy(), index=pd.to_datetime(df["label_date_h1"]))
    from ml.inr_proxy_labels import LABEL_OUTPUT_PATH

    lab = pd.read_parquet(LABEL_OUTPUT_PATH)
    lvl = lab["label_22k_per_10g"].astype(float)
    lvl = lvl[lvl.diff() != 0]  # drop carried-forward non-publication days
    series["inr_proxy_daily"] = np.log(lvl).diff().dropna()
    ib = pd.read_parquet(ROOT / "data" / "ibja_rates.parquet")
    s_ib = ib.set_index(pd.to_datetime(ib["date"]))["pm_916"].astype(float).sort_index()
    s_ib = s_ib[s_ib.diff() != 0]
    series["ibja_real_daily"] = np.log(s_ib).diff().dropna()

    out: dict[str, Any] = {}
    for name, s in series.items():
        s = s.sort_index()
        vol = s.rolling(20).std()
        med = vol.median()
        out[name] = {
            "all": stats(s.to_numpy()),
            "2013-2017": stats(s[(s.index >= "2013") & (s.index < "2018")].to_numpy()),
            "2018-2021": stats(s[(s.index >= "2018") & (s.index < "2022")].to_numpy()),
            "2022-2026": stats(s[s.index >= "2022"].to_numpy()),
            "low_vol": stats(s[vol <= med].to_numpy()),
            "high_vol": stats(s[vol > med].to_numpy()),
            "range": [str(s.index.min().date()), str(s.index.max().date())],
        }
        print(
            "series",
            name,
            out[name]["all"].get("n"),
            out[name]["all"].get("ljung_box_p"),
            flush=True,
        )
    return out


def shard_permute(kind: str) -> dict[str, Any]:
    """D5: out-of-sample permutation importance per feature family. Test-row
    values of a family are shuffled across ALL test rows (training untouched);
    the rise in validation Brier is that family's out-of-sample contribution.
    CI: moving-block bootstrap over days (block 20)."""
    import numpy as np

    d = load(kind)
    n = len(d["y"])
    start = d["min_train"]
    fam_idx = {
        k: [d["features"].index(c) for c in v if c in d["features"]]
        for k, v in d["families"].items()
    }
    out: dict[str, Any] = {"dataset": kind, "models": {}}
    for m in ["logit", "gbm_small"]:
        res: dict[str, Any] = {}
        for rep in range(3):
            rng = np.random.default_rng(SEED + rep)
            perms = {}
            test_rows = np.arange(start, n)
            for fam, cols in fam_idx.items():
                Xp = d["X"].copy()
                shuffled = test_rows.copy()
                for w0 in range(0, len(test_rows), PERMUTE_WINDOW):
                    seg = shuffled[w0 : w0 + PERMUTE_WINDOW]
                    shuffled[w0 : w0 + PERMUTE_WINDOW] = rng.permutation(seg)
                Xp[np.ix_(test_rows, cols)] = d["X"][np.ix_(shuffled, cols)]
                perms[fam] = Xp
            wf = walk_forward(d, m, permute=perms)
            y = d["y"][wf["idx"]]
            base_loss = (wf["p"] - y) ** 2
            for fam in fam_idx:
                delta = (wf["p_perm"][fam] - y) ** 2 - base_loss
                boots = []
                bl = 20
                nb = max(1, len(delta) // bl)
                for _ in range(500):
                    starts = rng.integers(0, max(1, len(delta) - bl), nb)
                    boots.append(
                        float(np.mean(np.concatenate([delta[s : s + bl] for s in starts])))
                    )
                res.setdefault(fam, []).append(
                    {
                        "delta_brier": float(delta.mean()),
                        "ci95": [
                            float(np.percentile(boots, 2.5)),
                            float(np.percentile(boots, 97.5)),
                        ],
                    }
                )
            res.setdefault("_base_val_brier", []).append(float(base_loss.mean()))
        out["models"][m] = res
        print("permute", kind, m, {k: v[0] for k, v in res.items()}, flush=True)
    return out


def shard_family(kind: str) -> dict[str, Any]:
    """D5 complement: train on ONE feature family only (two low-capacity
    models), score against climatology / majority on unseen days."""
    d = load(kind)
    out: dict[str, Any] = {"dataset": kind, "families": {}}
    for fam, names in d["families"].items():
        cols = [d["features"].index(c) for c in names if c in d["features"]]
        out["families"][fam] = {}
        for m in ["logit_strong_l2", "gbm_stumps"]:
            wf = walk_forward(d, m, columns=cols)
            out["families"][fam][m] = score(d, wf)
            print("family", kind, fam, m, out["families"][fam][m], flush=True)
    return out


def run_shard(key: str) -> dict[str, Any]:
    warnings.filterwarnings("ignore")
    t0 = time.time()
    if key == "capacity_comex":
        r = shard_capacity("comex")
    elif key == "capacity_inr":
        r = shard_capacity("inr")
    elif key == "curve_comex":
        r = shard_curve()
    elif key == "inject_comex_linear":
        r = shard_inject("comex", "linear")
    elif key == "inject_comex_nonlinear":
        r = shard_inject("comex", "nonlinear")
    elif key == "inject_inr_linear":
        r = shard_inject("inr", "linear")
    elif key == "series":
        r = shard_series()
    elif key == "permute_comex":
        r = shard_permute("comex")
    elif key == "permute_inr":
        r = shard_permute("inr")
    elif key == "family_comex":
        r = shard_family("comex")
    elif key == "family_inr":
        r = shard_family("inr")
    elif key == "inject_comex_linear_stationary":
        r = shard_inject("comex", "linear", stationary=True)
    elif key == "inject_inr_level":
        r = shard_inject("inr", "level")
    else:
        raise SystemExit(f"unknown shard {key}")
    r["shard"] = key
    r["seconds"] = round(time.time() - t0, 1)
    r["git_sha"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout.strip()
    r["generated_at_utc"] = datetime.now(UTC).isoformat()
    return r


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
        got = {}
        for k in SHARDS:
            p = args.aggregate / f"{k}.json"
            if p.exists():
                got[k] = json.loads(p.read_text(encoding="utf-8"))
        missing = [k for k in SHARDS if k not in got]
        args.out.write_text(
            json.dumps({"shards": got, "missing": missing}, default=str) + "\n", encoding="utf-8"
        )
        print("missing:", missing)
        return 1 if missing else 0
    ap.error("need --list-shards, --shard or --aggregate")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
