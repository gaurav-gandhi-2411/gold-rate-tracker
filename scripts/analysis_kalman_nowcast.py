"""scripts/analysis_kalman_nowcast.py -- ADR 055: the state-space (Kalman) nowcast, walk-forward.

Pre-registered in docs/adr/055-kalman-nowcast-preregistration.md (read it first; the tests,
family, alpha and success gate are fixed there). This script:

  1. builds the readings the filter sees (ml.kalman_nowcast.build_observations):
       Tanishq 22K     data/prices.json, last reading per UTC date (the scorecard's truth series,
                       scripts/analysis_nowcast.load_truth); day d's own reading is the target and
                       is never visible to day d's nowcast.
       IBJA AM / PM    data/ibja_rates.parquet am_916 / pm_916 per 10 g / 10, value date (IST
                       publication date, AM ~06:30 UTC, PM ~11:30 UTC); stale re-publications
                       (identical to the previous row) skipped.
       GRT, Malabar    data/fusion_snapshots.parquet national rows, last capture per as_of_date
                       (UTC capture date); staleness = capture date - the retailer's observed_at
                       date. Kalyan is excluded (city-level rows only; corrupt timestamps since
                       2026-09-08, #2022; not in ml.fusion.DEFAULT_WEIGHTS).
       COMEX x USD/INR Yahoo GC=F and INR=X daily closes (ml.macro._download_with_retry, the
                       same loader as scripts/analysis_derived_premium.py), converted to landed
                       22K Rs/g with the duty in force from data/duty_cbic.json
                       (analysis_derived_premium.duty_rate_series); a close dated c is assimilated
                       on c + 1 (final only after the US session), age 1.
  2. refits the noise parameters by MLE every 7 calendar days on days strictly before the block
     (walk-forward) and records each scored day's pre-anchor nowcast and 80% band;
  3. scores it on exactly the scorecard's (#2015, scripts/analysis_scorecard.py) day sets and
     baselines: IBJA x fixed markup (median Tanishq/IBJA-PM of the first 30 same-day pairs,
     frozen, scored only after them), yesterday's Tanishq (carry-forward), and the fusion
     benchmark (ml.fusion.DEFAULT_WEIGHTS over the national snapshots);
  4. runs the pre-registered family and writes reports/kalman_nowcast/results.json.

No raw retailer prices are written: only errors, coverage flags, widths and parameters.

Usage:
    python scripts/analysis_kalman_nowcast.py --shape-only   # counts only, no outcome metrics
    python scripts/analysis_kalman_nowcast.py                # the pre-registered run
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.kalman_nowcast import (
    Params,
    build_observations,
    comex_rs_per_g_22k,
    default_initial_state,
    fit_params,
    nontrading_flags,
    predictive,
    run_filter,
)

ADR = ROOT / "docs" / "adr" / "055-kalman-nowcast-preregistration.md"
OUT_DIR = ROOT / "reports" / "kalman_nowcast"
FIXED_MARKUP_PAIRS = 30  # scorecard constant
REFIT_EVERY_DAYS = 7
DM_HORIZON = 2  # diebold_mariano_test lag = horizon - 1 = 1, as the scorecard
DM_HORIZON_SENSITIVITY = 6  # lag 5, secondary
ALPHA = 0.05
RETAIL_SOURCES = ("grt", "malabar")


def _load_script(name: str) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def prereg_sha256() -> str:
    """sha256 of the ADR text above the '## Results' heading (the whole file at freeze time)."""
    text = ADR.read_bytes()
    marker = b"\n## Results"
    cut = text.find(marker)
    return hashlib.sha256(text if cut < 0 else text[: cut + 1]).hexdigest()


# --- data --------------------------------------------------------------------------------------


def load_retail() -> pd.DataFrame:
    snaps = pd.read_parquet(ROOT / "data" / "fusion_snapshots.parquet")
    nat = snaps[snaps["city"].isna() & snaps["source"].isin(RETAIL_SOURCES)].copy()
    nat = nat.sort_values("capture_utc").groupby(["as_of_date", "source"]).last().reset_index()
    obs_at = pd.to_datetime(nat["observed_at"], format="mixed", utc=True)
    return pd.DataFrame(
        {
            "day": pd.to_datetime(nat["as_of_date"]),
            "source": nat["source"],
            "rate_22k": nat["rate_22k"].astype(float),
            "observed_at_day": obs_at.dt.tz_localize(None).dt.normalize(),
        }
    )


def load_comex(start: str, end: str) -> pd.Series:
    """Landed 22K parity (Rs/g) per COMEX trading date: GC=F close (real rows only, identical
    repeats dropped) x the INR=X close of that date (forward-filled to it) x (1 + duty in force on
    the assimilation day c + 1) x 22/24."""
    from ml.macro import _download_with_retry

    dp = _load_script("analysis_derived_premium")
    raw = _download_with_retry(["GC=F", "INR=X"], start=start, end=end)
    raw.index = pd.to_datetime(raw.index).tz_localize(None).normalize()
    gc = raw[("Close", "GC=F")].dropna()
    gc = gc[gc.ne(gc.shift(1))]
    inr = raw[("Close", "INR=X")].ffill().reindex(gc.index)
    table = json.loads(dp.DUTY_TABLE.read_text(encoding="utf-8"))["rows"]
    duty = dp.duty_rate_series(pd.DatetimeIndex(gc.index + pd.Timedelta(days=1)), table)
    vals = [
        comex_rs_per_g_22k(float(g), float(i), float(du))
        for g, i, du in zip(gc.to_numpy(), inr.to_numpy(), duty.to_numpy(), strict=True)
    ]
    return pd.Series(vals, index=gc.index, dtype=float).dropna()


def load_ibja_am_pm() -> pd.DataFrame:
    nc = _load_script("analysis_nowcast")
    return nc.load_ibja()[["am", "pm"]]


# --- baselines: the scorecard's construction, verbatim in substance ----------------------------


def scorecard_nowcast_frame() -> tuple[pd.DataFrame, float]:
    """scripts/analysis_scorecard.nowcast() on #2015: the production-M0 day set with carry-forward
    and IBJA x fixed markup (first 30 same-day pairs, scored only after them)."""
    nc = _load_script("analysis_nowcast")
    truth = nc.load_truth()
    ibja = nc.load_ibja()
    start = (truth.index.min() - pd.Timedelta(days=40)).strftime("%Y-%m-%d")
    end = (truth.index.max() + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    m = nc.build(truth, ibja, nc.load_global(start, end))
    preds = nc.predict_all(m)
    m["carry"] = m["date"].map(truth.shift(1))
    same = m[m["gap_days"] == 0]
    ratio = float(np.median((same["t22"] / same["pm"]).to_numpy()[:FIXED_MARKUP_PAIRS]))
    first_scored = same["date"].iloc[FIXED_MARKUP_PAIRS]
    m["nowcast_M0"] = preds["M0_current"]
    m["ibja_fixed_markup"] = np.where(
        m["date"].to_numpy() >= np.datetime64(first_scored), m["pm"].to_numpy() * ratio, np.nan
    )
    m["day_type"] = day_type(m["date"], m["gap_days"])
    return m, ratio


def scorecard_fusion_frame() -> pd.DataFrame:
    """scripts/analysis_scorecard.fusion() on #2015."""
    from ml.fusion import DEFAULT_WEIGHTS

    nc = _load_script("analysis_nowcast")
    snaps = pd.read_parquet(ROOT / "data" / "fusion_snapshots.parquet")
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
    j["ibja_fixed_markup_fusion"] = j["pm"].to_numpy() * ratio
    j["day_type"] = day_type(j["date"], j["gap_days"])
    return j


