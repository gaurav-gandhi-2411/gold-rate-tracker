"""scripts/run_kalman_shadow.py -- ADR 062 forward shadow of the ADR 055 Kalman nowcast, scored
ONLY at moments when Tanishq is not fresh (re-pre-registration after the ADR 055 leak audit).

Each run takes the run moment R (now, UTC) and:
  1. keeps only inputs known STRICTLY before R (the leak audit's availability rule,
     scripts/audit_kalman_leak.py: is_available / ibja_known_at / comex_known_at):
       Tanishq readings with timestamp < R; IBJA AM/PM whose publication (value date 06:30 / 11:30
       UTC) < R; GRT/Malabar national captures with capture_utc < R; COMEX closes dated c with
       c + 1 day 00:00 UTC < R;
  2. fits the ADR 055 noise parameters by MLE on the UTC days strictly before R's date, filters
     through every known input up to R (including any Tanishq reading earlier on R's own date),
     and takes the filtered level as the nowcast;
  3. records whether Tanishq was fresh at R, using the site's rule (app.js STALE_THRESHOLD_H =
     ml/inference.py _STALE_THRESHOLD_H = 8): fresh iff R - (last reading's timestamp) <= 8 h;
  4. appends one entry to reports/kalman_nowcast/shadow_v2.json.

E1: the log holds NO absolute price. Point and baselines are stored as differences from Tanishq's
last reading before R (Rs/g), the band as a log standard deviation, and the last reading is
identified by its timestamp only. Scoring recovers the levels from data/prices.json (and its git
history), so every number in the log is derived.

Entries where Tanishq was fresh are logged but never scored. Scoring (score(), `--score`) is
frozen with ADR 062 and is not run before the ADR's read rule allows.

Not wired into any workflow (the orchestrator does that, after E1). Shadow only.

Usage:
    python scripts/run_kalman_shadow.py [--out PATH]            # append one entry for now
    python scripts/run_kalman_shadow.py --score [--out PATH]    # ADR 062 scorer (read rule applies)
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
    ANCHOR,
    Z80,
    build_observations,
    default_initial_state,
    fit_params,
    nontrading_flags,
    run_filter,
)

OUT = ROOT / "reports" / "kalman_nowcast" / "shadow_v2.json"
ADR_V2 = ROOT / "docs" / "adr" / "062-kalman-shadow-stale-moments-preregistration.md"
SCHEMA = 2

STALE_THRESHOLD_H = 8.0  # = app.js STALE_THRESHOLD_H = ml/inference.py _STALE_THRESHOLD_H
TARGET_MAX_WAIT = pd.Timedelta(hours=72)  # next reading later than this after R: unscored
FIXED_MARKUP = 1.0138681104363907  # scorecard IBJA-PM fixed markup (ADR 055 results.json)
DM_HORIZON = 2  # HAC lag 1, as ADR 055
ALPHA = 0.05
MIN_EPISODES = 30
HARD_READ_DATE = pd.Timestamp("2027-03-31")
RETAIL_SOURCES = ("grt", "malabar")


def _load(name: str) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _audit() -> Any:
    # Swap-in point: when feat/known-at-leak-guard (ml/known_at.py) merges, its guard replaces
    # these three primitives; the rule (known_at strictly before R) does not change.
    return _load("audit_kalman_leak")


def prereg_sha256_v2() -> str | None:
    """sha256 of ADR 062's text above its '## Results' heading (the whole file at freeze)."""
    if not ADR_V2.exists():
        return None
    text = ADR_V2.read_bytes()
    cut = text.find(b"\n## Results")
    body = text if cut < 0 else text[: cut + 1].rstrip(b"\r\n") + b"\n"
    return hashlib.sha256(body).hexdigest()


def _iso(t: pd.Timestamp) -> str:
    return pd.Timestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- inputs known strictly before R ------------------------------------------------------------


