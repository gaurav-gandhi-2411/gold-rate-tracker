"""scripts/analysis_derived_premium.py -- G4b: the derived Indian gold premium.

premium_pct(t) = IBJA 999 PM fix(t) / landed parity(t) - 1, where

    landed parity(t) = COMEX(t-1) [USD/troy oz] / 31.1034768 * 10 * USDINR(t-1)
                       * (1 + effective import duty rate in force on t)

Leakage-safe timing: IBJA's PM fix is published ~17:00 IST on day t. COMEX (GC=F) and USD/INR
(INR=X) daily closes are taken from the PREVIOUS calendar day with data (t-1), the latest values
that were final before either IBJA fix (same rule as ml/inr_proxy.py). The duty rate is the one in
force on t (effective date <= t) from data/duty_cbic.json, where every row cites a CBIC notification.

Known approximations (reported, not hidden):
  * Duty is charged on CBIC's fortnightly TARIFF VALUE, not on COMEX. The duty base here is the
    COMEX price, so the premium absorbs the tariff-value gap (usually within a few percent of the
    price, i.e. a few tenths of a point of premium at a 6-15% duty rate).
  * GC=F is the front-month future, not spot: it carries contango (roughly 0-0.8%), which puts a
    small sawtooth in the premium at each roll.
  * IBJA rates exclude GST, so parity excludes the 3% IGST (creditable against output GST).

Research only: reads the existing Yahoo feeds (same terms risk as the live pipeline), writes
reports/derived_premium.json. It does not touch any live data source or user-facing output.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TROY_OZ_TO_GRAM = 31.1034768
DUTY_TABLE = ROOT / "data" / "duty_cbic.json"
IBJA_PATH = ROOT / "data" / "ibja_rates.parquet"
OUT = ROOT / "reports" / "derived_premium.json"
DENSE_MAX_GAP_DAYS = 4  # same "dense segment" rule as ADR 040 D4 / ADR 039 R3
EVENT_WINDOW_DAYS = 21  # calendar days either side of a duty change


def duty_rate_series(index: pd.DatetimeIndex, table: list[dict[str, Any]]) -> pd.Series:
    """Total ad-valorem import duty (fraction) in force on each date: the latest row with
    effective_date <= date. Dates before the first row are NaN (unknown, never guessed)."""
    rows = sorted(table, key=lambda r: r["effective_date"])
    out = pd.Series(np.nan, index=index, dtype=float)
    for r in rows:
        out[index >= pd.Timestamp(r["effective_date"])] = float(r["total_duty_pct"]) / 100.0
    return out


def load_drivers(start: str, end: str) -> pd.DataFrame:
    """GC=F and INR=X daily closes, on a full calendar, forward-filled, then lagged one day."""
    from ml.macro import _download_with_retry

    raw = _download_with_retry(["GC=F", "INR=X"], start=start, end=end)
    raw.index = pd.to_datetime(raw.index).tz_localize(None).normalize()
    full = pd.date_range(raw.index.min(), raw.index.max(), freq="D")
    df = pd.DataFrame(
        {"comex_usd_oz": raw[("Close", "GC=F")], "usd_inr": raw[("Close", "INR=X")]}
    ).reindex(full)
    df = df.ffill()
    return df.shift(1)  # value known before day t's IBJA fixes


def build(table: list[dict[str, Any]], ibja: pd.DataFrame, drivers: pd.DataFrame) -> pd.DataFrame:
    d = ibja[["date", "pm_999"]].dropna().copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d.drop_duplicates("date").set_index("date").sort_index()
    d = d.join(drivers, how="left")
    d["duty_rate"] = duty_rate_series(pd.DatetimeIndex(d.index), table)
    d["parity_pre_duty"] = d["comex_usd_oz"] / TROY_OZ_TO_GRAM * 10 * d["usd_inr"]
    d["landed_parity"] = d["parity_pre_duty"] * (1 + d["duty_rate"])
    d["premium_pct"] = (d["pm_999"] / d["landed_parity"] - 1) * 100
    gaps = d.index.to_series().diff().dt.days
    d["segment"] = (gaps.isna() | (gaps > DENSE_MAX_GAP_DAYS)).cumsum()
    # a price identical to the previous row is a stale re-publication, not a new fix
    d["stale_repeat"] = d["pm_999"].eq(d["pm_999"].shift(1))
    return d


def _ar1(x: pd.Series, seg: pd.Series) -> dict[str, Any]:
    pairs = [ab for (_, g) in x.groupby(seg) for ab in itertools.pairwise(g)]
    if len(pairs) < 10:
        return {"n_pairs": len(pairs), "ar1": None, "half_life_rows": None}
    a, b = np.array(pairs).T
    a, b = a - a.mean(), b - b.mean()
    rho = float((a * b).sum() / math.sqrt((a * a).sum() * (b * b).sum()))
    hl = math.log(0.5) / math.log(rho) if 0 < rho < 1 else None
    return {"n_pairs": len(pairs), "ar1": rho, "half_life_rows": hl}


def _desc(x: pd.Series) -> dict[str, Any]:
    x = x.dropna()
    if x.empty:
        return {"n": 0}
    return {
        "n": len(x),
        "mean": float(x.mean()),
        "median": float(x.median()),
        "std": float(x.std(ddof=1)) if len(x) > 1 else None,
        "p10": float(x.quantile(0.1)),
        "p90": float(x.quantile(0.9)),
    }


def timing_diagnostic(ok: pd.DataFrame) -> dict[str, Any]:
    """DIAGNOSTIC ONLY (uses a value not known at fix time): how much of the premium is the gold
    move between the t-1 COMEX close and IBJA's fix on t. Proxy for that move: COMEX close on t
    over close on t-1 (the next row's lagged value), consecutive rows (<= 4 days) only."""
    x = ok.copy()
    x["move_pct"] = (x["comex_usd_oz"].shift(-1) / x["comex_usd_oz"] - 1) * 100
    nxt = x.index.to_series().shift(-1)
    keep = ((nxt - x.index.to_series()).dt.days <= DENSE_MAX_GAP_DAYS) & x["move_pct"].notna()
    x = x[keep]
    if len(x) < 20:
        return {"n": len(x)}
    slope, icpt = np.polyfit(x["move_pct"], x["premium_pct"], 1)
    resid = x["premium_pct"] - (slope * x["move_pct"] + icpt)
    r = float(np.corrcoef(x["move_pct"], x["premium_pct"])[0, 1])
    return {
        "n": len(x),
        "corr": r,
        "r2": r * r,
        "slope": float(slope),
        "premium_sd": float(x["premium_pct"].std(ddof=1)),
        "residual_sd": float(resid.std(ddof=1)),
        "residual_persistence": _ar1(resid, x["segment"]),
    }


def summarise(d: pd.DataFrame, table: list[dict[str, Any]]) -> dict[str, Any]:
    from ml.calendar_events import get_festival_info, get_wedding_season_info

    ok = d.dropna(subset=["premium_pct"])
    ok = ok[~ok["stale_repeat"]]
    seg_len = ok.groupby("segment").size()
    dense = ok[ok["segment"].isin(seg_len[seg_len >= 10].index)]
    fest = pd.Series(
        [bool(get_festival_info(t.date()).get("is_festival_window")) for t in dense.index],
        index=dense.index,
    )
    wed = pd.Series(
        [bool(get_wedding_season_info(t.date()).get("is_wedding_season")) for t in dense.index],
        index=dense.index,
    )
    events = []
    for r in sorted(table, key=lambda r: r["effective_date"]):
        t0 = pd.Timestamp(r["effective_date"])
        # rows within EVENT_WINDOW_DAYS calendar days either side; none -> reported as n = 0
        before = ok[(ok.index < t0) & (ok.index >= t0 - pd.Timedelta(days=EVENT_WINDOW_DAYS))]
        after = ok[(ok.index >= t0) & (ok.index < t0 + pd.Timedelta(days=EVENT_WINDOW_DAYS))]
        events.append(
            {
                "effective_date": r["effective_date"],
                "notification": r.get("notification"),
                "total_duty_pct": r["total_duty_pct"],
                "before": _desc(before["premium_pct"]),
                "after": _desc(after["premium_pct"]),
            }
        )
    by_year = {str(y): _desc(g["premium_pct"]) for y, g in ok.groupby(ok.index.year)}
    return {
        "history": {
            "ibja_rows": len(d),
            "usable_rows": len(ok),
            "stale_repeats_dropped": int(d["stale_repeat"].sum()),
            "first": str(ok.index.min().date()) if len(ok) else None,
            "last": str(ok.index.max().date()) if len(ok) else None,
            "dense_segments_ge_10_rows": int((seg_len >= 10).sum()),
            "rows_in_dense_segments": len(dense),
        },
        "premium_pct_all": _desc(ok["premium_pct"]),
        "premium_pct_by_year": by_year,
        "persistence_dense": _ar1(dense["premium_pct"], dense["segment"]),
        "festival_window_dense": {
            "in": _desc(dense["premium_pct"][fest]),
            "out": _desc(dense["premium_pct"][~fest]),
        },
        "wedding_season_dense": {
            "in": _desc(dense["premium_pct"][wed]),
            "out": _desc(dense["premium_pct"][~wed]),
        },
        "duty_change_events": events,
        "timing_diagnostic_not_usable_for_prediction": timing_diagnostic(ok),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duty-table", type=Path, default=DUTY_TABLE)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    table = json.loads(args.duty_table.read_text(encoding="utf-8"))["rows"]
    ibja = pd.read_parquet(IBJA_PATH)
    start = (pd.Timestamp(ibja["date"].min()) - timedelta(days=15)).date().isoformat()
    drivers = load_drivers(start, (date.today() + timedelta(days=1)).isoformat())
    d = build(table, ibja, drivers)
    summary = summarise(d, table)
    series = d.reset_index().rename(columns={"index": "date"})
    series["date"] = series["date"].dt.date.astype(str)
    cols = ["date", "pm_999", "comex_usd_oz", "usd_inr", "duty_rate", "landed_parity"]
    cols += ["premium_pct", "segment", "stale_repeat"]
    out = {
        "definition": "premium_pct = IBJA 999 PM / (COMEX(t-1)/31.1034768*10*USDINR(t-1)"
        "*(1+duty in force on t)) - 1",
        "duty_table": str(args.duty_table.relative_to(ROOT)),
        "summary": summary,
        "series": json.loads(series[cols].to_json(orient="records")),
    }
    args.out.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
