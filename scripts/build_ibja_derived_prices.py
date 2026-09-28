"""Rebuild data/prices.json from IBJA x calibration only (retailer takedown, ADR 059).

Step 3 of docs/RETAILER_TAKEDOWN.md for Tanishq. ``prices.json`` is the site's price
history (chart, history table, week/month comparisons, 24K/18K cards) and today it
holds only scraped Tanishq readings. Taking Tanishq down means replacing it with a
series built without Tanishq:

    22K = slope x (IBJA pm_916 / 10) + intercept      (data/calibration.json, frozen)
    24K = 22K x pm_999 / pm_916                       (IBJA's own purity ratio)
    18K = 22K x pm_750 / pm_916

one row per IBJA publishing day, timestamped at IBJA's ~17:00 IST publication
(11:30 UTC), ``source = "ibja_calibrated_derived"``. That source tag is what app.js
keys on to stop labelling the history as Tanishq's ("Tanishq last confirmed", the
hero location line) -- see ``isDerivedReading`` in app.js.

The calibration coefficients were fitted against Tanishq, so the derived series is
Tanishq-*shaped* but contains no Tanishq observation: it cannot be inverted back to
any Tanishq reading (it is a pure function of IBJA plus two constants).

Default is a dry run that prints a summary; ``--write`` replaces the output file.
Never run with --write on master without GG's takedown go-ahead (live data change).

``--public-out PATH`` (GG decision 4c, 2026-09-25) is separate and routine: it writes the
site's trend-chart series, ``data/ibja_derived_prices.json`` -- the same derived 22K rows,
reduced to ``{"timestamp", "22k"}`` (no 24K/18K: the chart plots 22K only, and the karat
ratios would publish IBJA's pm_999/pm_750 ratios for nothing). check-price.yml rebuilds it
every run; it never touches prices.json. The app labels it as our estimate (app.js
``chartSeries``). Note: with the public calibration.json, a 22K row can be inverted back to
IBJA's pm_916 to within rounding -- publishing it is GG's 4c decision, not an oversight.

Usage:
    python scripts/build_ibja_derived_prices.py [--data-dir data] [--out PATH] [--write]
    python scripts/build_ibja_derived_prices.py --public-out data/ibja_derived_prices.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DERIVED_SOURCE = "ibja_calibrated_derived"
# IBJA's PM fix is published ~17:00 IST = 11:30 UTC (same constant as ml/inference.py).
PUBLISH_UTC = "T11:30:00.000Z"


def build_derived_prices(ibja: pd.DataFrame, calibration: dict) -> list[dict]:
    """Pure transform: IBJA rows + calibration -> prices.json-shaped rows (oldest first)."""
    slope = calibration.get("slope")
    intercept = calibration.get("intercept")
    if not isinstance(slope, (int, float)) or not isinstance(intercept, (int, float)):
        raise ValueError("calibration.json has no numeric slope/intercept -- cannot derive")

    rows: list[dict] = []
    df = ibja.dropna(subset=["pm_916"]).copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df = df.sort_values("date").drop_duplicates("date", keep="last")
    for rec in df.itertuples(index=False):
        pm916 = float(rec.pm_916)
        if not math.isfinite(pm916) or pm916 <= 0:
            continue
        k22 = slope * (pm916 / 10.0) + intercept
        pm999 = getattr(rec, "pm_999", float("nan"))
        pm750 = getattr(rec, "pm_750", float("nan"))
        if not (math.isfinite(pm999) and math.isfinite(pm750)):
            continue  # all three karat cards need a value; skip a partial IBJA row
        rows.append(
            {
                "timestamp": f"{rec.date}{PUBLISH_UTC}",
                "22k": round(k22),
                "24k": round(k22 * pm999 / pm916),
                "18k": round(k22 * pm750 / pm916),
                "source": DERIVED_SOURCE,
            }
        )
    return rows


def public_chart_rows(rows: list[dict]) -> list[dict]:
    """The chart series published at data/ibja_derived_prices.json: timestamp + 22K only."""
    return [{"timestamp": r["timestamp"], "22k": r["22k"]} for r in rows]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--out", type=Path, default=None, help="default: <data-dir>/prices.json")
    ap.add_argument("--write", action="store_true", help="replace the output file")
    ap.add_argument(
        "--public-out",
        type=Path,
        default=None,
        help="write the trend-chart series (timestamp + 22k) here instead; never prices.json",
    )
    args = ap.parse_args(argv)

    ibja = pd.read_parquet(args.data_dir / "ibja_rates.parquet")
    calibration = json.loads((args.data_dir / "calibration.json").read_text(encoding="utf-8"))
    rows = build_derived_prices(ibja, calibration)
    if len(rows) < 2:
        print(f"refusing: only {len(rows)} derived row(s) -- the site needs >= 2", file=sys.stderr)
        return 1

    if args.public_out is not None:
        if args.public_out.name == "prices.json":
            print("refusing: --public-out must not target prices.json", file=sys.stderr)
            return 1
        public = public_chart_rows(rows)
        args.public_out.write_text(json.dumps(public, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {len(public)} chart rows to {args.public_out}")
        return 0

    out = args.out or (args.data_dir / "prices.json")
    print(f"{len(rows)} derived rows, {rows[0]['timestamp'][:10]} .. {rows[-1]['timestamp'][:10]}")
    print(f"latest: {rows[-1]}")
    if args.write:
        out.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(f"dry run -- nothing written (pass --write to replace {out})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
