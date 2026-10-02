"""scripts/check_test_counts.py -- fails if a test file's number of test definitions falls below the
committed baseline (tests/test_count_baseline/) without the baseline being updated in the same PR.

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

Storage (2026-09-26): one small file per test file, not one shared JSON. The baseline for
`tests/test_x.py` lives at `tests/test_count_baseline/tests/test_x.py.count` and holds a single
integer. The single `tests/test_count_baseline.json` it replaces was the conflict hotspot of the
2026-09-26 merge train: every PR that added a test file added a line to the same JSON object, so
any two such PRs conflicted there. With one file per test file, two PRs that add different test
files touch different paths and cannot conflict; two PRs that change the SAME file's floor still
conflict, which is correct (a human must decide the floor). The rules above are unchanged.
The `.count` suffix keeps these files out of pytest collection and out of this script's own and
check_test_registry_complete.py's test-file sweeps.

Usage:
    python scripts/check_test_counts.py [--repo-root PATH] [--baseline DIR]
    python scripts/check_test_counts.py --update      # rewrite the baseline from the current tree
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

BASELINE_REL = "tests/test_count_baseline"
_COUNT_SUFFIX = ".count"
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


def read_baseline(baseline_dir: Path) -> dict[str, int]:
    """Every `<test path>.count` file under baseline_dir, keyed by the test path it guards.
    Raises ValueError on a file that is not a single non-negative integer."""
    baseline: dict[str, int] = {}
    for p in sorted(baseline_dir.rglob("*" + _COUNT_SUFFIX)):
        rel = p.relative_to(baseline_dir).as_posix()[: -len(_COUNT_SUFFIX)]
        text = p.read_text(encoding="utf-8").strip()
        if not text.isdigit():
            raise ValueError(f"{p}: expected a single non-negative integer, got {text!r}")
        baseline[rel] = int(text)
    return baseline


def write_baseline(baseline_dir: Path, counts: dict[str, int]) -> None:
    """Make baseline_dir hold exactly one `.count` file per entry in counts: write each, and delete
    any whose test file no longer exists (the explicit, reviewable removal)."""
    baseline_dir.mkdir(parents=True, exist_ok=True)
    wanted = {path + _COUNT_SUFFIX for path in counts}
    for p in baseline_dir.rglob("*" + _COUNT_SUFFIX):
        if p.relative_to(baseline_dir).as_posix() not in wanted:
            p.unlink()
    for path, n in counts.items():
        target = baseline_dir / (path + _COUNT_SUFFIX)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or target.read_text(encoding="utf-8").strip() != str(n):
            target.write_text(f"{n}\n", encoding="utf-8", newline="\n")
    for d in sorted((d for d in baseline_dir.rglob("*") if d.is_dir()), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()


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
        write_baseline(baseline_path, current)
        print(
            f"baseline written: {len(current)} file(s), {sum(current.values())} test(s) -> {baseline_path}"
        )
        return 0

    if not baseline_path.is_dir():
        print(
            f"ERROR: baseline {baseline_path} missing -- create it with --update", file=sys.stderr
        )
        return 2
    try:
        baseline = read_baseline(baseline_path)
        assert baseline
    except (ValueError, AssertionError):
        print(f"ERROR: baseline {baseline_path} is malformed or empty", file=sys.stderr)
        return 2

    failures, notes = compare(current, baseline)
    for n in notes:
        print(f"note: {n}")
    if failures:
        print(f"FAIL: {len(failures)} test-count regression(s) against {baseline_path}:")
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
