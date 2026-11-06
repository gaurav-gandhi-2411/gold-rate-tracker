"""Monthly samples of the 2022 - May 2026 estimate history, for the "long-term view" (flag OFF).

Why this exists: until 2026-06-01 the IBJA-based estimate series is sampled, not daily (13 of the
41 months with data have a single day, 12 of 53 months have none), so it cannot be drawn as a
line without inventing a path between months (#3585, #3594). A monthly view states exactly what
exists: for each month with at least one official-rate day, one dot at the MIDDLE value (median)
of that month's days, plotted mid-month, with the day count ``n`` and the min/max kept for the
tooltip. A month with no data has no dot. Dots are never joined.

The values are the same estimate the chart plots (22K = IBJA 916 x the fixed calibration), taken
from the series as published before #3585 trimmed it (``--source``), so this file adds nothing
that was not already public. The method is stated here and in the page's own note.

Usage:
    python scripts/build_long_history_samples.py --source PATH --source-ref SHA --out data/long_history_monthly.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

FIRST_DAILY_DAY = (
    "2026-06-01"  # the published daily series starts here (scripts/build_ibja_derived_prices.py)
)
FIRST_MONTH = "2022-01"
LAST_MONTH = "2026-05"
METHOD = (
    "One dot per calendar month that has at least one official-rate (IBJA) day in our history: "
    "the median of that month's 22K estimates, plotted mid-month. 'n' is how many of that "
    "month's days we have; min and max are the lowest and highest of them. A month with no data "
    "has no dot. Dots are not joined: between them the history is not daily."
)


def months_between(first: str, last: str) -> list[str]:
    y, m = int(first[:4]), int(first[5:7])
    end = (int(last[:4]), int(last[5:7]))
    out: list[str] = []
    while (y, m) <= end:
        out.append(f"{y}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def monthly_samples(rows: list[dict]) -> dict:
    """Pure: rows of {timestamp, 22k} -> the long-history document. Rows from FIRST_DAILY_DAY on
    are the daily series and are excluded; rows are never reordered or interpolated."""
    by_month: dict[str, list[int]] = defaultdict(list)
    used = 0
    for r in rows:
        day = r["timestamp"][:10]
        if day >= FIRST_DAILY_DAY:
            continue
        by_month[day[:7]].append(int(r["22k"]))
        used += 1
    samples = []
    for month in months_between(FIRST_MONTH, LAST_MONTH):
        vals = by_month.get(month)
        if not vals:
            continue
        samples.append(
            {
                "month": month,
                # mid-month at IBJA's 17:00 IST publication time (11:30Z), like the daily rows
                "timestamp": f"{month}-15T11:30:00.000Z",
                "22k": round(statistics.median(vals)),
                "n": len(vals),
                "min": min(vals),
                "max": max(vals),
            }
        )
    months_all = months_between(FIRST_MONTH, LAST_MONTH)
    return {
        "method": METHOD,
        "first_month": FIRST_MONTH,
        "last_month": LAST_MONTH,
        "months_total": len(months_all),
        "months_with_data": len(samples),
        "months_without_data": [m for m in months_all if m not in by_month],
        "source_rows_used": used,
        "samples": samples,
    }


def render(doc: dict) -> str:
    """The JSON text: header keys one per line, then ONE SAMPLE PER LINE (a monthly table is read
    row by row; the default indent=1 spreads 41 samples over 330 lines). Valid JSON, stable."""
    lines = ["{"]
    for key, value in doc.items():
        if key != "samples":
            lines.append(f" {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)},")
    lines.append(' "samples": [')
    rows = [f"  {json.dumps(s, ensure_ascii=False)}" for s in doc["samples"]]
    lines.append(",\n".join(rows))
    lines.extend([" ]", "}"])
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", type=Path, required=True, help="the pre-2026-06 chart series JSON")
    ap.add_argument("--source-ref", required=True, help="commit SHA the source file was taken from")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    rows = json.loads(args.source.read_text(encoding="utf-8"))
    doc = monthly_samples(rows)
    if not doc["samples"]:
        print("refusing: no month had any data", file=sys.stderr)
        return 1
    doc = {"source": f"data/ibja_derived_prices.json at commit {args.source_ref}", **doc}
    args.out.write_text(render(doc), encoding="utf-8")
    print(
        f"wrote {len(doc['samples'])} monthly samples from {doc['source_rows_used']} rows to {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
