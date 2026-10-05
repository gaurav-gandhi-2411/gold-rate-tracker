"""scripts/audit_kalman_leak.py -- independent leak audit of the ADR 055 Kalman nowcast.

Analysis only. Retrospective and exploratory relative to the frozen ADR 055 pre-registration: the
re-score below CORRECTS the measurement (strict availability-time filtering of every input); it is
not a new hypothesis and it does not re-open the pre-registered verdict.

What "the moment being estimated" is (verified in scripts/analysis_nowcast.load_truth, which the
Kalman run and the #2015 scorecard share): for UTC calendar date d, the target is the LAST Tanishq
22K reading in data/prices.json whose `timestamp` (ISO-8601, all rows end in `Z`, i.e. UTC) falls on
d. `prices.json` is chronologically sorted, so "last in file order" == "max timestamp" (asserted
below). T_d below is that reading's timestamp (naive UTC).

Availability ("known at") convention, per source (naive UTC throughout):
  tanishq        the reading's own timestamp.
  ibja_am/pm     value date D only in the data. ASSUMPTION: AM known at D 12:00 IST = D 06:30 UTC,
                 PM known at D 17:00 IST = D 11:30 UTC (IBJA publication times). `fetched_at` (when
                 this repo actually fetched the row) is reported as a descriptive sensitivity only:
                 many rows were backfilled long after publication, so it is not an availability time.
  grt/malabar    the snapshot's `capture_utc` (when the repo captured the board).
  comex          GC=F close dated c (COMEX settlement 13:30 ET on c) x INR=X close dated c. ASSUMED
                 known by c+1 00:00 UTC at the latest (settlement is 17:30/18:30 UTC; Yahoo's INR=X
                 daily bar closes by 23:00 UTC). The model assimilates it on UTC day c+1.

An input is available for target T iff known_at < T - guard (strict; guard defaults to 0).

Outputs (derived only -- source names, timestamps, deltas, errors; NO raw retailer prices):
  reports/kalman_leak_audit/inputs_audit.json   per scored day, every input and its delta
  reports/kalman_leak_audit/leak_summary.json   leak counts and magnitudes
  reports/kalman_leak_audit/rescore.json        strict re-score (family, lag 1 and 5, coverage)

Usage: python scripts/audit_kalman_leak.py        (~5 walk-forwards, a few minutes; needs Yahoo)
"""

from __future__ import annotations

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
    ANCHOR,
    Observation,
    build_observations,
    default_initial_state,
    fit_params,
    nontrading_flags,
    predictive,
    run_filter,
)

OUT_DIR = ROOT / "reports" / "kalman_leak_audit"
IBJA_PUBLISH_UTC = {
    "am": pd.Timedelta(hours=6, minutes=30),
    "pm": pd.Timedelta(hours=11, minutes=30),
}
COMEX_KNOWN_LAG = pd.Timedelta(days=1)  # close dated c known by c+1 00:00 UTC (see docstring)
SAME_RUN_GUARD = pd.Timedelta(minutes=15)  # sensitivity: drop captures from the target's own run
PERTURB_LOG = 0.05  # +5% on every Tanishq reading from day d on; the day-d nowcast must not move

# --- availability primitives (unit-tested) ------------------------------------------------------


def is_available(
    known_at: pd.Timestamp, target_ts: pd.Timestamp, guard: pd.Timedelta | None = None
) -> bool:
    """True iff an input known at `known_at` may be used to estimate the reading at `target_ts`:
    strictly earlier (minus an optional guard). An input known at exactly the target is excluded."""
    g = guard if guard is not None else pd.Timedelta(0)
    return bool(pd.Timestamp(known_at) < pd.Timestamp(target_ts) - g)


def ibja_known_at(value_date: Any, field: str) -> pd.Timestamp:
    """IBJA value date D -> the assumed publication instant (naive UTC)."""
    return pd.Timestamp(value_date).normalize() + IBJA_PUBLISH_UTC[field]


def comex_known_at(close_date: Any) -> pd.Timestamp:
    return pd.Timestamp(close_date).normalize() + COMEX_KNOWN_LAG


def first_available_day(
    known_at: pd.Timestamp, first_day: pd.Timestamp, targets: dict[pd.Timestamp, pd.Timestamp]
) -> pd.Timestamp:
    """The first UTC day >= first_day whose target is strictly after known_at (a day with no
    target has nothing to leak into, so it qualifies)."""
    d = pd.Timestamp(first_day).normalize()
    while d in targets and not is_available(known_at, targets[d]):
        d += pd.Timedelta(days=1)
    return d


