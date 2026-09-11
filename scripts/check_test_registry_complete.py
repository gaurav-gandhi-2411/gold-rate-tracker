"""scripts/check_test_registry_complete.py -- fails if a JS test file exists
in the repo but is referenced by NO GitHub Actions workflow, unless it is
explicitly listed in KNOWN_EXCLUSIONS with a stated reason (AJ2, production
audit, 2026-09-11/12).

The incident this closes: this session added
worker-deadman/test/pr_trigger_health.test.mjs and, in the same PR, had to
separately remember to add it to lint.yml's Worker-test step's explicit
`node --test <file1> <file2>` line -- a hand-enumerated file list that would
otherwise have silently never run the new file in CI (it would still pass
locally, since `node --test` run without arguments in that directory WOULD
pick it up, making the gap invisible unless someone specifically diffed the
two lists). A hand-typed test registry drifts exactly like a hand-typed
number (rule 65c's own framing, applied to CI wiring instead of published
metrics).

Swept the whole repo (excluding node_modules, .git, __pycache__) for every
JS test file across every naming convention actually in use here --
`test_*.js`, `test_*.mjs`, `*.test.js`, `*.test.mjs` -- not just the first
convention found (rule 85b: enumerate every shape before writing the
pattern). Python is NOT swept: `pytest tests/ -v` (lint.yml) is directory-
based discovery, not a hand-enumerated file list, so pytest test files
structurally cannot suffer this exact drift -- a new `tests/test_*.py` file
is picked up automatically. This script's whole reason to exist is that the
JS side of this repo uses hand-enumerated `node --test <file> <file> ...`
lines in three places (lint.yml's pwa-js job, lint.yml's Worker-test step,
scraper-canary.yml) instead of directory-based discovery.

What "referenced" means, and its stated limitation (rule 85a/85b): a test
file counts as referenced if its own relative-to-repo-root path string (or,
for files invoked with a workflow-level `working-directory:` override, its
basename) appears literally anywhere in any `.github/workflows/*.yml` file's
text. This is a grep-level heuristic, not a YAML/shell parser -- it would
miss a file referenced only through a variable or a glob expansion computed
at runtime. No workflow in this repo does that for test invocations as of
this writing (every one uses a literal `node --test <literal paths>` line);
if one ever does, this script would need strengthening, the same way
check_bot_pr_sync_allowlist.py's own docstring names its blind spots rather
than implying full coverage.

KNOWN_EXCLUSIONS: a test file that deliberately never runs in CI, with the
reason stated inline. A file present on disk but absent from BOTH the
workflows text AND this dict is the actual defect this script exists to
catch -- fails closed (reports it as a gap), never silently ignored.

Usage:
    python scripts/check_test_registry_complete.py [--repo-root PATH]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# AJ2 (2026-09-11/12): documented, deliberate exclusions only -- each entry
# is a decision, not a place to silently accumulate gaps. Justification is
# mandatory (a dict value, never just a bare path) so a reader can judge
# whether the reason still holds.
KNOWN_EXCLUSIONS: dict[str, str] = {
    "tests/test_stale_banner_headless.js": (
        "needs Playwright (imports scraper/node_modules/playwright) and a browser "
        "binary -- lint.yml's pwa-js job has neither; tracked as a follow-up "
        "(lint.yml's own comment, unchanged as of this script's writing). AJ2 "
        "(2026-09-11/12) ran it manually and found a live failing assertion -- "
        "see docs/SESSION_AUDIT_2026-08.md for the finding; being excluded from "
        "CI does not mean it's exempt from being fixed."
    ),
}

JS_TEST_FILENAME_PATTERNS = ("test_*.js", "test_*.mjs", "*.test.js", "*.test.mjs")
EXCLUDED_DIR_NAMES = {"node_modules", ".git", "__pycache__"}


class RegistryCheckError(Exception):
    """Raised when the check itself cannot be completed -- callers must
    treat this as a failure, never as a pass (rule 98a: fail closed)."""


def find_all_js_test_files(repo_root: Path) -> list[Path]:
    """Every JS test file under repo_root, across every naming convention
    actually in use in this repo -- returns paths relative to repo_root,
    sorted for stable output."""
    found: set[Path] = set()
    for pattern in JS_TEST_FILENAME_PATTERNS:
        for path in repo_root.rglob(pattern):
            if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
                continue
            found.add(path.relative_to(repo_root))
    return sorted(found)


def read_all_workflow_text(workflows_dir: Path) -> str:
    if not workflows_dir.is_dir():
        raise RegistryCheckError(f"{workflows_dir} does not exist or is not a directory")
    chunks = []
    for path in sorted(workflows_dir.glob("*.yml")):
        chunks.append(path.read_text(encoding="utf-8"))
    if not chunks:
        raise RegistryCheckError(
            f"no .yml files found under {workflows_dir} -- cannot verify anything"
        )
    return "\n".join(chunks)


def find_unreferenced_test_files(
    test_files: list[Path], workflow_text: str, known_exclusions: dict[str, str]
) -> list[Path]:
    """A test file is unreferenced if neither its full relative path nor its
    bare filename appears anywhere in the combined workflow text, AND it is
    not in known_exclusions. Checking the bare filename too (not just the
    full path) accounts for `working-directory:`-scoped steps, where a
    workflow legitimately writes just the filename, not the repo-root-
    relative path."""
    unreferenced = []
    for test_file in test_files:
        posix_path = test_file.as_posix()
        if posix_path in known_exclusions:
            continue
        if posix_path in workflow_text or test_file.name in workflow_text:
            continue
        unreferenced.append(test_file)
    return unreferenced


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()

    repo_root: Path = args.repo_root
    workflows_dir = repo_root / ".github" / "workflows"

    try:
        test_files = find_all_js_test_files(repo_root)
        workflow_text = read_all_workflow_text(workflows_dir)
        unreferenced = find_unreferenced_test_files(test_files, workflow_text, KNOWN_EXCLUSIONS)
    except RegistryCheckError as exc:
        print(f"FAIL: could not complete the registry check -- failing closed. Reason: {exc}")
        return 1

    print(
        f"Swept {len(test_files)} JS test file(s) across test_*.js, test_*.mjs, *.test.js, *.test.mjs."
    )
    print(f"{len(KNOWN_EXCLUSIONS)} documented, deliberate exclusion(s).")

    if unreferenced:
        print(f"FAIL: {len(unreferenced)} JS test file(s) exist but are referenced by NO workflow:")
        for path in unreferenced:
            print(f"  - {path.as_posix()}")
        print(
            "Either add each file to the relevant workflow's `node --test` line, "
            "or add it to KNOWN_EXCLUSIONS in this script with a stated reason."
        )
        return 1

    print("PASS: every JS test file is either referenced by a workflow or a documented exclusion.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
