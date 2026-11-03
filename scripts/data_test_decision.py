"""Decide a data-only PR's tests: only the tests that read data/ can fail it (FOR_GG item 11).

GG decision 2026-11-03 (option C refined): on a PR that changes only data, the tests that read the
committed data run after the lock install and decide; a failure in a test that never touches data
cannot have been caused by the PR (it changed only data) and is reported, not blocking.

A failed test BLOCKS when any of these holds:
  * it read data/ during this run (measured by scripts/data_reads_plugin.py);
  * any test in its FILE read data/, or the file read data/ while being imported: a value loaded
    once and cached (lru_cache, a module-level load) is read only by the first test that needs it,
    and a later test failing on that bad value must still block;
  * its file names data/ in its source (scripts/select_data_tests.py), which covers a test that
    reads data/ through a child process or a native reader the measurement cannot see.
Any collection error blocks, and so does a pytest run that stopped early (exit status other than
0 = all passed or 1 = some failed): a partial record proves nothing about the tests that never ran.
A missing, unreadable or empty record is an ERROR, never a pass.

Residual (stated, not covered): a cache filled by a test in a DIFFERENT file and read by a failing
test in a file that neither reads data/ nor names it. Over-blocking is cheap here (the tests run on
the pinned lock, so a code-caused failure on a data-only PR means master itself is red).

Usage: python scripts/data_test_decision.py --reads FILE [--root DIR]
Exit 0 = pass, 1 = a blocking failure, 2 = error (no usable record). Prints one summary line
starting with "DATA TESTS:" and the blocking / non-blocking failures, indented.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from select_data_tests import select


def decide(record: dict, static_files: set[str]) -> tuple[str, list[str], list[str], int]:
    """(verdict, blocking, non_blocking, n_reading). Raises ValueError on an unusable record."""
    tests = record.get("tests")
    errors = record.get("collect_errors") or []
    if record.get("version") != 1 or not isinstance(tests, dict) or not isinstance(errors, list):
        raise ValueError("no per-test record (plugin not loaded)")
    if not tests and not errors:
        raise ValueError("no test ran")
    # a collection error stops pytest before any test runs; it blocks on its own
    blocking = [f"collection error: {n}" for n in errors]
    status = record.get("exitstatus")
    if status not in (0, 1):
        blocking.append(f"pytest stopped early (exit status {status}); the record is partial")

    def _file(node: str) -> str:
        return node.split("::", 1)[0].replace("\\", "/")

    reading_files = {_file(n) for n, r in tests.items() if r.get("data_reads")}
    reading_files |= {k.replace("\\", "/") for k in (record.get("collect_reads") or {})}
    non_blocking = []
    n_reading = 0
    for node, r in sorted(tests.items()):
        reads = bool(r.get("data_reads"))
        n_reading += reads
        if r.get("outcome") != "failed":
            continue
        f = _file(node)
        if reads:
            blocking.append(f"{node} (read {', '.join(r['data_reads'][:2])})")
        elif f in reading_files:
            blocking.append(f"{node} (its file read data/; a cached value may be the cause)")
        elif f in static_files:
            blocking.append(f"{node} (names data/ in its source)")
        else:
            non_blocking.append(node)
    return ("fail" if blocking else "pass"), blocking, non_blocking, n_reading


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--reads", type=Path, required=True)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = ap.parse_args(argv)
    try:
        record = json.loads(args.reads.read_text(encoding="utf-8"))
        static_files = set(select(args.root))
        verdict, blocking, non_blocking, n_reading = decide(record, static_files)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(f"DATA TESTS: error ({exc})")
        return 2
    print(
        f"DATA TESTS: {verdict} tests={len(record['tests'])} reading_data={n_reading} "
        f"static_files={len(static_files)} blocking={len(blocking)} "
        f"non_blocking_failures={len(non_blocking)}"
    )
    for b in blocking:
        print(f"  BLOCKING: {b}")
    for n in non_blocking:
        print(f"  not blocking (reads no data): {n}")
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
