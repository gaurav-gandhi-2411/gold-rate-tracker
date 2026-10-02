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
from datetime import UTC, datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
CADENCE_METRICS_PATH = DATA_DIR / "cadence_metrics.json"
# AE3a (audit 2026-09-10): check-price.yml's own catch-up-dispatch step now
# classifies each firing as "late" (a schedule event fired recently, the
# cycle was just slow) or "dropped" (no schedule event fired at all in the
# preceding window -- AD1's finding, same audit: check-price's 06:07 UTC
# slot stopped firing as a schedule event for 9 straight days, silently
# absorbed by catch-up every time with nothing distinguishing it from an
# ordinary slow cycle). This digest is where that distinction becomes
# visible to a human instead of staying buried in per-run Actions logs.
CATCHUP_LOG_PATH = DATA_DIR / "catchup_dispatch_log.jsonl"

PROMISE_HOURS = 3  # the check-price.yml cron's design target, not a rolling number
CATCHUP_WINDOW_DAYS = 7  # matches cadence_metrics.py's WINDOW_DAYS convention


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


def load_catchup_log(path: Path = CATCHUP_LOG_PATH) -> list[dict]:
    """Parse the append-only catch-up-dispatch log. Skips (does not raise
    on) any malformed line -- same convention as ml.cadence_metrics.load_log,
    a single corrupt append must not take down digest generation."""
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


def summarize_catchup_causes(
    records: list[dict], window_days: int = CATCHUP_WINDOW_DAYS, now: datetime | None = None
) -> dict | None:
    """Counts catch-up firings in the trailing window by cause. Returns None
    (not a zeroed dict) when there's nothing in the window -- distinct from
    "zero dropped, N late", which is a real, reportable state."""
    if not records:
        return None
    cutoff = (now or datetime.now(UTC)) - timedelta(days=window_days)
    in_window = []
    for r in records:
        ts = r.get("timestamp")
        if not isinstance(ts, str):
            continue
        try:
            parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed >= cutoff:
            in_window.append(r)
    if not in_window:
        return None
    dropped = sum(1 for r in in_window if r.get("cause") == "dropped")
    late = sum(1 for r in in_window if r.get("cause") == "late")
    unknown = len(in_window) - dropped - late
    return {"total": len(in_window), "dropped": dropped, "late": late, "unknown": unknown}


def build_digest_body(metrics: dict, catchup_summary: dict | None = None) -> str:
    """Builds the ntfy body text. Only ever states numbers taken directly
    from `metrics`/`catchup_summary` -- never fabricates a percentile or
    count either file doesn't actually carry."""
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
    # AE3a: surfaces the late-vs-dropped split so a dropped-slot streak (AD1's
    # finding shape -- a scheduled trigger silently not firing at all,
    # absorbed by catch-up every cycle with nothing distinguishing it from
    # ordinary lateness) becomes visible here instead of staying buried in
    # per-run Actions logs. Omitted entirely when there's nothing to report --
    # zero catch-up firings this week is not itself news.
    if catchup_summary:
        line += (
            f" Catch-up fired {catchup_summary['total']}x this week: "
            f"{catchup_summary['dropped']} due to the scheduled trigger not firing at all, "
            f"{catchup_summary['late']} due to an ordinary slow cycle"
        )
        if catchup_summary["unknown"]:
            line += f", {catchup_summary['unknown']} unclassified"
        line += "."
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
    catchup_summary = summarize_catchup_causes(load_catchup_log(CATCHUP_LOG_PATH))
    body = build_digest_body(metrics, catchup_summary)
    print(body)


if __name__ == "__main__":
    main()
