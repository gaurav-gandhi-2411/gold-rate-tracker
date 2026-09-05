"""
cadence_metrics.py -- Rolling measured interval between successful data runs.

R2 (audit 2026-09-04): i18n.js/README.md hand-typed "checked every 3 hours"
as a present-tense claim. Actual scheduled-trigger reliability stepped from
0.0% to 18.5%+ miss rate at 2026-08-27T00:15 UTC (GitHub-reported Actions
incident, see docs/RUNBOOK.md), so the real observed cadence is worse than
the nominal 3h cron and drifts with platform conditions -- exactly the
defect class scripts/inject_metrics.py exists to prevent for README, and
its client-side equivalent for i18n.js (which that script doesn't cover;
app.js fetches this module's own output file directly rather than
recomputing, so there is exactly one implementation of "median gap").

Reads data/run_cadence_log.jsonl -- one line per check-price.yml run that
actually reached its commit step with a real change (appended by the
"Commit updated data files" step itself, same append-only-log convention
as data/tanishq_scrape_outcomes.jsonl), filters to a rolling window, and
reports the median gap between consecutive timestamps -- the real observed
interval between successful data commits, not the nominal cron cadence.

data/metrics_history.json was considered and rejected as the source: its
records are deduped to one per IST calendar day (for the 5-day
outcome-resolution window ml.metrics tracks), so consecutive-record gaps
there measure ~24h, not the actual multi-times-per-day commit cadence.

Usage:
    python -m ml.cadence_metrics
"""

from __future__ import annotations

import json
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
CADENCE_LOG_PATH = DATA_DIR / "run_cadence_log.jsonl"
OUTPUT_PATH = DATA_DIR / "cadence_metrics.json"

# 7 days: matches scrape_metrics.py's existing "weekly" window convention.
# At the nominal 3h cadence this gives n~56 gaps; even at the degraded ~6h
# observed cadence (R2) it's still n~28 -- large enough to be meaningful,
# short enough that a multi-day outage doesn't dominate a monthly figure.
WINDOW_DAYS = 7


def load_log(path: Path = CADENCE_LOG_PATH) -> list[dict]:
    """Parse the append-only cadence log. Skips (does not raise on) any
    malformed line -- a single corrupt append must not take down every
    subsequent run's metric computation."""
    if not path.exists():
        return []
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def compute_median_gap(
    records: list[dict],
    now: datetime,
    window_days: int = WINDOW_DAYS,
) -> dict:
    """Filter to the rolling window and compute the median inter-commit gap.

    Returns a dict with window_days/n/median_gap_hours/as_of. n is the
    number of GAPS (record count in window minus one), not the record
    count itself. median_gap_hours and as_of are None when n == 0 (fewer
    than 2 records in window) -- no data is a distinct, honest state from
    a specific number, not something to default to 0 or the nominal 3h.
    """
    cutoff = now - timedelta(days=window_days)
    timestamps: list[datetime] = []
    for r in records:
        ts_raw = r.get("timestamp")
        if not ts_raw:
            continue
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts >= cutoff:
            timestamps.append(ts)
    timestamps.sort()

    if len(timestamps) < 2:
        return {
            "window_days": window_days,
            "n": 0,
            "median_gap_hours": None,
            "as_of": None,
        }

    gaps_hours = [
        (timestamps[i] - timestamps[i - 1]).total_seconds() / 3600.0
        for i in range(1, len(timestamps))
    ]
    return {
        "window_days": window_days,
        "n": len(gaps_hours),
        "median_gap_hours": statistics.median(gaps_hours),
        "as_of": timestamps[-1].isoformat(),
    }


def main(now: datetime | None = None) -> None:
    if now is None:
        now = datetime.now(UTC)
    # Explicit module-attribute reference (not load_log's own default arg)
    # so monkeypatching CADENCE_LOG_PATH/OUTPUT_PATH in tests actually takes
    # effect -- a default arg is bound once at function-definition time,
    # before any test could patch it.
    records = load_log(CADENCE_LOG_PATH)
    result = compute_median_gap(records, now)
    result["schema_version"] = 1
    result["generated_at_utc"] = now.isoformat()

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}: {result}")


if __name__ == "__main__":
    main()

# scratch test line -- deliberate overlap with boundary-gated PR #1406, will be reverted
