"""scripts/run_markup_reversion_shadow.py -- ADR 057 forward shadow (confirmatory test log).

Each run:
  1. computes the walk-forward markup z for the latest IST date in the committed data (or
     ``--as-of``) on the pre-registered primary series (same-day-fresh IBJA pairs);
  2. appends ONE entry for that date to reports/markup_reversion/shadow.json if none exists yet
     (an existing entry's signal fields are never rewritten -- they are the frozen prediction);
  3. fills in outcomes for earlier entries whose N-business-day target is now observable.

Derived numbers only (markup %, markup Rs/g, z, Rs/g changes) -- no raw retailer price levels.
Not wired into any workflow (ADR 057: GG decides). No network; reads committed data files.

Usage: python scripts/run_markup_reversion_shadow.py [--as-of YYYY-MM-DD] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.markup_reversion import (
    FORWARD_PRIMARY,
    build_eligible_series,
    ibja_business_days,
    load_default_frame,
    resolve_outcomes,
    shadow_entry_for,
)

OUT = ROOT / "reports" / "markup_reversion" / "shadow.json"
FORWARD_FIRST_DATE = "2026-09-25"  # ADR 057: first IST decision date of the forward sample


def load_log(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "adr": "docs/adr/057-markup-reversion-preregistration.md",
        "forward_primary_cell": {"z_threshold": FORWARD_PRIMARY[0], "n": FORWARD_PRIMARY[1]},
        "forward_sample_first_date": FORWARD_FIRST_DATE,
        "entries": [],
    }


def run(as_of: date | None, out: Path) -> dict[str, Any]:
    frame, ibja_df = load_default_frame()
    series = build_eligible_series(frame)
    bdays = ibja_business_days(ibja_df)
    log = load_log(out)
    target = as_of or frame["date"].max()
    have = {e["date"] for e in log["entries"]}
    entry = shadow_entry_for(series, target)
    status = "not_eligible"
    if entry is not None and entry.date not in have:
        # logged_at proves the signal was recorded prospectively (before its outcome existed).
        log["entries"].append({**entry.to_dict(), "logged_at_utc": datetime.now(UTC).isoformat()})
        status = "appended"
    elif entry is not None:
        status = "already_logged"
    log["entries"].sort(key=lambda e: e["date"])
    resolve_outcomes(log["entries"], series, bdays)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(log, indent=1) + "\n", encoding="utf-8")
    return {"as_of": str(target), "status": status, "n_entries": len(log["entries"])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", type=date.fromisoformat, default=None)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    print(json.dumps(run(args.as_of, args.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
