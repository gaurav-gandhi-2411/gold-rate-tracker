"""scripts/analysis_timing_audit.py -- ADR 058 Part 1: clock evidence, audit table, re-runs.

Writes reports/timing_audit/audit.json:
  clock_evidence  Yahoo daily Close vs intraday bars: which wall-clock time does each daily value
                  represent (GC=F, GLD, INR=X)? Recomputed from the last ~60 days of 5-minute bars
                  and ~2 years of 1-hour bars (Yahoo's intraday limits).
  audit_table     every analysis/feature that mixes clocks: inputs, convention, issue, severity,
                  whether a published result could change, proposed fix (static, reasoned in ADR 058).
  reruns          the three findings that could change a shipped conclusion, re-measured with
                  correct alignment on the window where 1-hour bars exist (EXPLORATORY: these are
                  re-measurements of already-published results, not confirmations):
    R1  ADR 032 "timing misalignment ruled out": IBJA PM direction agreement for the daily-close
        label (lag 0), the T-1 feature series (lag -1), and COMEX x USD/INR as known at 11:30 UTC.
    R2  R2 nowcast (#1957) "a COMEX x USD/INR adjustment makes weekend days worse": M4 with the
        move measured from IBJA's PM fix to the time the scored Tanishq board was first seen.
    R3  Derived premium / ADR 046: premium AR(1) and C-vs-B1 when parity is taken at the fix time.

Nothing here touches the live pipeline. Raw third-party prices are not written (ADR 054).
"""

from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
import warnings
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.direction.evaluate_reframed import diebold_mariano_test
from ml.direction.stats_corrections import one_sided_mcnemar
from ml.range_forecast.metrics import wilson_ci
from ml.timing_alignment import IBJA_PM, asof_intraday, known_at

OUT = ROOT / "reports" / "timing_audit" / "audit.json"
TROY_OZ_TO_GRAM = 31.1034768
ONE_H = pd.Timedelta(hours=1)
PRE_AM_UTC = (6, 0)  # 11:30 IST: a value "known before t's AM fix" (ADR 046's prediction time)
MIN_PAIRS = 30  # ADR 046

warnings.filterwarnings("ignore")


def _load_script(name: str) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _yf(ticker: str, **kw: Any) -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(ticker, auto_adjust=True, progress=False, threads=False, **kw)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


# --- clock evidence ---------------------------------------------------------------------------


def _match_daily_to_intraday(daily: pd.DataFrame, intra: pd.DataFrame, tz: str, hhmm: list[str]):  # type: ignore[no-untyped-def]
    m = intra.tz_convert(tz)
    mins = m.index.hour * 60 + m.index.minute
    close = pd.Series(daily["Close"].to_numpy(), index=pd.Index(daily.index.date))
    out = {}
    for t in hhmm:
        hh, mm = map(int, t.split(":"))
        sel = m[mins <= hh * 60 + mm]
        last = sel["Close"].groupby(pd.Index(sel.index.date)).last()
        j = pd.concat([close.rename("d"), last.rename("i")], axis=1).dropna()
        bp = (j["d"] - j["i"]).abs() / j["d"] * 1e4
        out[t] = {"n_days": len(j), "median_abs_gap_bp": float(bp.median())}
    last = m["Close"].groupby(pd.Index(m.index.date)).last()
    j = pd.concat([close.rename("d"), last.rename("i")], axis=1).dropna()
    out["last_bar_of_day"] = {
        "n_days": len(j),
        "median_abs_gap_bp": float(((j["d"] - j["i"]).abs() / j["d"] * 1e4).median()),
    }
    return out


def clock_evidence() -> dict[str, Any]:
    res: dict[str, Any] = {
        "method": "daily Close vs the close of the last 5-min bar STARTING at or before each "
        "local time (a bar starting 13:25 ends 13:30); smallest gap = the daily value's clock"
    }
    gc_d, gc_m = _yf("GC=F", period="60d", interval="1d"), _yf("GC=F", period="59d", interval="5m")
    res["GC=F"] = _match_daily_to_intraday(
        gc_d, gc_m, "America/New_York", ["12:00", "13:00", "13:25", "14:00", "15:00", "16:00"]
    )
    gld_d, gld_m = _yf("GLD", period="60d", interval="1d"), _yf("GLD", period="59d", interval="5m")
    res["GLD"] = _match_daily_to_intraday(
        gld_d, gld_m, "America/New_York", ["13:25", "14:00", "15:00", "15:55"]
    )
    inr_long = _yf("INR=X", start="2022-01-01", interval="1d")
    inr_h = _yf("INR=X", period="729d", interval="1h")
    res["INR=X"] = {
        "n_daily_rows": len(inr_long),
        "close_equals_open_share": float((inr_long["Close"] == inr_long["Open"]).mean()),
        "vs_1h_bar_starting_at_or_before_utc_hour": _match_daily_to_intraday(
            inr_long, inr_h, "UTC", [f"{h:02d}:00" for h in (0, 1, 2, 4, 6, 11, 17, 23)]
        ),
    }
    return res


