"""scripts/check_test_counts.py -- fails if a test file's number of test definitions falls below the
committed baseline (tests/test_count_baseline.json) without the baseline being updated in the same PR.

The incident this closes (production audit continuation, 2026-09-21): a patch script truncated
worker-deadman/test/pr_trigger_health.test.mjs to an empty file. `node --test` on the remaining
file reported "67 passed, 0 failed" -- green, and hollow, because the missing tests were simply not
collected. A test suite whose members can silently vanish is a control with a narrower surface than
its name (rule 85a): it proves the tests that still exist pass, not that the tests that should exist do.

What is counted, and the stated limits (rule 85a/85b -- name the shapes, name the blind spots):
  * Python `tests/**/test_*.py`: `def test_*` / `async def test_*` at module level and inside
    classes, via `ast` (no imports, so it needs no test dependencies and cannot be fooled by an
    import error hiding a file). A parametrized test counts once, however many cases it expands to.
  * JS/MJS `tests/**`, `scraper/**` (not node_modules), `worker-deadman/test/**`, named `test_*.js|mjs`
    or `*.test.js|mjs`: a regex for lines beginning `test(` / `it(` (also `.skip` / `.only` / `.todo`).
    A test registered through a helper or a loop is invisible to this regex.
  * It does NOT detect a test that is present but weakened (an assertion deleted), skipped at runtime,
    or replaced by an equal number of vacuous tests. It detects removal of tests, nothing subtler.

Rules (all fail closed, exit 1; exit 2 if the tree looks wrong):
  * a baselined file whose current count is LOWER, or which no longer exists, fails;
  * a test file on disk that is not in the baseline fails (an unlisted file is unguarded);
  * a HIGHER count passes (and is noted), so adding tests never needs a baseline edit to merge, but
    `--update` records it and is the only way to lower a baseline: the diff to the baseline file in the
    PR is the explicit, reviewable act.

Usage:
    python scripts/check_test_counts.py [--repo-root PATH] [--baseline PATH]
    python scripts/check_test_counts.py --update      # rewrite the baseline from the current tree
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

BASELINE_REL = "tests/test_count_baseline.json"
_JS_SUFFIXES = (".js", ".mjs")
_JS_TEST_LINE = re.compile(r"^\s*(?:test|it)(?:\.(?:skip|only|todo))?\s*\(", re.M)
_SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv"}
_JS_ROOTS = ("tests", "scraper", "worker-deadman/test")


def _is_js_test_name(name: str) -> bool:
    if not name.endswith(_JS_SUFFIXES):
        return False
    return name.startswith("test_") or ".test." in name


def count_python_tests(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    )


def count_js_tests(path: Path) -> int:
    return len(_JS_TEST_LINE.findall(path.read_text(encoding="utf-8")))


def collect_counts(repo_root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    tests_dir = repo_root / "tests"
    if tests_dir.is_dir():
        for p in sorted(tests_dir.rglob("test_*.py")):
            if _SKIP_DIRS.isdisjoint(p.relative_to(repo_root).parts):
                counts[p.relative_to(repo_root).as_posix()] = count_python_tests(p)
    for root in _JS_ROOTS:
        base = repo_root / root
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(repo_root)
            if p.is_file() and _is_js_test_name(p.name) and _SKIP_DIRS.isdisjoint(rel.parts):
                counts[rel.as_posix()] = count_js_tests(p)
    return counts


def compare(current: dict[str, int], baseline: dict[str, int]) -> tuple[list[str], list[str]]:
    """Return (failures, notes)."""
    failures: list[str] = []
    notes: list[str] = []
    for path, floor in sorted(baseline.items()):
        if path not in current:
            failures.append(f"{path}: baselined at {floor} test(s) but the file no longer exists")
        elif current[path] < floor:
            failures.append(
                f"{path}: {current[path]} test(s), baseline is {floor} (fell by {floor - current[path]})"
            )
        elif current[path] > floor:
            notes.append(f"{path}: {current[path]} > baseline {floor} (raise it with --update)")
    for path in sorted(set(current) - set(baseline)):
        failures.append(
            f"{path}: test file not in the baseline (unguarded) -- run --update and commit it"
        )
    return failures, notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--baseline", type=Path, default=None)
    ap.add_argument(
        "--update", action="store_true", help="rewrite the baseline from the current tree"
    )
    args = ap.parse_args()
    root: Path = args.repo_root
    baseline_path: Path = args.baseline or root / BASELINE_REL

    current = collect_counts(root)
    if not current:
        print(
            f"ERROR: no test files found under {root} -- refusing to pass on an empty sweep",
            file=sys.stderr,
        )
        return 2

    if args.update:
        payload = {
            "_comment": "Minimum number of test definitions per file. Lowering an entry is a deliberate act "
            "made with `python scripts/check_test_counts.py --update`; see scripts/check_test_counts.py.",
            "files": dict(sorted(current.items())),
        }
        baseline_path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
        print(
            f"baseline written: {len(current)} file(s), {sum(current.values())} test(s) -> {baseline_path}"
        )
        return 0

    if not baseline_path.is_file():
        print(
            f"ERROR: baseline {baseline_path} missing -- create it with --update", file=sys.stderr
        )
        return 2
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))["files"]
        assert isinstance(baseline, dict) and baseline
    except (KeyError, ValueError, AssertionError):
        print(f"ERROR: baseline {baseline_path} is malformed or empty", file=sys.stderr)
        return 2

    failures, notes = compare(current, baseline)
    for n in notes:
        print(f"note: {n}")
    if failures:
        print(f"FAIL: {len(failures)} test-count regression(s) against {baseline_path.name}:")
        for f in failures:
            print(f"  - {f}")
        print(
            "If a test was removed on purpose, run `python scripts/check_test_counts.py --update` "
            "and commit the baseline change in the same PR."
        )
        return 1
    print(
        f"PASS: {len(current)} test file(s), {sum(current.values())} test(s); none below its baseline."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
