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
  FIXED (AA2a, audit 2026-09-10): check_foreign_commits now also compares
  against PRs closed in the last CLOSED_PR_WINDOW_DAYS days, not just open
  ones -- see its own docstring for the cost/false-positive numbers behind
  the window and the bot/-branch exclusion. This closes the specific gap
  above but keeps the same rebase blind spot noted below (patch-id
  matching either way).
  CONSIDERED, NOT IMPLEMENTED (AA2b, audit 2026-09-10): a broader
  file-overlap + time-proximity signal was measured against this repo's
  own last-30-days PR history (44 real non-bot, non-scratch PRs, 946
  pairs) as a way to also catch a rebased leak that shifts patch-ids --
  16.7% of pairs share at least one changed file, and 7.5% of ALL pairs
  (71/946) share a file AND were created within 24h of each other, purely
  from ordinary sequential work on the same files (e.g. this audit's own
  Y1/Y2/Y3 PRs repeatedly touching docs/RUNBOOK.md and
  worker-deadman/src/deadman.mjs within hours of each other). That is
  ~1.6 false flags per real PR on average -- a gate at that noise level
  either gets muted or trains reviewers to rubber-stamp it, which is
  itself the "control that stops reporting" defect class this audit
  exists to find. Not implemented; the rebase blind spot below remains
  open on its merits, not from neglect.
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
from datetime import UTC, datetime, timedelta

# AA2a (audit 2026-09-10): how far back check_foreign_commits looks for a
# now-closed source PR. 30 days chosen to match this repo's own audit/
# incident-retention horizon (docs/SESSION_AUDIT_2026-08.md); see
# check_foreign_commits' docstring for the measured cost at this window.
CLOSED_PR_WINDOW_DAYS = 30


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


def _recent_closed_prs(
    repo: str, exclude_pr: int, window_days: int = CLOSED_PR_WINDOW_DAYS
) -> list[dict]:
    """PRs (merged or abandoned) closed within the last window_days, excluding
    bot-pr-sync's reusable `bot/`-prefixed branches (.github/actions/bot-pr-sync
    -- every caller passes a fixed, force-pushed branch name like
    bot/data-sync), `scratch/`-prefixed branches (AG2, audit 2026-09-11 --
    see below), and the PR under test itself.

    Measured against this repo's actual history (2026-09-10, 30-day window):
    745 closed PRs total, ~89% (663, sampled at 447/500) on bot/ branches --
    each one a single, machine-authored, data-only commit that can neither
    source nor receive a #1393-style leak (nothing here is a human building a
    branch on top of another open PR's tip). Excluding them leaves ~79 real
    candidates to compare against, not 745 -- the difference between ~1-2
    minutes of added CI time on the ~2.6/day non-bot PRs that actually pay
    this cost, versus an O(n^2) blowup if paid by every one of the ~25 bot
    PRs/day too. See check_foreign_commits for where exclude_pr is also used
    to skip this entirely when the PR under test IS itself a bot/ branch.

    AG2 (audit 2026-09-11): this same closed-PR comparison (AA2a) has no
    concept of DIRECTION -- a commit copied INTO a PR under test reads
    identically to one copied FROM it, since both are "the same patch-id
    appears on both branches." AF1b's own proof method (scratch/-prefixed
    branches, deliberately built off a real PR's tip to reconstruct and
    verify a historical incident, then closed+deleted) triggered exactly
    this false positive against its own source PR -- 3 flags, one per
    scratch PR, immediately after closing them (#1561/#1562/#1563 against
    #1564). Investigated three candidate direction signals before choosing
    this fix, all found unreliable or expensive to establish cheaply:
    commit AUTHOR timestamp is preserved by rebase/cherry-pick and is
    therefore IDENTICAL across both occurrences in exactly this scenario --
    no signal. Commit COMMITTER timestamp differs (rebase/cherry-pick both
    stamp a fresh committer-date) but is actively MISLEADING here: the
    scratch branches' copy was created (committer-stamped) BEFORE the
    source PR's own commit was rebased to a new SHA, so "earlier
    committer-date wins" would call the derivative the original and the
    original the copy -- backwards. "Which branch did this commit first
    appear on" would need GitHub's push-event/ref history, which isn't
    reliably or cheaply queryable via a stable API for this purpose.
    Direction cannot be established cheaply from any of these -- see the
    PR that introduced this comment for the full writeup.

    Narrowed the comparison instead, following the EXACT precedent already
    set by the bot/ exclusion above: PRs on a `scratch/`-prefixed branch are
    -- by this repo's own established convention, first used by this same
    AA2a work's own proof PRs (#1526-#1530) and reused identically by AF1b
    (#1561-#1563) -- deliberately disposable reconstructions/proofs of
    something that already exists elsewhere (an open PR, a historical
    incident, or a hypothesis under test), never a PR someone intends to
    land. Measured against this repo's real 30-day closed-PR history
    (2026-09-11): 8 of 785 closed PRs (1.0%) are scratch/-prefixed -- all 8
    confirmed audit-proof artifacts (5 from AA2a itself, 3 from AF1b), zero
    genuine feature/fix work. Excluding them removes 100% of the currently-
    observed false-positive instances (3/3) with zero measured true-positive
    cost, since the convention has never (until this session) been used for
    anything but throwaway proof work.

    Residual gap this reopens, stated plainly: a PR carrying genuinely
    dangerous, should-be-caught content, deliberately or carelessly named
    with a `scratch/` prefix, would now slip past this specific comparison.
    Narrower and more acceptable than excluding all closed-unmerged PRs
    (would hide genuinely abandoned/rejected content resurfacing -- exactly
    what this comparison exists to catch) or all branch-deleted closed PRs
    (would gut most of this comparison's real coverage, since deleting a
    branch after closing/merging is routine hygiene, not a leak signal)."""
    cutoff = (datetime.now(UTC) - timedelta(days=window_days)).strftime("%Y-%m-%d")
    prs = _gh_json(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "closed",
            "--search",
            f"closed:>={cutoff}",
            "--json",
            "number,baseRefName,headRefOid,headRefName",
            "--limit",
            "1000",
        ]
    )
    return [
        p
        for p in prs
        if p["number"] != exclude_pr
        and not p["headRefName"].startswith("bot/")
        and not p["headRefName"].startswith("scratch/")
    ]