def strict_inputs(
    run: pd.Timestamp,
    tanishq: pd.DataFrame,
    ibja: pd.DataFrame,
    snaps: pd.DataFrame,
    comex: pd.Series,
) -> dict[str, Any]:
    """Everything the filter may use at run moment `run` (naive UTC).

    tanishq  columns ts (naive UTC), v (Rs/g 22K), chronological (audit_kalman_leak.tanishq_readings)
    ibja     index value date, columns am, pm (Rs/g 22K)
    snaps    national snapshots with columns source, cap (naive UTC capture), as_of_date,
             rate_22k, obs_day (audit_kalman_leak.national_snapshots)
    comex    landed 22K parity (Rs/g) indexed by COMEX close date
    """
    au = _audit()
    ok = au.is_available
    tk = tanishq[[ok(t, run) for t in tanishq["ts"]]]
    if tk.empty:
        raise SystemExit("no Tanishq reading before the run moment")
    tk_day = tk.assign(d=tk["ts"].dt.normalize()).groupby("d")
    t_series = tk_day["v"].last()
    t_known = tk_day["ts"].last()

    ib = ibja.sort_index().copy()
    for col in ("am", "pm"):
        published = [au.ibja_known_at(d, col) for d in ib.index]
        ib[col] = ib[col].where(np.array([ok(k, run) for k in published], dtype=bool))
    ib = ib.dropna(how="all")

    r = snaps[snaps["source"].isin(RETAIL_SOURCES)]
    r = r[[ok(c, run) for c in r["cap"]]]
    r = r.sort_values("cap").groupby(["as_of_date", "source"]).last().reset_index()
    retail = pd.DataFrame(
        {
            "day": pd.to_datetime(r["as_of_date"]),
            "source": r["source"],
            "rate_22k": r["rate_22k"].astype(float),
            "observed_at_day": r["obs_day"],
            "cap": r["cap"],
        }
    )
    cx = comex[[ok(au.comex_known_at(c), run) for c in comex.index]]

    days = pd.date_range(t_series.index.min(), run.normalize(), freq="D")
    obs = build_observations(days, t_series, ib[["am", "pm"]], retail.drop(columns="cap"), cx)

    known: dict[str, list[pd.Timestamp]] = {
        ANCHOR: list(t_known),
        "ibja_am": [au.ibja_known_at(d, "am") for d in ib.index[ib["am"].notna()]],
        "ibja_pm": [au.ibja_known_at(d, "pm") for d in ib.index[ib["pm"].notna()]],
        "comex": [au.comex_known_at(c) for c in cx.index],
    }
    for src in RETAIL_SOURCES:
        known[src] = list(retail.loc[retail["source"] == src, "cap"])
    # inputs inside the filter's window (earlier IBJA/COMEX rows never reach it)
    known = {s: [k for k in v if pd.Timestamp(k) >= days[0]] for s, v in known.items()}
    latest = max(max(v) for v in known.values() if v)
    assert latest < run, "an input known at or after the run moment reached the filter"
    today = run.normalize()
    run_day = sorted(
        (_iso(k), s) for s, v in known.items() for k in v if pd.Timestamp(k).normalize() == today
    )
    return {
        "days": days,
        "obs": obs,
        "last_ts": pd.Timestamp(tk["ts"].iloc[-1]),
        "last_v": float(tk["v"].iloc[-1]),
        "ibja": ib,
        "provenance": {
            "per_source": {
                s: {"n": len(v), "max_known_at": _iso(max(v)) if v else None}
                for s, v in sorted(known.items())
            },
            "run_day_inputs": [{"known_at": k, "source": s} for k, s in run_day],
            "max_input_known_at": _iso(latest),
        },
    }


def ibja_markup_baseline(ib: pd.DataFrame) -> tuple[float, str] | None:
    """Secondary baseline: the latest IBJA PM already published (ib is pre-filtered to < R)
    times the scorecard's fixed markup. Returns (Rs/g, value date)."""
    pm = ib["pm"].dropna()
    if pm.empty:
        return None
    return float(pm.iloc[-1]) * FIXED_MARKUP, str(pd.Timestamp(pm.index[-1]).date())