# --- intraday price known at a UTC instant ---------------------------------------------------


class Intraday:
    """GC=F x INR=X (Rs per troy oz) as known at any UTC instant, from 1-hour bars."""

    def __init__(self) -> None:
        self.gc = _yf("GC=F", period="729d", interval="1h")["Close"].dropna()
        self.inr = _yf("INR=X", period="729d", interval="1h")["Close"].dropna()
        self.start = max(self.gc.index.min(), self.inr.index.min()).tz_convert("UTC")

    def gold_usd(self, t: pd.Timestamp) -> float:
        return asof_intraday(self.gc, t, ONE_H)

    def rs_per_oz(self, t: pd.Timestamp) -> float:
        return asof_intraday(self.gc, t, ONE_H) * asof_intraday(self.inr, t, ONE_H)


def _utc(d: date, hm: tuple[int, int]) -> pd.Timestamp:
    return pd.Timestamp(datetime(d.year, d.month, d.day, *hm, tzinfo=UTC))


# --- R1: ADR 032 lag sweep, aligned -----------------------------------------------------------


def rerun_r1(ix: Intraday) -> dict[str, Any]:
    from ml.inr_proxy_labels import _new_value_direction_pairs

    lab = pd.read_parquet(ROOT / "data" / "history_seed_inr22k_label.parquet")
    lab.index = pd.to_datetime(lab.index).tz_convert(None).normalize()
    ib = pd.read_parquet(ROOT / "data" / "ibja_rates.parquet").dropna(subset=["pm_916"])
    ibja = pd.Series(ib["pm_916"].to_numpy(dtype=float), index=pd.to_datetime(ib["date"]))
    ibja = ibja[ibja.index >= ix.start.tz_convert(None).normalize() + pd.Timedelta(days=1)]
    # daily-close candidates exactly as ADR 032 built them (raw_pre_duty = same-day settle x FX)
    raw = lab["raw_pre_duty"]
    aligned = pd.Series(
        [ix.rs_per_oz(known_at(d.date(), IBJA_PM)) for d in ibja.index], index=ibja.index
    )
    cands = {
        "lag0_same_day_close (ADR 032 label)": raw,
        "lag-1_prev_day_close (ADR 030 feature)": raw.shift(1),
        "aligned_at_ibja_pm_11:30_utc": aligned,
    }
    out: dict[str, Any] = {}
    signs: dict[str, pd.Series] = {}
    for name, c in cands.items():
        for rule in ("adr032_consecutive_rows", "consecutive_busdays_le_2"):
            p = _new_value_direction_pairs(c, ibja)
            p = p[p["d_ibja"] != 0]
            if rule == "consecutive_busdays_le_2":
                prev = ibja.index.to_series().shift(1).reindex(p.index)
                bd = np.busday_count(
                    prev.to_numpy().astype("datetime64[D]"),
                    p.index.to_numpy().astype("datetime64[D]"),
                )
                p = p[bd <= 2]
            agree = np.sign(p["d_candidate"]) == np.sign(p["d_ibja"])
            k, n = int(agree.sum()), len(p)
            out[f"{name} | {rule}"] = {
                "n": n,
                "agreement": k / n if n else None,
                "wilson95": list(wilson_ci(k, n)) if n else None,
                "corr_of_changes": float(np.corrcoef(p["d_candidate"], p["d_ibja"])[0, 1])
                if n > 2
                else None,
            }
            if rule == "adr032_consecutive_rows":
                signs[name] = agree
    a, b = signs["aligned_at_ibja_pm_11:30_utc"], signs["lag0_same_day_close (ADR 032 label)"]
    common = a.index.intersection(b.index)
    a, b = a.loc[common], b.loc[common]
    out["mcnemar_aligned_beats_lag0"] = one_sided_mcnemar(int((a & ~b).sum()), int((~a & b).sum()))
    out["window"] = [str(ibja.index.min().date()), str(ibja.index.max().date())]
    return out


# --- R2: nowcast M4 with an aligned global move ------------------------------------------------