def check_foreign_commits(pr_number: int, repo: str, base_ref: str) -> list[str]:
    """Returns a list of error messages (empty = pass). Flags any commit
    unique to this PR (relative to its own declared base) whose exact patch
    content also appears as a commit on any OTHER currently open PR, or on a
    PR closed in the last CLOSED_PR_WINDOW_DAYS days (AA2a, audit
    2026-09-10) -- a leak whose source PR was merged/closed before this
    check ran is otherwise invisible, since there is then nothing open left
    to compare against. The closed-PR comparison is skipped when the PR
    under test is itself a bot-pr-sync `bot/`-branch PR: those are always
    single, machine-authored, data-only commits and structurally cannot be
    on either end of this leak mechanism -- see _recent_closed_prs for the
    measured cost this exclusion avoids.

    KNOWN GAP, not fixed here: a leak whose source was rebased/force-pushed
    enough to shift hunk boundaries changes its patch-id and slips past this
    check regardless of the open/closed window -- see the module docstring's
    AA2b entry for why a broader file-overlap+time-proximity signal was
    measured and rejected (7.5% false-positive pair rate on this repo's own
    real PR history, ~1.6 false flags per real PR)."""
    pr = _gh_json(
        ["pr", "view", str(pr_number), "--repo", repo, "--json", "headRefOid,headRefName"]
    )
    head_sha = pr["headRefOid"]
    this_patch_ids = _commit_patch_ids(base_ref, head_sha)
    if not this_patch_ids:
        return []

    other_open_prs = _gh_json(
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
    candidates = list(other_open_prs)
    is_bot_pr = pr["headRefName"].startswith("bot/")
    if not is_bot_pr:
        candidates.extend(_recent_closed_prs(repo, exclude_pr=pr_number))

    errors: list[str] = []
    for other in candidates:
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
                f"commit {other_sha[:8]} on PR #{other_number}'s branch -- this PR's "
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
