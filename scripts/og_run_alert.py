"""Record in the ops log when the share-card workflow (generate-og-image.yml) keeps failing.

A workflow that fails twice is a CI failure, not a wrong price, so since 2026-11-06 it goes to the ops
log (scripts/ops_log.py), not ntfy. A card that is wrong or stale is still paged by live_share_card.py.
The wording below says "page"; read it as "ops-log entry".


    python3 scripts/og_run_alert.py --current success|failure --previous runs.json [--dry-run]

`runs.json` is the GitHub API's list of this workflow's earlier COMPLETED runs, newest first (this run
excluded). Only success/failure count; skipped and cancelled runs say nothing about the card.

An incident is two failures in a row. One failure does not page: the card is rebuilt after every
check-price run (about every 3 h), and of the 7 failures in the 30 days to 2026-11-02 that this
change does not remove, 2 were a Playwright download error that the next run did not repeat. So:
- this run failed, the previous one failed, and the one before that did not: page (incident opens);
- this run failed and the previous two failed too: nothing (already paged);
- this run succeeded after two or more failures in a row: one recovery message.
No state file: the run history is the state, so a page cannot be lost to cache eviction.

A card that is stale or wrong while every run is green is a different failure; the hourly share-card
check in ci-health.yml catches that one.

Exit code 0 always (the run's own result already shows red); a page that could not be sent is
printed as ::error::. Stdlib only: the runner's own python3, nothing installed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

WORKFLOW_URL = "https://github.com/gaurav-gandhi-2411/gold-rate-tracker/actions/workflows/generate-og-image.yml"


def streak(previous: list[str]) -> int:
    """How many of the newest previous runs failed in a row."""
    n = 0
    for c in previous:
        if c != "failure":
            break
        n += 1
    return n


def decide(current: str, previous: list[str]) -> dict[str, str] | None:
    """The message to send, or None. Pure: no I/O. `previous` is newest first, success/failure only."""
    before = streak(previous)
    if current == "failure" and before == 1:
        return {
            "title": "Gold Tracker: share card failed twice in a row",
            "priority": "high",
            "body": "generate-og-image failed on this run and the one before, so og.png (the picture "
            "shown when the site is shared) is not being refreshed. It keeps the last good card. "
            f"Runs: {WORKFLOW_URL}",
        }
    if current == "success" and before >= 2:
        return {
            "title": "Gold Tracker: share card refreshing again",
            "priority": "default",
            "body": f"generate-og-image succeeded after {before} failed runs in a row. {WORKFLOW_URL}",
        }
    return None


def conclusions(runs: Any) -> list[str]:
    """success/failure conclusions, newest first. ValueError when `runs` is not the API's shape."""
    rows = runs.get("workflow_runs") if isinstance(runs, dict) else runs
    if not isinstance(rows, list):
        raise ValueError("not a list of workflow runs")
    return [
        r["conclusion"]
        for r in rows
        if isinstance(r, dict) and r.get("conclusion") in ("success", "failure")
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--current", required=True, help="this run's job status")
    ap.add_argument("--previous", required=True, type=Path, help="earlier runs, API JSON")
    ap.add_argument("--dry-run", action="store_true", help="print the message, send nothing")
    args = ap.parse_args(argv)

    current = args.current.lower()
    try:
        previous = conclusions(json.loads(args.previous.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        # Fail closed: without the history a failure cannot be judged, so a failure pages, and says
        # why it paged rather than claiming a second failure it could not see.
        print("::warning::could not read the earlier runs; judging this run alone")
        if current != "failure":
            print("no page")
            return 0
        msg: dict[str, str] | None = {
            "title": "Gold Tracker: share card failed (run history unreadable)",
            "priority": "high",
            "body": "generate-og-image failed, and its earlier runs could not be read to tell "
            "whether this is a repeat, so this pages on every failure until they can. "
            f"og.png keeps the last good card. Runs: {WORKFLOW_URL}",
        }
    else:
        print(f"this run: {current}; earlier failures in a row: {streak(previous)}")
        msg = decide(current, previous)
    if msg is None:
        print("no page")
        return 0
    print(f"PAGE: {msg['title']} -- {msg['body']}")
    if args.dry_run:
        return 0
    import ops_log  # scripts/ is on sys.path when this file runs as a script

    ops_log.record("og_run_alert", msg["title"], msg["body"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
