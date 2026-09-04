"""scripts/check_pr_boundary_leak.py -- detects the exact mechanism that let
PR #1393's un-reviewed threshold change reach master through PR #1394's
squash-merge (audit 2026-09-05, X2).

What happened: #1394's branch was created via `git checkout -b` while still
checked out on #1393's branch instead of returning to master first. #1394's
own diff was small and correctly scoped, but its branch history included
#1393's commit as an ancestor -- so squash-merging #1394 computed the diff
against master and pulled #1393's boundary-gated content in with it. #1393
itself still correctly showed as OPEN, unmerged. The STOP boundary held at
the "click merge" level and failed at the artifact level, and nothing
detected it -- it was found by chance while preparing an unrelated PR.

Two independent checks, either one sufficient to have caught this:

1. check_branch_base(): the PR branch's divergence point from the target
   branch must itself be an ancestor of the target branch. If the first
   commit unique to the branch has a parent that is NOT reachable from the
   target, the branch was built on top of some other (possibly unmerged)
   ref instead of the target directly -- exactly what happened here.

2. check_boundary_overlap(): if any OTHER currently-open PR carries the
   "boundary-gated" label (the convention this session established --
   applied to PRs touching alert thresholds, user-facing copy, or anything
   else on the STOP boundary) and touches any file this PR also touches,
   flag it. A file-overlap heuristic is weaker evidence than #1 on its own
   (two unrelated, legitimate PRs can touch the same file), but combined
   with the boundary-gated label it is a cheap, explicit, high-value
   warning: "you are about to merge something that shares files with a
   change someone deliberately marked as needing a human decision first."

Usage (in CI, `gh` CLI must be authenticated -- GITHUB_TOKEN is sufficient
for read-only PR/file listing):
    python scripts/check_pr_boundary_leak.py --pr <number> --repo <owner/repo>

Exits 1 (fails the check) on either violation. Never silently skips a
check it could not complete (rule 98a) -- a `gh` call that fails is a
hard failure of this script, not a pass-through.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys


class BoundaryLeakError(Exception):
    pass


def _gh_json(args: list[str]) -> object:
    """Run a `gh` CLI command expecting JSON output. Raises on any failure
    (non-zero exit, unparseable output) -- never returns a default value
    that could be mistaken for a real (empty) result."""
    result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise BoundaryLeakError(
            f"gh {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BoundaryLeakError(f"gh {' '.join(args)} returned unparseable output: {exc}") from exc


def _git(args: list[str]) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise BoundaryLeakError(
            f"git {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def check_branch_base(pr_number: int, repo: str, base_ref: str) -> list[str]:
    """Returns a list of error messages (empty = pass). Verifies every commit
    unique to the PR branch has an ancestry rooted in `base_ref`, not in some
    other unmerged branch."""
    errors: list[str] = []
    pr = _gh_json(
        ["pr", "view", str(pr_number), "--repo", repo, "--json", "headRefName,headRefOid"]
    )
    head_sha = pr["headRefOid"]

    _git(["fetch", "origin", base_ref, head_sha])
    unique_commits = _git(
        ["log", f"origin/{base_ref}..{head_sha}", "--format=%H", "--reverse"]
    ).splitlines()
    if not unique_commits:
        # No commits unique to the branch relative to base -- nothing to check
        # (this is itself unusual for an open PR, but not a boundary leak).
        return errors

    oldest_unique = unique_commits[0]
    parent = _git(["rev-parse", f"{oldest_unique}^"])
    is_ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", parent, f"origin/{base_ref}"],
        capture_output=True,
        check=False,
    )
    if is_ancestor.returncode != 0:
        errors.append(
            f"Branch's divergence point ({oldest_unique[:8]}'s parent {parent[:8]}) "
            f"is NOT an ancestor of origin/{base_ref} -- this branch was built on top "
            f"of some other ref (a feature branch, not {base_ref}), so merging it can "
            f"pull in that other ref's un-reviewed content. Rebase onto origin/{base_ref} "
            f"before merging."
        )
    return errors


def check_boundary_overlap(pr_number: int, repo: str) -> list[str]:
    """Returns a list of error messages (empty = pass). Checks this PR's
    changed files against every other open PR carrying the boundary-gated
    label."""
    errors: list[str] = []
    this_files_raw = subprocess.run(
        ["gh", "pr", "diff", str(pr_number), "--repo", repo, "--name-only"],
        capture_output=True,
        text=True,
        check=False,
    )
    if this_files_raw.returncode != 0:
        raise BoundaryLeakError(
            f"gh pr diff --name-only failed for #{pr_number}: {this_files_raw.stderr.strip()}"
        )
    this_files = set(this_files_raw.stdout.strip().splitlines())

    other_prs = _gh_json(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--label",
            "boundary-gated",
            "--state",
            "open",
            "--json",
            "number",
        ]
    )
    for other in other_prs:
        other_number = other["number"]
        if other_number == pr_number:
            continue
        other_files_raw = subprocess.run(
            ["gh", "pr", "diff", str(other_number), "--repo", repo, "--name-only"],
            capture_output=True,
            text=True,
            check=False,
        )
        if other_files_raw.returncode != 0:
            raise BoundaryLeakError(
                f"gh pr diff --name-only failed for boundary-gated #{other_number}: "
                f"{other_files_raw.stderr.strip()}"
            )
        other_files = set(other_files_raw.stdout.strip().splitlines())
        overlap = this_files & other_files
        if overlap:
            errors.append(
                f"Shares file(s) with open boundary-gated PR #{other_number}: "
                f"{', '.join(sorted(overlap))}. If #{other_number}'s content is not "
                f"meant to land via this PR, verify this branch's ancestry "
                f"(check_branch_base) and that #{other_number} is not an ancestor."
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--base", default="master")
    args = parser.parse_args()

    all_errors: list[str] = []
    try:
        all_errors.extend(check_branch_base(args.pr, args.repo, args.base))
        all_errors.extend(check_boundary_overlap(args.pr, args.repo))
    except BoundaryLeakError as exc:
        print(f"FAIL (could not complete check -- failing closed, not skipping): {exc}")
        return 1

    if all_errors:
        print(f"FAIL: PR #{args.pr} boundary-leak check found {len(all_errors)} issue(s):")
        for e in all_errors:
            print(f"  - {e}")
        return 1

    print(f"OK: PR #{args.pr} -- clean branch ancestry, no boundary-gated file overlap.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
