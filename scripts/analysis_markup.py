"""scripts/analysis_markup.py -- F1 "store markup meter": research pass over ml.markup's output.

Writes reports/markup_analysis.json with, per retailer:
  * history length (first/last IST date, n days, n same-day-fresh-IBJA days vs stale-IBJA days),
  * markup_pct distribution (mean, median, sd, p10/p90),
  * stability (day-to-day change sd, AR(1) persistence, share of adjacent days where the
    "lower/usual/higher than usual" category changes -- and the raw transition matrix),
  * base rate of "higher than usual" (how often the meter would say that today),
  * Tanishq specifically: weekday vs weekend markup_pct, and whether Tanishq's own board rate
    changes at all over the weekend (it has daily scrape cadence; IBJA does not publish weekends,
    so a weekend reading is always paired against a stale Friday PM fix by construction).

Research only: reads the already-committed data files ml.markup reads, no network. Does not touch
any live pipeline output.

Usage: python scripts/analysis_markup.py [--out reports/markup_analysis.json]
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.ibja import load_ibja_parquet
from ml.markup import (
    CATEGORY_HIGHER,
    DailyMarkupRow,
    MarkupPosition,
    build_daily_markup_series,
    compute_rolling_positions,
)
from ml.markup import _load_all_retailer_readings as load_all_retailer_readings

OUT = ROOT / "reports" / "markup_analysis.json"


def _desc(values: list[float]) -> dict[str, Any]:
    """Mean/median/sd/p10/p90 -- n=0 reported honestly, never a fabricated 0.0."""
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1) if n > 1 else None
    return {
        "n": n,
        "mean": mean,
        "median": ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2,
        "std": math.sqrt(variance) if variance is not None else None,
        "p10": ordered[max(0, round(0.10 * (n - 1)))],
        "p90": ordered[max(0, round(0.90 * (n - 1)))],
    }


def _ar1(values: list[float]) -> dict[str, Any]:
    """Lag-1 autocorrelation of successive (adjacent-row) values. n_pairs < 10 -> None, too
    noisy to report a persistence estimate from."""
    pairs = list(itertools.pairwise(values))
    if len(pairs) < 10:
        return {"n_pairs": len(pairs), "ar1": None}
    a = [p[0] for p in pairs]
    b = [p[1] for p in pairs]
    mean_a, mean_b = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True))
    den_a = math.sqrt(sum((x - mean_a) ** 2 for x in a))
    den_b = math.sqrt(sum((y - mean_b) ** 2 for y in b))
    if den_a == 0 or den_b == 0:
        return {"n_pairs": len(pairs), "ar1": None}
    return {"n_pairs": len(pairs), "ar1": num / (den_a * den_b)}


def _transition_matrix(categories: list[str | None]) -> dict[str, Any]:
    """Category(t) -> category(t+1) raw counts over adjacent rows with a defined category on
    both sides, plus the overall share of transitions where the category changed."""
    defined = [(a, b) for a, b in itertools.pairwise(categories) if a is not None and b is not None]
    matrix: dict[str, dict[str, int]] = {}
    changed = 0
    for a, b in defined:
        matrix.setdefault(a, {}).setdefault(b, 0)
        matrix[a][b] += 1
        if a != b:
            changed += 1
    return {
        "n_transitions": len(defined),
        "matrix": matrix,
        "share_changed": changed / len(defined) if defined else None,
    }


def _base_rate_higher(categories: list[str | None]) -> dict[str, Any]:
    defined = [c for c in categories if c is not None]
    if not defined:
        return {"n": 0, "rate": None}
    return {"n": len(defined), "rate": sum(c == CATEGORY_HIGHER for c in defined) / len(defined)}


def _same_day_vs_stale(daily: list[DailyMarkupRow]) -> dict[str, Any]:
    fresh = sum(not r.stale_ibja for r in daily)
    stale = sum(r.stale_ibja for r in daily)
    return {"n_same_day_fresh_ibja": fresh, "n_stale_ibja": stale, "n_total": len(daily)}


def _weekend_split(daily: list[DailyMarkupRow]) -> dict[str, Any]:
    """Weekday (Mon-Fri) vs weekend (Sat/Sun) markup_pct, and whether Tanishq's own board rate
    (retailer_rate_per_gram) actually MOVES on a weekend day vs the immediately preceding Friday
    -- weekends are always paired against a stale Friday IBJA PM fix by construction, so the
    interesting question is whether the retailer side of the ratio changes at all.
    """
    weekday = [r.markup_pct for r in daily if r.ist_date.weekday() < 5]
    weekend = [r.markup_pct for r in daily if r.ist_date.weekday() >= 5]

    by_date = {r.ist_date: r for r in daily}
    weekend_moves = 0
    weekend_with_prior_friday = 0
    for r in daily:
        if r.ist_date.weekday() not in (5, 6):
            continue
        # Nearest preceding Friday (1 day back for Saturday, 2 for Sunday).
        friday = r.ist_date - timedelta(days=1 if r.ist_date.weekday() == 5 else 2)
        prior = by_date.get(friday)
        if prior is None:
            continue
        weekend_with_prior_friday += 1
        if prior.retailer_rate_per_gram != r.retailer_rate_per_gram:
            weekend_moves += 1

    return {
        "weekday": _desc(weekday),
        "weekend": _desc(weekend),
        "weekend_days_with_prior_friday": weekend_with_prior_friday,
        "weekend_days_rate_moved_vs_prior_friday": weekend_moves,
    }


def analyze_retailer(name: str, daily: list[DailyMarkupRow]) -> dict[str, Any]:
    positions: list[MarkupPosition] = compute_rolling_positions(daily)
    markups = [r.markup_pct for r in daily]
    categories = [p.category for p in positions]
    diffs = [markups[i] - markups[i - 1] for i in range(1, len(markups))]

    out: dict[str, Any] = {
        "history": {
            "first_date": daily[0].ist_date.isoformat() if daily else None,
            "last_date": daily[-1].ist_date.isoformat() if daily else None,
            "n_days": len(daily),
            **_same_day_vs_stale(daily),
        },
        "markup_pct_distribution": _desc(markups),
        "stability": {
            "day_to_day_change": _desc(diffs),
            "ar1_markup_pct": _ar1(markups),
            "category_transitions": _transition_matrix(categories),
        },
        "base_rate_higher_than_usual": _base_rate_higher(categories),
    }
    if name == "tanishq":
        out["weekend_effect"] = _weekend_split(daily)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    ibja_df = load_ibja_parquet()
    if ibja_df.empty:
        print("analysis_markup: data/ibja_rates.parquet is empty/missing", file=sys.stderr)
        return 1

    all_readings = load_all_retailer_readings()
    retailers: dict[str, Any] = {}
    for name, readings in all_readings.items():
        if not readings:
            retailers[name] = {"history": {"n_days": 0}}
            continue
        daily = build_daily_markup_series(readings, ibja_df)
        retailers[name] = analyze_retailer(name, daily)

    out = {
        "generated_at": date.today().isoformat(),
        "definition": "markup_pct = (retailer_22k_per_gram / ibja_22k_per_gram_in_force - 1) * 100",
        "retailers": retailers,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: v["history"] for k, v in retailers.items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
