"""scripts/analysis_kalman_nowcast_strict_timing.py -- ADR 055 post-hoc EXPLORATORY sensitivity.

NOT pre-registered; added after the pre-registered run. It checks one timing question. The
pre-registered run gives the filter the same end-of-UTC-day retailer information as the
scorecard's fusion baseline (the last capture of the date). On ~42% of set-B day/source pairs,
that capture post-dates the target Tanishq reading.

This re-runs the identical walk-forward with retailer captures restricted to
capture_utc < the target date's last Tanishq reading timestamp. Everything else is unchanged:
the model, the refits, the baselines and the family.
Output: reports/kalman_nowcast/exploratory_strict_retail_timing.json.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "reports" / "kalman_nowcast" / "exploratory_strict_retail_timing.json"

_spec = importlib.util.spec_from_file_location(
    "analysis_kalman_nowcast", ROOT / "scripts" / "analysis_kalman_nowcast.py"
)
assert _spec is not None and _spec.loader is not None
ak = importlib.util.module_from_spec(_spec)
sys.modules["analysis_kalman_nowcast"] = ak
_spec.loader.exec_module(ak)


def target_timestamps() -> pd.Series:
    """UTC timestamp (naive) of the last Tanishq reading per UTC date."""
    raw = json.loads((ROOT / "data" / "prices.json").read_text(encoding="utf-8"))
    t = pd.DataFrame([r for r in raw if r.get("22k") is not None])
    ts = pd.to_datetime(t["timestamp"]).dt.tz_localize(None)
    return ts.groupby(ts.dt.normalize()).max()


def strict_retail() -> pd.DataFrame:
    tlast = target_timestamps()
    s = pd.read_parquet(ROOT / "data" / "fusion_snapshots.parquet")
    n = s[s["city"].isna() & s["source"].isin(ak.RETAIL_SOURCES)].copy()
    n["cap"] = pd.to_datetime(n["capture_utc"]).dt.tz_localize(None)
    lim = pd.to_datetime(n["as_of_date"]).map(tlast)
    n = n[lim.isna() | (n["cap"] < lim)]
    n = n.sort_values("cap").groupby(["as_of_date", "source"]).last().reset_index()
    oa = pd.to_datetime(n["observed_at"], format="mixed", utc=True)
    return pd.DataFrame(
        {
            "day": pd.to_datetime(n["as_of_date"]),
            "source": n["source"],
            "rate_22k": n["rate_22k"].astype(float),
            "observed_at_day": oa.dt.tz_localize(None).dt.normalize(),
        }
    )


def main() -> int:
    ak.load_retail = strict_retail
    sys.argv = [sys.argv[0], "--out", str(OUT)]
    return int(ak.main())


if __name__ == "__main__":
    raise SystemExit(main())
