"""Clock sweep: run test files at several FIXED instants and report tests whose outcome differs.

Uses tests/clock_guard.py in sweep mode (CLOCK_GUARD_FIXED): every clock read that a test did not
pin returns the fixed instant. A test that passes at one instant and fails at another depends on
the time window (the 2026-11-06 T8 class) and must pin its clock.

Usage: python scripts/clock_sweep.py [--files-from FILE] [--out DIR] [test paths...]
Reads the GUARD only: it cannot see time.time(), subprocess clocks or test-owned reads.
Exit 0 always; the report is the product (it is a diagnostic, not a gate).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Weekday and Sunday, both T8 digest windows (IST = UTC+5:30: 03:00Z=08:30, 09:00Z=14:30,
# 14:00Z=19:30, 17:00Z=22:30) and the quiet gap, plus month-end, year-end and a Saturday.
INSTANTS = {
    "Mon 03:00Z": "2026-11-09T03:00:00+00:00",
    "Mon 09:00Z": "2026-11-09T09:00:00+00:00",
    "Mon 14:00Z": "2026-11-09T14:00:00+00:00",
    "Mon 17:00Z": "2026-11-09T17:00:00+00:00",
    "Sun 03:00Z": "2026-11-08T03:00:00+00:00",
    "Sun 14:00Z": "2026-11-08T14:00:00+00:00",
    "Sat month-end 2026-10-31 09:00Z": "2026-10-31T09:00:00+00:00",
    "Thu year-end 2026-12-31 18:30Z": "2026-12-31T18:30:00+00:00",
}


def run_one(label: str, instant: str, paths: list[str], out: Path) -> dict[str, str]:
    """Run pytest once at ``instant``; return {testcase id: passed|failed|skipped}."""
    xml = out / f"sweep_{abs(hash(label))}.xml"
    env = dict(os.environ, CLOCK_GUARD_FIXED=instant)
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-m",
        "not integration",
        "-q",
        "-p",
        "no:cacheprovider",
        f"--junitxml={xml}",
        *paths,
    ]
    subprocess.run(cmd, cwd=ROOT, env=env, check=False, capture_output=True, timeout=1500)
    results: dict[str, str] = {}
    for case in ET.parse(xml).getroot().iter("testcase"):
        key = f"{case.get('classname')}::{case.get('name')}"
        if case.find("failure") is not None or case.find("error") is not None:
            results[key] = "failed"
        elif case.find("skipped") is not None:
            results[key] = "skipped"
        else:
            results[key] = "passed"
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--files-from")
    ap.add_argument("--out", default=str(ROOT / ".clock_sweep_tmp"))
    args = ap.parse_args()
    paths = list(args.paths)
    if args.files_from:
        paths += [p.strip() for p in Path(args.files_from).read_text().splitlines() if p.strip()]
    if not paths:
        print("no test paths given", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    runs: dict[str, dict[str, str]] = {}
    for label, inst in INSTANTS.items():
        runs[label] = run_one(label, inst, paths, out)
        failed = sum(v == "failed" for v in runs[label].values())
        print(f"{label:34} tests={len(runs[label]):5} failed={failed}", flush=True)
    all_ids = sorted(set().union(*[set(r) for r in runs.values()]))
    differing = [i for i in all_ids if len({r.get(i, "missing") for r in runs.values()}) > 1]
    print(f"tests whose outcome differs between instants: {len(differing)}")
    for i in differing:
        print(" ", i, {lbl: r.get(i, "missing") for lbl, r in runs.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