def _board_first_seen() -> pd.Series:
    """For each UTC date (the nowcast's truth convention), when the scored value (the day's last
    reading) was FIRST seen in the contiguous run of identical readings ending at it."""
    raw = json.loads((ROOT / "data" / "prices.json").read_text(encoding="utf-8"))
    df = pd.DataFrame(
        [(pd.Timestamp(r["timestamp"]), float(r["22k"])) for r in raw if r.get("22k") is not None],
        columns=["ts", "p"],
    ).sort_values("ts")
    df["run"] = (df["p"] != df["p"].shift(1)).cumsum()
    first_ts = df.groupby("run")["ts"].transform("min")
    df["first_seen"] = first_ts
    df["date"] = pd.to_datetime(df["ts"].dt.strftime("%Y-%m-%d"))
    return df.groupby("date")["first_seen"].last()


def rerun_r2(ix: Intraday) -> dict[str, Any]:
    nc = _load_script("analysis_nowcast")
    truth = nc.load_truth()
    ibja = nc.load_ibja()
    start = (truth.index.min() - pd.Timedelta(days=40)).strftime("%Y-%m-%d")
    end = (truth.index.max() + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    m = nc.build(truth, ibja, nc.load_global(start, end))
    preds = nc.predict_all(m)
    seen = _board_first_seen()
    mv_al = []
    for d, i, gd in zip(m["date"], m["ibja_date"], m["gap_days"], strict=True):
        if gd == 0:
            mv_al.append(1.0)
            continue
        t_fix = known_at(i.date(), IBJA_PM)
        t_board = seen.get(d, pd.NaT)
        a, b = ix.rs_per_oz(t_board), ix.rs_per_oz(t_fix)
        mv_al.append(a / b if np.isfinite(a) and np.isfinite(b) else np.nan)
    m["move_aligned"] = mv_al
    y = m["t22"].to_numpy()
    p0 = preds["M0_current"]
    p4 = preds["M4_global_move"]
    p4a = p0 * m["move_aligned"].to_numpy()
    out: dict[str, Any] = {"days": [str(m["date"].min().date()), str(m["date"].max().date())]}
    for sname, mask in {
        "carried_forward": (m["gap_days"] > 0).to_numpy(),
        "all": np.ones(len(m), dtype=bool),
    }.items():
        ok = mask & np.isfinite(p0) & np.isfinite(p4) & np.isfinite(p4a)
        e0, e4, e4a = (np.abs(p[ok] - y[ok]) for p in (p0, p4, p4a))
        cell: dict[str, Any] = {
            "n": int(ok.sum()),
            "mae_M0": float(e0.mean()),
            "mae_M4_published_alignment": float(e4.mean()),
            "mae_M4_aligned_move": float(e4a.mean()),
        }
        for tag, e in (("published", e4), ("aligned", e4a)):
            r = diebold_mariano_test(list(e), list(e0), 2, alternative="less")
            cell[f"M4_{tag}_vs_M0"] = {"p_one_sided": r["p_value"], "effective_n": r["effective_n"]}
        r = diebold_mariano_test(list(e4a), list(e4), 2, alternative="less")
        cell["M4_aligned_vs_M4_published"] = {
            "p_one_sided": r["p_value"],
            "effective_n": r["effective_n"],
        }
        out[sname] = cell
    out["n_carried_forward_days_without_intraday_move"] = int(
        ((m["gap_days"] > 0) & ~np.isfinite(m["move_aligned"])).sum()
    )
    return out


# --- R3: derived premium with parity at the fix time -------------------------------------------


def _ar1(pairs: list[tuple[float, float]]) -> tuple[float, float]:
    """ADR 046's fit_ar1 (branch feat/premium-nowcast-prereg), reproduced verbatim in logic."""
    a = np.array([p[0] for p in pairs])
    b = np.array([p[1] for p in pairs])
    mu = float(np.mean(np.r_[a, b]))
    da, db = a - mu, b - mu
    den = float((da * da).sum())
    rho = float((da * db).sum() / den) if den > 0 else 0.0
    return mu, min(1.0, max(0.0, rho))


def _score(x: pd.DataFrame, prem: str, parity_now: str) -> pd.DataFrame:
    dates = list(x.index)
    pairs: list[tuple[float, float]] = []
    rows = []
    for k in range(1, len(dates)):
        t0, t = dates[k - 1], dates[k]
        if int(np.busday_count(t0.date(), t.date())) > 2:
            continue
        p0 = float(x.loc[t0, prem]) / 100
        if len(pairs) >= MIN_PAIRS:
            mu, rho = _ar1(pairs)
            par = float(x.loc[t, parity_now])
            rows.append(
                {
                    "date": t,
                    "actual": float(x.loc[t, "pm_999"]),
                    "B1": par * (1 + p0),
                    "C": par * (1 + mu + rho * (p0 - mu)),
                }
            )
        pairs.append((p0, float(x.loc[t, prem]) / 100))
    return pd.DataFrame(rows).set_index("date")


def rerun_r3(ix: Intraday) -> dict[str, Any]:
    prem = _load_script("analysis_derived_premium")
    table = json.loads(prem.DUTY_TABLE.read_text(encoding="utf-8"))["rows"]
    ibja = pd.read_parquet(prem.IBJA_PATH)
    start = (pd.Timestamp(ibja["date"].min()) - pd.Timedelta(days=15)).date().isoformat()
    end = (pd.Timestamp.today() + pd.Timedelta(days=1)).date().isoformat()
    d = prem.build(table, ibja, prem.load_drivers(start, end))
    d = d[d.index >= ix.start.tz_convert(None).normalize() + pd.Timedelta(days=1)]
    d = d[~d["stale_repeat"]].dropna(subset=["pm_999", "landed_parity", "duty_rate"])
    conv = 10 / TROY_OZ_TO_GRAM
    d["parity_fix"] = [
        ix.rs_per_oz(known_at(t.date(), IBJA_PM)) * conv * (1 + dr)
        for t, dr in zip(d.index, d["duty_rate"], strict=True)
    ]
    d["parity_pre_am"] = [
        ix.rs_per_oz(_utc(t.date(), PRE_AM_UTC)) * conv * (1 + dr)
        for t, dr in zip(d.index, d["duty_rate"], strict=True)
    ]
    d["premium_fix_pct"] = (d["pm_999"] / d["parity_fix"] - 1) * 100
    d = d.dropna(subset=["parity_fix", "parity_pre_am"])
    seg = d.index.to_series().diff().dt.days.gt(4).cumsum()

    def ar1_of(col: str) -> dict[str, Any]:
        pairs = [
            (a, b)
            for _, g in d[col].groupby(seg)
            for a, b in zip(g.to_numpy()[:-1], g.to_numpy()[1:], strict=True)
        ]
        a, b = np.array(pairs).T
        a, b = a - a.mean(), b - b.mean()
        return {
            "n_pairs": len(pairs),
            "ar1": float((a * b).sum() / math.sqrt((a * a).sum() * (b * b).sum())),
            "sd_pct": float(d[col].std()),
        }

    out: dict[str, Any] = {
        "window": [str(d.index.min().date()), str(d.index.max().date())],
        "premium_published_t-1_close": ar1_of("premium_pct"),
        "premium_aligned_at_fix": ar1_of("premium_fix_pct"),
    }
    s_pub = _score(d, "premium_pct", "landed_parity")
    s_al = _score(d, "premium_fix_pct", "parity_pre_am")
    common = s_pub.index.intersection(s_al.index)
    for tag, s in (("published_convention", s_pub.loc[common]), ("aligned", s_al.loc[common])):
        eb = (s["B1"] - s["actual"]).abs().to_numpy() / 10
        ec = (s["C"] - s["actual"]).abs().to_numpy() / 10
        r = diebold_mariano_test(list(ec), list(eb), 2, alternative="less")
        out[f"H1_C_vs_B1_{tag}"] = {
            "n": len(s),
            "mae_B1_rs_per_g_999": float(eb.mean()),
            "mae_C_rs_per_g_999": float(ec.mean()),
            "p_one_sided": r["p_value"],
            "effective_n": r["effective_n"],
        }
    return out


# --- audit table (reasoned; evidence in ADR 058) -----------------------------------------------

AUDIT_TABLE: list[dict[str, str]] = json.loads(
    (ROOT / "reports" / "timing_audit" / "audit_table.json").read_text(encoding="utf-8")
)["rows"]


def main() -> int:
    ix = Intraday()
    res: dict[str, Any] = {
        "adr": "058 Part 1",
        "intraday_window_start_utc": str(ix.start),
        "clock_evidence": clock_evidence(),
        "audit_table": AUDIT_TABLE,
        "reruns_exploratory": {
            "R1_adr032_lag_sweep": rerun_r1(ix),
            "R2_nowcast_weekend_global_move": rerun_r2(ix),
            "R3_derived_premium_adr046": rerun_r3(ix),
        },
        "git_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
        ).stdout.strip(),
        "generated_at_utc": datetime.now(UTC).isoformat(),
    }
    OUT.write_text(json.dumps(res, indent=1, default=str) + "\n", encoding="utf-8")
    print(json.dumps(res["reruns_exploratory"], indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