def shadow_entry(
    run: pd.Timestamp,
    tanishq: pd.DataFrame,
    ibja: pd.DataFrame,
    snaps: pd.DataFrame,
    comex: pd.Series,
    code_commit: str | None = None,
) -> dict[str, Any]:
    inp = strict_inputs(run, tanishq, ibja, snaps, comex)
    obs, days = inp["obs"], inp["days"]
    nt = nontrading_flags(days)
    first_anchor = next(o.log_value for day in obs for o in day if o.source == ANCHOR)
    init = default_initial_state(first_anchor)
    n = len(days)
    params, info = fit_params(obs[: n - 1], nt[: n - 1], init)  # UTC days strictly before R's
    res = run_filter(obs, nt, params, init)
    mean, var = float(res.final_mean[0]), float(res.final_cov[0, 0])
    point = math.exp(mean)
    age_h = (run - inp["last_ts"]).total_seconds() / 3600
    ib = ibja_markup_baseline(inp["ibja"])
    au = _audit()
    return {
        "schema": SCHEMA,
        "adr": "062",
        "run_utc": _iso(run),
        "day_type": "weekend" if run.dayofweek >= 5 else "weekday",
        "tanishq_fresh": bool(age_h <= STALE_THRESHOLD_H),
        "stale_threshold_h": STALE_THRESHOLD_H,
        "last_tanishq_ts": _iso(inp["last_ts"]),
        "last_tanishq_age_h": round(age_h, 3),
        "kalman_minus_last_rs_g": round(point - inp["last_v"], 2),
        "ibja_markup_minus_last_rs_g": None if ib is None else round(ib[0] - inp["last_v"], 2),
        "ibja_pm_value_date": None if ib is None else ib[1],
        "ibja_pm_known_at": None if ib is None else _iso(au.ibja_known_at(ib[1], "pm")),
        "sd_log": math.sqrt(max(var, 0.0) + params["r_tanishq"]),
        "band_z": Z80,
        "inputs": inp["provenance"],
        "params": params.values,
        "fit": info,
        "prereg_sha256_adr062": prereg_sha256_v2(),
        "code_commit": code_commit,
    }