def day_type(dates: pd.Series, gap_days: pd.Series) -> pd.Series:
    wd = pd.to_datetime(dates).dt.dayofweek
    return pd.Series(
        np.where(
            wd >= 5, "weekend", np.where(gap_days.to_numpy() == 0, "ibja_day", "weekday_no_ibja")
        ),
        index=dates.index,
    )


# --- statistics (the scorecard's, on the repo's shared DM / BH / Bonferroni / Wilson) -----------


def hac_mean_ci(x: np.ndarray, lag: int = 1) -> dict[str, Any]:
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


def dm_less(loss_model: np.ndarray, loss_base: np.ndarray, horizon: int) -> dict[str, Any]:
    from ml.direction.evaluate_reframed import diebold_mariano_test

    if len(loss_model) < 5:
        return {"p_one_sided": None, "effective_n": None, "mean_diff": None}
    r = diebold_mariano_test(list(loss_model), list(loss_base), horizon, alternative="less")
    return {
        "p_one_sided": r["p_value"],
        "effective_n": r["effective_n"],
        "mean_diff": r["mean_diff"],
        "dm_stat": r["dm_stat"],
    }


def coverage_block(covered: np.ndarray, width: np.ndarray) -> dict[str, Any]:
    from ml.calibration_adaptive import _wilson_ci

    n = len(covered)
    k = int(np.sum(covered))
    if n == 0:
        return {"n": 0}
    lo, hi = _wilson_ci(k, n)
    # exact two-sided binomial p vs 0.80 (sum of outcomes no more likely than the observed one)
    pk = [math.comb(n, i) * 0.8**i * 0.2 ** (n - i) for i in range(n + 1)]
    p_two = float(sum(v for v in pk if v <= pk[k] * (1 + 1e-9)))
    return {
        "n": n,
        "covered": k,
        "coverage": k / n,
        "wilson95": [lo, hi],
        "ci_contains_80": bool(lo <= 0.8 <= hi),
        "binom_two_sided_p_vs_80": min(p_two, 1.0),
        "mean_width_rs_g": float(np.mean(width)),
        "median_width_rs_g": float(np.median(width)),
    }


