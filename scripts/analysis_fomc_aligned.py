"""scripts/analysis_fomc_aligned.py -- F4 re-run for FOMC with correct timing (ADR 058 Part 2).

Pre-registered in docs/adr/058-timing-audit.md ("Part 2 pre-registration") BEFORE this script was
run on any price data. Does move SIZE spike on the first session whose price could reflect the
14:00 ET FOMC decision, per market?

  C1 comex  GC=F daily settlement (13:30 ET, roll-adjusted): first settlement after the decision
            = the next trading day. CONTAMINATED: ADR 050's measurement audit already saw next-day
            medians on 2000-2026, so C1 is a replication, never a confirmation.
  C2 gld    GLD NYSE close (16:00 ET): the decision day's own close.
  C3 ibja   IBJA 22K PM fix (17:00 IST): the first PM publication after the decision, vs the
            previous PM publication (consecutive-business-day pairs only, ADR 042 rule).
  retail    Tanishq 22K board, first IST day whose board (set ~11:00 IST) follows the decision.
            Descriptive only (n ~ 3), outside the test family.

Metric |ln(event value / previous value)|. Normal pool: same series' sessions in the window, minus
any session dated within +/-1 calendar day of any event (FOMC, CPI, jobs, budget; ADR 050 rule),
minus the event sessions. Zero-change IBJA pairs (stale repeats) are dropped from both pools.
Test: OLS of |r| on an event dummy with Newey-West (Bartlett, lag 5) variance, one-sided H1
event > normal; effective n = n_event x iid variance / HAC variance of the coefficient.
Secondary: ADR 050's block bootstrap (events i.i.d., normal days moving blocks of 5, 2000 reps,
seed 42). Gate per cell: HAC p <= 0.05/3 (Bonferroni, m = 3) AND ratio of means >= 1.2 (ADR 050).
BH over the 3 cells reported alongside. A cell with fewer than 8 event sessions is "not testable".
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.direction.stats_corrections import benjamini_hochberg, bonferroni
from ml.timing_alignment import (
    COMEX_SETTLE,
    FOMC_DECISION,
    GLD_CLOSE,
    IBJA_PM,
    TANISHQ_BOARD,
    at_utc,
    first_bar_after,
    utc_to_ist_date,
)

EVENTS_PATH = ROOT / "reports" / "timing_audit" / "event_dates_adr050.json"
OUT = ROOT / "reports" / "timing_audit" / "fomc_aligned.json"

WINDOW_START = "2013-01-01"  # every scheduled statement since 2013 is released at 14:00 ET
WINDOW_END = "2026-09-24"  # last data day at the freeze
# Unscheduled actions whose announcement time is not 14:00 ET (federalreserve.gov):
# 2019-10-11 statement after the Oct 4 call, 2020-03-03 (10:00 ET), 2020-03-15 (Sunday).
EXCLUDED_UNSCHEDULED = {"2019-10-11", "2020-03-03", "2020-03-15"}
# Missing from the ADR 050 calendar; federalreserve.gov/monetarypolicy/fomchistorical2019.htm
# lists "September 17-18 Meeting - 2019" (regular). Added here, flagged in ADR 058.
ADDED_SCHEDULED = {"2019-09-18"}

ALPHA = 0.05
M_FAMILY = 3
RATIO_GATE = 1.2
MIN_EVENTS = 8
HAC_LAG = 5
N_BOOT = 2000
BLOCK = 5
SEED = 42
IBJA_MAX_BUSDAYS = 2  # ADR 042 consecutive-publication rule
MAX_LAG_DAYS = 4  # a first session more than 4 calendar days after the decision is a data gap


# --- events ------------------------------------------------------------------------------------


def load_events() -> tuple[list[date], set[str]]:
    """(FOMC decision dates in the window, every event date of every type for the exclusion)."""
    raw = json.loads(EVENTS_PATH.read_text(encoding="utf-8"))["dates"]
    fomc = (set(raw["fomc_decision"]) | ADDED_SCHEDULED) - EXCLUDED_UNSCHEDULED
    fomc_dates = sorted(date.fromisoformat(d) for d in fomc if WINDOW_START <= d <= WINDOW_END)
    every = set(ADDED_SCHEDULED) | {d for v in raw.values() for d in v}
    return fomc_dates, every


def exclusion(every: set[str]) -> set[date]:
    out: set[date] = set()
    for s in every:
        c = date.fromisoformat(s)
        out |= {c - timedelta(days=1), c, c + timedelta(days=1)}
    return out


# --- series ------------------------------------------------------------------------------------


def load_yahoo() -> tuple[pd.Series, pd.Series]:
    """(roll-adjusted GC=F on genuine COMEX days, GLD on genuine NYSE days), date-indexed."""
    from ml.inr_proxy import _detect_and_adjust_rolls
    from ml.macro import _download_with_retry

    end = (date.fromisoformat(WINDOW_END) + timedelta(days=5)).isoformat()
    raw = _download_with_retry(["GC=F", "GLD"], start="2012-09-01", end=end)
    raw.index = pd.to_datetime(raw.index).tz_localize(None).normalize()
    full = pd.date_range(raw.index.min(), raw.index.max(), freq="D")
    gc_raw = raw[("Close", "GC=F")].reindex(full)
    gld_raw = raw[("Close", "GLD")].reindex(full)
    adj, _ = _detect_and_adjust_rolls(gc_raw.ffill(), gld_raw.ffill())
    gc = adj[gc_raw.notna()]
    gld = gld_raw.dropna()
    return gc, gld


def load_ibja_pm() -> pd.Series:
    ib = pd.read_parquet(ROOT / "data" / "ibja_rates.parquet").dropna(subset=["pm_916"])
    s = pd.Series(ib["pm_916"].to_numpy(dtype=float), index=pd.to_datetime(ib["date"]))
    return s.sort_index()


def load_tanishq_boards() -> pd.Series:
    raw = json.loads((ROOT / "data" / "prices.json").read_text(encoding="utf-8"))
    rows = [
        (utc_to_ist_date(pd.Timestamp(r["timestamp"])), pd.Timestamp(r["timestamp"]), r["22k"])
        for r in raw
        if r.get("22k") is not None
    ]
    df = pd.DataFrame(rows, columns=["ist_date", "ts", "p"]).sort_values("ts")
    last = df.groupby("ist_date")["p"].last()
    return pd.Series(last.to_numpy(dtype=float), index=pd.to_datetime(list(last.index)))


# --- pools -------------------------------------------------------------------------------------


def pools(
    series: pd.Series,
    event_sessions: list[pd.Timestamp],
    excluded: set[date],
    consecutive_busdays: int | None = None,
    drop_zero: bool = False,
) -> pd.DataFrame:
    """One row per session in the window: |log return| vs the previous session, event flag.
    Normal rows inside the exclusion set are removed; event rows are always kept."""
    s = series[(series.index >= pd.Timestamp(WINDOW_START) - pd.Timedelta(days=10))]
    s = s[s.index <= pd.Timestamp(WINDOW_END)]
    prev_idx = s.index[:-1]
    cur_idx = s.index[1:]
    r = np.abs(np.log(s.to_numpy()[1:] / s.to_numpy()[:-1]))
    df = pd.DataFrame({"prev": prev_idx, "abs_ret": r}, index=cur_idx)
    df = df[df.index >= pd.Timestamp(WINDOW_START)]
    if consecutive_busdays is not None:
        bd = np.busday_count(
            df["prev"].to_numpy().astype("datetime64[D]"),
            df.index.to_numpy().astype("datetime64[D]"),
        )
        df = df[bd <= consecutive_busdays]
    if drop_zero:
        df = df[df["abs_ret"] > 0]
    ev = set(event_sessions)
    df["event"] = [d in ev for d in df.index]
    keep = df["event"] | ~pd.Series([d.date() in excluded for d in df.index], index=df.index)
    return df[keep]


# --- tests -------------------------------------------------------------------------------------


def hac_event_test(df: pd.DataFrame) -> dict[str, Any]:
    """OLS |r| = a + b * event with Newey-West (Bartlett, HAC_LAG) variance; one-sided b > 0."""
    y = df["abs_ret"].to_numpy(dtype=float)
    x = df["event"].to_numpy(dtype=float)
    n = len(y)
    X = np.column_stack([np.ones(n), x])
    xtx_inv = np.linalg.inv(X.T @ X)
    beta = xtx_inv @ X.T @ y
    u = y - X @ beta
    xu = X * u[:, None]
    s0 = xu.T @ xu
    s = s0.copy()
    for k in range(1, HAC_LAG + 1):
        g = xu[k:].T @ xu[:-k]
        s += (1 - k / (HAC_LAG + 1)) * (g + g.T)
    dof = n / (n - 2)
    var_hac = float((xtx_inv @ s @ xtx_inv)[1, 1] * dof)
    var_iid = float((xtx_inv @ s0 @ xtx_inv)[1, 1] * dof)
    se = math.sqrt(max(var_hac, 1e-30))
    n_ev = int(x.sum())
    return {
        "diff_mean": float(beta[1]),
        "hac_se": se,
        "z": float(beta[1] / se),
        "p_one_sided_hac": float(norm.sf(beta[1] / se)),
        "ci95_diff": [float(beta[1] - 1.96 * se), float(beta[1] + 1.96 * se)],
        "effective_n_event": float(n_ev * var_iid / var_hac) if var_hac > 0 else float(n_ev),
    }


def _boot_means(v: np.ndarray, block: int, rng: np.random.Generator) -> np.ndarray:
    n = len(v)
    k = int(np.ceil(n / block))
    out = np.empty(N_BOOT)
    for b in range(N_BOOT):
        starts = rng.integers(0, n, size=k)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n] % n
        out[b] = v[idx].mean()
    return out


def block_bootstrap_p(ev: np.ndarray, normal: np.ndarray) -> float:
    rng = np.random.default_rng(SEED)
    diffs = _boot_means(ev, 1, rng) - _boot_means(normal, BLOCK, rng)
    return float(np.mean(diffs <= 0))


def cell(name: str, df: pd.DataFrame, series_note: str) -> dict[str, Any]:
    ev = df.loc[df["event"], "abs_ret"].to_numpy()
    nm = df.loc[~df["event"], "abs_ret"].to_numpy()
    out: dict[str, Any] = {
        "cell": name,
        "series": series_note,
        "n_event": len(ev),
        "n_normal": len(nm),
        "event_sessions": [d.date().isoformat() for d in df.index[df["event"]]],
    }
    if len(ev) == 0 or len(nm) == 0:
        out["testable"] = False
        return out
    out.update(
        {
            "mean_abs_event_pct": float(ev.mean() * 100),
            "mean_abs_normal_pct": float(nm.mean() * 100),
            "median_abs_event_pct": float(np.median(ev) * 100),
            "median_abs_normal_pct": float(np.median(nm) * 100),
            "ratio_of_means": float(ev.mean() / nm.mean()),
            "ratio_of_medians": float(np.median(ev) / np.median(nm)),
            "testable": len(ev) >= MIN_EVENTS,
        }
    )
    if out["testable"]:
        out.update(hac_event_test(df))
        out["p_one_sided_block_bootstrap"] = block_bootstrap_p(ev, nm)
    return out


# --- main --------------------------------------------------------------------------------------


def run() -> dict[str, Any]:
    fomc, every = load_events()
    excl = exclusion(every)
    decisions = {d: at_utc(d, FOMC_DECISION) for d in fomc}
    gc, gld = load_yahoo()
    cells: list[dict[str, Any]] = []

    for name, s, clock, note in (
        ("C1_comex", gc, COMEX_SETTLE, "GC=F settlement 13:30 ET, roll-adjusted (contaminated)"),
        ("C2_gld", gld, GLD_CLOSE, "GLD NYSE close 16:00 ET"),
    ):
        days = [d.date() for d in s.index]
        sess = [(d, first_bar_after(ev, days, clock)) for d, ev in decisions.items()]
        ev_ts = [pd.Timestamp(x) for d, x in sess if x is not None and (x - d).days <= MAX_LAG_DAYS]
        cells.append(cell(name, pools(s, ev_ts, excl), note))

    ib = load_ibja_pm()
    ib_days = [d.date() for d in ib.index]
    ib_sess = [(d, first_bar_after(ev, ib_days, IBJA_PM)) for d, ev in decisions.items()]
    ib_ts = [pd.Timestamp(x) for d, x in ib_sess if x is not None and (x - d).days <= MAX_LAG_DAYS]
    ib_df = pools(ib, ib_ts, excl, consecutive_busdays=IBJA_MAX_BUSDAYS, drop_zero=True)
    cells.append(cell("C3_ibja_pm", ib_df, "IBJA 22K PM fix 17:00 IST, consecutive pairs"))

    ps = [c.get("p_one_sided_hac") for c in cells]
    bon = bonferroni([p if p is not None else 1.0 for p in ps], ALPHA)
    bh = benjamini_hochberg([p if p is not None else 1.0 for p in ps], ALPHA)
    for c, b_ok, h_ok in zip(cells, bon["significant"], bh["significant"], strict=True):
        c["bonferroni_significant"] = bool(c.get("testable") and b_ok)
        c["bh_significant"] = bool(c.get("testable") and h_ok)
        c["passes_gate"] = bool(
            c["bonferroni_significant"] and c.get("ratio_of_means", 0.0) >= RATIO_GATE
        )
        if not c.get("testable"):
            c["verdict"] = "not testable (n_event < 8)"
        elif c["passes_gate"]:
            c["verdict"] = "passes"
        else:
            c["verdict"] = "fails"

    boards = load_tanishq_boards()
    b_days = [d.date() for d in boards.index]
    retail = []
    for d, ev in decisions.items():
        s_day = first_bar_after(ev, b_days, TANISHQ_BOARD)
        if s_day is None or s_day <= b_days[0] or (s_day - d).days > MAX_LAG_DAYS:
            continue
        pos = b_days.index(s_day)
        r = abs(math.log(boards.iloc[pos] / boards.iloc[pos - 1]))
        retail.append(
            {"decision": d.isoformat(), "board_ist_date": s_day.isoformat(), "abs_ret_pct": r * 100}
        )
    rb = np.abs(np.diff(np.log(boards.to_numpy())))
    rb_dates = boards.index[1:]
    normal_rb = [v for v, d in zip(rb, rb_dates, strict=True) if d.date() not in excl and v == v]
    return {
        "adr": "058 Part 2",
        "window": [WINDOW_START, WINDOW_END],
        "n_fomc_decisions_in_window": len(fomc),
        "excluded_unscheduled": sorted(EXCLUDED_UNSCHEDULED),
        "added_scheduled": sorted(ADDED_SCHEDULED),
        "gate": f"HAC one-sided p <= {ALPHA}/{M_FAMILY} AND ratio of means >= {RATIO_GATE}",
        "cells": cells,
        "retail_descriptive": {
            "series": "Tanishq 22K board, last reading per IST date (prices.json)",
            "events": retail,
            "normal_median_abs_ret_pct": float(np.median(normal_rb) * 100) if normal_rb else None,
            "normal_n": len(normal_rb),
        },
        "git_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
        ).stdout.strip(),
        "generated_at_utc": datetime.now(UTC).isoformat(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    res = run()
    args.out.write_text(json.dumps(res, indent=1, default=str) + "\n", encoding="utf-8")
    for c in res["cells"]:
        print(
            c["cell"],
            c["n_event"],
            c.get("ratio_of_means"),
            c.get("p_one_sided_hac"),
            c.get("verdict"),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
