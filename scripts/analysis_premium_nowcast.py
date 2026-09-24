"""scripts/analysis_premium_nowcast.py -- G4d: does the derived Indian premium add information?
Pre-registered in docs/adr/046 BEFORE this code was run on any data.

Target: IBJA 999 PM on a publication day t, predicted before t's fixes. Scored only when the
previous publication day t' is consecutive (np.busday_count(t', t) <= 2, ADR 042's rule).

  B0 no change   : IBJA(t')
  B1 global move : parity(t) * (1 + p(t'))           (premium assumed fully persistent)
  C  premium     : parity(t) * (1 + mu + rho * (p(t') - mu))

where parity(t) uses the COMEX and USD/INR closes of calendar day t-1 and the duty in force on t
(scripts/analysis_derived_premium.py), p(t') is the premium on t', and mu, rho are fitted on
consecutive premium pairs (p(s'), p(s)) with s < t only (expanding window, at least MIN_PAIRS
pairs, rho clipped to [0, 1]). Everything C uses is known before t's AM fix.

Primary test (H1): |error| of C < |error| of B1, one-sided paired HAC Diebold-Mariano, lag 1.
Secondary (H2: C < B0, H3: B1 < B0), Holm over the two. alpha = 0.05.
--since restricts scoring to days after a date (the confirmatory set is days after 2026-09-24).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REGISTERED_AFTER = "2026-09-24"
CONFIRMATORY_MIN_N = 120
MIN_PAIRS = 30
ALPHA = 0.05
HAC_LAG = 1


def _premium_module() -> Any:
    name = "analysis_derived_premium"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def consecutive(prev: pd.Timestamp, cur: pd.Timestamp) -> bool:
    return int(np.busday_count(prev.date(), cur.date())) <= 2


def fit_ar1(pairs: list[tuple[float, float]]) -> tuple[float, float]:
    """Mean and AR(1) coefficient of the premium from consecutive pairs; rho clipped to [0, 1]."""
    a = np.array([p[0] for p in pairs])
    b = np.array([p[1] for p in pairs])
    mu = float(np.mean(np.r_[a, b]))
    da, db = a - mu, b - mu
    den = float((da * da).sum())
    rho = float((da * db).sum() / den) if den > 0 else 0.0
    return mu, min(1.0, max(0.0, rho))


def score_frame(d: pd.DataFrame) -> pd.DataFrame:
    """d: rows indexed by IBJA date with pm_999, landed_parity, premium_pct, stale_repeat.
    Returns one row per scoreable day with the three predictions."""
    x = d[~d["stale_repeat"]].dropna(subset=["pm_999", "landed_parity", "premium_pct"])
    dates = list(x.index)
    pairs: list[tuple[float, float]] = []
    rows = []
    for i in range(1, len(dates)):
        t0, t = dates[i - 1], dates[i]
        if not consecutive(t0, t):
            continue
        p0 = float(x.loc[t0, "premium_pct"]) / 100
        if len(pairs) >= MIN_PAIRS:
            mu, rho = fit_ar1(pairs)
            parity = float(x.loc[t, "landed_parity"])
            rows.append(
                {
                    "date": t,
                    "actual": float(x.loc[t, "pm_999"]),
                    "B0": float(x.loc[t0, "pm_999"]),
                    "B1": parity * (1 + p0),
                    "C": parity * (1 + mu + rho * (p0 - mu)),
                    "mu": mu,
                    "rho": rho,
                }
            )
        pairs.append((p0, float(x.loc[t, "premium_pct"]) / 100))  # known after t's fix
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()


def _dm(loss_a: np.ndarray, loss_b: np.ndarray) -> dict[str, Any]:
    from ml.direction.evaluate_reframed import diebold_mariano_test

    r = diebold_mariano_test(list(loss_a), list(loss_b), HAC_LAG + 1, alternative="less")
    return {"p_one_sided": r["p_value"], "effective_n": r["effective_n"]}


def evaluate(s: pd.DataFrame) -> dict[str, Any]:
    err = {k: (s[k] - s["actual"]).abs() / 10 for k in ("B0", "B1", "C")}  # Rs per gram
    out: dict[str, Any] = {
        "n": len(s),
        "first": str(s.index.min().date()) if len(s) else None,
        "last": str(s.index.max().date()) if len(s) else None,
        "mae_rs_per_g": {k: float(v.mean()) for k, v in err.items()},
    }
    if len(s) < 10:
        return out
    h1 = _dm(err["C"].to_numpy(), err["B1"].to_numpy())
    h2 = _dm(err["C"].to_numpy(), err["B0"].to_numpy())
    h3 = _dm(err["B1"].to_numpy(), err["B0"].to_numpy())
    # Holm over the two secondaries
    ps = sorted([("H2", h2["p_one_sided"]), ("H3", h3["p_one_sided"])], key=lambda kv: kv[1])
    holm = {}
    stop = False
    for rank, (k, p) in enumerate(ps):
        ok = (not stop) and p is not None and p < ALPHA / (2 - rank)
        holm[k] = ok
        stop = stop or not ok
    out["H1_C_vs_B1"] = {**h1, "significant": bool(h1["p_one_sided"] < ALPHA)}
    out["H2_C_vs_B0"] = {**h2, "holm_significant": holm["H2"]}
    out["H3_B1_vs_B0"] = {**h3, "holm_significant": holm["H3"]}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=REGISTERED_AFTER, help="score days strictly after this")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    prem = _premium_module()
    table = json.loads(prem.DUTY_TABLE.read_text(encoding="utf-8"))["rows"]
    ibja = pd.read_parquet(prem.IBJA_PATH)
    start = (pd.Timestamp(ibja["date"].min()) - pd.Timedelta(days=15)).date().isoformat()
    end = (pd.Timestamp.today() + pd.Timedelta(days=1)).date().isoformat()
    d = prem.build(table, ibja, prem.load_drivers(start, end))
    s = score_frame(d)
    s = s[s.index > pd.Timestamp(args.since)] if len(s) else s
    res = {
        "since": args.since,
        "confirmatory": args.since == REGISTERED_AFTER,
        "confirmatory_read_allowed": args.since == REGISTERED_AFTER
        and len(s) >= CONFIRMATORY_MIN_N,
        **evaluate(s),
    }
    text = json.dumps(res, indent=1, default=str)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
