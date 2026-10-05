"""Error analysis of the promoted next-fix model by day type (brief item 2e).

Descriptive, not a test: strata are chosen for diagnosis and several (large moves) condition on
the outcome, so none of the intervals below are confirmatory. Read-only over
data/nextfix_oos.json, data/ibja_rates.parquet and data/events_calendar.json.

Strata (decision day D, target the next PM fix on D1):
  gap          D1 - D = 1 day (consecutive IBJA days) vs more (weekend / holiday between);
  weekday      weekday of D (Fri -> Monday is the usual multi-day gap);
  volatility   terciles of the fold's recorded volatility (known at decision time);
  event        a calendar event (FOMC, CPI, ...) dated after D and on or before D1;
  large moves  |actual move| in the top decile vs the rest (conditions on the outcome);
  window       how big the other two forecast windows' errors are (hold-the-fix, no model).

For each: n, MAE model vs hold-last-fix, % change with a moving-block bootstrap 95% interval
(seed 42, only when n >= 15), direction accuracy, and the calibration slope of the realised
return on the forecast (1 = right size; < 1 = overshoots; > 1 = undershoots).

    python scripts/analysis_nextfix_errors.py [--out reports/model_audit_2026-10/error_analysis.json]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml import nextfix as nf

MIN_CI = 15
EVENTS = Path(__file__).resolve().parent.parent / "data" / "events_calendar.json"


def stratum_stats(f: pd.DataFrame) -> dict:
    n = len(f)
    if n == 0:
        return {"n": 0}
    pm0, pm1 = f["pm0"].to_numpy(float), f["pm1"].to_numpy(float)
    err_m = np.abs(pm1 - pm0 * np.exp(f["ret"].to_numpy(float)))
    err_f = np.abs(pm1 - pm0)
    y, r = f["y"].to_numpy(float), f["ret"].to_numpy(float)
    moved = pm1 != pm0
    hit = (f["p_up"].to_numpy(float) > 0.5) == (pm1 > pm0)
    out = {
        "n": n,
        "mae_model": round(float(err_m.mean()), 1),
        "mae_hold": round(float(err_f.mean()), 1),
        "change_pct": round(100.0 * (err_m.mean() / err_f.mean() - 1.0), 1),
        "direction_accuracy": round(float(hit[moved].mean()), 3) if moved.any() else None,
        "calibration_slope_y_on_ret": round(float(r @ y / (r @ r)), 2) if (r @ r) > 0 else None,
    }
    if n >= MIN_CI:
        out["change_pct_ci95"] = [
            round(v, 1)
            for v in nf.block_bootstrap_ci(
                n, lambda ix: 100.0 * (err_m[ix].mean() / err_f[ix].mean() - 1.0), seed=42
            )
        ]
    return out


def event_between(d0: pd.Timestamp, d1: pd.Timestamp, events: pd.DataFrame) -> bool:
    return bool(((events["date"] > d0) & (events["date"] <= d1)).any())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/model_audit_2026-10/error_analysis.json")
    args = ap.parse_args()
    f = pd.DataFrame(nf.load_oos())
    f["d0"], f["d1"] = pd.to_datetime(f["d0"]), pd.to_datetime(f["d1"])
    f["gap"] = (f["d1"] - f["d0"]).dt.days
    ev = pd.DataFrame(json.loads(EVENTS.read_text()))
    ev["date"] = pd.to_datetime(ev["date"])
    f["event"] = [event_between(a, b, ev) for a, b in zip(f["d0"], f["d1"], strict=True)]
    f["vol_tercile"] = pd.qcut(f["vol"], 3, labels=["calm", "middle", "volatile"])
    f["large"] = f["y"].abs() >= f["y"].abs().quantile(0.9)
    strata = {
        "all": f,
        "gap_1_day": f[f["gap"] == 1],
        "gap_over_1_day": f[f["gap"] > 1],
        **{
            f"decision_{d}": f[f["d0"].dt.day_name() == d]
            for d in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
        },
        "calm": f[f["vol_tercile"] == "calm"],
        "middle_vol": f[f["vol_tercile"] == "middle"],
        "volatile": f[f["vol_tercile"] == "volatile"],
        "event_between": f[f["event"]],
        "no_event_between": f[~f["event"]],
        "large_move_top_decile (conditions on outcome)": f[f["large"]],
        "other_days": f[~f["large"]],
    }
    out = {k: stratum_stats(v) for k, v in strata.items()}
    full = nf.load_ibja_full()
    out["other_windows_hold_the_fix"] = {}
    for kind in ("am_to_pm", "pm_to_am"):
        p = nf.flat_pairs(full, kind)
        e = (p["target"] - p["base"]).abs()
        out["other_windows_hold_the_fix"][kind] = {
            "n": len(p),
            "mae_hold": round(float(e.mean()), 1),
            "median_abs_move": round(float(e.median()), 1),
            "share_unchanged": round(float((e == 0).mean()), 3),
        }
    out["event_types_in_calendar"] = sorted(ev["type"].unique().tolist())
    out["note"] = "Descriptive strata for diagnosis; not confirmatory tests."
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1, default=float) + "\n")
    print(json.dumps(out, indent=1, default=float))
    if any(
        isinstance(v, dict) and v.get("mae_model") is not None and math.isnan(v["mae_model"])
        for v in out.values()
    ):
        raise SystemExit("NaN in a stratum")


if __name__ == "__main__":
    main()
