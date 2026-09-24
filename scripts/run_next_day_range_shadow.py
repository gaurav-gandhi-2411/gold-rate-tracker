"""scripts/run_next_day_range_shadow.py -- ADR 047: score the v2 next-day range against the
displayed naive flat-hold range, on the same resolved decision days.

Frozen 2026-09-24 (docs/adr/047-next-day-range-v2.md), before this script was ever run. Rebuilds
v2's 1-day range (ml.next_day_range.next_day_range_pct -- the ADR 043 / ml.weekly_range method,
reused exactly) at every decision day `d` in data/metrics_history.json whose displayed range has
already resolved, using ONLY data known strictly before `d`. v2 uses the same current_22k and the
same actual_next_22k the displayed range is scored against, so the two are directly comparable.

Two reported blocks, split by the freeze date (FREEZE_DATE, the day this PR merged):
  * retrospective -- decision_date <= FREEZE_DATE. These are the days the displayed range was
    already scored on (73.0% coverage over 63 days as of the freeze); v2 was never computed on
    them before this PR, so this is a genuine (if already-seen-for-the-displayed-range) backtest
    of v2, not a live forward shadow.
  * forward -- decision_date > FREEZE_DATE, i.e. genuinely confirmatory: decisions made after the
    method was frozen. n = 0 until enough weeks accrue; this script is meant to be re-run weekly
    (weekly-backtest.yml) so that block grows on its own.
Full recompute every run (not append-only): both blocks are pure functions of metrics_history.json
plus the proxy/IBJA price history, and ml.next_day_range.next_day_range_pct enforces its own
no-look-ahead by filtering to `index < d` internally -- there is no accumulation step that could
leak future data backward into an already-computed day, so nothing is lost by recomputing from
scratch each run (unlike scripts/run_weekly_range_shadow.py, which issues forecasts incrementally
and must not re-derive them once issued).

Usage: python scripts/run_next_day_range_shadow.py [--out data/next_day_range_shadow.json]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import binomtest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.metrics import ADR_022_FIX_DATE, METRICS_PATH
from ml.next_day_range import next_day_range_pct
from ml.range_forecast.data import load_ibja_price_series, load_proxy_price_series
from ml.range_forecast.metrics import wilson_ci

LEVEL = 0.80
# The day this PR merged (docs/adr/047-next-day-range-v2.md). Decisions on/before this date are
# the pre-existing, already-scored-for-the-displayed-range retrospective; decisions after it are
# the genuinely confirmatory forward set.
FREEZE_DATE = "2026-09-24"
OUT_PATH = ROOT / "data" / "next_day_range_shadow.json"


def _resolved_decisions(min_decision_date: str = ADR_022_FIX_DATE) -> list[dict[str, Any]]:
    """Decisions with a resolved displayed range (same filter as ml.metrics.compute_band_
    coverage), on/after min_decision_date -- pre-fix decisions were scored under a different,
    since-corrected band (ADR 022) and are excluded the same way the displayed coverage figure
    excludes them."""
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


def _score_one(proxy: pd.Series, ibja: pd.Series, entry: dict[str, Any]) -> dict[str, Any] | None:
    """v2's range for one resolved decision, plus the displayed range already on the entry."""
    d = pd.Timestamp(entry["decision_date"])
    pct = next_day_range_pct(proxy, ibja, d)
    if pct is None:
        return None
    lo_pct, hi_pct = pct
    p = float(entry["current_22k"])
    lo_v2 = p * (1.0 + lo_pct)
    hi_v2 = p * (1.0 + hi_pct)
    truth = float(entry["actual_next_22k"])
    return {
        "decision_date": entry["decision_date"],
        "weekday": d.strftime("%A"),
        "current_22k": p,
        "actual_next_22k": truth,
        "lo_v2": round(lo_v2, 1),
        "hi_v2": round(hi_v2, 1),
        "hit_v2": lo_v2 <= truth <= hi_v2,
        "width_v2": hi_v2 - lo_v2,
        "lo_displayed": float(entry["lower"]),
        "hi_displayed": float(entry["upper"]),
        "hit_displayed": float(entry["lower"]) <= truth <= float(entry["upper"]),
        "width_displayed": float(entry["upper"]) - float(entry["lower"]),
    }


