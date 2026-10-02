"""scripts/analysis_event_watch.py -- runs the ADR 050 pre-registered F4 "Event watch" test.

Measures whether price MOVE SIZE (never direction) is larger on known calendar events
(FOMC decisions, US CPI/jobs releases, India Union Budget) than on normal days, per event
type, with a block-bootstrap significance test and Bonferroni correction across the 4 types.
Every constant is frozen by ADR 050; changing one makes the result exploratory.

Usage:
    python scripts/analysis_event_watch.py [--results-out reports/event_watch_results.json]
                                            [--today-out data/event_watch_today.json]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.event_watch import (
    BONFERRONI_THRESHOLD,
    EVENT_TYPES,
    HALF_SPLIT_DATE,
    block_bootstrap_diff_test,
    bootstrap_median_ci,
    build_sentence,
    event_day_log_return,
    events_of_type,
    exclusion_window_dates,
    latest_ibja_price_per_gram,
    load_comex_series,
    load_events_calendar,
    load_inr_proxy_series,
    normal_day_log_returns,
    passes_success_gate,
)

UPCOMING_WINDOW_DAYS = 14


def _git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False, cwd=ROOT
    ).stdout.strip()


def _collect_moves(
    dates: list[str], series, is_genuine
) -> tuple[list[float], list[dict[str, Any]]]:
    """Per-event |log return| plus the actual (post roll-forward) dates used,
    for every event date that falls inside the series' coverage."""
    moves: list[float] = []
    detail: list[dict[str, Any]] = []
    for d in dates:
        result = event_day_log_return(d, series, is_genuine)
        if result is None:
            continue
        move, event_day, prior_day = result
        moves.append(move)
        detail.append(
            {
                "event_date": d,
                "priced_event_day": event_day,
                "priced_prior_day": prior_day,
                "abs_log_return": move,
            }
        )
    return moves, detail


def _run_type(
    event_type: str,
    all_rows: list[dict],
    series,
    is_genuine,
    excluded: set[str],
    date_lo: str | None = None,
    date_hi: str | None = None,
) -> dict[str, Any]:
    dates = events_of_type(all_rows, event_type)
    if date_lo is not None:
        dates = [d for d in dates if d >= date_lo]
    if date_hi is not None:
        dates = [d for d in dates if d <= date_hi]

    normal_series = normal_day_log_returns(series, is_genuine, excluded)
    if date_lo is not None:
        normal_series = normal_series[normal_series.index >= date_lo]
    if date_hi is not None:
        normal_series = normal_series[normal_series.index <= date_hi]

    if not dates:
        return {
            "n_events_in_calendar": 0,
            "n_events_priced": 0,
            "note": "no calendar events in this range",
        }

    moves, detail = _collect_moves(dates, series, is_genuine)
    test = block_bootstrap_diff_test(np.array(moves), normal_series.to_numpy())
    test["bonferroni_threshold"] = BONFERRONI_THRESHOLD
    test["bonferroni_significant"] = (
        test["p_one_sided"] is not None and test["p_one_sided"] <= BONFERRONI_THRESHOLD
    )
    test["passes_success_gate"] = passes_success_gate(test)
    return {
        "n_events_in_calendar": len(dates),
        "n_events_priced": len(moves),
        "n_normal_days": len(normal_series),
        **test,
        "event_detail": detail,
    }


def run() -> dict[str, Any]:
    all_rows = load_events_calendar()
    excluded = exclusion_window_dates(all_rows)

    comex_series, comex_genuine = load_comex_series()
    inr_series, inr_genuine = load_inr_proxy_series()

    series_by_key = {"comex": (comex_series, comex_genuine), "inr_proxy": (inr_series, inr_genuine)}

    results: dict[str, Any] = {}
    for event_type, series_key in EVENT_TYPES.items():
        series, genuine = series_by_key[series_key]
        full = _run_type(event_type, all_rows, series, genuine, excluded)
        half_1 = _run_type(event_type, all_rows, series, genuine, excluded, date_hi="2012-12-31")
        half_2 = _run_type(event_type, all_rows, series, genuine, excluded, date_lo=HALF_SPLIT_DATE)
        results[event_type] = {
            "price_series": series_key,
            "full_sample": full,
            "half_2000_2012": half_1,
            "half_2013_present": half_2,
        }

    return results


def build_today_snapshot(results: dict[str, Any], run_date: date) -> dict[str, Any]:
    """data/event_watch_today.json -- upcoming events (next 14 days) of a
    type that passed the full-sample success gate, with X + interval + a
    sentence built only from computed values (ADR 050 'Card figure')."""
    all_rows = load_events_calendar()
    today_price_per_g = latest_ibja_price_per_gram()
    window_end = (run_date + timedelta(days=UPCOMING_WINDOW_DAYS)).isoformat()
    window_start = run_date.isoformat()

    surfaced_types = {t for t, r in results.items() if r["full_sample"].get("passes_success_gate")}

    upcoming: list[dict[str, Any]] = []
    for r in all_rows:
        if r["type"] not in surfaced_types:
            continue
        if not (window_start <= r["date"] <= window_end):
            continue
        full = results[r["type"]]["full_sample"]
        event_detail = full["event_detail"]
        moves = np.array([e["abs_log_return"] for e in event_detail])
        median, lo, hi = bootstrap_median_ci(moves)
        move_rs_per_g = median * today_price_per_g
        move_rs_per_g_lo = lo * today_price_per_g
        move_rs_per_g_hi = hi * today_price_per_g
        upcoming.append(
            {
                "type": r["type"],
                "date": r["date"],
                "time_utc": r.get("time_utc"),
                "today_price_per_g_inr": today_price_per_g,
                "median_abs_log_return": median,
                "move_rs_per_g": move_rs_per_g,
                "move_rs_per_g_ci_95": [move_rs_per_g_lo, move_rs_per_g_hi],
                "n_historical_events": len(moves),
                "sentence": build_sentence(r["type"], r["date"], move_rs_per_g),
            }
        )

    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "run_date": run_date.isoformat(),
        "window_days": UPCOMING_WINDOW_DAYS,
        "surfaced_types": sorted(surfaced_types),
        "upcoming_events": upcoming,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--results-out", type=Path, default=ROOT / "reports" / "event_watch_results.json"
    )
    ap.add_argument("--today-out", type=Path, default=ROOT / "data" / "event_watch_today.json")
    args = ap.parse_args()

    results = run()
    out = {
        "adr": "050",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "git_sha": _git_sha(),
        "bonferroni_m": 4,
        "bonferroni_alpha": 0.05,
        "results_by_type": results,
    }
    args.results_out.parent.mkdir(parents=True, exist_ok=True)
    args.results_out.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    today_snapshot = build_today_snapshot(results, date.today())
    args.today_out.parent.mkdir(parents=True, exist_ok=True)
    args.today_out.write_text(json.dumps(today_snapshot, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({k: v["full_sample"] for k, v in results.items()}, indent=2, default=str))
    print(json.dumps(today_snapshot, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
