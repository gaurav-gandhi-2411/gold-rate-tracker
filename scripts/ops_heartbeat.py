"""One "alive" line a day in the ops log, and a plain note when the log has been silent too long.

2026-11-16: the ops log had no entry after 2026-11-12 03:18Z. Every sender run since then was
enumerated and none had anything to write, so the quiet was real; but nothing could tell "quiet"
from "writer broken". This closes that: once a day it

  1. reads the newest ops-log entry (BEFORE writing, so the heartbeat cannot hide a silence),
  2. says plainly in the job summary and as an annotation when that entry is older than 48 h,
  3. writes one `heartbeat: alive` line, and reads the file back to confirm the write landed
     (a failed write is a warning on the run, never a silent pass).

The note goes to the job summary and the annotation, never to ntfy (owner decision 2026-11-06: ntfy
carries gold-price information only). Never fails the run. Stdlib only.

    python scripts/ops_heartbeat.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import ops_log

SILENT_AFTER = timedelta(hours=48)
SOURCE = "heartbeat"
TITLE = "alive"


def newest_entry(text: str) -> datetime | None:
    """Timestamp of the newest parseable entry in the file text; None when there is none."""
    newest: datetime | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            ts = datetime.strptime(str(json.loads(line)["ts"]), "%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, KeyError, TypeError):
            continue
        ts = ts.replace(tzinfo=UTC)
        if newest is None or ts > newest:
            newest = ts
    return newest


def silence_note(newest: datetime | None, now: datetime) -> tuple[bool, str]:
    """(silent, plain sentence). Silent means no entry at all, or none within SILENT_AFTER."""
    if newest is None:
        return True, "OPS LOG SILENT: the ops log has no readable entry at all."
    age = now - newest
    hours = int(age.total_seconds() // 3600)
    stamp = newest.strftime("%Y-%m-%d %H:%MZ")
    if age > SILENT_AFTER:
        return True, (
            f"OPS LOG SILENT: no entry for {hours} h (newest {stamp}; limit 48 h). Either "
            "nothing needed saying or the writers are broken: check the senders' runs for "
            "'ops log write failed' lines."
        )
    return False, f"ops log alive: newest entry {hours} h ago ({stamp})."


def _read(repo: str, gh: ops_log.GhFn) -> tuple[bool, datetime | None, str]:
    """(read_ok, newest, error text)."""
    try:
        got = ops_log.read_remote(repo, gh)
    except ops_log.OpsLogWriteError as exc:
        return False, None, str(exc)
    return True, newest_entry(got[1]) if got else None, ""


def run(
    now: datetime,
    environ: dict[str, str] | None = None,
    gh: ops_log.GhFn = ops_log.run_gh,
    out=None,
) -> int:
    env = dict(os.environ) if environ is None else environ
    out = sys.stdout if out is None else out
    repo = env.get("GITHUB_REPOSITORY", "")
    if not repo:
        print("ops-heartbeat: no GITHUB_REPOSITORY; nothing to do", file=out)
        return 0
    ok, before, err = _read(repo, gh)
    if ok:
        silent, note = silence_note(before, now)
    else:
        silent, note = True, f"OPS LOG UNREADABLE: {err[:160]}"
    summary = env.get("GITHUB_STEP_SUMMARY", "")
    if summary:
        try:
            with Path(summary).open("a", encoding="utf-8") as fh:
                fh.write(f"### Ops log\n\n{note}\n\n")
        except OSError:
            print("ops-heartbeat: could not write the job summary", file=out)
    print(f"::{'warning' if silent else 'notice'} title=ops-log-heartbeat::{note}", file=out)
    ops_log.record(SOURCE, TITLE, note[:200], environ=env, stream=out, gh=gh)
    if env.get("OPS_LOG_NO_FILE") or env.get("GITHUB_EVENT_NAME") == "pull_request":
        return 0  # record() writes no file in these cases, so there is nothing to confirm
    ok2, after, err2 = _read(repo, gh)
    landed = ok2 and after is not None and (before is None or after > before)
    if not landed:
        why = err2 or "the newest entry did not advance"
        print(
            f"::warning title=ops-log-heartbeat::heartbeat write NOT confirmed ({why[:120]})",
            file=out,
        )
    else:
        print("ops-heartbeat: write confirmed (newest entry advanced)", file=out)
    return 0


def main() -> int:
    return run(datetime.now(UTC))


if __name__ == "__main__":
    sys.exit(main())
