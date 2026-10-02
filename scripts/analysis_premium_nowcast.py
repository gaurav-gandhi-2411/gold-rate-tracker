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

Addendum A1, option A (adopted 2026-09-25, ADR 046): the confirmatory run reads both parities on
the fix clock instead of from day t-1 closes (--parity-clock fix_1h, the default):
  parity(t) = GC=F x INR=X from the last Yahoo 1-hour bars that ENDED by 06:30 UTC on t (AM fix),
  p(t')     = IBJA PM(t') / (the same product at 11:30 UTC on t' (PM fix), times duty) - 1.
Every bar used is saved to BARS_ARCHIVE (append-only, never rewritten; archived bars win over a
re-fetch), because Yahoo drops 1-hour bars after 730 days. That file holds raw Yahoo prices: it is
gitignored and must only ever be committed encrypted (E1, ADR 060). --parity-clock t-1_close
reproduces the registered exploratory run and can never be confirmatory.
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

# Addendum A1 option A (adopted 2026-09-25): parity is read at the fix, from 1-hour bars.
PARITY_CLOCKS = ("fix_1h", "t-1_close")
AM_CUTOFF_UTC = (6, 30)  # IBJA AM ~12:00 IST
PM_CUTOFF_UTC = (11, 30)  # IBJA PM ~17:00 IST
BAR_LENGTH = pd.Timedelta(hours=1)  # Yahoo labels intraday bars by their START time
TICKERS = ("GC=F", "INR=X")
TROY_OZ_TO_GRAM = 31.1034768  # same constant as scripts/analysis_derived_premium.py
BARS_ARCHIVE = ROOT / "data" / "premium_nowcast_bars.json"  # raw Yahoo prices: E1, gitignored


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


def bar_known_by(bars: pd.Series, t_utc: pd.Timestamp) -> dict[str, Any] | None:
    """The last bar (labelled by START time) that ENDED at or before t_utc, i.e. the latest
    price known at t. None if no bar had ended by then."""
    b = bars.dropna()
    if b.empty:
        return None
    idx = pd.DatetimeIndex(b.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    pos = int((idx + BAR_LENGTH).searchsorted(t_utc, side="right")) - 1
    if pos < 0:
        return None
    return {"bar_start_utc": idx[pos].isoformat(), "close": float(b.to_numpy()[pos])}


def _cutoff(day: pd.Timestamp, hm: tuple[int, int]) -> pd.Timestamp:
    return pd.Timestamp(day.year, day.month, day.day, hm[0], hm[1], tz="UTC")


def fix_clock_bars(
    dates: list[pd.Timestamp], bars: dict[str, pd.Series], archive: dict[str, Any]
) -> tuple[dict[str, Any], int]:
    """Bars used at each date's AM and PM cutoffs. Archived entries are kept as they are; only
    missing (date, fix) entries are filled from `bars`, and only when both tickers have a bar.
    Returns (updated archive dates, number of archived entries that disagree with a re-fetch of
    the same bar)."""
    out: dict[str, Any] = {k: dict(v) for k, v in archive.items()}
    mismatches = 0
    for day in dates:
        key = str(day.date())
        entry = out.setdefault(key, {})
        for fix, hm in (("am", AM_CUTOFF_UTC), ("pm", PM_CUTOFF_UTC)):
            got = {
                tk: bar_known_by(bars.get(tk, pd.Series(dtype=float)), _cutoff(day, hm))
                for tk in TICKERS
            }
            fresh = {k: v for k, v in got.items() if v is not None} if all(got.values()) else None
            if fix in entry:
                if fresh is not None and any(
                    fresh[tk]["bar_start_utc"] == entry[fix][tk]["bar_start_utc"]
                    and abs(fresh[tk]["close"] / entry[fix][tk]["close"] - 1) > 1e-9
                    for tk in TICKERS
                ):
                    mismatches += 1
            elif fresh is not None:
                entry[fix] = fresh
        if not entry:
            del out[key]
    return out, mismatches


def apply_fix_clock(d: pd.DataFrame, archive: dict[str, Any]) -> pd.DataFrame:
    """Replace landed_parity (AM-cutoff parity on t) and premium_pct (IBJA PM over the PM-cutoff
    parity of the same day) with option A's fix-clock values. Days without bars become NaN and
    drop out exactly as days without parity do in the registered test."""
    conv = 10 / TROY_OZ_TO_GRAM

    def parity(day: pd.Timestamp, fix: str) -> float:
        e = archive.get(str(day.date()), {}).get(fix)
        if e is None:
            return float("nan")
        return float(e["GC=F"]["close"] * e["INR=X"]["close"] * conv)

    x = d.copy()
    duty = 1 + x["duty_rate"]
    x["landed_parity"] = pd.Series([parity(t, "am") for t in x.index], index=x.index) * duty
    pm_parity = pd.Series([parity(t, "pm") for t in x.index], index=x.index) * duty
    x["premium_pct"] = (x["pm_999"] / pm_parity - 1) * 100
    return x


def _fetch_hourly(ticker: str) -> pd.Series:
    import yfinance as yf

    df = yf.download(
        ticker, period="729d", interval="1h", auto_adjust=True, progress=False, threads=False
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df["Close"].dropna() if "Close" in df else pd.Series(dtype=float)


def load_archive(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return dict(json.loads(path.read_text(encoding="utf-8"))["dates"])


def save_archive(path: Path, dates: dict[str, Any]) -> None:
    doc = {
        "schema": 1,
        "about": "ADR 046 addendum A1 option A: Yahoo 1-hour bars (Close; bar_start_utc is the "
        "bar's START) used for parity at 06:30 UTC (am) and 11:30 UTC (pm). Append-only. Raw "
        "third-party prices: commit only encrypted (E1, ADR 060).",
        "dates": dict(sorted(dates.items())),
    }
    path.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")


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
    ap.add_argument("--parity-clock", choices=PARITY_CLOCKS, default="fix_1h")
    ap.add_argument("--bars-archive", type=Path, default=BARS_ARCHIVE)
    args = ap.parse_args()
    prem = _premium_module()
    table = json.loads(prem.DUTY_TABLE.read_text(encoding="utf-8"))["rows"]
    ibja = pd.read_parquet(prem.IBJA_PATH)
    start = (pd.Timestamp(ibja["date"].min()) - pd.Timedelta(days=15)).date().isoformat()
    end = (pd.Timestamp.today() + pd.Timedelta(days=1)).date().isoformat()
    d = prem.build(table, ibja, prem.load_drivers(start, end))
    extra: dict[str, Any] = {"parity_clock": args.parity_clock}
    if args.parity_clock == "fix_1h":
        dates = list(d.dropna(subset=["pm_999", "duty_rate"]).index)
        bars = {tk: _fetch_hourly(tk) for tk in TICKERS}
        archive, mismatches = fix_clock_bars(dates, bars, load_archive(args.bars_archive))
        save_archive(args.bars_archive, archive)
        d = apply_fix_clock(d, archive)
        extra |= {"bars_archive": str(args.bars_archive), "archive_refetch_mismatches": mismatches}
    s = score_frame(d)
    s = s[s.index > pd.Timestamp(args.since)] if len(s) else s
    res = {
        "since": args.since,
        **extra,
        "confirmatory": args.since == REGISTERED_AFTER and args.parity_clock == "fix_1h",
        "confirmatory_read_allowed": args.since == REGISTERED_AFTER
        and args.parity_clock == "fix_1h"
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
