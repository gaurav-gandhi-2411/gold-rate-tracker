"""When is the daily INR=X / GC=F value set? Match each daily value to the nearest hourly bar.

Resolves the open USD/INR clock question for ml.nextfix's leak audit (ADR 058 left it
"not pinned down"; ml.known_at treats it as known only at 23:59 UTC). Needs
data/macro_intraday.parquet (about 60 days of 1-hour bars; written by ``python ml/macro.py``) and
data/macro_cache.parquet. Weekdays only. The matched bar's START hour (UTC) is reported.

    python scripts/audit_inrx_daily_clock.py [--out reports/model_audit_2026-10/inrx_daily_clock.json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent


def match_hours(hourly: pd.Series, daily: pd.Series) -> pd.DataFrame:
    rows = {}
    for day, val in daily.items():
        day = pd.Timestamp(day)
        if day.dayofweek >= 5:
            continue
        w = hourly[(hourly.index >= day) & (hourly.index < day + pd.Timedelta(days=1))]
        if w.empty:
            continue
        diff = (w - val).abs()
        rows[day] = {
            "best_bar_start_hour_utc": int(diff.idxmin().hour),
            "min_gap_bp": float(diff.min() / val * 1e4),
        }
    return pd.DataFrame(rows).T


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/model_audit_2026-10/inrx_daily_clock.json")
    args = ap.parse_args()
    hourly = pd.read_parquet(ROOT / "data" / "macro_intraday.parquet")
    daily = pd.read_parquet(ROOT / "data" / "macro_cache.parquet")
    out: dict = {"hourly_range": [str(hourly.index.min()), str(hourly.index.max())]}
    for col in ("usd_inr", "gold_usd"):
        s = hourly[col].dropna()
        s.index = pd.DatetimeIndex(s.index)
        s.index = s.index.tz_localize("UTC") if s.index.tz is None else s.index.tz_convert("UTC")
        d = daily[col].loc[hourly.index.min().normalize() :]
        r = match_hours(s, d)
        out[col] = {
            "n_days": len(r),
            "start_hour_counts": {
                int(k): int(v)
                for k, v in r["best_bar_start_hour_utc"].value_counts().sort_index().items()
            },
            "median_min_gap_bp": round(float(r["min_gap_bp"].median()), 2),
        }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
