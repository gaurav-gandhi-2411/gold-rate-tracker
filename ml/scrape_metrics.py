"""
scrape_metrics.py — Rolling Tanishq self-hosted scrape success rate.

H3 (session dated 2026-08-28): ADR 025/README stated "Tanishq's site now
blocks automated access most of the time" as a present-tense, hand-typed
claim. Production's actual scrape path (the self-hosted Playwright runner,
docs/RUNBOOK.md) contradicts this on essentially every cycle. This module
computes the real rate from real run history instead of leaving the claim
hand-typed and unmeasured.

Reads the append-only log scrape-tanishq-selfhosted.yml's scrape-tanishq-
selfhosted job writes one line to per run (data/tanishq_scrape_outcomes.jsonl -- {timestamp,
outcome, fetch_method}), filters to a rolling window, and reports:
  - overall success rate (any successful reading, requests OR playwright)
  - the requests-path vs playwright-fallback split among successes
  - Wilson 95% CI on the overall rate (ml.metrics.wilson_confidence_interval)

Distinct from data/tanishq_selfhosted_health.json's consecutive_job_failures:
that's a single rolling counter for T12's "runner online but failing" alert;
this is the full outcome history, aggregated over a documented window, for
the honest present-tense claim in README/ADR 025.

Usage:
    python -m ml.scrape_metrics
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ml.metrics import wilson_confidence_interval

DATA_DIR = Path(__file__).parent.parent / "data"
OUTCOMES_LOG_PATH = DATA_DIR / "tanishq_scrape_outcomes.jsonl"
OUTPUT_PATH = DATA_DIR / "tanishq_scrape_success_rate.json"

# 7 days: matches the ~8-runs/day check-price.yml cadence closely enough to
# give a meaningful weekly sample (n~50-56) without being so short a single
# runner-offline stretch dominates it, and reuses this repo's existing
# "weekly" cadence vocabulary (eval-direction.yml, T-series daily gates)
# rather than inventing a new one.
WINDOW_DAYS = 7


def load_outcomes(path: Path = OUTCOMES_LOG_PATH) -> list[dict]:
    """Parse the append-only outcomes log. Skips (does not raise on) any
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


def compute_rolling_success_rate(
    records: list[dict],
    now: datetime,
    window_days: int = WINDOW_DAYS,
) -> dict:
    """Filter to the rolling window and compute the success-rate breakdown.

    Returns a dict with n/n_success/n_failure/n_requests_path/
    n_playwright_fallback/success_rate/wilson_ci_low/wilson_ci_high.
    success_rate and the CI are None when n == 0 -- no data in the window is
    a distinct, honest state from "0% success", not something to default to
    a specific number for.
    """
    cutoff = now - timedelta(days=window_days)
    in_window = []
    for r in records:
        ts_raw = r.get("timestamp")
        if not ts_raw:
            continue
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts >= cutoff:
            in_window.append(r)

    n = len(in_window)
    n_success = sum(1 for r in in_window if r.get("outcome") == "success")
    n_failure = n - n_success
    n_requests_path = sum(
        1
        for r in in_window
        if r.get("outcome") == "success" and r.get("fetch_method") == "requests"
    )
    n_playwright_fallback = sum(
        1
        for r in in_window
        if r.get("outcome") == "success" and r.get("fetch_method") == "playwright"
    )

    if n == 0:
        success_rate = None
        ci_low = ci_high = None
    else:
        success_rate = n_success / n
        ci_low, ci_high = wilson_confidence_interval(n_success, n)

    return {
        "window_days": window_days,
        "n": n,
        "n_success": n_success,
        "n_failure": n_failure,
        "n_requests_path": n_requests_path,
        "n_playwright_fallback": n_playwright_fallback,
        "success_rate": success_rate,
        "wilson_ci_low": ci_low,
        "wilson_ci_high": ci_high,
    }


def main(now: datetime | None = None) -> None:
    if now is None:
        now = datetime.now(UTC)
    # Explicit module-attribute reference (not load_outcomes' own default
    # arg) so monkeypatching OUTCOMES_LOG_PATH/OUTPUT_PATH in tests actually
    # takes effect -- a default arg is bound once at function-definition
    # time, before any test could patch it.
    records = load_outcomes(OUTCOMES_LOG_PATH)
    result = compute_rolling_success_rate(records, now)
    result["schema_version"] = 1
    result["generated_at_utc"] = now.isoformat()

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}: {result}")


if __name__ == "__main__":
    main()
