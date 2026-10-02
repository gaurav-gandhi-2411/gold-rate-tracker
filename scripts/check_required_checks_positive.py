"""scripts/check_required_checks_positive.py -- positive assertion that a
PR's required checks actually passed, before self-merging (AJ1, production
audit, 2026-09-11/12).

The defect this closes: AI1 (§8 instance #20, docs/SESSION_AUDIT_2026-08.md)
found `enforce_admins: false` (ADR 028, a deliberate, accepted decision) means
branch protection does NOT platform-enforce required checks against an admin
merge -- every "all required checks green" claim made while self-merging in
this audit has been THIS SESSION'S OWN discipline reading `gh pr checks` /
`mergeStateStatus`, never something the platform actually verified. That
discipline failed twice: #1539 and #1569 both merged with ZERO check-runs
ever recorded against their head SHA -- not failing, not pending, simply
absent -- and #1539 cost 7 hours of blocked production data sync.

The specific failure shape: "no red checks" and "checks passed" are
different states, and the merge decision (a human, or this session, reading
`gh pr checks`/GitHub's UI) was reading the first. A PR whose checks never
triggered shows literally nothing in `gh pr checks` -- no red, no pending,
no rows at all -- which reads, at a glance, exactly like "nothing to worry
about" instead of "nothing has been verified."

What this script does, precisely: for the given PR, (1) reads the required
contexts LIVE from branch protection (never a hardcoded copy -- same "read
from the one real source" discipline as check_bot_pr_sync_allowlist.py's
allowlist regex), (2) reads the PR's actual head SHA, (3) reads every
check-run recorded against that SHA, (4) for EVERY required context, asserts
a check-run with that name exists AND its most recent conclusion is
literally "success". Absent is treated identically to failed -- a required
context with zero recorded check-runs is a BLOCK, not a pass, not a skip.

What it cannot catch (rule 85a: state a control's real surface, don't imply
more):
- It is invoked discipline, not a platform gate -- exactly the same class
  of thing that already failed twice (a human, or this session, choosing to
  check before merging). Nothing stops a future merge from skipping this
  script entirely, same as nothing stopped #1539/#1569 from merging without
  anyone reading `gh pr checks` closely enough. It converts an implicit
  habit into an explicit, scriptable one -- it does not convert `enforce_
  admins:false` into `true`; only branch protection's own setting does that
  (see docs/adr/028-disable-strict-branch-protection.md's follow-up note).
- It verifies check-run STATE (name + conclusion), not code correctness --
  a required check that genuinely ran and reported "success" is trusted at
  face value, the same trust every CI gate in existence extends to its own
  test suite's result.
- A required context name that has silently drifted from what branch
  protection actually expects (a job renamed without updating protection,
  or vice versa) reads as ABSENT here -- which fails closed correctly, but
  the resulting message says "no check-run found," not "the names have
  drifted," so a human still has to notice the real cause.
- A TOCTOU race exists in principle: a check-run's conclusion could change
  between this script's read and the actual `gh pr merge` call (e.g. a
  flaky re-run started manually in that window). Minimized by running this
  script immediately before merging, in the same invocation, never cached
  from an earlier check -- but not eliminated by construction.

Usage:
    python scripts/check_required_checks_positive.py --pr 1591 [--repo OWNER/REPO]

Exit code 0: every required context has a recorded SUCCESS conclusion --
    safe to merge. Exit code 1: at least one required context is absent,
    pending, or not successful -- do not merge.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

DEFAULT_REPO = "gaurav-gandhi-2411/gold-rate-tracker"


class RequiredChecksError(Exception):
    """Raised when the check itself cannot be completed -- callers must
    treat this as BLOCK, never as an implicit pass (rule 98a: fail closed)."""


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _gh_json(args: list[str]) -> object:
    """Run a `gh` CLI command expecting JSON output. Raises on any failure
    (non-zero exit, unparseable output) -- never returns a default value
    that could be mistaken for a real (empty) result."""
    result = _run(["gh", *args])
    if result.returncode != 0:
        raise RequiredChecksError(
            f"gh {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RequiredChecksError(
            f"gh {' '.join(args)} returned unparseable output: {exc}"
        ) from exc


def get_required_contexts(repo: str) -> list[str]:
    """Reads required_status_checks.contexts LIVE from branch protection --
    never a hardcoded copy of ["lint", "pwa-js"] that could silently drift
    from what protection actually requires later. An empty or missing list
    is treated as "cannot verify what's required" -- fails closed, not as
    "nothing is required, anything passes"."""
    data = _gh_json(["api", f"repos/{repo}/branches/master/protection"])
    contexts = (
        data.get("required_status_checks", {}).get("contexts", []) if isinstance(data, dict) else []
    )
    if not contexts:
        raise RequiredChecksError(
            "branch protection returned no required_status_checks.contexts -- "
            "cannot positively verify anything, failing closed"
        )
    return contexts


def get_pr_head_sha(pr_number: int, repo: str) -> str:
    data = _gh_json(["pr", "view", str(pr_number), "--repo", repo, "--json", "headRefOid"])
    sha = data.get("headRefOid") if isinstance(data, dict) else None
    if not sha:
        raise RequiredChecksError(
            f"PR #{pr_number} has no headRefOid -- cannot verify, failing closed"
        )
    return sha


def get_check_runs(sha: str, repo: str) -> list[dict[str, object]]:
    data = _gh_json(["api", f"repos/{repo}/commits/{sha}/check-runs"])
    runs = data.get("check_runs") if isinstance(data, dict) else None
    if runs is None:
        raise RequiredChecksError(
            f"commits/{sha}/check-runs returned no check_runs field -- failing closed"
        )
    return runs


def assert_required_checks_positive(
    required_contexts: list[str], check_runs: list[dict[str, object]]
) -> tuple[bool, dict[str, str]]:
    """The positive assertion itself (AJ1a): for every required context,
    a check-run with that name must exist AND its most recent conclusion
    must be literally "success". Absent is BLOCK, same as failed -- never
    silently treated as "nothing to report, must be fine".

    Where a context has multiple check-runs (re-runs), the one with the
    latest started_at wins -- matches GitHub's own semantics for "the
    current state of this check", not the first attempt.
    """
    by_context: dict[str, list[dict[str, object]]] = {}
    for run in check_runs:
        by_context.setdefault(run.get("name"), []).append(run)

    results: dict[str, str] = {}
    all_pass = True
    for context in required_contexts:
        runs = by_context.get(context, [])
        if not runs:
            results[context] = "ABSENT (zero check-runs recorded -- treated as BLOCK, same as red)"
            all_pass = False
            continue
        latest = max(runs, key=lambda r: r.get("started_at") or "")
        conclusion = latest.get("conclusion")
        if conclusion == "success":
            results[context] = "SUCCESS"
        else:
            results[context] = f"NOT SUCCESS (conclusion={conclusion!r})"
            all_pass = False
    return all_pass, results


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--pr", type=int, required=True, help="PR number to verify")
    parser.add_argument(
        "--repo", default=DEFAULT_REPO, help=f"owner/repo (default: {DEFAULT_REPO})"
    )
    args = parser.parse_args()

    try:
        required_contexts = get_required_contexts(args.repo)
        sha = get_pr_head_sha(args.pr, args.repo)
        check_runs = get_check_runs(sha, args.repo)
        all_pass, results = assert_required_checks_positive(required_contexts, check_runs)
    except RequiredChecksError as exc:
        print(f"BLOCK: could not positively verify PR #{args.pr} -- failing closed. Reason: {exc}")
        return 1

    print(f"PR #{args.pr} (sha {sha}) -- required contexts: {', '.join(required_contexts)}")
    for context, status in results.items():
        print(f"  {context}: {status}")

    if all_pass:
        print(
            f"PASS: all {len(required_contexts)} required context(s) positively confirmed SUCCESS."
        )
        return 0

    print(
        "BLOCK: at least one required context is absent, pending, or not successful. Do not merge."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
