"""scripts/analysis_weekly_range.py -- measure the weekly price range (ml.weekly_range) on real IBJA.

Two out-of-sample views per horizon ("1d", "week"), both at nominal 80%:
  * walk-forward: every window's conformal scale comes only from windows that ended before it;
  * chronological hold-out: the scale is frozen from the first half of the scored windows and
    applied unchanged to the second half (no calibration data from the test half at all).
Reported for the calibrated range and for raw historical simulation (scale 1) on the same days:
coverage with a Wilson 95% CI, Kupiec's test, mean width. The weekly windows overlap (a new one
starts every publication day), so their hits are correlated; a non-overlapping subsample (every
5th window) is reported alongside, and it is the honest n for the weekly figure.

Usage: python scripts/analysis_weekly_range.py [--out reports/weekly_range_results.json]
"""

from __future__ import annotations

import argparse
import json
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

from ml.range_forecast.data import load_ibja_price_series, load_proxy_price_series
from ml.range_forecast.metrics import kupiec_pof_test, wilson_ci
from ml.weekly_range import HORIZONS, LEVEL, conformal_scale, times_out_of_ten, walk_forward

STRIDE = 5  # publication days per week: every 5th weekly window does not overlap the previous one


def coverage_block(hits: np.ndarray, widths: np.ndarray) -> dict[str, Any]:
    n = len(hits)
    k = int(hits.sum())
    lo, hi = wilson_ci(k, n)
    return {
        "n": n,
        "coverage": k / n if n else None,
        "wilson_95": [lo, hi],
        "kupiec_p": kupiec_pof_test(~hits.astype(bool), 1.0 - LEVEL)["p_value"] if n else None,
        "mean_width_pct": float(np.mean(widths)) if n else None,
        "times_out_of_10": times_out_of_ten(k / n) if n else None,
    }


def evaluate(df: pd.DataFrame, horizon: str) -> dict[str, Any]:
    scored = df[df["scale"].notna()].reset_index(drop=True)
    hits = scored["hit"].to_numpy(dtype=bool)
    raw = scored["raw_hit"].to_numpy(dtype=bool)
    out: dict[str, Any] = {
        "first_as_of": str(scored["as_of"].min().date()),
        "last_as_of": str(scored["as_of"].max().date()),
        "walk_forward": {
            "calibrated": coverage_block(hits, scored["width_pct"].to_numpy(dtype=float)),
            "raw_historical_simulation": coverage_block(
                raw, scored["raw_width_pct"].to_numpy(dtype=float)
            ),
        },
    }
    if horizon == "week":
        sub = scored.iloc[::STRIDE]
        out["walk_forward_non_overlapping"] = {
            "calibrated": coverage_block(
                sub["hit"].to_numpy(dtype=bool), sub["width_pct"].to_numpy(dtype=float)
            ),
            "raw_historical_simulation": coverage_block(
                sub["raw_hit"].to_numpy(dtype=bool), sub["raw_width_pct"].to_numpy(dtype=float)
            ),
        }
    # Chronological hold-out: freeze the scale from the first half's windows (all of which have
    # ended before the second half starts, for "1d"; for "week" drop the overlap at the seam).
    half = len(scored) // 2
    cal = scored.iloc[:half]
    test = scored.iloc[half:]
    test = test[test["as_of"] > cal["end"].max()]
    s = conformal_scale(cal["score"].tolist())
    if s is not None and len(test):
        hit = (test["score"] <= s).to_numpy(dtype=bool)
        width = (np.exp(s * test["base_hi"]) - np.exp(s * test["base_lo"])).to_numpy() * 100
        out["holdout"] = {
            "calibrated_on": [str(cal["as_of"].min().date()), str(cal["as_of"].max().date())],
            "tested_on": [str(test["as_of"].min().date()), str(test["as_of"].max().date())],
            "frozen_scale": s,
            "calibrated": coverage_block(hit, width),
            "raw_historical_simulation": coverage_block(
                test["raw_hit"].to_numpy(dtype=bool), test["raw_width_pct"].to_numpy(dtype=float)
            ),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "weekly_range_results.json")
    args = ap.parse_args()
    warnings.filterwarnings("ignore")
    proxy = load_proxy_price_series()
    ibja = load_ibja_price_series()
    res: dict[str, Any] = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
        ).stdout.strip(),
        "nominal": LEVEL,
        "truth": "IBJA PM 916 (data/ibja_rates.parquet), complete windows only",
        "horizons": {h: evaluate(walk_forward(proxy, ibja, h), h) for h in HORIZONS},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