# --- data ---------------------------------------------------------------------------------------


def load_ak() -> Any:
    name = "analysis_kalman_nowcast"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def tanishq_readings() -> pd.DataFrame:
    raw = json.loads((ROOT / "data" / "prices.json").read_text(encoding="utf-8"))
    rows = [r for r in raw if r.get("22k") is not None]
    assert all(str(r["timestamp"]).endswith("Z") for r in rows), "non-UTC timestamp in prices.json"
    df = pd.DataFrame(
        {
            "ts": pd.to_datetime([r["timestamp"] for r in rows]).tz_localize(None),
            "v": [float(r["22k"]) for r in rows],
        }
    )
    assert df["ts"].is_monotonic_increasing, "prices.json not chronological"
    df["d"] = df["ts"].dt.normalize()
    return df


def target_table(tr: pd.DataFrame) -> pd.DataFrame:
    """Per UTC date: T (target timestamp), the target value, and the last reading strictly before
    T (any date) -- the intraday carry-forward baseline -- with its timestamp."""
    last = tr.groupby("d").last()
    out = pd.DataFrame({"T": last["ts"], "t22": last["v"]})
    prev_ts, prev_v = [], []
    for t in out["T"]:
        before = tr[tr["ts"] < t]
        prev_ts.append(before["ts"].iloc[-1] if len(before) else pd.NaT)
        prev_v.append(float(before["v"].iloc[-1]) if len(before) else np.nan)
    out["prev_ts"] = prev_ts
    out["carry_intraday"] = prev_v
    return out


def national_snapshots() -> pd.DataFrame:
    s = pd.read_parquet(ROOT / "data" / "fusion_snapshots.parquet")
    s = s[s["city"].isna()].copy()
    s["cap"] = pd.to_datetime(s["capture_utc"]).dt.tz_localize(None)
    s["obs_day"] = (
        pd.to_datetime(s["observed_at"], format="mixed", utc=True)
        .dt.tz_localize(None)
        .dt.normalize()
    )
    assert (s["cap"].dt.strftime("%Y-%m-%d") == s["as_of_date"]).all(), "as_of_date != capture date"
    return s


# --- observation builders with provenance ------------------------------------------------------


def build_with_provenance(
    days: pd.DatetimeIndex,
    tanishq: pd.Series,
    targets: dict[pd.Timestamp, pd.Timestamp],
    ibja: pd.DataFrame,
    snaps: pd.DataFrame,
    comex: pd.Series,
    *,
    strict_ibja: bool,
    strict_retail: bool,
    retail_guard: pd.Timedelta | None = None,
) -> tuple[list[list[Observation]], list[list[dict[str, Any]]]]:
    """Mirror of ml.kalman_nowcast.build_observations that also records, per observation, its
    source time and known-at time. strict_* moves/filters inputs so every input assimilated on day d
    (before d's anchor) was known strictly before T_d. With both False it reproduces the registered
    information set exactly (asserted against build_observations in main())."""
    pos = {d: i for i, d in enumerate(pd.DatetimeIndex(days).normalize())}
    obs: list[list[Observation]] = [[] for _ in days]
    prov: list[list[dict[str, Any]]] = [[] for _ in days]

    def put(day: pd.Timestamp, o: Observation, meta: dict[str, Any]) -> None:
        i = pos.get(pd.Timestamp(day).normalize())
        if i is not None and math.isfinite(o.log_value):
            obs[i].append(o)
            prov[i].append({"source": o.source, **meta})

    ib = ibja.sort_index()
    for col, src in (("am", "ibja_am"), ("pm", "ibja_pm")):
        s = ib[col]
        prev = s.shift(1)
        for d, v, pv in zip(s.index, s.to_numpy(), prev.to_numpy(), strict=True):
            if not (np.isfinite(v) and v > 0) or (np.isfinite(pv) and v == pv):
                continue
            known = ibja_known_at(d, col)
            day = first_available_day(known, d, targets) if strict_ibja else pd.Timestamp(d)
            age = float((day - pd.Timestamp(d).normalize()).days)
            put(day, Observation(src, math.log(v), age), {"obs_time": d, "known_at": known})

    r = snaps[snaps["source"].isin(("grt", "malabar"))].copy()
    if strict_retail:
        lim = pd.to_datetime(r["as_of_date"]).map(targets)
        g = retail_guard if retail_guard is not None else pd.Timedelta(0)
        r = r[lim.isna() | (r["cap"] < lim - g)]
    r = r.sort_values("cap").groupby(["as_of_date", "source"]).last().reset_index()
    r["day"] = pd.to_datetime(r["as_of_date"])
    r = r.sort_values(["day", "source"])
    last_obs: dict[str, pd.Timestamp] = {}
    for day, src, v, od, cap in zip(
        r["day"], r["source"], r["rate_22k"], r["obs_day"], r["cap"], strict=True
    ):
        v = float(v)
        if not (np.isfinite(v) and v > 0) or last_obs.get(src) == od:
            continue
        last_obs[src] = od
        age = max(float((day - od).days), 0.0)
        put(day, Observation(src, math.log(v), age), {"obs_time": cap, "known_at": cap})

    for c, v in zip(comex.index, comex.to_numpy(), strict=True):
        if np.isfinite(v) and v > 0:
            put(
                pd.Timestamp(c) + pd.Timedelta(days=1),
                Observation("comex", math.log(v), 1.0),
                {"obs_time": pd.Timestamp(c), "known_at": comex_known_at(c)},
            )
    for d, v in tanishq.items():
        if np.isfinite(v) and v > 0:
            put(
                d,
                Observation(ANCHOR, math.log(v), 0.0),
                {"obs_time": targets[d], "known_at": targets[d]},
            )
    return obs, prov


