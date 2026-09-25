"""scripts/analysis_markup_reversion.py -- ADR 057 markup-reversion analysis.

    --step 1   persistence diagnostic (data construction; allowed before the freeze)
               -> reports/markup_reversion/step1_persistence.json
    --step 2   pre-registered walk-forward test on all history (run only AFTER the ADR 057
               pre-registration commit is pushed) -> reports/markup_reversion/historical_test.json

Reads only committed repo data (data/prices.json, data/ibja_rates.parquet); no network. Writes
derived numbers only -- no raw retailer price series (GG rule G1c).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.markup_reversion import (
    FORWARD_PRIMARY,
    HORIZONS,
    MIN_TRAILING,
    TRAILING_WINDOW,
    Z_THRESHOLDS,
    apply_gate,
    build_eligible_series,
    evaluate_cell,
    forward_frame,
    ibja_business_days,
    load_default_frame,
    persistence_diagnostic,
    robust_trailing_z,
    select_variant,
)

OUT_DIR = ROOT / "reports" / "markup_reversion"
PREREG_ADR = "docs/adr/057-markup-reversion-preregistration.md"


def _clean(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_clean(v) for v in obj]
    if isinstance(obj, float | np.floating):
        f = float(obj)
        return None if np.isnan(f) else f
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def step1() -> dict[str, Any]:
    frame, ibja_df = load_default_frame()
    bdays = ibja_business_days(ibja_df)
    s = build_eligible_series(frame)
    zs = s["z"].dropna()
    return {
        "adr": PREREG_ADR,
        "label": "step 1 diagnostic -- data construction only, no forward outcome computed",
        "data": {
            "tanishq_first_date": frame["date"].min().isoformat(),
            "tanishq_last_date": frame["date"].max().isoformat(),
            "n_daily_rows": len(frame),
            "n_stale_ibja": int(frame["stale_ibja"].sum()),
            "n_tanishq_repeat": int(frame["tanishq_repeat"].sum()),
            "n_backfill": int(frame["backfill"].sum()),
            "n_weekend": int(frame["weekend"].sum()),
        },
        "persistence": persistence_diagnostic(frame, bdays),
        # Signal frequency (feature only, not an outcome): used to size the forward decision date.
        "signal_frequency_primary_series": {
            "n_rows_with_z": len(zs),
            **{f"share_z_ge_{t:g}": float((zs >= t).mean()) for t in Z_THRESHOLDS},
        },
    }


def step2() -> dict[str, Any]:
    frame, ibja_df = load_default_frame()
    bdays = ibja_business_days(ibja_df)
    out: dict[str, Any] = {
        "adr": PREREG_ADR,
        "label": (
            "historical run on all data -- SEMI-CONFIRMATORY at best: F1 (#2022) computed AR(1) "
            "on this same data before the freeze. The forward shadow is the confirmatory test."
        ),
        "constants": {
            "trailing_window": TRAILING_WINDOW,
            "min_trailing": MIN_TRAILING,
            "z_thresholds": list(Z_THRESHOLDS),
            "horizons_business_days": list(HORIZONS),
            "forward_primary_cell": list(FORWARD_PRIMARY),
        },
    }
    primary = build_eligible_series(frame, "b_same_day_fresh")
    cells = []
    for h in HORIZONS:
        ff = forward_frame(primary, bdays, h)
        for t in Z_THRESHOLDS:
            cells.append(evaluate_cell(ff, t, h))
    out["primary_family"] = apply_gate(cells)

    # Sensitivity (exploratory, NOT in the gated family): variant c and F1's all-rows series.
    sens: dict[str, Any] = {}
    for variant in ("c_no_carry_forward", "a_all_pairs_f1"):
        s = select_variant(frame, variant).sort_values("date").reset_index(drop=True).copy()
        s["z"] = robust_trailing_z(s["markup_pct"].to_numpy(dtype=float))
        cal = bdays if variant != "a_all_pairs_f1" else sorted(s["date"])
        vcells = []
        for h in HORIZONS:
            ff = forward_frame(s, cal, h)
            for t in Z_THRESHOLDS:
                c = evaluate_cell(ff, t, h)
                if variant == "a_all_pairs_f1":
                    wk = ff[ff["date"].map(lambda d: d.weekday() < 5)]
                    we = ff[ff["date"].map(lambda d: d.weekday() >= 5)]
                    c["weekday_only"] = evaluate_cell(wk, t, h)
                    c["weekend_only"] = evaluate_cell(we, t, h)
                vcells.append(c)
        sens[variant] = {
            "horizon_unit": (
                "IBJA business days" if variant != "a_all_pairs_f1" else "rows of the series"
            ),
            "cells": vcells,
        }
    out["sensitivity_exploratory"] = sens
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, choices=(1, 2), required=True)
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.step == 1:
        res, path = step1(), OUT_DIR / "step1_persistence.json"
    else:
        res, path = step2(), OUT_DIR / "historical_test.json"
    path.write_text(json.dumps(_clean(res), indent=1, default=str) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