def append(entry: dict[str, Any], out: Path) -> int:
    rows: list[dict[str, Any]] = []
    if out.exists():
        rows = json.loads(out.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise SystemExit(f"{out} is not a JSON list; refusing to overwrite it")
    rows.append(entry)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    return len(rows)


# --- ADR 062 scorer (frozen with the ADR; the read rule below decides when it counts) -----------


def score(rows: list[dict[str, Any]], tanishq: pd.DataFrame, now: pd.Timestamp) -> dict[str, Any]:
    """Score the not-fresh entries. Target: the first Tanishq reading with timestamp strictly
    after R, if it arrives within TARGET_MAX_WAIT. One unit per target reading (an 'episode'):
    each predictor's loss is the mean absolute error over the episode's entries."""
    from ml.calibration_adaptive import _wilson_ci
    from ml.direction.stats_corrections import benjamini_hochberg, bonferroni

    ak = _load("analysis_kalman_nowcast")
    by_ts = dict(zip(tanishq["ts"], tanishq["v"], strict=True))
    ts = tanishq["ts"].to_numpy()
    counts = {"logged": len(rows), "fresh_excluded": 0, "pending": 0, "no_target_72h": 0}
    counts["baseline_reading_missing"] = 0
    ep: dict[pd.Timestamp, list[dict[str, float]]] = {}
    for r in rows:
        if r.get("schema") != SCHEMA:
            continue
        if r["tanishq_fresh"]:
            counts["fresh_excluded"] += 1
            continue
        run = pd.Timestamp(r["run_utc"]).tz_localize(None)
        i = int(np.searchsorted(ts, np.datetime64(run), side="right"))
        if i >= len(ts):
            counts["pending" if now - run <= TARGET_MAX_WAIT else "no_target_72h"] += 1
            continue
        tgt_ts = pd.Timestamp(ts[i])
        if tgt_ts - run > TARGET_MAX_WAIT:
            counts["no_target_72h"] += 1
            continue
        last = by_ts.get(pd.Timestamp(r["last_tanishq_ts"]).tz_localize(None))
        if last is None:
            counts["baseline_reading_missing"] += 1
            continue
        y = float(by_ts[tgt_ts])
        k = last + r["kalman_minus_last_rs_g"]
        half = Z80 * r["sd_log"]
        ib = r["ibja_markup_minus_last_rs_g"]
        ep.setdefault(tgt_ts, []).append(
            {
                "k": abs(k - y),
                "last": abs(last - y),
                "ibja": abs(last + ib - y) if ib is not None else math.nan,
                "cov": float(k * math.exp(-half) <= y <= k * math.exp(half)),
                "weekend": float(run.dayofweek >= 5),
            }
        )
    keys = sorted(ep)
    frame = pd.DataFrame(
        [{c: float(np.mean([e[c] for e in ep[t]])) for c in ep[t][0]} for t in keys],
        index=pd.DatetimeIndex(keys),
    )
    n = len(frame)
    read_allowed = n >= MIN_EPISODES or now >= HARD_READ_DATE
    out: dict[str, Any] = {
        "adr": "062",
        "prereg_sha256_adr062": prereg_sha256_v2(),
        "scored_at_utc": _iso(now),
        "counts": {**counts, "entries_scored": sum(len(v) for v in ep.values())},
        "n_episodes": n,
        "read_allowed": read_allowed,
    }
    if n == 0:
        out["verdict"] = "NOT YET"
        return out
    out["mae_rs_g"] = {c: ak.hac_mean_ci(frame[c].dropna().to_numpy()) for c in ("k", "last")}
    out["mae_rs_g"]["ibja"] = ak.hac_mean_ci(frame["ibja"].dropna().to_numpy())
    h1 = ak.dm_less(frame["k"].to_numpy(), frame["last"].to_numpy(), DM_HORIZON)
    both = frame.dropna(subset=["ibja"])
    h2 = ak.dm_less(both["k"].to_numpy(), both["ibja"].to_numpy(), DM_HORIZON)
    ps = [h1["p_one_sided"], h2["p_one_sided"]]
    if all(p is not None for p in ps):
        bon, bh = bonferroni(ps, ALPHA), benjamini_hochberg(ps, ALPHA)
        h1 |= {"bonferroni": bon["significant"][0], "bh": bh["significant"][0]}
        h2 |= {"bonferroni": bon["significant"][1], "bh": bh["significant"][1]}
    out["H1_kalman_vs_last_tanishq"] = {"n": n, **h1}
    out["H2_kalman_vs_ibja_markup"] = {"n": len(both), **h2}
    wk = frame["weekend"] >= 0.5
    out["descriptive_by_day_type"] = {
        name: {
            "n": int(m.sum()),
            "mae_k": float(frame.loc[m, "k"].mean()) if m.any() else None,
            "mae_last": float(frame.loc[m, "last"].mean()) if m.any() else None,
        }
        for name, m in (("weekday", ~wk), ("weekend", wk))
    }
    hits = int((frame["cov"] >= 0.5).sum())
    out["band80_descriptive"] = {"episodes_mostly_covered": hits, "wilson95": _wilson_ci(hits, n)}
    if not read_allowed:
        out["verdict"] = "NOT YET"
    elif n < MIN_EPISODES:
        out["verdict"] = "INCONCLUSIVE (underpowered)"
    else:
        out["verdict"] = "PASS" if h1.get("bonferroni") else "NEGATIVE"
    return out


# --- CLI ---------------------------------------------------------------------------------------


def _live_inputs(run: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.Series]:
    au, ak = _audit(), _load("analysis_kalman_nowcast")
    tanishq = au.tanishq_readings()
    start = (tanishq["ts"].min().normalize() - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    end = (run.normalize() + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    return tanishq, ak.load_ibja_am_pm(), au.national_snapshots(), ak.load_comex(start, end)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--score", action="store_true", help="run the ADR 062 scorer on --out")
    args = ap.parse_args()
    now = pd.Timestamp(datetime.now(UTC)).tz_localize(None)
    if args.score:
        rows = json.loads(args.out.read_text(encoding="utf-8")) if args.out.exists() else []
        print(json.dumps(score(rows, _audit().tanishq_readings(), now), indent=1, default=str))
        return 0
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        sha = None
    entry = shadow_entry(now, *_live_inputs(now), code_commit=sha)
    n = append(entry, args.out)
    print(
        f"[adr062-shadow] appended entry {n}: fresh={entry['tanishq_fresh']} "
        f"age_h={entry['last_tanishq_age_h']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
