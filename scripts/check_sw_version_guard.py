"""scripts/check_sw_version_guard.py -- lint.yml's sw-version-guard job's decision logic,
factored out of inline bash so it is unit-testable (previously the entire rule lived as a
`run: |` block in .github/workflows/lint.yml with no test coverage of its own).

The rule this enforces has TWO shapes depending on how GitHub Pages is currently deployed
(repo variable PAGES_BUILD_MODE -- see docs/PAGES_DEPLOY.md and the CACHE INVALIDATION
CONTRACT comment at the top of service-worker.js for the full switch-over story):

  mode == "legacy" (default; PAGES_BUILD_MODE unset or anything other than "actions"):
    Pages still builds straight off master's committed service-worker.js, so VERSION has
    to be hand-bumped in the same PR that changes a precached shell file, or an installed
    client's registration.update() byte-check never notices the shell changed. This is the
    ORIGINAL rule, unchanged in behaviour from the bash it replaces.

  mode == "actions" (PAGES_BUILD_MODE == "actions", set only after GG flips Pages' source
  to "GitHub Actions"): the pages-deploy.yml workflow stamps VERSION from a hash of the
  shell files at build time (scripts/stamp_sw_version.py), so a PR must NEVER hand-edit the
  VERSION line -- that is exactly what caused v58/v59 bump collisions across stacked PRs in
  the first place. A PR is free to change shell files without touching VERSION at all.

evaluate() takes only booleans/strings, not git or the filesystem, so its four decision
paths are testable directly. main() is the thin, mostly-untested wrapper that computes
those booleans from a real `git diff` -- kept as small as possible on purpose.

Usage (in CI, from a checkout with fetch-depth: 0):
    PAGES_BUILD_MODE=$PAGES_BUILD_MODE python scripts/check_sw_version_guard.py --base origin/master
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sw_shell_files import ShellFilesError, shell_files

VALID_MODES = ("legacy", "actions")


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    message: str


def evaluate(
    mode: str,
    precached_changed: bool,
    sw_changed: bool,
    version_line_changed: bool,
) -> GuardResult:
    """Pure decision logic -- see module docstring for the two modes' rules.

    An unrecognised mode fails closed (rule 98a): a typo'd or unexpected repo-variable
    value must never silently fall back to whichever branch happens to be more permissive.
    """
    if mode not in VALID_MODES:
        return GuardResult(
            False,
            f"unknown PAGES_BUILD_MODE {mode!r} -- expected one of {VALID_MODES}. "
            "Refusing to guess which VERSION rule applies.",
        )

    if mode == "legacy":
        if precached_changed:
            if not sw_changed:
                return GuardResult(
                    False,
                    "a precached shell file (see scripts/sw_shell_files.py) changed but "
                    "service-worker.js wasn't touched. Bump VERSION in service-worker.js "
                    "-- see the CACHE INVALIDATION CONTRACT comment at the top of that file.",
                )
            if not version_line_changed:
                return GuardResult(
                    False,
                    "service-worker.js changed but the VERSION const wasn't. Bump VERSION "
                    "-- see the CACHE INVALIDATION CONTRACT comment at the top of that file.",
                )
        return GuardResult(True, "ok (legacy mode)")

    # mode == "actions"
    if version_line_changed:
        return GuardResult(
            False,
            "VERSION is stamped automatically at build/deploy time from a hash of the "
            "shell files (scripts/stamp_sw_version.py) now that Pages builds via "
            "pages-deploy.yml -- do not hand-edit the VERSION line in a PR, that's exactly "
            "what caused conflicting v58/v59 bumps across stacked PRs. See the CACHE "
            "INVALIDATION CONTRACT comment at the top of service-worker.js.",
        )
    return GuardResult(True, "ok (actions mode)")


def _git_diff_name_only(repo_root: Path, base: str) -> set[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        cwd=repo_root,
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return {line for line in out.splitlines() if line}


def _version_line_changed(repo_root: Path, base: str) -> bool:
    out = subprocess.run(
        ["git", "diff", f"{base}...HEAD", "--", "service-worker.js"],
        cwd=repo_root,
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    return any(line.startswith(("+const VERSION", "-const VERSION")) for line in out.splitlines())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--base", required=True, help="base ref to diff against, e.g. origin/master")
    ap.add_argument(
        "--mode",
        default=None,
        help="override PAGES_BUILD_MODE (mainly for local testing); "
        "else read from the PAGES_BUILD_MODE env var, default 'legacy'",
    )
    args = ap.parse_args()

    mode = args.mode if args.mode is not None else (os.environ.get("PAGES_BUILD_MODE") or "legacy")

    try:
        precached = set(shell_files(args.repo_root))
    except ShellFilesError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    changed = _git_diff_name_only(args.repo_root, args.base)
    precached_changed = bool(changed & precached)
    sw_changed = "service-worker.js" in changed
    version_line_changed = sw_changed and _version_line_changed(args.repo_root, args.base)

    result = evaluate(mode, precached_changed, sw_changed, version_line_changed)
    if not result.ok:
        print(f"::error::{result.message}", file=sys.stderr)
        return 1
    print(result.message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