# --- walk-forward ------------------------------------------------------------------------------


def walk_forward(
    days: pd.DatetimeIndex,
    obs: list[list[Any]],
    eval_days: list[pd.Timestamp],
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    nt = nontrading_flags(days)
    pos = {d: i for i, d in enumerate(days)}
    first_anchor = next(o.log_value for day in obs for o in day if o.source == "tanishq")
    init = default_initial_state(first_anchor)
    ev = sorted(pos[d] for d in eval_days if d in pos)
    rows: list[dict[str, Any]] = []
    fits: list[dict[str, Any]] = []
    params: Params | None = None
    b = ev[0]
    while b <= ev[-1]:
        e = b + REFIT_EVERY_DAYS
        params, info = fit_params(obs[:b], nt[:b], init, start=params)
        fits.append(
            {
                "block_start": str(days[b].date()),
                "train_days": b,
                **info,
                "params": params.values,
            }
        )
        res = run_filter(obs[:e], nt[:e], params, init)
        for i in (i for i in ev if b <= i < e):
            pt, lo, hi = predictive(res.pre_anchor_mean[i], res.pre_anchor_var[i], params)
            rows.append({"date": days[i], "kalman": pt, "k_lo": lo, "k_hi": hi})
        print(f"[kalman] block {days[b].date()} fitted on {b} days: {info}", flush=True)
        b = e
    return pd.DataFrame(rows), fits


def _stratum_masks(dt: pd.Series) -> dict[str, np.ndarray]:
    wkend = (dt == "weekend").to_numpy()
    return {"all": np.ones(len(dt), dtype=bool), "weekday": ~wkend, "weekend": wkend}


def point_cells(
    y: np.ndarray, preds: dict[str, np.ndarray], dt: pd.Series, horizon: int
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for g, mask in _stratum_masks(dt).items():
        cell: dict[str, Any] = {"n": int(mask.sum())}
        if mask.sum() >= 3:
            for name, p in preds.items():
                cell[f"mae_{name}"] = hac_mean_ci(np.abs(p[mask] - y[mask]))
            for name, p in preds.items():
                if name != "kalman":
                    cell[f"kalman_vs_{name}"] = dm_less(
                        np.abs(preds["kalman"][mask] - y[mask]), np.abs(p[mask] - y[mask]), horizon
                    )
        out[g] = cell
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shape-only", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT_DIR / "results.json")
    args = ap.parse_args()

    nc = _load_script("analysis_nowcast")
    truth = nc.load_truth()
    days = pd.date_range(truth.index.min(), truth.index.max(), freq="D")
    start = (days[0] - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    end = (days[-1] + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    comex = load_comex(start, end)
    obs = build_observations(days, truth, load_ibja_am_pm(), load_retail(), comex)

    nowf, ratio = scorecard_nowcast_frame()
    set_a = nowf.dropna(subset=["nowcast_M0", "carry", "ibja_fixed_markup"]).reset_index(drop=True)
    fus = scorecard_fusion_frame()
    set_b = fus.dropna(subset=["fusion", "carry", "ibja_fixed_markup_fusion"]).reset_index(
        drop=True
    )

    counts: dict[str, int] = {}
    for d in obs:
        for o in d:
            counts[o.source] = counts.get(o.source, 0) + 1
    shape = {
        "calendar": [str(days[0].date()), str(days[-1].date()), len(days)],
        "readings_by_source": counts,
        "set_A_scorecard_nowcast_days": {
            "n": len(set_a),
            "range": [str(set_a["date"].min().date()), str(set_a["date"].max().date())],
            "by_day_type": set_a["day_type"].value_counts().to_dict(),
        },
        "set_B_scorecard_fusion_days": {
            "n": len(set_b),
            "range": [str(set_b["date"].min().date()), str(set_b["date"].max().date())],
            "by_day_type": set_b["day_type"].value_counts().to_dict(),
        },
        "fixed_markup_ratio": ratio,
    }
    if args.shape_only:
        print(json.dumps(shape, indent=1, default=str))
        return 0

    eval_days = sorted(set(set_a["date"]) | set(set_b["date"]))
    kal, fits = walk_forward(days, obs, eval_days)
    a = set_a.merge(kal, on="date", how="inner")
    b = fus_scored = set_b.merge(kal, on="date", how="inner")

    def cover(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        y = df["t22"].to_numpy()
        return (df["k_lo"].to_numpy() <= y) & (y <= df["k_hi"].to_numpy()), (
            df["k_hi"] - df["k_lo"]
        ).to_numpy()

    ya = a["t22"].to_numpy()
    preds_a = {
        "kalman": a["kalman"].to_numpy(),
        "ibja_fixed_markup": a["ibja_fixed_markup"].to_numpy(),
        "carry_forward": a["carry"].to_numpy(dtype=float),
        "nowcast_M0": a["nowcast_M0"].to_numpy(),
    }
    yb = b["t22"].to_numpy()
    preds_b = {
        "kalman": b["kalman"].to_numpy(),
        "fusion": b["fusion"].to_numpy(),
        "carry_forward": b["carry"].to_numpy(dtype=float),
    }
    cells_a = point_cells(ya, preds_a, a["day_type"], DM_HORIZON)
    cells_b = point_cells(yb, preds_b, fus_scored["day_type"], DM_HORIZON)
    sens_a = point_cells(ya, preds_a, a["day_type"], DM_HORIZON_SENSITIVITY)
    sens_b = point_cells(yb, preds_b, fus_scored["day_type"], DM_HORIZON_SENSITIVITY)

    family = [
        ("T1", "vs IBJA x fixed markup, all days", cells_a["all"]["kalman_vs_ibja_fixed_markup"]),
        (
            "T2",
            "vs IBJA x fixed markup, weekdays",
            cells_a["weekday"]["kalman_vs_ibja_fixed_markup"],
        ),
        (
            "T3",
            "vs IBJA x fixed markup, weekends",
            cells_a["weekend"]["kalman_vs_ibja_fixed_markup"],
        ),
        ("T4", "vs fusion, weekdays", cells_b["weekday"]["kalman_vs_fusion"]),
        ("T5", "vs fusion, weekends", cells_b["weekend"]["kalman_vs_fusion"]),
        ("T6", "vs yesterday's Tanishq, weekends", cells_a["weekend"]["kalman_vs_carry_forward"]),
    ]
    from ml.direction.stats_corrections import benjamini_hochberg, bonferroni

    pv = [f[2]["p_one_sided"] for f in family]
    bon = bonferroni(pv, ALPHA)
    bh = benjamini_hochberg(pv, ALPHA)
    fam_out = [
        {
            "id": fid,
            "test": desc,
            **res,
            "bonferroni_significant": bon["significant"][i],
            "bh_significant": bh["significant"][i],
        }
        for i, (fid, desc, res) in enumerate(family)
    ]

    cov_a, wid_a = cover(a)
    masks_a = _stratum_masks(a["day_type"])
    coverage = {g: coverage_block(cov_a[m], wid_a[m]) for g, m in masks_a.items()}
    cov_b, wid_b = cover(b)
    masks_b = _stratum_masks(fus_scored["day_type"])
    coverage_b = {g: coverage_block(cov_b[m], wid_b[m]) for g, m in masks_b.items()}

    gate = {
        "T1_bonferroni": bool(fam_out[0]["bonferroni_significant"]),
        "weekday_band_ci_contains_80": bool(coverage["weekday"].get("ci_contains_80", False)),
        "weekend_band_ci_contains_80": bool(coverage["weekend"].get("ci_contains_80", False)),
    }
    gate["PASS"] = all(gate.values())

    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        sha = None
    out = {
        "adr": "055",
        "prereg_sha256_of_adr_above_results": prereg_sha256(),
        "code_commit": sha,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "shape": shape,
        "primary_family": fam_out,
        "bonferroni_threshold": bon["threshold"],
        "coverage_set_A": coverage,
        "success_gate": gate,
        "secondary": {
            "set_A_point_cells_lag1": cells_a,
            "set_B_point_cells_lag1": cells_b,
            "set_A_point_cells_lag5": sens_a,
            "set_B_point_cells_lag5": sens_b,
            "coverage_set_B": coverage_b,
        },
        "refits": fits,
        "per_day_set_A": [
            {
                "date": str(r.date.date()),
                "day_type": r.day_type,
                "abs_err_kalman": abs(r.kalman - r.t22),
                "abs_err_fixed_markup": abs(r.ibja_fixed_markup - r.t22),
                "abs_err_carry": abs(r.carry - r.t22),
                "covered": bool(r.k_lo <= r.t22 <= r.k_hi),
                "band_width": r.k_hi - r.k_lo,
            }
            for r in a.itertuples(index=False)
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1, default=str) + "\n", encoding="utf-8")
    print(
        json.dumps({"family": fam_out, "coverage": coverage, "gate": gate}, indent=1, default=str)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
