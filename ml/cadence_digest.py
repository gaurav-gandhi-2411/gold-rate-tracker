"""
cadence_digest.py -- weekly, non-paging summary of the "condition" band:
gaps past the 3h promise but below the dead-man's-switch EVENT/page level.

Y1 (audit 2026-09-05): WARN was conflating two different things -- "the
platform is slower than our promise" (a CONDITION: account-wide,
platform-side, unfixable from this repo, already disclosed on the page,
nothing GG can act on) and "the pipeline has stopped" (an EVENT: actionable,
what a page is for). Paging on the condition produces ~34-39 false pages a
month (see worker-deadman/src/deadman.mjs's own history) until the channel
gets muted; that is itself the recurring "control that stops reporting"
defect class this whole audit exists to find. Routing the condition band to
a periodic digest instead keeps it visible without demanding action every
time.

Reads data/cadence_metrics.json (already committed, refreshed every
check-price.yml run) and prints a low-priority ntfy summary. Does NOT
recompute anything -- if that file is missing or has no data, this posts
nothing (rule 98a: no data is a distinct state from "everything is fine",
never defaulted to a specific claim). Deliberately reads only
median_gap_hours/n/as_of -- the fields already on master -- so this doesn't
depend on whichever cadence_metrics.py enhancement (e.g. p90) lands first;
if p90_gap_hours is present it's included, but its absence is not an error.

Usage:
    python -m ml.cadence_digest            # prints the digest body, or
                                            # nothing (exit 0) if no data
"""

from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
CADENCE_METRICS_PATH = DATA_DIR / "cadence_metrics.json"

PROMISE_HOURS = 3  # the check-price.yml cron's design target, not a rolling number


def load_cadence_metrics(path: Path = CADENCE_METRICS_PATH) -> dict | None:
    """Returns the parsed cadence metrics, or None if missing/malformed/empty.
    None is a distinct, honest "nothing to report" state -- never defaulted
    to a specific number."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("n", 0) < 1 or data.get("median_gap_hours") is None:
        return None
    return data


def build_digest_body(metrics: dict) -> str:
    """Builds the ntfy body text. Only ever states numbers taken directly
    from `metrics` -- never fabricates a percentile or count metrics.json
    doesn't actually carry."""
    median = metrics["median_gap_hours"]
    n = metrics["n"]
    as_of = str(metrics.get("as_of", ""))[:10]
    p90 = metrics.get("p90_gap_hours")

    line = (
        f"Median gap between successful data commits this week: {median:.1f}h "
        f"(n={n}, as of {as_of}) vs. the {PROMISE_HOURS}h design target."
    )
    if isinstance(p90, int | float):
        line += f" Worst case (p90): {p90:.1f}h."
    line += (
        " This is a platform-side condition, not a repo bug -- see docs/RUNBOOK.md. "
        "No action needed unless the dead-man's switch pages separately."
    )
    return line


def main() -> None:
    # Explicit module-attribute reference (not load_cadence_metrics' own
    # default arg) so monkeypatching CADENCE_METRICS_PATH in tests actually
    # takes effect -- a default arg is bound once at function-definition
    # time, before any test could patch it. Same gotcha ml/cadence_metrics.py
    # already documents and avoids.
    metrics = load_cadence_metrics(CADENCE_METRICS_PATH)
    if metrics is None:
        print("No cadence data to digest this week -- skipping (not an error).")
        return
    body = build_digest_body(metrics)
    print(body)


if __name__ == "__main__":
    main()
