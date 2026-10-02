"""scripts/analysis_nowcast.py — R2: "what is today's true retail price?"

The site estimates Tanishq's 22K retail price from the latest IBJA PM rate
through ml.calibration (recency-weighted Huber, same-day overlap pairs, the
latest IBJA as-of the day within 14 days). This scores that model (M0) and
candidate improvements on the SAME days, walk-forward, each day predicted from
pairs strictly before it:

  M0  current: Huber(ibja_pm) -> tanishq, recency half-life 10
  M1  ratio:   recency-weighted median(tanishq / ibja_pm) x ibja_pm
  M2  lag:     Huber on [ibja_pm, previous IBJA pm] (Tanishq trails IBJA ~1 session)
  M3  am+pm:   Huber on [ibja_am, ibja_pm]
  M4  M0 x global-move adjustment: COMEX GC=F x USD/INR close before the
      scored day / the same product's close before the IBJA date (1.0 on
      same-day pairs, >1 or <1 on carried-forward weekend/holiday days)
  M5  M2 with the M4 adjustment

Truth: Tanishq 22K Rs/g (data/prices.json, last reading per UTC date).
Metric: MAE in Rs/g (and %); paired one-sided HAC test (lag 1) of |error|
vs M0; Bonferroni over the 5 candidates on all days, BH over all
candidate x subset tests. Subsets: all days, same-day IBJA, carried-forward.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HALF_LIFE = 10.0
MIN_TRAIN = 30
MAX_AGE_DAYS = 14
CANDIDATES = ["M1_ratio", "M2_lag", "M3_am_pm", "M4_global_move", "M5_lag_global_move"]


def load_truth() -> pd.Series:
    raw = json.loads((ROOT / "data" / "prices.json").read_text(encoding="utf-8"))
    rows = [(r["timestamp"][:10], float(r["22k"])) for r in raw if r.get("22k") is not None]
    df = pd.DataFrame(rows, columns=["date", "t22"]).groupby("date").last()
    df.index = pd.to_datetime(df.index)
    return df["t22"].sort_index()


def load_ibja() -> pd.DataFrame:
    ib = pd.read_parquet(ROOT / "data" / "ibja_rates.parquet")
    ib = ib.dropna(subset=["pm_916"]).copy()
    ib["date"] = pd.to_datetime(ib["date"])
    ib = ib.sort_values("date").set_index("date")
    out = pd.DataFrame({"pm": ib["pm_916"] / 10.0, "am": ib["am_916"] / 10.0})
    out["pm_prev"] = out["pm"].shift(1)
    return out


def load_global(start: str, end: str) -> pd.Series:
    """Daily close of GC=F x INR=X (Rs per troy oz), indexed by date."""
    import yfinance as yf

    raw = yf.download(
        ["GC=F", "INR=X"], start=start, end=end, auto_adjust=True, progress=False, threads=False
    )
    c = raw["Close"].ffill().dropna()
    g = c["GC=F"] * c["INR=X"]
    g.index = pd.to_datetime(g.index).tz_localize(None)
    return g


def weights(n: int) -> np.ndarray:
    age = np.arange(n, 0, -1)
    return 0.5 ** ((age - 1) / HALF_LIFE)


def huber(X: np.ndarray, y: np.ndarray, w: np.ndarray):  # type: ignore[no-untyped-def]
    from sklearn.linear_model import HuberRegressor, LinearRegression

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return HuberRegressor(epsilon=1.35, max_iter=200).fit(X, y, sample_weight=w)
        except Exception:
            return LinearRegression().fit(X, y, sample_weight=w)


def weighted_median(v: np.ndarray, w: np.ndarray) -> float:
    order = np.argsort(v)
    cw = np.cumsum(w[order])
    return float(v[order][np.searchsorted(cw, cw[-1] / 2)])


def build(truth: pd.Series, ibja: pd.DataFrame, glob: pd.Series) -> pd.DataFrame:
    """Scoring days: truth days with an IBJA as-of within MAX_AGE_DAYS."""
    t = truth.rename("t22").reset_index()
    ib = ibja.reset_index().rename(columns={"date": "ibja_date"})
    m = pd.merge_asof(
        t.sort_values("date"),
        ib.sort_values("ibja_date"),
        left_on="date",
        right_on="ibja_date",
        direction="backward",
    )
    m = m.dropna(subset=["pm"])
    m["gap_days"] = (m["date"] - m["ibja_date"]).dt.days
    m = m[m["gap_days"] < MAX_AGE_DAYS].reset_index(drop=True)

    def g_before(d: pd.Timestamp) -> float:
        s = glob[glob.index < d]
        return float(s.iloc[-1]) if len(s) else np.nan

    m["move"] = [
        1.0 if gd == 0 else g_before(d) / g_before(i)
        for d, i, gd in zip(m["date"], m["ibja_date"], m["gap_days"], strict=True)
    ]
    return m


def predict_all(m: pd.DataFrame) -> dict[str, np.ndarray]:
    """Walk-forward: day k is predicted from same-day pairs dated strictly before it."""
    preds = {k: np.full(len(m), np.nan) for k in ["M0_current", *CANDIDATES]}
    same = m[m["gap_days"] == 0]
    for k in range(len(m)):
        d = m.loc[k, "date"]
        tr = same[same["date"] < d]
        if len(tr) < MIN_TRAIN:
            continue
        w = weights(len(tr))
        row = m.loc[k]
        mv = row["move"] if np.isfinite(row["move"]) else 1.0
        f0 = huber(tr[["pm"]].to_numpy(), tr["t22"].to_numpy(), w)
        preds["M0_current"][k] = f0.predict([[row["pm"]]])[0]
        ratio = weighted_median((tr["t22"] / tr["pm"]).to_numpy(), w)
        preds["M1_ratio"][k] = ratio * row["pm"]
        tl = tr.dropna(subset=["pm_prev"])
        if len(tl) >= MIN_TRAIN and np.isfinite(row["pm_prev"]):
            f2 = huber(tl[["pm", "pm_prev"]].to_numpy(), tl["t22"].to_numpy(), weights(len(tl)))
            preds["M2_lag"][k] = f2.predict([[row["pm"], row["pm_prev"]]])[0]
            preds["M5_lag_global_move"][k] = preds["M2_lag"][k] * mv
        ta = tr.dropna(subset=["am"])
        if len(ta) >= MIN_TRAIN and np.isfinite(row["am"]):
            f3 = huber(ta[["am", "pm"]].to_numpy(), ta["t22"].to_numpy(), weights(len(ta)))
            preds["M3_am_pm"][k] = f3.predict([[row["am"], row["pm"]]])[0]
        preds["M4_global_move"][k] = preds["M0_current"][k] * mv
    return preds


def score(m: pd.DataFrame, preds: dict[str, np.ndarray]) -> dict[str, Any]:
    from ml.direction.evaluate_reframed import diebold_mariano_test
    from ml.direction.stats_corrections import benjamini_hochberg, bonferroni

    y = m["t22"].to_numpy()
    subsets = {
        "all": np.ones(len(m), dtype=bool),
        "same_day": (m["gap_days"] == 0).to_numpy(),
        "carried_forward": (m["gap_days"] > 0).to_numpy(),
    }
    out: dict[str, Any] = {"subsets": {}}
    tests = []
    for sname, mask in subsets.items():
        base_ok = mask & np.isfinite(preds["M0_current"])
        block: dict[str, Any] = {}
        for name, p in preds.items():
            ok = base_ok & np.isfinite(p)
            if ok.sum() < 3:
                block[name] = {"n": int(ok.sum())}
                continue
            err = np.abs(p[ok] - y[ok])
            cell: dict[str, Any] = {
                "n": int(ok.sum()),
                "mae_rs_per_g": float(err.mean()),
                "mae_pct": float(np.mean(err / y[ok]) * 100),
                "p90_abs_err": float(np.quantile(err, 0.9)),
            }
            if name != "M0_current":
                e0 = np.abs(preds["M0_current"][ok] - y[ok])
                dm = diebold_mariano_test(err.tolist(), e0.tolist(), 2, alternative="less")
                cell["mae_m0_same_days"] = float(e0.mean())
                cell["p_one_sided_vs_m0"] = dm["p_value"]
                cell["effective_n"] = dm["effective_n"]
                tests.append((sname, name, cell))
            block[name] = cell
        out["subsets"][sname] = block
    prim = [c for s, _, c in tests if s == "all" and c.get("p_one_sided_vs_m0") is not None]
    allc = [c for _, _, c in tests if c.get("p_one_sided_vs_m0") is not None]
    bon = bonferroni([c["p_one_sided_vs_m0"] for c in prim])
    bh = benjamini_hochberg([c["p_one_sided_vs_m0"] for c in allc])
    for c, s in zip(prim, bon["significant"], strict=True):
        c["bonferroni_primary"] = bool(s)
    for c, s in zip(allc, bh["significant"], strict=True):
        c["bh_all"] = bool(s)
    out["bonferroni_threshold"] = bon["threshold"]
    return out


def run() -> dict[str, Any]:
    truth = load_truth()
    ibja = load_ibja()
    start = (truth.index.min() - pd.Timedelta(days=40)).strftime("%Y-%m-%d")
    end = (truth.index.max() + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    glob = load_global(start, end)
    m = build(truth, ibja, glob)
    preds = predict_all(m)
    res = score(m, preds)
    res["days"] = [str(m["date"].min().date()), str(m["date"].max().date())]
    res["n_scoring_days"] = len(m)
    res["n_same_day"] = int((m["gap_days"] == 0).sum())
    res["git_sha"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout.strip()
    res["generated_at_utc"] = datetime.now(UTC).isoformat()
    return res


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
        args.out.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
        return 0
    res = run()
    if args.shard:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "all.json").write_text(json.dumps(res, default=str) + "\n", encoding="utf-8")
    print(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
