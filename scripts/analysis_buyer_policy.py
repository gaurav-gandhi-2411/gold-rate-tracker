"""scripts/analysis_buyer_policy.py — R3: "should I wait?" judged in rupees.

Implements docs/adr/039 (R3 section) exactly; that text is the spec. Each
trading day t a buyer must buy 1 g of 22K within N trading days. A policy
says buy today, or waits for a trigger and otherwise buys on day t+N.
Saving_t = P_t - P_paid (Rs/g) vs always buying today.

Policies (ADR 039):
  P1 limit order   L = P_t * (1 - k * sigma_t), EWMA(0.94) daily vol; k grid
  P2 stretch-wait  z_t = (P_t - MA20) / SD20; wait if z_t > z*; buy at z <= 0
  P3 model-wait    logistic(C=0.1) P(dip >= 0.5% within N); wait if p > theta
Selection on the proxy up to 2021-12-31 (decision and outcome dates both in
the period); test on proxy and real IBJA from 2022. One-sided HAC test
(lag N-1) of mean saving > 0; Bonferroni over the 6 primary (real IBJA)
tests, BH over all 12. Oracle and always-wait-N reported for context.

analysis.yml contract: --list-shards / --shard KEY --out DIR /
--aggregate DIR --out F. Shadow research; writes nothing but its outputs.
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

SEED = 42
HORIZONS = [5, 10]
SELECTION_END = "2021-12-31"
TEST_START = "2022-01-01"
DIP = 0.005
GRIDS: dict[str, list[float]] = {
    "P1": [0.25, 0.5, 1.0, 1.5],
    "P2": [0.5, 1.0, 1.5],
    "P3": [0.5, 0.6, 0.7],
}
# Tie-break toward the more conservative value (ADR 039): smaller k, larger z*, larger theta.
CONSERVATIVE_FIRST: dict[str, bool] = {"P1": True, "P2": False, "P3": False}
REFIT_EVERY = 21
ALPHA = 0.05
# The real-IBJA file is not daily everywhere: before 2025-Q2 its median gap is
# 5-18 days, and 2026-Q1 has one row. ADR 039 defines horizons in TRADING days,
# so IBJA is split into dense segments (every gap <= 4 calendar days: weekend
# plus a holiday) and each segment is simulated on its own; windows and
# feature look-backs never span a gap.
MAX_GAP_DAYS = 4


def load_series(kind: str):  # type: ignore[no-untyped-def]
    """22K Rs/gram, one row per real trading day (carried-forward days dropped)."""
    import pandas as pd

    if kind == "proxy":
        from ml.inr_proxy_labels import LABEL_OUTPUT_PATH

        s = pd.read_parquet(LABEL_OUTPUT_PATH)["label_22k_per_10g"].astype(float)
        s.index = pd.to_datetime(s.index).tz_localize(None)
    else:
        ib = pd.read_parquet(ROOT / "data" / "ibja_rates.parquet")
        s = ib.set_index(pd.to_datetime(ib["date"]))["pm_916"].astype(float)
    s = s.sort_index().dropna()
    s = s[s.diff() != 0] / 10.0
    s.index.name = "date"
    return s


def features(p):  # type: ignore[no-untyped-def]
    """Everything at row t uses prices through t only."""
    import numpy as np
    import pandas as pd

    r = np.log(p).diff()
    var = r.pow(2).copy()
    ew = var.ewm(alpha=1 - 0.94, adjust=False).mean()  # EWMA(0.94) variance through t
    ma20 = p.rolling(20).mean()
    sd20 = p.rolling(20).std()
    f = pd.DataFrame(
        {
            "sigma": np.sqrt(ew),
            "z": (p - ma20) / sd20,
            "ret1": r,
            "ret5": np.log(p).diff(5),
            "ret20": np.log(p).diff(20),
            "vol5": r.rolling(5).std(),
            "vol20": r.rolling(20).std(),
            "dow": p.index.dayofweek,
            "month": p.index.month,
        },
        index=p.index,
    )
    return f


def simulate(p, f, n: int, policy: str, param: float, p3_prob=None):  # type: ignore[no-untyped-def]
    """Saving (Rs/g) per decision day t that has t+n in range; NaN where the
    policy cannot decide yet (feature warm-up)."""
    import numpy as np

    P = p.to_numpy()
    sig = f["sigma"].to_numpy()
    z = f["z"].to_numpy()
    out = np.full(len(P), np.nan)
    for t in range(len(P) - n):
        if policy == "P1":
            if np.isnan(sig[t]):
                continue
            limit = P[t] * (1 - param * sig[t])
            paid = P[t + n]
            for s in range(t + 1, t + n + 1):
                if P[s] <= limit:
                    paid = P[s]
                    break
        elif policy == "P2":
            if np.isnan(z[t]):
                continue
            if z[t] > param:
                paid = P[t + n]
                for s in range(t + 1, t + n + 1):
                    if not np.isnan(z[s]) and z[s] <= 0:
                        paid = P[s]
                        break
            else:
                paid = P[t]
        elif policy == "P3":
            if p3_prob is None or np.isnan(p3_prob[t]):
                continue
            if p3_prob[t] > param:
                limit = P[t] * (1 - DIP)
                paid = P[t + n]
                for s in range(t + 1, t + n + 1):
                    if P[s] <= limit:
                        paid = P[s]
                        break
            else:
                paid = P[t]
        else:
            raise ValueError(policy)
        out[t] = P[t] - paid
    return out


P3_FEATURES = ["ret1", "ret5", "ret20", "vol5", "vol20", "z", "dow", "month"]


def dip_label(p, n: int):  # type: ignore[no-untyped-def]
    import numpy as np

    P = p.to_numpy()
    y = np.full(len(P), np.nan)
    for t in range(len(P) - n):
        y[t] = float(P[t + 1 : t + n + 1].min() <= P[t] * (1 - DIP))
    return y


def p3_probabilities(train_p, train_f, target_f, target_dates, n: int):  # type: ignore[no-untyped-def]
    """Walk-forward P(dip) for each target date. The model at date d is fit on
    training rows whose label has matured by d (row j usable iff j + n <= the
    last training index dated <= d). Refit every REFIT_EVERY target days."""
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    y_all = dip_label(train_p, n)
    Xtr_all = train_f[P3_FEATURES].to_numpy(dtype=float)
    train_dates = train_p.index.to_numpy()
    Xte = target_f[P3_FEATURES].to_numpy(dtype=float)
    probs = np.full(len(target_dates), np.nan)
    model = None
    for i, d in enumerate(target_dates):
        if model is None or i % REFIT_EVERY == 0:
            last = int(np.searchsorted(train_dates, d, side="right")) - 1
            usable = np.arange(0, max(0, last - n + 1))  # j + n <= last
            ok = usable[~np.isnan(y_all[usable]) & ~np.isnan(Xtr_all[usable]).any(axis=1)]
            if len(ok) < 250 or len(set(y_all[ok])) < 2:
                model = None
                continue
            model = make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=2000))
            model.fit(Xtr_all[ok], y_all[ok].astype(int))
        if np.isnan(Xte[i]).any():
            continue
        probs[i] = model.predict_proba(Xte[i : i + 1])[0, 1]
    return probs


def hac_test(savings, n: int) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    import numpy as np
    from ml.direction.evaluate_reframed import diebold_mariano_test

    s = savings[~np.isnan(savings)]
    if len(s) < 3:
        return {"n": len(s)}
    dm = diebold_mariano_test((-s).tolist(), [0.0] * len(s), n, alternative="less")
    se = float(np.sqrt(dm["long_run_var"] / len(s))) if dm["long_run_var"] else float("nan")
    m = float(s.mean())
    return {
        "n": len(s),
        "effective_n": dm["effective_n"],
        "mean_saving_rs_per_g": m,
        "hac_ci95": [m - 1.96 * se, m + 1.96 * se],
        "p_one_sided": dm["p_value"],
        "share_saved": float((s > 0).mean()),
        "share_cost": float((s < 0).mean()),
    }


def mask_between(dates, start: str | None, end: str | None, n_out_end=None):  # type: ignore[no-untyped-def]
    import numpy as np

    m = np.ones(len(dates), dtype=bool)
    if start:
        m &= dates >= np.datetime64(start)
    if end:
        m &= dates <= np.datetime64(end)
    if n_out_end is not None:
        m &= n_out_end
    return m


def dense_segments(p):  # type: ignore[no-untyped-def]
    """Split a series into runs whose consecutive gaps are <= MAX_GAP_DAYS."""
    gaps = p.index.to_series().diff().dt.days.fillna(0).to_numpy()
    seg_id = (gaps > MAX_GAP_DAYS).cumsum()
    return [p[seg_id == k] for k in sorted(set(seg_id))]


def simulate_segmented(p, n: int, policy: str, param: float, probs_by_date=None):  # type: ignore[no-untyped-def]
    """simulate() per dense segment; returns savings aligned to p's index."""
    import numpy as np
    import pandas as pd

    out = pd.Series(np.nan, index=p.index)
    for seg in dense_segments(p):
        if len(seg) <= n:
            continue
        f = features(seg)
        probs = None
        if probs_by_date is not None:
            probs = probs_by_date.reindex(seg.index).to_numpy()
        out.loc[seg.index] = simulate(seg, f, n, policy, param, probs)
    return out.to_numpy()