def _stats(hits: list[bool], widths: list[float]) -> dict[str, Any]:
    n = len(hits)
    if n == 0:
        return {
            "n": 0,
            "k": 0,
            "coverage": None,
            "wilson95": [None, None],
            "p_below_80": None,
            "mean_width": None,
        }
    k = int(sum(hits))
    lo, hi = wilson_ci(k, n)
    return {
        "n": n,
        "k": k,
        "coverage": k / n,
        "wilson95": [lo, hi],
        # One-sided: H0 true coverage = 80%, H1 true coverage < 80% -- the concern is
        # under-coverage, not over-coverage (a range that is too wide is a cost, not a
        # correctness failure the way under-coverage is).
        "p_below_80": float(binomtest(k, n, LEVEL, alternative="less").pvalue),
        "mean_width": float(np.mean(widths)),
    }


_MON_THU = frozenset({0, 1, 2, 3})


def _by_stratum(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mon_thu = [r for r in rows if pd.Timestamp(r["decision_date"]).weekday() in _MON_THU]
    fri_sun = [r for r in rows if pd.Timestamp(r["decision_date"]).weekday() not in _MON_THU]
    out = {}
    for name, sub in (("mon_thu", mon_thu), ("fri_sun", fri_sun)):
        out[name] = {
            "v2": _stats([r["hit_v2"] for r in sub], [r["width_v2"] for r in sub]),
            "displayed": _stats(
                [r["hit_displayed"] for r in sub], [r["width_displayed"] for r in sub]
            ),
        }
    return out


def _block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    v2 = _stats([r["hit_v2"] for r in rows], [r["width_v2"] for r in rows])
    displayed = _stats([r["hit_displayed"] for r in rows], [r["width_displayed"] for r in rows])
    success = None
    if rows:
        wlo, whi = v2["wilson95"]
        coverage_contains_80 = wlo is not None and wlo <= LEVEL <= whi
        ge_displayed = displayed["coverage"] is not None and v2["coverage"] >= displayed["coverage"]
        width_ok = (
            displayed["mean_width"] is not None
            and v2["mean_width"] <= 1.25 * displayed["mean_width"]
        )
        success = {
            "coverage_wilson_ci_contains_80": coverage_contains_80,
            "coverage_ge_displayed": ge_displayed,
            "width_not_more_than_25pct_wider": width_ok,
            "overall": bool(coverage_contains_80 and ge_displayed and width_ok),
        }
    return {
        "n": len(rows),
        "v2": v2,
        "displayed": displayed,
        "by_stratum": _by_stratum(rows),
        "success": success,
        "first_decision_date": rows[0]["decision_date"] if rows else None,
        "last_decision_date": rows[-1]["decision_date"] if rows else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT_PATH)
    args = ap.parse_args()

    proxy = load_proxy_price_series()
    ibja = load_ibja_price_series()
    decisions = sorted(_resolved_decisions(), key=lambda e: e["decision_date"])

    rows: list[dict[str, Any]] = []
    for entry in decisions:
        row = _score_one(proxy, ibja, entry)
        if row is not None:
            rows.append(row)

    retro_rows = [r for r in rows if r["decision_date"] <= FREEZE_DATE]
    forward_rows = [r for r in rows if r["decision_date"] > FREEZE_DATE]

    retrospective = _block(retro_rows)
    retrospective["note"] = (
        "Decisions since "
        f"{ADR_022_FIX_DATE} (ADR 022 fix) through the freeze date {FREEZE_DATE}: the same "
        "decision days the displayed naive flat-hold range has already been scored on. v2 "
        "itself was never computed on any of these days before this PR -- this is a genuine "
        "backtest of v2, but on days that are held-out-but-already-seen for the DISPLAYED "
        "range's own coverage figure, not a forward shadow."
    )
    forward = _block(forward_rows)

    payload = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
        ).stdout.strip(),
        "frozen_at": FREEZE_DATE,
        "min_decision_date": ADR_022_FIX_DATE,
        "retrospective": retrospective,
        "forward": forward,
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")

    def _fmt(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.3f}"

    r_v2_cov = _fmt(retrospective["v2"]["coverage"])
    r_disp_cov = _fmt(retrospective["displayed"]["coverage"])
    print(
        f"[adr047] retrospective n={retrospective['n']} v2_coverage={r_v2_cov} "
        f"vs displayed={r_disp_cov}; forward n={forward['n']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
