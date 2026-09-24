"""scripts/analysis_wait_or_buy.py -- measure F2 "buy now or wait?" (ADR 049).

(a) P(price lower after N than today), on real IBJA and the INR proxy, for
    N in (1, 2, 7): naive + overlap-corrected (effective-n) + non-overlapping-
    stride Wilson intervals, the two-sided HAC P=0.5 test, and Bonferroni/BH
    over the 3-horizon x 2-dataset family.
(b) the calibrated ENDPOINT range's measured coverage on real IBJA: walk-
    forward and chronological hold-out, Wilson 95%, against the nominal 80%.
(c) today's realised-volatility percentile and category (descriptive check,
    not a hypothesis test -- see ADR 049).

Usage: python scripts/analysis_wait_or_buy.py [--out reports/wait_or_buy_results.json]
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

from ml.direction.stats_corrections import benjamini_hochberg, bonferroni
from ml.range_forecast.data import load_ibja_price_series, load_proxy_price_series
from ml.range_forecast.metrics import kupiec_pof_test, wilson_ci
from ml.wait_or_buy import (
    N_VALUES,
    endpoint_walk_forward,
    outcomes_frame,
    prob_lower_stats,
    realized_vol_today,
    rolling_realized_vol,
    vol_category,
    vol_percentile,
)
from ml.weekly_range import LEVEL, conformal_scale

STRIDE_NON_OVERLAP = 5  # matches scripts/analysis_weekly_range.py's "week" convention


def _coverage_block(hits: np.ndarray, widths: np.ndarray) -> dict[str, Any]:
    n = len(hits)
    k = int(hits.sum())
    lo, hi = wilson_ci(k, n)
    return {
        "n": n,
        "coverage": k / n if n else None,
        "wilson_95": [lo, hi],
        "kupiec_p": kupiec_pof_test(~hits.astype(bool), 1.0 - LEVEL)["p_value"] if n else None,
        "mean_width_pct": float(np.mean(widths)) if n else None,
        "meets_80pct": (hi >= 0.80) if n else None,
    }


def _range_evaluate(df: pd.DataFrame) -> dict[str, Any]:
    scored = df[df["scale"].notna()].reset_index(drop=True)
    if scored.empty:
        return {"n": 0}
    hits = scored["hit"].to_numpy(dtype=bool)
    out: dict[str, Any] = {
        "first_as_of": str(scored["as_of"].min().date()),
        "last_as_of": str(scored["as_of"].max().date()),
        "walk_forward": _coverage_block(hits, scored["width_pct"].to_numpy(dtype=float)),
    }
    sub = scored.iloc[::STRIDE_NON_OVERLAP]
    out["walk_forward_non_overlapping"] = _coverage_block(
        sub["hit"].to_numpy(dtype=bool), sub["width_pct"].to_numpy(dtype=float)
    )
    half = len(scored) // 2
    cal, test = scored.iloc[:half], scored.iloc[half:]
    test = test[test["as_of"] > cal["end"].max()] if half else test
    s = conformal_scale(cal["score"].tolist()) if half else None
    if s is not None and len(test):
        hit = (test["score"] <= s).to_numpy(dtype=bool)
        width = (np.exp(s * test["base_hi"]) - np.exp(s * test["base_lo"])).to_numpy() * 100
        out["holdout"] = {
            "calibrated_on": [str(cal["as_of"].min().date()), str(cal["as_of"].max().date())],
            "tested_on": [str(test["as_of"].min().date()), str(test["as_of"].max().date())],
            "frozen_scale": s,
            **_coverage_block(hit, width),
        }
    return out


def _direction_block(ibja: pd.Series, proxy: pd.Series) -> dict[str, Any]:
    cells: dict[str, dict] = {}
    p_values: list[float | None] = []
    cell_keys: list[str] = []
    for n in N_VALUES:
        for name, series in (("real_ibja", ibja), ("proxy", proxy)):
            df = outcomes_frame(series, n)
            stats = prob_lower_stats(df, n)
            key = f"n{n}_{name}"
            cells[key] = stats
            p_values.append(stats.get("p_value_two_sided"))
            cell_keys.append(key)
    bonf = bonferroni(p_values, alpha=0.05)
    bh = benjamini_hochberg(p_values, alpha=0.05)
    for i, key in enumerate(cell_keys):
        cells[key]["bonferroni_significant"] = bonf["significant"][i]
        cells[key]["bh_significant"] = bh["significant"][i]
    return {
        "family_size": len(cell_keys),
        "bonferroni_threshold": bonf["threshold"],
        "cells": cells,
    }


def _range_block(proxy: pd.Series, ibja: pd.Series) -> dict[str, Any]:
    return {f"n{n}": _range_evaluate(endpoint_walk_forward(proxy, ibja, n)) for n in N_VALUES}


def _volatility_block(ibja: pd.Series, proxy: pd.Series) -> dict[str, Any]:
    as_of = ibja.index.max()
    today_vol = realized_vol_today(ibja, as_of)
    ref = rolling_realized_vol(proxy)
    pctile = vol_percentile(ref, as_of, today_vol) if today_vol is not None else None
    return {
        "as_of": str(as_of.date()),
        "realized_vol_20d": today_vol,
        "reference_series": "proxy",
        "reference_n_prior": int((ref.index < as_of).sum()),
        "percentile": pctile,
        "category": vol_category(pctile),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "wait_or_buy_results.json")
    args = ap.parse_args()
    warnings.filterwarnings("ignore")

    proxy = load_proxy_price_series()
    ibja = load_ibja_price_series()

    res: dict[str, Any] = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
        ).stdout.strip(),
        "adr": "docs/adr/049-buy-now-or-wait.md",
        "series": {
            "real_ibja": {
                "n": len(ibja),
                "first": str(ibja.index.min().date()),
                "last": str(ibja.index.max().date()),
            },
            "proxy": {
                "n": len(proxy),
                "first": str(proxy.index.min().date()),
                "last": str(proxy.index.max().date()),
            },
        },
        "component_a_prob_lower": _direction_block(ibja, proxy),
        "component_b_range_coverage": _range_block(proxy, ibja),
        "component_c_volatility_today": _volatility_block(ibja, proxy),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(res, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