# --- walk-forward with the anchor-perturbation check ---------------------------------------------


def walk_forward(
    days: pd.DatetimeIndex, obs: list[list[Observation]], eval_days: list[pd.Timestamp], check: bool
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Same procedure as analysis_kalman_nowcast.walk_forward (7-day blocks, MLE on days strictly
    before the block, warm start). With check=True, for every scored day d the whole Tanishq series
    from d on is shifted by +5% in log and the filter re-run: d's nowcast must not change."""
    ak = load_ak()
    nt = nontrading_flags(days)
    pos = {d: i for i, d in enumerate(days)}
    first_anchor = next(o.log_value for day in obs for o in day if o.source == ANCHOR)
    init = default_initial_state(first_anchor)
    ev = sorted(pos[d] for d in eval_days if d in pos)
    rows: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    params = None
    b = ev[0]
    while b <= ev[-1]:
        e = b + ak.REFIT_EVERY_DAYS
        params, info = fit_params(obs[:b], nt[:b], init, start=params)
        res = run_filter(obs[:e], nt[:e], params, init)
        for i in (i for i in ev if b <= i < e):
            pt, lo, hi = predictive(res.pre_anchor_mean[i], res.pre_anchor_var[i], params)
            row: dict[str, Any] = {
                "date": days[i],
                "kalman": pt,
                "k_lo": lo,
                "k_hi": hi,
                "block": str(days[b].date()),
                "train_end_idx": b,
            }
            if check:
                pert = [
                    [
                        Observation(
                            o.source,
                            o.log_value + (PERTURB_LOG if (o.source == ANCHOR and t >= i) else 0.0),
                            o.age_days,
                        )
                        for o in day
                    ]
                    for t, day in enumerate(obs[:e])
                ]
                r2 = run_filter(pert, nt[:e], params, init)
                row["perturb_abs_diff_log"] = float(
                    abs(r2.pre_anchor_mean[i] - res.pre_anchor_mean[i])
                )
            rows.append(row)
        blocks.append({"block_start": str(days[b].date()), "train_days": b, **info})
        print(f"[audit] block {days[b].date()} ({b} train days) {info}", flush=True)
        b = e
    return pd.DataFrame(rows), blocks


# --- scoring -----------------------------------------------------------------------------------


def score(frame: pd.DataFrame, preds: dict[str, str], y_col: str, horizon: int) -> dict[str, Any]:
    ak = load_ak()
    y = frame[y_col].to_numpy(dtype=float)
    out: dict[str, Any] = {}
    for g, mask in ak._stratum_masks(frame["day_type"]).items():
        cell: dict[str, Any] = {"n": int(mask.sum())}
        if mask.sum() >= 3:
            for name, col in preds.items():
                cell[f"mae_{name}"] = ak.hac_mean_ci(
                    np.abs(frame[col].to_numpy(dtype=float)[mask] - y[mask])
                )
            k = np.abs(frame[preds["kalman"]].to_numpy(dtype=float)[mask] - y[mask])
            for name, col in preds.items():
                if name != "kalman":
                    base = np.abs(frame[col].to_numpy(dtype=float)[mask] - y[mask])
                    cell[f"kalman_vs_{name}"] = ak.dm_less(k, base, horizon)
        out[g] = cell
    return out


def family(tests: list[tuple[str, str, dict[str, Any]]]) -> list[dict[str, Any]]:
    from ml.direction.stats_corrections import benjamini_hochberg, bonferroni

    pv = [t[2]["p_one_sided"] for t in tests]
    bon = bonferroni(pv, 0.05)
    bh = benjamini_hochberg(pv, 0.05)
    return [
        {
            "id": tid,
            "test": desc,
            **res,
            "bonferroni_significant": bon["significant"][i],
            "bh_significant": bh["significant"][i],
            "bonferroni_threshold": bon["threshold"],
        }
        for i, (tid, desc, res) in enumerate(tests)
    ]


def strict_ibja_pm(
    dates: pd.Series, targets: dict[pd.Timestamp, pd.Timestamp], ibja: pd.DataFrame
) -> np.ndarray:
    """Latest IBJA PM (Rs/g) whose publication (value date 11:30 UTC) is strictly before T_d."""
    known = pd.Series(
        ibja["pm"].to_numpy(), index=[ibja_known_at(d, "pm") for d in ibja.index]
    ).sort_index()
    out = []
    for d in pd.to_datetime(dates):
        s = known[known.index < targets[pd.Timestamp(d)]]
        out.append(float(s.iloc[-1]) if len(s) else np.nan)
    return np.asarray(out)


def strict_fusion(
    dates: pd.Series, targets: dict[pd.Timestamp, pd.Timestamp], snaps: pd.DataFrame
) -> np.ndarray:
    """DEFAULT_WEIGHTS mean over each source's last national capture strictly before T_d (as-of,
    within 3 days)."""
    from ml.fusion import DEFAULT_WEIGHTS

    s = snaps[snaps["source"].isin(list(DEFAULT_WEIGHTS))].sort_values("cap")
    out = []
    for d in pd.to_datetime(dates):
        t = targets[pd.Timestamp(d)]
        w = s[(s["cap"] < t) & (s["cap"] >= t - pd.Timedelta(days=3))].groupby("source").last()
        if w.empty:
            out.append(np.nan)
            continue
        wt = w.index.map(DEFAULT_WEIGHTS).to_numpy(dtype=float)
        out.append(float((w["rate_22k"].to_numpy(dtype=float) * wt).sum() / wt.sum()))
    return np.asarray(out)


def js(x: Any) -> Any:
    if isinstance(x, pd.Timestamp):
        return None if pd.isna(x) else x.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(x, (np.floating, float)):
        return None if not math.isfinite(float(x)) else float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return str(x)


def main() -> int:
    ak = load_ak()
    nc = ak._load_script("analysis_nowcast")
    tr = tanishq_readings()
    tt = target_table(tr)
    truth = nc.load_truth()
    assert np.allclose(truth.reindex(tt.index).to_numpy(), tt["t22"].to_numpy()), "target mismatch"
    targets = {pd.Timestamp(d): pd.Timestamp(t) for d, t in tt["T"].items()}
    days = pd.date_range(truth.index.min(), truth.index.max(), freq="D")
    comex = ak.load_comex(
        (days[0] - pd.Timedelta(days=10)).strftime("%Y-%m-%d"),
        (days[-1] + pd.Timedelta(days=2)).strftime("%Y-%m-%d"),
    )
    ib = ak.load_ibja_am_pm()
    ib_full = nc.load_ibja()
    snaps = national_snapshots()
    _ibraw = pd.read_parquet(ROOT / "data" / "ibja_rates.parquet")
    ib_full_fetched = dict(
        zip(
            pd.to_datetime(_ibraw["date"]),
            pd.to_datetime(_ibraw["fetched_at"], format="mixed", utc=True).dt.tz_localize(None),
            strict=True,
        )
    )

    # 0. the provenance builder reproduces the registered information set exactly
    reg_obs_ref = build_observations(days, truth, ib, ak.load_retail(), comex)
    reg_obs, reg_prov = build_with_provenance(
        days, truth, targets, ib, snaps, comex, strict_ibja=False, strict_retail=False
    )
    for ref_day, prov_day in zip(reg_obs_ref, reg_obs, strict=True):
        assert sorted((o.source, round(o.log_value, 12), o.age_days) for o in ref_day) == sorted(
            (o.source, round(o.log_value, 12), o.age_days) for o in prov_day
        ), "provenance builder diverges from build_observations"

    variants = {
        "registered": dict(strict_ibja=False, strict_retail=False),
        "strict_retail_only": dict(strict_ibja=False, strict_retail=True),
        "strict_ibja_only": dict(strict_ibja=True, strict_retail=False),
        "strict_all": dict(strict_ibja=True, strict_retail=True),
        "strict_all_same_run_guard": dict(
            strict_ibja=True, strict_retail=True, retail_guard=SAME_RUN_GUARD
        ),
    }
    nowf, ratio = ak.scorecard_nowcast_frame()
    set_a = nowf.dropna(subset=["nowcast_M0", "carry", "ibja_fixed_markup"]).reset_index(drop=True)
    fus = ak.scorecard_fusion_frame()
    set_b = fus.dropna(subset=["fusion", "carry", "ibja_fixed_markup_fusion"]).reset_index(
        drop=True
    )
    eval_days = sorted(set(set_a["date"]) | set(set_b["date"]))

    kal: dict[str, pd.DataFrame] = {}
    provs: dict[str, list[list[dict[str, Any]]]] = {}
    blocks_out: dict[str, Any] = {}
    for name, kw in variants.items():
        obs_v, prov_v = build_with_provenance(days, truth, targets, ib, snaps, comex, **kw)  # type: ignore[arg-type]
        kal_v, blocks = walk_forward(
            days, obs_v, eval_days, check=name in ("registered", "strict_all")
        )
        kal[name], provs[name], blocks_out[name] = kal_v, prov_v, blocks

    # --- 1/2. per-day input audit ---------------------------------------------------------------
    pos = {d: i for i, d in enumerate(days)}
    in_a, in_b = set(set_a["date"]), set(set_b["date"])
    dtype_map = {
        **dict(zip(set_a["date"], set_a["day_type"], strict=True)),
        **dict(zip(set_b["date"], set_b["day_type"], strict=True)),
    }
    kreg = kal["registered"].set_index("date")
    audit_rows = []
    for d in eval_days:
        i = pos[d]
        t = targets[d]
        blk_train_end = int(kreg.loc[d, "train_end_idx"])
        max_prior = max((m["known_at"] for day in reg_prov[:i] for m in day), default=pd.NaT)
        max_train = max(
            (m["known_at"] for day in reg_prov[:blk_train_end] for m in day), default=pd.NaT
        )
        strict_same = {
            (m["source"], m["obs_time"]) for m in provs["strict_all"][i] if m["source"] != ANCHOR
        }
        same_day = []
        for m in reg_prov[i]:
            if m["source"] == ANCHOR:
                continue
            same_day.append(
                {
                    "source": m["source"],
                    "obs_time_utc": js(pd.Timestamp(m["obs_time"])),
                    "known_at_utc": js(pd.Timestamp(m["known_at"])),
                    "target_utc": js(t),
                    "known_minus_target_s": float(
                        (pd.Timestamp(m["known_at"]) - t).total_seconds()
                    ),
                    "strictly_earlier": is_available(m["known_at"], t),
                    "fetched_at_utc": (
                        js(ib_full_fetched[pd.Timestamp(m["obs_time"]).normalize()])
                        if m["source"].startswith("ibja")
                        and pd.Timestamp(m["obs_time"]).normalize() in ib_full_fetched
                        else None
                    ),
                    "used_in_strict_rescore": (m["source"], m["obs_time"]) in strict_same,
                }
            )
        # inputs the strict run adds on this day (deferred IBJA, pre-target retail captures)
        added = [
            {
                "source": m["source"],
                "obs_time_utc": js(pd.Timestamp(m["obs_time"])),
                "known_at_utc": js(pd.Timestamp(m["known_at"])),
                "known_minus_target_s": float((pd.Timestamp(m["known_at"]) - t).total_seconds()),
            }
            for m in provs["strict_all"][i]
            if m["source"] != ANCHOR
            and (m["source"], m["obs_time"])
            not in {(x["source"], x["obs_time"]) for x in reg_prov[i]}
        ]
        audit_rows.append(
            {
                "date": str(d.date()),
                "day_type": dtype_map[d],
                "set_A": d in in_a,
                "set_B": d in in_b,
                "target_utc": js(t),
                "registered_same_day_inputs": same_day,
                "strict_added_inputs": added,
                "filter_state_prior_days": {
                    "last_anchor_utc": js(targets[max(k for k in targets if k < d)])
                    if any(k < d for k in targets)
                    else None,
                    "max_known_at_utc": js(max_prior),
                    "max_known_minus_target_s": float((max_prior - t).total_seconds()),
                },
                "noise_params_mle": {
                    "block_start": kreg.loc[d, "block"],
                    "train_days_before": blk_train_end,
                    "train_includes_target_day": blk_train_end > i,
                    "max_known_at_utc": js(max_train),
                    "max_known_minus_target_s": float((max_train - t).total_seconds()),
                },
                "anchor_perturbation_abs_diff_log": float(kreg.loc[d, "perturb_abs_diff_log"]),
            }
        )

    # --- 3/4. re-score -----------------------------------------------------------------------
    def frame_a(k: pd.DataFrame) -> pd.DataFrame:
        a = set_a.merge(k[["date", "kalman", "k_lo", "k_hi"]], on="date", how="inner")
        a["ibja_fixed_markup_strict"] = ratio * strict_ibja_pm(a["date"], targets, ib_full)
        a["carry_intraday"] = a["date"].map(tt["carry_intraday"])
        return a

    def frame_b(k: pd.DataFrame) -> pd.DataFrame:
        b = set_b.merge(k[["date", "kalman", "k_lo", "k_hi"]], on="date", how="inner")
        b["fusion_strict"] = strict_fusion(b["date"], targets, snaps)
        b["carry_intraday"] = b["date"].map(tt["carry_intraday"])
        return b

    results: dict[str, Any] = {}
    for name, k in kal.items():
        a, b = frame_a(k), frame_b(k)
        strict_bases = name != "registered"
        fm = "ibja_fixed_markup_strict" if strict_bases else "ibja_fixed_markup"
        fu = "fusion_strict" if strict_bases else "fusion"
        pa = {
            "kalman": "kalman",
            "ibja_fixed_markup": fm,
            "carry_yesterday": "carry",
            "carry_intraday": "carry_intraday",
        }
        pb = {
            "kalman": "kalman",
            "fusion": fu,
            "carry_yesterday": "carry",
            "carry_intraday": "carry_intraday",
        }
        per_lag = {}
        for lag, h in (("lag1", 2), ("lag5", 6)):
            ca, cb = score(a, pa, "t22", h), score(b, pb, "t22", h)
            reg_fam = family(
                [
                    (
                        "T1",
                        "vs IBJA x fixed markup, all (set A)",
                        ca["all"]["kalman_vs_ibja_fixed_markup"],
                    ),
                    (
                        "T2",
                        "vs IBJA x fixed markup, weekdays (set A)",
                        ca["weekday"]["kalman_vs_ibja_fixed_markup"],
                    ),
                    (
                        "T3",
                        "vs IBJA x fixed markup, weekends (set A)",
                        ca["weekend"]["kalman_vs_ibja_fixed_markup"],
                    ),
                    ("T4", "vs fusion, weekdays (set B)", cb["weekday"]["kalman_vs_fusion"]),
                    ("T5", "vs fusion, weekends (set B)", cb["weekend"]["kalman_vs_fusion"]),
                    (
                        "T6",
                        "vs yesterday's Tanishq, weekends (set A)",
                        ca["weekend"]["kalman_vs_carry_yesterday"],
                    ),
                ]
            )
            expl = family(
                [
                    (
                        "E1",
                        "vs last Tanishq reading before target, all (set A)",
                        ca["all"]["kalman_vs_carry_intraday"],
                    ),
                    (
                        "E2",
                        "vs last Tanishq reading before target, weekdays (set A)",
                        ca["weekday"]["kalman_vs_carry_intraday"],
                    ),
                    (
                        "E3",
                        "vs last Tanishq reading before target, weekends (set A)",
                        ca["weekend"]["kalman_vs_carry_intraday"],
                    ),
                    (
                        "E4",
                        "vs last Tanishq reading before target, weekdays (set B)",
                        cb["weekday"]["kalman_vs_carry_intraday"],
                    ),
                    (
                        "E5",
                        "vs last Tanishq reading before target, weekends (set B)",
                        cb["weekend"]["kalman_vs_carry_intraday"],
                    ),
                ]
            )
            per_lag[lag] = {
                "set_A_cells": ca,
                "set_B_cells": cb,
                "family_T1_T6": reg_fam,
                "exploratory_E1_E5": expl,
            }
        cov = {}
        for sname, f in (("set_A", a), ("set_B", b)):
            y = f["t22"].to_numpy()
            covered = (f["k_lo"].to_numpy() <= y) & (y <= f["k_hi"].to_numpy())
            width = (f["k_hi"] - f["k_lo"]).to_numpy()
            cov[sname] = {
                g: ak.coverage_block(covered[m], width[m])
                for g, m in ak._stratum_masks(f["day_type"]).items()
            }
        results[name] = {"lags": per_lag, "coverage": cov, "n_set_A": len(a), "n_set_B": len(b)}

    # --- leak counts and magnitudes -----------------------------------------------------------
    same_rows = [
        (r["date"], r["day_type"], x) for r in audit_rows for x in r["registered_same_day_inputs"]
    ]
    leak_by_src: dict[str, Any] = {}
    for src in sorted({x["source"] for _, _, x in same_rows}):
        rows = [(d, dt, x) for d, dt, x in same_rows if x["source"] == src]
        bad = [(d, dt, x) for d, dt, x in rows if not x["strictly_earlier"]]
        leak_by_src[src] = {
            "day_source_pairs_used_same_day": len(rows),
            "known_at_or_after_target": len(bad),
            "weekday": sum(1 for _, dt, _ in bad if dt != "weekend"),
            "weekend": sum(1 for _, dt, _ in bad if dt == "weekend"),
            "median_minutes_after_target": float(
                np.median([x["known_minus_target_s"] / 60 for *_, x in bad])
            )
            if bad
            else None,
            "max_minutes_after_target": float(
                np.max([x["known_minus_target_s"] / 60 for *_, x in bad])
            )
            if bad
            else None,
        }
    kr = kal["registered"].set_index("date")["kalman"]
    magnitude = {}
    for name in (
        "strict_retail_only",
        "strict_ibja_only",
        "strict_all",
        "strict_all_same_run_guard",
    ):
        ks = kal[name].set_index("date")["kalman"]
        diff = (ks - kr).abs()
        dt = pd.Series(dtype_map)
        changed = diff[diff > 1e-6]
        magnitude[name] = {
            "days_nowcast_changed": len(changed),
            "mean_abs_change_rs_g_on_changed_days": float(changed.mean()) if len(changed) else 0.0,
            "max_abs_change_rs_g": float(diff.max()),
            "changed_weekday": int(sum(dt[d] != "weekend" for d in changed.index)),
            "changed_weekend": int(sum(dt[d] == "weekend" for d in changed.index)),
        }
    t_early = [r for r in audit_rows if r["set_A"]]
    leak_summary = {
        "target_definition": "last Tanishq 22K reading of the UTC date in data/prices.json (timestamps UTC 'Z'); T_d = its timestamp",
        "availability_assumptions": {
            "ibja_am": "value date 06:30 UTC (12:00 IST)",
            "ibja_pm": "value date 11:30 UTC (17:00 IST)",
            "grt_malabar": "capture_utc",
            "comex": "close date + 1 day 00:00 UTC",
            "tanishq": "reading timestamp",
        },
        "scored_days": len(audit_rows),
        "same_day_inputs_by_source": leak_by_src,
        "anchor_perturbation_max_abs_diff_log": float(
            max(r["anchor_perturbation_abs_diff_log"] for r in audit_rows)
        ),
        "strict_run_anchor_perturbation_max_abs_diff_log": float(
            kal["strict_all"]["perturb_abs_diff_log"].max()
        ),
        "prior_day_inputs_known_after_target": sum(
            r["filter_state_prior_days"]["max_known_minus_target_s"] >= 0 for r in audit_rows
        ),
        "mle_training_includes_target_day": sum(
            r["noise_params_mle"]["train_includes_target_day"] for r in audit_rows
        ),
        "mle_training_input_known_after_target": sum(
            r["noise_params_mle"]["max_known_minus_target_s"] >= 0 for r in audit_rows
        ),
        "ibja_used_before_fetched_at_descriptive": sum(
            1
            for r in audit_rows
            for x in r["registered_same_day_inputs"]
            if x["fetched_at_utc"] is not None and x["fetched_at_utc"] >= r["target_utc"]
        ),
        "set_A_targets_before_ibja_pm_publish": sum(
            pd.Timestamp(r["target_utc"]).tz_localize(None) <= ibja_known_at(r["date"], "pm")
            for r in t_early
        ),
        "set_A_targets_before_ibja_am_publish": sum(
            pd.Timestamp(r["target_utc"]).tz_localize(None) <= ibja_known_at(r["date"], "am")
            for r in t_early
        ),
        "set_B_days_without_any_pre_target_retail_capture": sum(
            1
            for r in audit_rows
            if r["set_B"]
            and not any(
                x["source"] in ("grt", "malabar") and x["strictly_earlier"]
                for x in r["registered_same_day_inputs"]
            )
            and not any(x["source"] in ("grt", "malabar") for x in r["strict_added_inputs"])
        ),
        "nowcast_change_when_leaks_removed": magnitude,
        "baseline_leaks": {
            "ibja_fixed_markup_days_using_pm_before_publish_set_A": int(
                np.sum(
                    ~np.isclose(
                        set_a["ibja_fixed_markup"].to_numpy(),
                        ratio * strict_ibja_pm(set_a["date"], targets, ib_full),
                    )
                )
            ),
            "fusion_days_changed_by_strict_capture_filter_set_B": int(
                np.sum(
                    ~np.isclose(
                        set_b["fusion"].to_numpy(), strict_fusion(set_b["date"], targets, snaps)
                    )
                )
            ),
        },
    }

    # --- weekend anatomy (strict run, set A) ---------------------------------------------------
    a = frame_a(kal["strict_all"])
    wk = a[a["day_type"] == "weekend"]
    pos_w = [pos[d] for d in wk["date"]]
    src_counts: dict[str, int] = {}
    for i in pos_w:
        for m in provs["strict_all"][i]:
            if m["source"] != ANCHOR:
                src_counts[m["source"]] = src_counts.get(m["source"], 0) + 1
    y = wk["t22"].to_numpy()
    weekend = {
        "n": len(wk),
        "same_day_input_counts_by_source": src_counts,
        "days_with_no_same_day_input": sum(
            1 for i in pos_w if not any(m["source"] != ANCHOR for m in provs["strict_all"][i])
        ),
        "target_equals_yesterday_last_reading": int(
            np.sum(np.isclose(y, wk["carry"].to_numpy(dtype=float)))
        ),
        "target_equals_last_reading_before_target": int(
            np.sum(np.isclose(y, wk["carry_intraday"].to_numpy(dtype=float)))
        ),
        "days_with_an_earlier_same_day_tanishq_reading": int(
            sum(
                pd.Timestamp(t).normalize() == d
                for t, d in zip(wk["date"].map(tt["prev_ts"]), wk["date"], strict=True)
            )
        ),
        "median_abs_gap_kalman_vs_last_reading_before_target_rs_g": float(
            np.median(np.abs(wk["kalman"] - wk["carry_intraday"]))
        ),
        "median_abs_gap_kalman_vs_yesterday_rs_g": float(
            np.median(np.abs(wk["kalman"] - wk["carry"]))
        ),
        "mae_kalman": float(np.mean(np.abs(wk["kalman"] - y))),
        "mae_carry_yesterday": float(np.mean(np.abs(wk["carry"] - y))),
        "mae_carry_last_reading_before_target": float(np.mean(np.abs(wk["carry_intraday"] - y))),
        "mae_ibja_fixed_markup_strict": float(np.mean(np.abs(wk["ibja_fixed_markup_strict"] - y))),
    }

    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        sha = None
    stamp = {"code_commit": sha, "generated_utc": datetime.now(UTC).isoformat(timespec="seconds")}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "inputs_audit.json").write_text(
        json.dumps(
            {
                **stamp,
                "note": "derived: source names, timestamps and deltas only",
                "days": audit_rows,
            },
            indent=1,
            default=js,
        )
        + "\n",
        encoding="utf-8",
    )
    (OUT_DIR / "leak_summary.json").write_text(
        json.dumps(
            {**stamp, **leak_summary, "weekend_anatomy_strict_set_A": weekend}, indent=1, default=js
        )
        + "\n",
        encoding="utf-8",
    )
    (OUT_DIR / "rescore.json").write_text(
        json.dumps(
            {
                **stamp,
                "status": "retrospective/exploratory correction of the ADR 055 measurement; not pre-registered",
                "fixed_markup_ratio": ratio,
                "variants": {k: {**v} for k, v in variants.items()},
                "results": results,
                "refits": blocks_out,
            },
            indent=1,
            default=js,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"leaks": leak_summary, "weekend": weekend}, indent=1, default=js))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
