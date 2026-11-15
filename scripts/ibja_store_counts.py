"""Counts-only check of the encrypted IBJA store (data/ibja_rates.parquet, ADR 060).

Prints aggregates only: row count, date span, the number of non-positive ("zero") rate cells, null
cells per rate column, and for one named date whether each rate cell is null / present / the row is
absent. It never prints a rate or a row. Run it where DATA_ENC_KEY has decrypted the store (a CI
runner): `python scripts/ibja_store_counts.py --date 2026-11-10`.

Standing data-validation rule (GG, 2026-11-15): zero cells must be 0. A stored 0 is "no rate"
(ml/ibja.py::is_rate); ingest turns one into NaN, so any that remain mean a write path skipped that
guard. Exit 0 = rule holds, 1 = it does not, 2 = the store cannot be read (never a silent pass).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "ibja_rates.parquet"
RULE = "ibja-zero-cells"


def _rate_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c.startswith(("am_", "pm_"))]


def summarize(df: pd.DataFrame, day: str | None = None) -> dict:
    """Aggregates for the rule and the report; no cell value is ever put in the result."""
    cols = _rate_columns(df)
    bad_cells = 0
    nulls: dict[str, int] = {}
    for c in cols:
        s = pd.to_numeric(df[c], errors="coerce")
        nulls[c] = int(s.isna().sum())
        # a rate is a finite number above 0 (ml/ibja.py::is_rate); anything else that is not null is bad
        bad_cells += int((s.notna() & ~((s > 0) & np.isfinite(s))).sum())
    dates = df["date"].astype(str) if "date" in df.columns else pd.Series([], dtype=str)
    out: dict = {
        "rows": len(df),
        "date_min": dates.min() if len(dates) else None,
        "date_max": dates.max() if len(dates) else None,
        "rate_columns": len(cols),
        "zero_cells": bad_cells,
        "null_cells": nulls,
        "day": day,
        "day_cells": None,
    }
    if day is not None:
        row = df[dates == day]
        if row.empty:
            out["day_cells"] = "row absent"
        else:
            r = row.iloc[0]
            out["day_cells"] = {
                c: ("null" if pd.isna(pd.to_numeric(r[c], errors="coerce")) else "present")
                for c in cols
                if c.startswith("pm_")
            }
    return out


def report(s: dict) -> list[str]:
    lines = [
        f"IBJA-STORE: rows={s['rows']} dates={s['date_min']}..{s['date_max']} "
        f"rate_columns={s['rate_columns']}",
        f"IBJA-STORE: zero_cells={s['zero_cells']} (rule {RULE}: must be 0)",
    ]
    for c, n in s["null_cells"].items():
        lines.append(f"IBJA-STORE: null_cells {c}={n}")
    if s["day"] is not None:
        lines.append(f"IBJA-STORE: {s['day']} pm cells: {s['day_cells']}")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--store", default=str(STORE))
    ap.add_argument(
        "--date", default=None, help="ISO date whose pm cells are reported null/present"
    )
    args = ap.parse_args(argv)
    path = Path(args.store)
    try:
        df = pd.read_parquet(path)
    except (OSError, ValueError) as exc:
        print(f"IBJA-STORE: cannot read the store ({type(exc).__name__}); not a pass")
        return 2
    s = summarize(df, args.date)
    print("\n".join(report(s)))
    if s["zero_cells"] != 0:
        print(
            f"::error title={RULE}::{s['zero_cells']} stored rate cell(s) are 0, negative or not finite"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
