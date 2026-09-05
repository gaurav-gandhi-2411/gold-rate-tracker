"""scripts/check_pr_boundary_leak.py -- detects the exact mechanism that let
PR #1393's un-reviewed threshold change reach master through PR #1394's
squash-merge (audit 2026-09-05, X2), rebuilt around the EFFECT rather than
branch ancestry (Z1, audit 2026-09-05).

What happened: #1394's branch was created via `git checkout -b` while still
checked out on #1393's branch instead of returning to master first. #1394's
own diff was small and correctly scoped, but its branch history included
#1393's commit as an ancestor -- so squash-merging #1394 computed the diff
against master and pulled #1393's boundary-gated content in with it. #1393
itself still correctly showed as OPEN, unmerged. The STOP boundary held at
the "click merge" level and failed at the artifact level, and nothing
detected it -- it was found by chance while preparing an unrelated PR.

V1 of this script (still in git history) added two checks: check_branch_base
(an ancestry-parent check) and check_boundary_overlap (a label + file-overlap
heuristic). Y2 (audit 2026-09-05) pressure-tested both with real, deliberately
constructed scratch PRs and found check_branch_base CANNOT fire for the exact
incident it was named for: a branch built by `checkout -b` off an unmerged
sibling PR's tip always has a divergence-point parent that is trivially an
ancestor of the current base (the sibling branch was itself normally forked
from base, and base only moves forward) -- so `is-ancestor` is always true,
by construction, regardless of whether a leak occurred. Verified via scratch
PR #1419 (built off boundary-gated #1406's tip): check_branch_base returned
zero errors. check_boundary_overlap only caught it because #1406 happened to
carry the "boundary-gated" label; the worst case (#1421, built off UNLABELED
#1420's tip) made the WHOLE script report "OK", exit 0, on a genuine leak.
check_branch_base is deleted here rather than kept as apparent coverage that
cannot actually fire (rule: a check that cannot fire is worse than no check).

Two checks now, both effect-based -- they inspect what the diff actually
contains, not how the branch claims to be built, and need no label:

1. check_foreign_commits(): for every commit unique to this PR (relative to
   ITS OWN declared base -- so an intentionally stacked PR, base declared as
   another PR's branch, is unaffected), compute its `git patch-id --stable`
   and check whether that exact patch content ALSO appears as a commit on
   ANY other currently-open PR in this repo (no label filter). A shared
   patch-id means this PR's diff includes content that has not been through
   THIS PR's own review -- it was authored as part of a different, still-open
   PR. Patch-id (not raw commit SHA) also catches a cherry-picked leak, which
   ancestry-based reasoning could never catch even in principle.

2. check_file_level_residue(): every file in the PR's full diff vs its base
   must be explained by at least one commit unique to that branch. Catches
   any file change that a naive per-commit review would miss -- most plausibly
   a merge commit whose own combined-diff view hides a change explained by
   only one parent, or any other tooling anomaly that decouples the recorded
   commit list from the actual tree diff.

3. check_boundary_overlap(): kept from V1 -- still a cheap, high-value signal
   ON TOP of the above when the overlapping PR happens to be manually labeled
   "boundary-gated" (an explicit human signal that a human decision is
   required first), even though it is no longer the primary defense.

Known blind spots (state plainly, not hidden -- see the module's own
docstring discipline, rule 85a):

- A leak from a PR whose branch has since been rebased/force-pushed so its
  commits no longer literally exist with the same patch content (e.g. the
  source content was squashed together with unrelated changes) can slip
  past check_foreign_commits -- patch-id matching requires the leaked
  commit's diff hunks to still be byte-for-byte reproducible from the
  source PR's CURRENT head. A rebase that reflows unrelated surrounding
  lines can change hunk boundaries enough to change the patch-id.
- Two independent, unrelated PRs that happen to make a byte-identical small
  change (e.g. both bump the same dependency to the same version, both fix
  the same one-line typo) will produce a real patch-id collision and fail
  this check even though nothing leaked. This is a genuine, rare noise
  source -- documented here rather than silently suppressed, since silently
  excluding "small" patches would reopen exactly the kind of narrow-window
  blind spot this audit keeps finding (see check_boundary_overlap's own
  render-window gap in `scripts/audit_silent_fallbacks.py`). A human
  re-reading the flagged commit is the correct resolution, not a threshold.
- check_foreign_commits only compares against OTHER CURRENTLY OPEN PRs. A
  leak from a PR that was closed/deleted between the leak and this check
  running is invisible to it (there is nothing left to compare against) --
  the same blind spot check_boundary_overlap's label-gating had, moved from
  "labeled" to "still open," not eliminated.
- check_file_level_residue trusts `gh pr diff --name-only` and local
  `git show --name-only` to agree on what a rename/mode-only change is
  called; a GitHub-side rename-detection quirk that diverges from git's own
  local rename heuristics could theoretically produce a false residue flag.
  Not observed in testing; documented as a theoretical edge the pressure
  test below did not specifically exercise.

Usage (in CI, `gh` CLI must be authenticated -- GITHUB_TOKEN is sufficient
for read-only PR/file listing):
    python scripts/check_pr_boundary_leak.py --pr <number> --repo <owner/repo>

Exits 1 (fails the check) on any violation. Never silently skips a check it
could not complete (rule 98a) -- a `gh`/`git` call that fails is a hard
failure of this script, not a pass-through.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys


class BoundaryLeakError(Exception):
    pass


# Commit messages/diffs in this repo routinely carry non-ASCII characters
# (em-dashes, per this repo's own prose style). subprocess.run(text=True)
# with no explicit encoding falls back to locale.getpreferredencoding(),
# which is cp1252 on Windows and silently corrupts (or crashes, in
# capture_output's background reader threads) on those bytes. Explicit
# UTF-8 + errors="replace" makes this correct and deterministic on every
# platform, matching what the GitHub Actions (Linux, UTF-8-locale) runner
# already did implicitly -- this is a portability fix, not a behavior
# change for CI, but it is a mandatory fix for local reproduction/testing.
def _run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **kwargs,  # type: ignore[arg-type]
    )


def _gh_json(args: list[str]) -> object:
    """Run a `gh` CLI command expecting JSON output. Raises on any failure
    (non-zero exit, unparseable output) -- never returns a default value
    that could be mistaken for a real (empty) result."""
    result = _run(["gh", *args])
    if result.returncode != 0:
        raise BoundaryLeakError(
            f"gh {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BoundaryLeakError(f"gh {' '.join(args)} returned unparseable output: {exc}") from exc


def _git(args: list[str]) -> str:
    result = _run(["git", *args])
    if result.returncode != 0:
        raise BoundaryLeakError(
            f"git {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _gh_pr_diff_names(pr_number: int, repo: str) -> set[str]:
    result = _run(["gh", "pr", "diff", str(pr_number), "--repo", repo, "--name-only"])
    if result.returncode != 0:
        raise BoundaryLeakError(
            f"gh pr diff --name-only failed for #{pr_number}: {result.stderr.strip()}"
        )
    return {line for line in result.stdout.strip().splitlines() if line}


def _unique_commits(base_ref: str, head_sha: str) -> list[str]:
    """Commits reachable from head_sha but not from origin/base_ref. Fetches
    both first so this is correct even against a stale local clone."""
    _git(["fetch", "origin", base_ref, head_sha])
    log = _git(["log", f"origin/{base_ref}..{head_sha}", "--format=%H", "--reverse"])
    return [line for line in log.splitlines() if line]


def _patch_id_for_commit(sha: str) -> str | None:
    """Returns the stable patch-id for one commit's own diff, or None if the
    commit introduces no content of its own (e.g. an empty merge commit)."""
    show = _run(["git", "show", sha])
    if show.returncode != 0:
        raise BoundaryLeakError(f"git show {sha} failed: {show.stderr.strip()}")
    pid = _run(["git", "patch-id", "--stable"], input=show.stdout)
    if pid.returncode != 0:
        raise BoundaryLeakError(f"git patch-id failed for {sha}: {pid.stderr.strip()}")
    line = pid.stdout.strip()
    if not line:
        return None
    return line.split()[0]


def _commit_patch_ids(base_ref: str, head_sha: str) -> dict[str, str]:
    """Returns {patch_id: commit_sha} for every commit unique to head_sha
    relative to origin/base_ref."""
    result: dict[str, str] = {}
    for sha in _unique_commits(base_ref, head_sha):
        patch_id = _patch_id_for_commit(sha)
        if patch_id:
            result[patch_id] = sha
    return result


def check_foreign_commits(pr_number: int, repo: str, base_ref: str) -> list[str]:
    """Returns a list of error messages (empty = pass). Flags any commit
    unique to this PR (relative to its own declared base) whose exact patch
    content also appears as a commit on any OTHER currently open PR."""
    pr = _gh_json(["pr", "view", str(pr_number), "--repo", repo, "--json", "headRefOid"])
    head_sha = pr["headRefOid"]
    this_patch_ids = _commit_patch_ids(base_ref, head_sha)
    if not this_patch_ids:
        return []

    other_prs = _gh_json(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--json",
            "number,baseRefName,headRefOid",
        ]
    )
    errors: list[str] = []
    for other in other_prs:
        other_number = other["number"]
        if other_number == pr_number:
            continue
        other_patch_ids = _commit_patch_ids(other["baseRefName"], other["headRefOid"])
        for pid, own_sha in this_patch_ids.items():
            other_sha = other_patch_ids.get(pid)
            if other_sha is None:
                continue
            errors.append(
                f"Commit {own_sha[:8]} carries the same content (patch-id {pid[:12]}) as "
                f"commit {other_sha[:8]} on open PR #{other_number}'s branch -- this PR's "
                f"diff includes content that has not been reviewed as part of THIS PR. If "
                f"#{other_number}'s content is meant to land here, merge or land #{other_number} "
                f"first instead of carrying it in via this branch."
            )
    return errors


def check_file_level_residue(pr_number: int, repo: str, base_ref: str) -> list[str]:
    """Returns a list of error messages (empty = pass). Every file in the
    PR's full diff vs base must be explained by at least one commit unique
    to this branch -- catches content a naive per-commit review would miss
    (e.g. hidden inside a merge commit)."""
    pr = _gh_json(["pr", "view", str(pr_number), "--repo", repo, "--json", "headRefOid"])
    head_sha = pr["headRefOid"]
    full_diff_files = _gh_pr_diff_names(pr_number, repo)
    unique_commits = _unique_commits(base_ref, head_sha)

    if not unique_commits:
        if full_diff_files:
            return [
                f"PR has {len(full_diff_files)} changed file(s) but zero commits unique to "
                f"origin/{base_ref} -- the diff is not attributable to any reviewable commit."
            ]
        return []

    explained_files: set[str] = set()
    for sha in unique_commits:
        result = _run(["git", "show", "--name-only", "--format=", sha])
        if result.returncode != 0:
            raise BoundaryLeakError(f"git show --name-only {sha} failed: {result.stderr.strip()}")
        explained_files.update(line.strip() for line in result.stdout.splitlines() if line.strip())

    residue = full_diff_files - explained_files
    if residue:
        return [
            f"File(s) changed in the full diff vs {base_ref} but not attributable to any "
            f"commit unique to this branch: {', '.join(sorted(residue))}. This can happen "
            "with a merge commit that silently pulls in tree changes -- verify none of this "
            "content is unreviewed."
        ]
    return []


def check_boundary_overlap(pr_number: int, repo: str) -> list[str]:
    """Returns a list of error messages (empty = pass). Checks this PR's
    changed files against every other open PR carrying the boundary-gated
    label. Weaker evidence than check_foreign_commits on its own (two
    unrelated, legitimate PRs can touch the same file), kept as a cheap,
    explicit, high-value supplementary signal when the overlapping PR
    carries an explicit "needs a human decision" label."""
    errors: list[str] = []
    this_files = _gh_pr_diff_names(pr_number, repo)

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
        other_files = _gh_pr_diff_names(other_number, repo)
        overlap = this_files & other_files
        if overlap:
            errors.append(
                f"Shares file(s) with open boundary-gated PR #{other_number}: "
                f"{', '.join(sorted(overlap))}. If #{other_number}'s content is not "
                f"meant to land via this PR, check check_foreign_commits' output above "
                f"and verify #{other_number}'s commits are not present on this branch."
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
        all_errors.extend(check_foreign_commits(args.pr, args.repo, args.base))
        all_errors.extend(check_file_level_residue(args.pr, args.repo, args.base))
        all_errors.extend(check_boundary_overlap(args.pr, args.repo))
    except BoundaryLeakError as exc:
        print(f"FAIL (could not complete check -- failing closed, not skipping): {exc}")
        return 1

    if all_errors:
        print(f"FAIL: PR #{args.pr} boundary-leak check found {len(all_errors)} issue(s):")
        for e in all_errors:
            print(f"  - {e}")
        return 1

    print(
        f"OK: PR #{args.pr} -- no foreign commits, no unexplained file residue, "
        "no boundary-gated file overlap."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