def ibja_features_segmented(p):  # type: ignore[no-untyped-def]
    import pandas as pd

    return pd.concat([features(seg) for seg in dense_segments(p)])


def run_all() -> dict[str, Any]:
    import numpy as np
    import pandas as pd

    proxy = load_series("proxy")
    ibja = load_series("ibja")
    fp, fi = features(proxy), ibja_features_segmented(ibja)
    pdates, idates = proxy.index.to_numpy(), ibja.index.to_numpy()
    out: dict[str, Any] = {"selection": {}, "test": {}, "context": {}}
    for n in HORIZONS:
        # Selection: decision AND outcome (t+n) inside the selection period.
        outcome_date = np.r_[pdates[n:], np.array([np.datetime64("NaT")] * n)]
        sel = mask_between(pdates, None, SELECTION_END) & (
            outcome_date <= np.datetime64(SELECTION_END)
        )
        p3_proxy = {"probs": p3_probabilities(proxy, fp, fp, pdates, n)}
        chosen: dict[str, float] = {}
        for pol, grid in GRIDS.items():
            scores = []
            for g in grid:
                sv = simulate(proxy, fp, n, pol, g, p3_proxy["probs"] if pol == "P3" else None)
                scores.append(float(np.nanmean(sv[sel])))
            order = list(range(len(grid)))
            if not CONSERVATIVE_FIRST[pol]:
                order = order[::-1]
            best = max(order, key=lambda i: scores[i])  # max() keeps the first of ties
            chosen[pol] = grid[best]
            out["selection"][f"{pol}_N{n}"] = {
                "grid": grid,
                "mean_saving": scores,
                "chosen": grid[best],
            }
        # Test sets.
        p3_ibja = p3_probabilities(proxy, fp, fi, idates, n)
        for series, P, F, dates, probs in (
            ("real_ibja", ibja, fi, idates, p3_ibja),
            ("proxy", proxy, fp, pdates, p3_proxy["probs"]),
        ):
            test = mask_between(dates, TEST_START, None)
            for pol in GRIDS:
                if series == "real_ibja":
                    pb = pd.Series(probs, index=P.index) if pol == "P3" else None
                    sv = simulate_segmented(P, n, pol, chosen[pol], pb)
                else:
                    sv = simulate(P, F, n, pol, chosen[pol], probs if pol == "P3" else None)
                sv = np.where(test, sv, np.nan)
                out["test"][f"{series}/{pol}/N{n}"] = {"param": chosen[pol]} | hac_test(sv, n)
            oracle = pd.Series(np.nan, index=P.index)
            wait = pd.Series(np.nan, index=P.index)
            segs = dense_segments(P) if series == "real_ibja" else [P]
            for seg in segs:
                Pa = seg.to_numpy()
                for t in range(len(Pa) - n):
                    oracle.loc[seg.index[t]] = Pa[t] - Pa[t : t + n + 1].min()
                    wait.loc[seg.index[t]] = Pa[t] - Pa[t + n]
            oracle, wait = oracle.to_numpy(), wait.to_numpy()
            out["context"][f"{series}/N{n}"] = {
                "oracle": hac_test(np.where(test, oracle, np.nan), n),
                "always_wait_N": hac_test(np.where(test, wait, np.nan), n),
                "test_first_date": str(dates[test][0])[:10] if test.any() else None,
                "test_last_decision": str(dates[test][-n - 1])[:10] if test.sum() > n else None,
            }
    # Corrections: primary = real IBJA (6); BH over all 12.
    from ml.direction.stats_corrections import benjamini_hochberg, bonferroni

    keys = [k for k in out["test"] if out["test"][k].get("p_one_sided") is not None]
    prim = [k for k in keys if k.startswith("real_ibja/")]
    bon = bonferroni([out["test"][k]["p_one_sided"] for k in prim])
    bh = benjamini_hochberg([out["test"][k]["p_one_sided"] for k in keys])
    for k, sig in zip(prim, bon["significant"], strict=True):
        out["test"][k]["bonferroni_primary"] = bool(sig)
    for k, sig in zip(keys, bh["significant"], strict=True):
        out["test"][k]["bh_all"] = bool(sig)
    out["bonferroni_threshold_primary"] = bon["threshold"]
    out["proxy_range"] = [str(pdates[0])[:10], str(pdates[-1])[:10]]
    out["ibja_range"] = [str(idates[0])[:10], str(idates[-1])[:10]]
    out["ibja_dense_segments"] = [
        [str(sg.index[0].date()), str(sg.index[-1].date()), len(sg)]
        for sg in dense_segments(ibja)
        if len(sg) > max(HORIZONS)
    ]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list-shards", action="store_true")
    ap.add_argument("--shard")
    ap.add_argument("--aggregate", type=Path)
    ap.add_argument("--out", type=Path, default=Path("out"))
    args = ap.parse_args()
    if args.list_shards:
        print(json.dumps(["all"]))
        return 0
    if args.aggregate:
        res = json.loads((args.aggregate / "all.json").read_text(encoding="utf-8"))
        args.out.write_text(json.dumps(res, indent=2, default=str) + "\n", encoding="utf-8")
        print(json.dumps(res["test"], indent=1, default=str))
        return 0
    import warnings

    warnings.filterwarnings("ignore")
    res = run_all()
    res["git_sha"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout.strip()
    res["generated_at_utc"] = datetime.now(UTC).isoformat()
    if args.shard:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "all.json").write_text(json.dumps(res, default=str) + "\n", encoding="utf-8")
    print(json.dumps(res["test"], indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
