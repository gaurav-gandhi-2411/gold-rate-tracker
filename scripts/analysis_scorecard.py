"""scripts/analysis_scorecard.py -- one verified view of every live and shadow model (brief item 0).

Re-measures each model from the committed data and current code, next to the simplest sensible
baseline scored on the SAME days, split by day type. Nothing here changes a model or the site.

  nowcast            today's Tanishq 22K from the latest IBJA (production M0, walk-forward) and the
                     morning-rate variant (M3, IBJA AM+PM); baselines: yesterday's Tanishq carried
                     forward, and IBJA x a fixed markup (median ratio of the first 30 same-day pairs,
                     frozen).
  fusion (shadow)    the national retail benchmark (ml.fusion DEFAULT_WEIGHTS over the persisted
                     snapshots) vs the same day's Tanishq; same baselines.
  accuracy band      the displayed band around the estimate (production) and the weekend/stale-IBJA
                     stratified shadow band, 80% nominal (ml.calibration).
  tomorrow's range   the displayed next-day range, forward decisions logged in metrics_history.json
                     since the ADR 022 fix; baseline: a historical-simulation band from the 10th/90th
                     percentile of earlier next-day Tanishq changes (same days).
  weekly range       ADR 043 path coverage (walk-forward on real IBJA), calibrated vs raw.
  5-day volatility   the "moving about +/- Rs.X over 5 days" note (ml.volatility): how often the
                     next 5-reading move lands inside +/-X; baseline: a fixed +/-X from the median
                     earlier 5-day move.
  direction, Chronos read from the committed evaluation outputs (direction_baseline.json produced by
                     eval-direction.yml on current master; backtest.json from weekly-backtest.yml) and
                     re-tested from their per-fold rows where the rows are present.

Day types: "ibja_day" = IBJA published that date (Mon-Fri, same-day pair); "weekend" = Sat/Sun;
"weekday_no_ibja" = a weekday IBJA did not publish (holiday or IBJA gap).
Intervals: MAE +/- 1.96 x HAC standard error (Newey-West, lag 1); coverage with Wilson 95%.
Tests: one-sided paired HAC Diebold-Mariano (model lower than baseline), lag 1, with effective n;
Benjamini-Hochberg across every model-vs-baseline test in the report.
"""

from __future__ import annotations

import importlib.util
import json
import math
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
DATA = ROOT / "data"
OUT = ROOT / "reports" / "model_scorecard.json"
FIXED_MARKUP_PAIRS = 30


def _load_script(name: str) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# --- statistics -------------------------------------------------------------------------------


def wilson(k: int, n: int, z: float = 1.959964) -> list[float] | None:
    if n == 0:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(c - h, 4), round(c + h, 4)]


def p_below(k: int, n: int, nominal: float = 0.8) -> float | None:
    """Exact one-sided binomial P(X <= k | n, nominal): small = coverage reliably below target.
    Treats days as independent (consecutive days are mildly dependent; read as approximate)."""
    if n == 0:
        return None
    return float(sum(math.comb(n, i) * nominal**i * (1 - nominal) ** (n - i) for i in range(k + 1)))


