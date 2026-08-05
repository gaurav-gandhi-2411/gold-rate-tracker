#!/usr/bin/env python3
"""Fail CI if a .portfolio/metrics.json metric has no committed artifact backing it.

A number without a committed source is a build failure, not a warning (2026-08-05
audit, see gg-portfolio's provenance.md wave 19 section for the incident this
guards against: a gitignored eval report drifted from what the repo's own
manifest claimed, and nobody could tell because neither was in version control).

Pass rule: source_file must either (a) be a real path, relative to the repo
root, that `git ls-files` reports as tracked, or (b) be an explicit non-file
citation this script recognizes (e.g. "pytest --collect-only (live run, not a
committed artifact)") -- those are allowed but printed as a warning, since a
live-run citation can't be re-verified from history the way a committed file
can. Anything else -- a path that doesn't exist, or exists but isn't tracked
-- is a hard failure.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MANIFEST_PATH = Path(".portfolio/metrics.json")


def tracked_files() -> set[str]:
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return set(out.stdout.splitlines())


def main() -> int:
    if not MANIFEST_PATH.exists():
        print(f"no {MANIFEST_PATH} in this repo -- nothing to check")
        return 0

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    tracked = tracked_files()
    failures: list[str] = []
    warnings: list[str] = []

    for metric in manifest.get("metrics", []):
        mid = metric.get("id", "<no id>")
        source_file = metric.get("source_file")
        if not source_file:
            failures.append(f"{mid}: no source_file at all")
            continue
        # Non-file citations are explicit strings containing a paren-note or a
        # space (a real repo-relative path never has spaces in this convention).
        if "(" in source_file or " " in source_file:
            warnings.append(f"{mid}: non-file citation ({source_file!r}) -- can't be verified from git history")
            continue
        if source_file not in tracked:
            failures.append(f"{mid}: source_file {source_file!r} is not a tracked file (missing or gitignored)")

    for w in warnings:
        print(f"WARN: {w}")

    if failures:
        print(f"\nFAIL: {len(failures)} metric(s) in {MANIFEST_PATH} have no committed artifact:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"OK: every {MANIFEST_PATH} metric with a file citation is committed ({len(warnings)} non-file citation(s) warned above).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
