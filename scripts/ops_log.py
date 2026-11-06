"""The ops log: where system-health alerts go instead of the phone (owner decision 2026-11-06).

ntfy carries gold-price information only. Everything about the system's own health (a stuck bot PR,
a red check, a parked branch, a visitor-count run) is recorded here instead:

    python scripts/ops_log.py --source ci-health --title "Check red" --body "what happened"

`record()` does two things and never raises:
  1. appends a markdown section to $GITHUB_STEP_SUMMARY when that file is set (the run page shows it);
  2. prints `::warning title=ops-alert::<source>: <title> -- <body on one line>`, a workflow
     annotation. Annotations survive in the check run, so scripts/read_ops_log.py can list them
     across runs for the weekly report.

The repo is PUBLIC and so is every job summary and annotation: never put a secret, a token, a topic
name or a visitor count in `body`.

Stdlib only: runs on the runner's own python3.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

ANNOTATION_TITLE = "ops-alert"
_MAX_ANNOTATION = 900  # GitHub truncates long annotation messages; keep the one-liner readable


def one_line(text: str) -> str:
    """Collapse all whitespace (and newlines) so the annotation stays on a single log line."""
    return re.sub(r"\s+", " ", text).strip()


def annotation_line(source: str, title: str, body: str) -> str:
    """The exact `::warning` line; `%`, CR and LF are escaped the way the runner expects."""
    msg = f"{one_line(source)}: {one_line(title)} -- {one_line(body)}"[:_MAX_ANNOTATION]
    msg = msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::warning title={ANNOTATION_TITLE}::{msg}"


def summary_section(source: str, title: str, body: str) -> str:
    """The markdown block appended to the job summary."""
    return f"### ops-alert: {one_line(title)}\n\nsource: `{one_line(source)}`\n\n{body.strip()}\n\n"


def record(
    source: str,
    title: str,
    body: str,
    environ: Mapping[str, str] | None = None,
    stream=None,
) -> str:
    """Write one ops-log entry; return the annotation line. Best effort: never raises."""
    env = os.environ if environ is None else environ
    out = sys.stdout if stream is None else stream
    line = annotation_line(source, title, body)
    summary = env.get("GITHUB_STEP_SUMMARY", "")
    if summary:
        try:
            with Path(summary).open("a", encoding="utf-8") as fh:
                fh.write(summary_section(source, title, body))
        except OSError as exc:
            print(f"ops_log: could not write the job summary: {type(exc).__name__}", file=out)
    print(line, file=out)
    return line


def main(argv: list[str] | None = None) -> int:
    """CLI used by workflow steps. Always exits 0: logging must never fail a run."""
    ap = argparse.ArgumentParser(description="Record one ops-log entry (no push is sent).")
    ap.add_argument("--source", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--body", default="")
    args = ap.parse_args(argv)
    record(args.source, args.title, args.body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