def hac_mean_ci(x: np.ndarray, lag: int = 1) -> dict[str, Any]:
    """Mean with a Newey-West (Bartlett, `lag`) 95% interval."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 3:
        return {"n": n, "mean": float(x.mean()) if n else None, "ci95": None}
    d = x - x.mean()
    lrv = float(d @ d / n)
    for k in range(1, lag + 1):
        lrv += 2 * (1 - k / (lag + 1)) * float(d[k:] @ d[:-k] / n)
    se = math.sqrt(max(lrv, 0.0) / n)
    return {"n": n, "mean": float(x.mean()), "ci95": [x.mean() - 1.96 * se, x.mean() + 1.96 * se]}


def dm_less(loss_model: np.ndarray, loss_base: np.ndarray) -> dict[str, Any]:
    from ml.direction.evaluate_reframed import diebold_mariano_test

    if len(loss_model) < 5:
        return {"p_one_sided": None, "effective_n": None}
    r = diebold_mariano_test(list(loss_model), list(loss_base), 2, alternative="less")
    return {"p_one_sided": r["p_value"], "effective_n": r["effective_n"]}


def day_type(dates: pd.Series, gap_days: pd.Series) -> pd.Series:
    wd = pd.to_datetime(dates).dt.dayofweek
    return pd.Series(
        np.where(
            wd >= 5, "weekend", np.where(gap_days.to_numpy() == 0, "ibja_day", "weekday_no_ibja")
        ),
        index=dates.index,
    )


def point_block(
    y: np.ndarray, preds: dict[str, np.ndarray], model: str, strata: pd.Series
) -> dict[str, Any]:
    """MAE with HAC CI for the model and each baseline on the same days, overall and per stratum,
    plus one-sided DM model-vs-baseline."""
    out: dict[str, Any] = {}
    groups = {"all": np.ones(len(y), dtype=bool)}
    for s in ("ibja_day", "weekend", "weekday_no_ibja"):
        groups[s] = (strata == s).to_numpy()
    groups["weekend_or_holiday"] = groups["weekend"] | groups["weekday_no_ibja"]
    for g, mask in groups.items():
        ok = mask & np.isfinite(preds[model])
        for p in preds.values():
            ok &= np.isfinite(p)
        cell: dict[str, Any] = {"n": int(ok.sum())}
        if ok.sum() >= 3:
            for name, p in preds.items():
                cell[name] = hac_mean_ci(np.abs(p[ok] - y[ok]))
            for name, p in preds.items():
                if name != model:
                    cell[f"{model}_vs_{name}"] = dm_less(
                        np.abs(preds[model][ok] - y[ok]), np.abs(p[ok] - y[ok])
                    )
        out[g] = cell
    return out


# --- nowcast ----------------------------------------------------------------------------------


def nowcast() -> dict[str, Any]:
    nc = _load_script("analysis_nowcast")
    truth = nc.load_truth()
    ibja = nc.load_ibja()
    start = (truth.index.min() - pd.Timedelta(days=40)).strftime("%Y-%m-%d")
    end = (truth.index.max() + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    m = nc.build(truth, ibja, nc.load_global(start, end))
    preds = nc.predict_all(m)
    # carry-forward: the latest Tanishq reading on an earlier date
    prev = truth.shift(1)
    m["carry"] = m["date"].map(prev)
    same = m[m["gap_days"] == 0]
    ratio = float(np.median((same["t22"] / same["pm"]).to_numpy()[:FIXED_MARKUP_PAIRS]))
    first_scored = same["date"].iloc[FIXED_MARKUP_PAIRS] if len(same) > FIXED_MARKUP_PAIRS else None
    y = m["t22"].to_numpy()
    p = {
        "nowcast_M0": preds["M0_current"],
        "carry_forward": m["carry"].to_numpy(dtype=float),
        "ibja_fixed_markup": m["pm"].to_numpy() * ratio,
    }
    # fixed markup is fitted on the first 30 same-day pairs: score only after them
    if first_scored is not None:
        p["ibja_fixed_markup"] = np.where(
            m["date"].to_numpy() >= np.datetime64(first_scored), p["ibja_fixed_markup"], np.nan
        )
    strata = day_type(m["date"], m["gap_days"])
    res = {
        "days": [str(m["date"].min().date()), str(m["date"].max().date())],
        "fixed_markup_ratio": ratio,
        "production_M0": point_block(y, p, "nowcast_M0", strata),
    }
    q = dict(p)
    q["morning_M3"] = preds["M3_am_pm"]
    res["morning_variant_M3"] = point_block(
        y,
        {k: q[k] for k in ("morning_M3", "nowcast_M0", "carry_forward", "ibja_fixed_markup")},
        "morning_M3",
        strata,
    )
    return res


# --- fusion -----------------------------------------------------------------------------------


def fusion() -> dict[str, Any]:
    from ml.fusion import DEFAULT_WEIGHTS

    nc = _load_script("analysis_nowcast")
    snaps = pd.read_parquet(DATA / "fusion_snapshots.parquet")
    nat = snaps[snaps["city"].isna() & snaps["source"].isin(list(DEFAULT_WEIGHTS))].copy()
    nat = nat.sort_values("capture_utc").groupby(["as_of_date", "source"]).last().reset_index()
    nat["w"] = nat["source"].map(DEFAULT_WEIGHTS)
    bench = (nat["rate_22k"] * nat["w"]).groupby(nat["as_of_date"]).sum() / nat["w"].groupby(
        nat["as_of_date"]
    ).sum()
    bench.index = pd.to_datetime(bench.index)
    truth = nc.load_truth()
    ibja = nc.load_ibja()
    df = pd.DataFrame({"fusion": bench}).join(truth.rename("t22"), how="inner")
    df["carry"] = df.index.map(truth.shift(1))
    df.index.name = "date"
    ib = ibja.reset_index().rename(columns={"date": "ibja_date"})
    j = pd.merge_asof(
        df.reset_index().sort_values("date"),
        ib.sort_values("ibja_date"),
        left_on="date",
        right_on="ibja_date",
        direction="backward",
    )
    j["gap_days"] = (j["date"] - j["ibja_date"]).dt.days
    same = pd.merge(truth.rename("t22").reset_index(), ib, left_on="date", right_on="ibja_date")
    same = same[same["date"] < j["date"].min()]
    ratio = float(np.median((same["t22"] / same["pm"]).to_numpy()[-FIXED_MARKUP_PAIRS:]))
    y = j["t22"].to_numpy()
    p = {
        "fusion": j["fusion"].to_numpy(),
        "carry_forward": j["carry"].to_numpy(dtype=float),
        "ibja_fixed_markup": j["pm"].to_numpy() * ratio,
    }
    return {
        "days": [str(j["date"].min().date()), str(j["date"].max().date())],
        "fixed_markup_ratio_last_30_pairs_before_first_day": ratio,
        "block": point_block(y, p, "fusion", day_type(j["date"], j["gap_days"])),
    }


# --- accuracy band ----------------------------------------------------------------------------


def accuracy_band() -> dict[str, Any]:
    from ml.calibration import evaluate_stratified_band_coverage

    raw = json.loads((DATA / "prices.json").read_text(encoding="utf-8"))
    rows = [(r["timestamp"], r["22k"]) for r in raw if r.get("22k") is not None]
    t = pd.DataFrame(rows, columns=["ts", "22k"]).sort_values("ts")
    t["date"] = t["ts"].str[:10]
    t = t.groupby("date", as_index=False).last()[["date", "22k"]]
    ib = pd.read_parquet(DATA / "ibja_rates.parquet")
    r = evaluate_stratified_band_coverage(ib, t, level=80)
    out: dict[str, Any] = {"nominal": 0.8, "n": r["n"]}
    for name in ("production", "stratified"):
        k = r[name]["n_in_band"]
        out[name] = {
            "k": k,
            "coverage": r[name]["coverage"],
            "wilson95": wilson(k, r["n"]),
            "p_below_80": p_below(k, r["n"]),
        }
    for sname, s in r["strata"].items():
        out[sname] = {
            "n": s["n"],
            "production": {
                "k": s["production_in_band"],
                "coverage": s["production_in_band"] / s["n"] if s["n"] else None,
                "wilson95": wilson(s["production_in_band"], s["n"]),
                "p_below_80": p_below(s["production_in_band"], s["n"]),
                "mean_half_width": s["mean_half_width_production"],
            },
            "stratified_shadow": {
                "k": s["stratified_in_band"],
                "coverage": s["stratified_in_band"] / s["n"] if s["n"] else None,
                "wilson95": wilson(s["stratified_in_band"], s["n"]),
                "mean_half_width": s["mean_half_width_stratified"],
            },
        }
    out["carry_forward_fallback_days"] = r["carry_forward_fallback_days"]
    # baseline band: yesterday's Tanishq +/- the 80th percentile of earlier |day-to-day change|
    tt = t.set_index(pd.to_datetime(t["date"]))["22k"].astype(float)
    chg = tt.diff().abs()
    bk = bn = 0
    widths = []
    for d in tt.index[1:]:
        past = chg[chg.index < d].dropna()
        if len(past) < 30:
            continue
        hw = float(np.quantile(past.tail(250), 0.8))
        prev = tt[tt.index < d].iloc[-1]
        bn += 1
        bk += int(abs(tt[d] - prev) <= hw)
        widths.append(hw)
    out["baseline_carry_forward_band"] = {
        "n": bn,
        "k": bk,
        "wilson95": wilson(bk, bn),
        "p_below_80": p_below(bk, bn),
        "mean_half_width": float(np.mean(widths)) if widths else None,
        "note": "every Tanishq day with 30 earlier changes (not restricted to the production days)",
    }
    out["strata_note"] = (
        "carry_forward = the latest IBJA is older than the Tanishq day (mostly weekends)"
    )
    return out


# --- tomorrow's range ---------------------------------------------------------------------------


def tomorrows_range() -> dict[str, Any]:
    from ml.metrics import ADR_022_FIX_DATE

    hist = json.loads((DATA / "metrics_history.json").read_text(encoding="utf-8"))
    e = pd.DataFrame(
        [
            x
            for x in hist
            if x.get("outcome") not in ("pending", None)
            and isinstance(x.get("actual_next_22k"), (int, float))
            and x.get("decision_date", "") >= ADR_022_FIX_DATE
        ]
    ).sort_values("decision_date")
    e["inside"] = (e["lower"] <= e["actual_next_22k"]) & (e["actual_next_22k"] <= e["upper"])
    # baseline: historical simulation on Tanishq next-day changes known before the decision
    raw = json.loads((DATA / "prices.json").read_text(encoding="utf-8"))
    t = pd.DataFrame(
        [(r["timestamp"][:10], r["22k"]) for r in raw if r.get("22k")], columns=["d", "p"]
    )
    t = t.groupby("d")["p"].last()
    chg = t.diff().dropna()
    hs_in, width_hs = [], []
    for _, row in e.iterrows():
        past = chg[chg.index < row["decision_date"]].tail(250)
        lo, hi = np.quantile(past, [0.1, 0.9])
        hs_in.append(row["current_22k"] + lo <= row["actual_next_22k"] <= row["current_22k"] + hi)
        width_hs.append(hi - lo)
    e["hs_in"] = hs_in
    wd = pd.to_datetime(e["decision_date"]).dt.dayofweek
    out: dict[str, Any] = {"nominal": 0.8, "since": ADR_022_FIX_DATE}
    for g, mask in {"all": wd >= 0, "mon_thu": wd <= 3, "fri_sun": wd >= 4}.items():
        s = e[mask]
        out[g] = {
            "n": len(s),
            "displayed": {
                "k": int(s["inside"].sum()),
                "wilson95": wilson(int(s["inside"].sum()), len(s)),
                "p_below_80": p_below(int(s["inside"].sum()), len(s)),
            },
            "hist_sim_baseline": {
                "k": int(s["hs_in"].sum()),
                "wilson95": wilson(int(s["hs_in"].sum()), len(s)),
            },
        }
    out["mean_width_displayed"] = float((e["upper"] - e["lower"]).mean())
    out["mean_width_hist_sim"] = float(np.mean(width_hs))
    out["split_note"] = (
        "the next-day outcome of a Friday-Sunday decision falls on a weekend or Monday"
    )
    return out


# --- weekly range -------------------------------------------------------------------------------


def weekly_range() -> dict[str, Any]:
    path = ROOT / "reports" / "weekly_range_results.json"
    if not path.exists():
        return {"status": "reports/weekly_range_results.json missing"}
    d = json.loads(path.read_text(encoding="utf-8"))
    shadow = DATA / "weekly_range_shadow.json"
    return {
        "source": "reports/weekly_range_results.json (ADR 043, re-read; forward shadow separate)",
        "results": d.get("results", d),
        "forward_shadow_file_exists": shadow.exists(),
    }


# --- 5-day volatility note ----------------------------------------------------------------------


def vol_note() -> dict[str, Any]:
    from ml import volatility as vol

    raw = json.loads((DATA / "prices.json").read_text(encoding="utf-8"))
    daily = vol._dedup_daily([r for r in raw if r.get("22k") is not None])
    bt = json.loads((DATA / "backtest.json").read_text(encoding="utf-8"))
    fc = json.loads((DATA / "forecast.json").read_text(encoding="utf-8"))
    static = float(fc["headline"]["vol_context"]["static_pi_half"])
    rows = []
    for i in range(vol.MIN_CONTIGUOUS_DAYS, len(daily) - vol.HORIZON_DAYS):
        ctx = vol.compute_vol_context(daily[: i + 1], static)
        if ctx["is_degraded"]:
            continue
        p0 = float(daily[i]["22k"])
        d0 = daily[i]["timestamp"][:10]
        d5 = daily[i + vol.HORIZON_DAYS]["timestamp"][:10]
        if (pd.Timestamp(d5) - pd.Timestamp(d0)).days > 9:  # not 5 contiguous readings
            continue
        move = abs(float(daily[i + vol.HORIZON_DAYS]["22k"]) - p0)
        rows.append({"d": d0, "hw": ctx["half_width"], "move": move})
    r = pd.DataFrame(rows)
    fixed = [float(np.median(r["move"].iloc[:k])) if k >= 20 else np.nan for k in range(len(r))]
    r["fixed_hw"] = fixed
    s = r.dropna()
    k1 = int((s["move"] <= s["hw"]).sum())
    k2 = int((s["move"] <= s["fixed_hw"]).sum())
    return {
        "claim": "about +/- half_width over the next 5 readings (1 sd x sqrt(5); ~68% if normal)",
        "static_pi_half_used": static,
        "static_pi_half_note": "today's value, applied to all past days (INFERRED approximation: "
        "the floor anchor changes weekly)",
        "backtest_run_at": bt.get("backtest_run_at"),
        "n": len(s),
        "overlapping_windows": True,
        "note_inside": {"k": k1, "wilson95": wilson(k1, len(s))},
        "fixed_median_baseline_inside": {"k": k2, "wilson95": wilson(k2, len(s))},
        "mean_half_width": float(s["hw"].mean()),
        "mean_fixed_half_width": float(s["fixed_hw"].mean()),
        "median_abs_5d_move": float(s["move"].median()),
    }


# --- direction and Chronos ----------------------------------------------------------------------


def direction() -> dict[str, Any]:
    d = json.loads((DATA / "direction_baseline.json").read_text(encoding="utf-8"))
    out: dict[str, Any] = {
        "source_sha": d.get("source_sha"),
        "generated": d.get("generated_at_utc"),
    }
    for h, blk in d["horizons"].items():
        n = blk.get("n_test_folds")
        cands = {}
        for name, c in blk.items():
            if isinstance(c, dict) and "accuracy" in c:
                cands[name] = {
                    "accuracy": c.get("accuracy"),
                    "always_up": c.get("always_up_accuracy"),
                    "p_one_sided": c.get("p_value"),
                }
        out[h] = {"n": n, "always_up": blk.get("always_up_baseline_accuracy"), "models": cands}
    return out


def chronos() -> dict[str, Any]:
    bt = json.loads((DATA / "backtest.json").read_text(encoding="utf-8"))
    f = pd.DataFrame(bt["folds"])
    mc = np.array([np.mean(v) for v in f["mae_chronos_per_h"]])
    mn = np.array([np.mean(v) for v in f["mae_naive_per_h"]])
    inpi = np.array([np.mean(v) for v in f["in_pi_80"]])
    return {
        "backtest_run_at": bt["backtest_run_at"],
        "model_version": bt["model_version"],
        "n_folds": len(f),
        "mae_5d_chronos": hac_mean_ci(mc, lag=4),
        "mae_5d_no_change": hac_mean_ci(mn, lag=4),
        "chronos_vs_no_change": dm_less(mc, mn),
        "no_change_vs_chronos": dm_less(mn, mc),
        "pi80_coverage_5d_avg": float(inpi.mean()),
        "note": "5-day folds overlap; DM here uses lag 1 (conservative lag 4 would widen it)",
    }


def main() -> int:
    warnings.filterwarnings("ignore")
    res: dict[str, Any] = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
        ).stdout.strip(),
    }
    for name, fn in [
        ("nowcast", nowcast),
        ("fusion", fusion),
        ("accuracy_band", accuracy_band),
        ("tomorrows_range", tomorrows_range),
        ("weekly_range", weekly_range),
        ("vol_note", vol_note),
        ("direction", direction),
        ("chronos", chronos),
    ]:
        try:
            res[name] = fn()
        except Exception as exc:  # report, never hide
            res[name] = {"error": f"{type(exc).__name__}: {exc}"}
    # BH over every model-vs-baseline DM test in the report
    tests: list[tuple[list[str], float]] = []

    def walk(o: Any, path: list[str]) -> None:
        if isinstance(o, dict):
            if "p_one_sided" in o and isinstance(o["p_one_sided"], float) and "_vs_" in path[-1]:
                tests.append((path, o["p_one_sided"]))
            for k, v in o.items():
                walk(v, [*path, k])

    walk(res, [])
    from ml.direction.stats_corrections import benjamini_hochberg

    if tests:
        bh = benjamini_hochberg([p for _, p in tests])
        res["bh"] = {
            "m": len(tests),
            "significant": [
                "/".join(pth) for (pth, _), s in zip(tests, bh["significant"], strict=True) if s
            ],
        }
    OUT.write_text(json.dumps(res, indent=1, default=str) + "\n", encoding="utf-8")
    print(json.dumps(res, indent=1, default=str)[:20000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
