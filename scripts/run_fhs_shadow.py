"""scripts/run_fhs_shadow.py -- ADR 056: append forward-shadow FHS entries to reports/fhs_
ranges/shadow.json.

Unlike scripts/run_next_day_range_shadow.py (full recompute every run), this script is
APPEND-ONLY by design (brief item 5b's explicit instruction): each run reads the existing
shadow.json, computes FHS (both VOL_METHODS) plus the live/ADR047-v2/plain-HS comparison
figures for every RESOLVED decision day in data/metrics_history.json whose decision_date is
NOT ALREADY RECORDED, and appends only those new rows. Existing rows are never re-derived or
overwritten. This is safe even though it differs from ADR 047's full-recompute convention:
ml.fhs_ranges.fhs_next_day_range_pct_series enforces its own no-look-ahead internally (every
quantity used for a given decision day depends only on data strictly before that day), so an
already-recorded day's figure would be bit-for-bit identical if it were recomputed anyway --
appending is a performance choice here, not a leakage-avoidance one.

This script is deliberately NOT wired into any GitHub Actions workflow (brief item 5b) --
run manually, forward shadow only. Confirmatory (blind) rows are exactly those with
decision_date > FREEZE_DATE (2026-09-25, docs/adr/056-fhs-adaptive-ranges.md); rows on or
before the freeze date are the retrospective set reported in the ADR's Results section, not
a fresh blind read, and this script does not re-add them once they exist in shadow.json.

Usage: python scripts/run_fhs_shadow.py [--out reports/fhs_ranges/shadow.json]
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.fhs_ranges import VOL_METHODS, fhs_next_day_range_pct_series
from ml.metrics import ADR_022_FIX_DATE, METRICS_PATH
from ml.next_day_range import next_day_range_pct
from ml.range_forecast.data import load_ibja_price_series, load_proxy_price_series
from ml.weekly_range import base_range

FREEZE_DATE = "2026-09-25"  # docs/adr/056-fhs-adaptive-ranges.md
OUT_PATH = ROOT / "reports" / "fhs_ranges" / "shadow.json"


def _resolved_decisions(min_decision_date: str = ADR_022_FIX_DATE) -> list[dict[str, Any]]:
    """Same filter as scripts/analysis_fhs_ranges.py / scripts/run_next_day_range_shadow.py."""
    if not METRICS_PATH.exists():
        return []
    raw = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return []
    return [
        e
        for e in raw
        if e.get("outcome") not in ("pending", None)
        and isinstance(e.get("lower"), (int, float))
        and isinstance(e.get("upper"), (int, float))
        and isinstance(e.get("actual_next_22k"), (int, float))
        and isinstance(e.get("current_22k"), (int, float))
        and e.get("decision_date", "") >= min_decision_date
    ]


def _load_existing(out_path: Path) -> dict[str, Any]:
    if not out_path.exists():
        return {"schema_version": 1, "frozen_at": FREEZE_DATE, "rows": []}
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    payload.setdefault("rows", [])
    return payload


def _score_one(
    proxy: pd.Series, ibja: pd.Series, entry: dict[str, Any], fhs_pct_by_method: dict[str, Any]
) -> dict[str, Any] | None:
    d = pd.Timestamp(entry["decision_date"])
    p, truth = float(entry["current_22k"]), float(entry["actual_next_22k"])

    v2 = next_day_range_pct(proxy, ibja, d)
    proxy_before = proxy[proxy.index < d]
    plain = base_range(proxy_before, d, 1)
    if v2 is None or plain is None:
        return None
    for method in VOL_METHODS:
        if fhs_pct_by_method[method].get(d) is None:
            return None

    row: dict[str, Any] = {
        "decision_date": entry["decision_date"],
        "weekday": d.strftime("%A"),
        "confirmatory": entry["decision_date"] > FREEZE_DATE,
        "current_22k": p,
        "actual_next_22k": truth,
        "live_lo": float(entry["lower"]),
        "live_hi": float(entry["upper"]),
        "live_hit": float(entry["lower"]) <= truth <= float(entry["upper"]),
        "live_width": float(entry["upper"]) - float(entry["lower"]),
    }
    v2_lo, v2_hi = p * (1 + v2[0]), p * (1 + v2[1])
    row["v2_lo"], row["v2_hi"] = round(v2_lo, 1), round(v2_hi, 1)
    row["v2_hit"] = v2_lo <= truth <= v2_hi
    row["v2_width"] = v2_hi - v2_lo

    plain_lo_pct, plain_hi_pct = math.exp(plain[0]) - 1.0, math.exp(plain[1]) - 1.0
    plain_lo, plain_hi = p * (1 + plain_lo_pct), p * (1 + plain_hi_pct)
    row["plain_hs_lo"], row["plain_hs_hi"] = round(plain_lo, 1), round(plain_hi, 1)
    row["plain_hs_hit"] = plain_lo <= truth <= plain_hi
    row["plain_hs_width"] = plain_hi - plain_lo

    for method in VOL_METHODS:
        lo_pct, hi_pct = fhs_pct_by_method[method][d]
        lo, hi = p * (1 + lo_pct), p * (1 + hi_pct)
        row[f"fhs_{method}_lo"] = round(lo, 1)
        row[f"fhs_{method}_hi"] = round(hi, 1)
        row[f"fhs_{method}_hit"] = lo <= truth <= hi
        row[f"fhs_{method}_width"] = hi - lo
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()

    payload = _load_existing(args.out)
    existing_dates = {r["decision_date"] for r in payload["rows"]}

    decisions = sorted(_resolved_decisions(), key=lambda e: e["decision_date"])
    new_decisions = [e for e in decisions if e["decision_date"] not in existing_dates]

    if not new_decisions:
        print(
            f"[fhs-shadow] no new resolved decisions; {len(payload['rows'])} rows already recorded"
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        payload["generated_at_utc"] = datetime.now(UTC).isoformat()
        args.out.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
        return 0

    proxy = load_proxy_price_series()
    ibja = load_ibja_price_series()
    new_dates = [pd.Timestamp(e["decision_date"]) for e in new_decisions]
    fhs_pct_by_method = {
        method: fhs_next_day_range_pct_series(proxy, ibja, new_dates, method)
        for method in VOL_METHODS
    }

    added = 0
    for entry in new_decisions:
        row = _score_one(proxy, ibja, entry, fhs_pct_by_method)
        if row is not None:
            payload["rows"].append(row)
            added += 1

    payload["rows"].sort(key=lambda r: r["decision_date"])
    payload["schema_version"] = 1
    payload["frozen_at"] = FREEZE_DATE
    payload["generated_at_utc"] = datetime.now(UTC).isoformat()
    payload["git_sha"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout.strip()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")

    n_confirmatory = sum(1 for r in payload["rows"] if r.get("confirmatory"))
    print(
        f"[fhs-shadow] appended {added} new row(s); total {len(payload['rows'])} "
        f"({n_confirmatory} confirmatory, decision_date > {FREEZE_DATE})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
