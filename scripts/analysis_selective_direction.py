"""scripts/analysis_selective_direction.py — R4: predict direction only when
confident, abstain otherwise. Implements docs/adr/039 (R4 section); that text
is the spec.

Targets: up vs not-up over h in {1, 5} trading days on the INR proxy (test
2022+), real IBJA (test only, dense daily segments, model trained on the
proxy) and COMEX GC=F (test 2022+). Model: logistic regression (C=0.1,
standardized) on stationary features (returns 1/5/20 d, vol 5/20 d, z vs the
20-day mean, day of week, month). Refit every 21 days, expanding, embargo >= h.

Selection rule: confidence c = |p - 0.5|. For target coverage kappa in
{10, 25, 50, 100}%, the threshold at each refit is the (1 - kappa) quantile
of c over OUT-OF-SAMPLE predictions on the most recent 250 matured training
days (from a model fit only on data before those 250 days). Test days never
set a threshold.

Metric per kappa: realised coverage; precision on selected days; the
baseline ON THE SAME DAYS (training-majority class; always-up also shown);
one-sided HAC DM (lag h-1) of 0/1 loss vs the same-days majority. Family 24
tests: Bonferroni 0.05/24 and BH.

analysis.yml contract: --list-shards / --shard KEY --out DIR /
--aggregate DIR --out F.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HORIZONS = [1, 5]
KAPPAS = [0.10, 0.25, 0.50, 1.00]
REFIT_EVERY = 21
INNER = 250
MIN_TRAIN = 500
SELECTION_END = "2021-12-31"
TEST_START = "2022-01-01"
COMEX_END = "2026-09-23"
MAX_GAP_DAYS = 4
FEATURES = ["ret1", "ret5", "ret20", "vol5", "vol20", "z", "dow", "month"]
SHARDS = ["inr", "comex"]


def features(p):  # type: ignore[no-untyped-def]
    import numpy as np
    import pandas as pd

    lp = np.log(p)
    r = lp.diff()
    return pd.DataFrame(
        {
            "ret1": r,
            "ret5": lp.diff(5),
            "ret20": lp.diff(20),
            "vol5": r.rolling(5).std(),
            "vol20": r.rolling(20).std(),
            "z": (p - p.rolling(20).mean()) / p.rolling(20).std(),
            "dow": p.index.dayofweek,
            "month": p.index.month,
        },
        index=p.index,
    )


def up_label(p, h: int):  # type: ignore[no-untyped-def]
    import numpy as np

    P = p.to_numpy()
    y = np.full(len(P), np.nan)
    y[: len(P) - h] = (P[h:] > P[:-h]).astype(float)
    return y


def dense_segments(p):  # type: ignore[no-untyped-def]
    gaps = p.index.to_series().diff().dt.days.fillna(0).to_numpy()
    seg = (gaps > MAX_GAP_DAYS).cumsum()
    return [p[seg == k] for k in sorted(set(seg))]


def load(kind: str):  # type: ignore[no-untyped-def]
    import pandas as pd

    if kind == "proxy":
        from ml.inr_proxy_labels import LABEL_OUTPUT_PATH

        s = pd.read_parquet(LABEL_OUTPUT_PATH)["label_22k_per_10g"].astype(float)
        s.index = pd.to_datetime(s.index).tz_localize(None)
    elif kind == "ibja":
        ib = pd.read_parquet(ROOT / "data" / "ibja_rates.parquet")
        s = ib.set_index(pd.to_datetime(ib["date"]))["pm_916"].astype(float)
    else:
        from ml.direction.comex_daily import _fetch_all_drivers

        drivers, _rolls, trading = _fetch_all_drivers("2012-11-01", COMEX_END)
        s = drivers["gold_usd"][trading.to_numpy()].astype(float)
        s.index = pd.to_datetime(s.index).tz_localize(None)
    s = s.sort_index().dropna()
    return s[s.diff() != 0]


def _model():  # type: ignore[no-untyped-def]
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    return make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=2000))


def _usable(X, y, idx):  # type: ignore[no-untyped-def]
    import numpy as np

    idx = np.asarray(idx, dtype=int)
    ok = ~np.isnan(y[idx]) & ~np.isnan(X[idx]).any(axis=1)
    return idx[ok]


def fit_at(X, y, m: int, h: int):  # type: ignore[no-untyped-def]
    """Model + per-kappa thresholds using training rows < m whose labels have
    matured by row m (j + h <= m - 1, i.e. j <= m - 1 - h). Returns None if
    there is too little data."""
    import numpy as np

    last = m - 1 - h
    if last < MIN_TRAIN + INNER:
        return None
    inner = _usable(X, y, range(last - INNER + 1, last + 1))
    # Inner model: trained only on rows whose labels matured before the inner window starts.
    pre = _usable(X, y, range(0, last - INNER + 1 - h))
    full = _usable(X, y, range(0, last + 1))
    if len(pre) < MIN_TRAIN or len(inner) < 50 or len(set(y[pre])) < 2:
        return None
    mi = _model().fit(X[pre], y[pre].astype(int))
    c_inner = np.abs(mi.predict_proba(X[inner])[:, 1] - 0.5)
    taus = {k: float(np.quantile(c_inner, 1 - k)) if k < 1 else -1.0 for k in KAPPAS}
    mf = _model().fit(X[full], y[full].astype(int))
    majority = int(y[full].mean() >= 0.5)
    return mf, taus, majority


def walk(p_train, X_train, y_train, target_dates, X_target, h: int):  # type: ignore[no-untyped-def]
    """Probabilities, thresholds and majority for each target date. The model
    used at date d is fit on training rows dated <= d (then embargoed by h)."""
    import numpy as np

    train_dates = p_train.index.to_numpy()
    probs = np.full(len(target_dates), np.nan)
    maj = np.full(len(target_dates), np.nan)
    tau = {k: np.full(len(target_dates), np.nan) for k in KAPPAS}
    state = None
    for i, d in enumerate(target_dates):
        if state is None or i % REFIT_EVERY == 0:
            m = int(np.searchsorted(train_dates, d, side="right"))
            state = fit_at(X_train, y_train, m, h)
            if state is None:
                continue
        if np.isnan(X_target[i]).any():
            continue
        model, taus, majority = state
        probs[i] = model.predict_proba(X_target[i : i + 1])[0, 1]
        maj[i] = majority
        for k in KAPPAS:
            tau[k][i] = taus[k]
    return probs, maj, tau


def evaluate(y, probs, maj, tau, test_mask, h: int) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    import numpy as np
    from ml.direction.evaluate_reframed import diebold_mariano_test

    base = test_mask & ~np.isnan(y) & ~np.isnan(probs)
    out: dict[str, Any] = {"n_test_days": int(base.sum())}
    for k in KAPPAS:
        sel = base & (np.abs(probs - 0.5) >= tau[k])
        yy = y[sel].astype(int)
        pred = (probs[sel] >= 0.5).astype(int)
        mj = maj[sel].astype(int)
        n = len(yy)
        if n < 3:
            out[f"kappa={k}"] = {"n_selected": n}
            continue
        wrong = (pred != yy).astype(float)
        wrong_maj = (mj != yy).astype(float)
        dm = diebold_mariano_test(wrong.tolist(), wrong_maj.tolist(), h, alternative="less")
        out[f"kappa={k}"] = {
            "n_selected": n,
            "coverage": float(n / base.sum()),
            "precision": float(1 - wrong.mean()),
            "majority_same_days": float(1 - wrong_maj.mean()),
            "always_up_same_days": float(yy.mean()),
            "effective_n": dm["effective_n"],
            "p_one_sided": dm["p_value"],
        }
    return out


def run_inr() -> dict[str, Any]:
    import numpy as np
    import pandas as pd

    proxy = load("proxy")
    ibja = load("ibja")
    fp = features(proxy)
    Xp = fp[FEATURES].to_numpy(dtype=float)
    out: dict[str, Any] = {}
    for h in HORIZONS:
        yp = up_label(proxy, h)
        probs, maj, tau = walk(proxy, Xp, yp, proxy.index.to_numpy(), Xp, h)
        test = proxy.index.to_numpy() >= np.datetime64(TEST_START)
        out[f"proxy/h{h}"] = evaluate(yp, probs, maj, tau, test, h)
        # Real IBJA: features and labels inside dense segments only; model from the proxy.
        segs = [s for s in dense_segments(ibja) if len(s) > 25]
        yi = np.concatenate([up_label(s, h) for s in segs])
        fi = pd.concat([features(s) for s in segs])
        Xi = fi[FEATURES].to_numpy(dtype=float)
        dates = fi.index.to_numpy()
        pr, mj, ta = walk(proxy, Xp, yp, dates, Xi, h)
        out[f"real_ibja/h{h}"] = evaluate(yi, pr, mj, ta, dates >= np.datetime64(TEST_START), h)
        out[f"real_ibja/h{h}"]["segments"] = [
            [str(s.index[0].date()), str(s.index[-1].date()), len(s)] for s in segs
        ]
    return out


def run_comex() -> dict[str, Any]:
    import numpy as np

    s = load("comex")
    f = features(s)
    X = f[FEATURES].to_numpy(dtype=float)
    out: dict[str, Any] = {"range": [str(s.index[0].date()), str(s.index[-1].date())]}
    for h in HORIZONS:
        y = up_label(s, h)
        probs, maj, tau = walk(s, X, y, s.index.to_numpy(), X, h)
        out[f"comex/h{h}"] = evaluate(
            y, probs, maj, tau, s.index.to_numpy() >= np.datetime64(TEST_START), h
        )
    return out


def aggregate(results: dict[str, Any]) -> dict[str, Any]:
    from ml.direction.stats_corrections import benjamini_hochberg, bonferroni

    rows = []
    for block in results.values():
        for key, v in block.items():
            if not isinstance(v, dict) or "/" not in key:
                continue
            for kk, cell in v.items():
                if kk.startswith("kappa=") and cell.get("p_one_sided") is not None:
                    rows.append((f"{key}/{kk}", cell))
    ps = [c["p_one_sided"] for _, c in rows]
    bon, bh = bonferroni(ps), benjamini_hochberg(ps)
    for (_, c), b, h in zip(rows, bon["significant"], bh["significant"], strict=True):
        c["bonferroni"], c["bh"] = bool(b), bool(h)
    return {"family_size": len(ps), "bonferroni_threshold": bon["threshold"], "results": results}


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
        import warnings

        warnings.filterwarnings("ignore")
        res = run_inr() if args.shard == "inr" else run_comex()
        res["git_sha"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
        ).stdout.strip()
        res["generated_at_utc"] = datetime.now(UTC).isoformat()
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / f"{args.shard}.json").write_text(
            json.dumps(res, default=str) + "\n", encoding="utf-8"
        )
        return 0
    if args.aggregate:
        got = {}
        for k in SHARDS:
            path = args.aggregate / f"{k}.json"
            if path.exists():
                got[k] = json.loads(path.read_text(encoding="utf-8"))
        report = aggregate(got)
        report["missing"] = [k for k in SHARDS if k not in got]
        args.out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        return 1 if report["missing"] else 0
    ap.error("need --list-shards, --shard or --aggregate")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
