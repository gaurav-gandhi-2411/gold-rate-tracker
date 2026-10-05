"""scripts/check_scraper_dependency_change.py -- fail any PR that changes scraper/package*.json
unless its body cites a REAL proof run on the self-hosted runner (item 5a, 2026-10-05).

The defect this closes: a Dependabot bump of Playwright (#1846, merged 2026-10-02 19:10Z) broke the
self-hosted laptop scraper for about 11 hours (every run failed at "Install Playwright browsers" and
skipped the scrape; the last Tanishq reading was 18:56Z); #2316 reverted it. It was the SECOND time:
in 2026-08 a Playwright 1.63.0 trial on the same runner deleted the working production Chromium
(docs/SESSION_AUDIT_2026-08.md section 12.8 #43 and #53), after which "any trial must use a scratch
PLAYWRIGHT_BROWSERS_PATH". GitHub-hosted CI cannot catch this class of failure: the laptop's browser
cache, network (a build-153 download that never completes there) and OS are what break.

The rule: a PR whose diff touches any scraper/package*.json (package.json, package-lock.json, and
npm-shrinkwrap.json) must cite, in its body, a GitHub Actions run (URL or run ID) that this script
verifies LIVE through the gh API:

  1. the run exists, belongs to this repo's .github/workflows/scrape-tanishq-selfhosted.yml, was a
     workflow_dispatch run (the only trigger that can set the proof input), completed, conclusion
     success;
  2. it ran on the PR's own branch (head_branch == PR head ref, same repository) at the PR's head
     commit, or at an ancestor of it with NO change under scraper/ or to the workflow file in
     between (so the proof still describes the dependency state the PR will merge);
  3. its job ran on a self-hosted runner (labels include "self-hosted") and these steps all have
     conclusion success: "Use a scratch browser cache (dependency proof run only)" (sets the scratch
     PLAYWRIGHT_BROWSERS_PATH), "Install scraper dependencies", "Install Playwright browsers", and
     "Dependency proof: real scrape succeeded with scratch browsers" (fails the run unless the
     scrape step itself succeeded);
  4. the job log contains the proof marker line printed by that last step, with a scratch
     browsers path (contains "pw-proof-<run id>", not the default ms-playwright cache), at least one
     browser installed there, and the head_sha the run executed.

The proof run is produced by dispatching the workflow on the PR branch (see docs/RUNBOOK.md or
docs/TANISHQ_TIMED_VISITS.md section 11):

    gh workflow run scrape-tanishq-selfhosted.yml --ref <pr-branch> -f dependency_proof=true

dependency_proof=true installs into a scratch PLAYWRIGHT_BROWSERS_PATH, runs the real scrape, and
skips the commit/sync steps entirely, so it never alters production data or the production cache.

Fails CLOSED (rule 98a): an unfetchable or unparseable PR, file list, run, jobs list, log or compare
result is a FAIL, never a pass. A PR that does not touch scraper/package*.json passes without any
proof. Several runs may be cited; the check passes if at least one verifies (every cited run's
verdict is printed).

What it cannot catch (rule 85a, stated surface):
- It reads what the run LOGGED; someone with write access to the repo could dispatch a modified
  workflow on a branch that prints the marker without doing the work. It defends against
  forgetting and against wrong-surface evidence (a hosted CI run, a failed run, a stale run), not
  against a malicious collaborator. The runner itself is only trusted on the label "self-hosted"
  plus the step conclusions.
- The scrape succeeding on one visit does not prove Tanishq will keep answering; it proves the
  install-and-scrape path works with these dependencies on this machine at this time.
- Packages that load at scrape time but are not in scraper/package*.json (the system Node, OS
  libraries) are out of scope.
- It is only a gate once the check `scraper-dependency-guard` is a required status check
  (branch protection is not changed by this PR; GG does that).

Usage:
    python scripts/check_scraper_dependency_change.py --pr 1846 [--repo OWNER/REPO]

Exit code 0: the PR does not touch scraper/package*.json, or a cited run verified. Exit code 1: it
touches them without a verifiable proof run, or the check itself could not complete.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

DEFAULT_REPO = "gaurav-gandhi-2411/gold-rate-tracker"
WORKFLOW_PATH = ".github/workflows/scrape-tanishq-selfhosted.yml"

# Any package*.json directly in or under scraper/, plus npm-shrinkwrap.json (npm reads it in place
# of package-lock.json, so a change there is a dependency change too).
GUARDED_RE = re.compile(r"^scraper/(?:.*/)?(?:package[^/]*\.json|npm-shrinkwrap\.json)$")

STEP_SCRATCH = "Use a scratch browser cache (dependency proof run only)"
STEP_INSTALL_DEPS = "Install scraper dependencies"
STEP_INSTALL_BROWSERS = "Install Playwright browsers"
STEP_PROOF = "Dependency proof: real scrape succeeded with scratch browsers"
REQUIRED_STEPS = (STEP_SCRATCH, STEP_INSTALL_DEPS, STEP_INSTALL_BROWSERS, STEP_PROOF)

MARKER_RE = re.compile(
    r"DEPENDENCY_PROOF scratch_browsers_path=(?P<path>\S+) browsers_installed=(?P<n>\d+) "
    r"playwright=(?P<pw>\S+) head_sha=(?P<sha>[0-9a-f]{40}) scrape=success"
)


class GuardError(Exception):
    """The check itself could not be completed -- callers must treat this as FAIL (rule 98a)."""


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )


def _gh_text(args: list[str]) -> str:
    result = _run(["gh", *args])
    if result.returncode != 0:
        raise GuardError(
            f"gh {' '.join(args)} failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def _parse_json_stream(text: str) -> list[object]:
    """Parse one or more concatenated JSON documents (gh api --paginate prints one per page)."""
    decoder = json.JSONDecoder()
    docs: list[object] = []
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        try:
            doc, i = decoder.raw_decode(text, i)
        except json.JSONDecodeError as exc:
            raise GuardError(f"unparseable JSON from gh: {exc}") from exc
        docs.append(doc)
    if not docs:
        raise GuardError("empty response from gh")
    return docs


def _gh_json(args: list[str]) -> object:
    docs = _parse_json_stream(_gh_text(args))
    return docs[0]


def _gh_json_pages(args: list[str]) -> list[object]:
    """All pages of a paginated list endpoint, flattened. Items from every page are kept."""
    items: list[object] = []
    for doc in _parse_json_stream(_gh_text(["api", "--paginate", *args])):
        if not isinstance(doc, list):
            raise GuardError("expected a JSON array page from a paginated endpoint")
        items.extend(doc)
    return items


# --- data fetch ---------------------------------------------------------------------------------


def fetch_pr(pr: int, repo: str) -> dict[str, str]:
    data = _gh_json(["api", f"repos/{repo}/pulls/{pr}"])
    if not isinstance(data, dict) or "body" not in data:
        raise GuardError(f"PR #{pr}: response has no 'body' field -- cannot read the PR body")
    head = data.get("head")
    if not isinstance(head, dict) or not head.get("sha") or not head.get("ref"):
        raise GuardError(f"PR #{pr}: response has no head sha/ref")
    head_repo = (head.get("repo") or {}).get("full_name") or ""
    body = data["body"]
    if body is not None and not isinstance(body, str):
        raise GuardError(f"PR #{pr}: body is not text")
    return {
        "body": body or "",
        "head_sha": str(head["sha"]),
        "head_ref": str(head["ref"]),
        "head_repo": str(head_repo),
    }


def fetch_pr_files(pr: int, repo: str) -> list[str]:
    items = _gh_json_pages([f"repos/{repo}/pulls/{pr}/files?per_page=100"])
    names: list[str] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("filename"):
            raise GuardError(f"PR #{pr}: file entry without 'filename'")
        names.append(str(item["filename"]))
        # A rename's old path is a change too.
        if item.get("previous_filename"):
            names.append(str(item["previous_filename"]))
    return names


def extract_run_ids(body: str, repo: str) -> list[int]:
    """Run IDs cited in the PR body: this repo's actions/runs/<id> URLs, or 'run <id>' /
    'run id: <id>' / 'run #<id>' with a 9+ digit ID (a run ID is a ~11-digit number; requiring
    9+ digits keeps PR numbers and counts from being read as runs). URLs of other repos are
    ignored on purpose: a run elsewhere proves nothing about this runner."""
    ids: list[int] = []
    for m in re.finditer(
        rf"github\.com/{re.escape(repo)}/actions/runs/(\d+)", body, flags=re.IGNORECASE
    ):
        ids.append(int(m.group(1)))
    for m in re.finditer(r"\bruns?(?:\s*id)?\s*[:#]?\s*(\d{9,})\b", body, flags=re.IGNORECASE):
        ids.append(int(m.group(1)))
    seen: set[int] = set()
    out: list[int] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


# --- run verification ---------------------------------------------------------------------------


def _check_head_relation(run: dict[str, object], pr_head_sha: str, repo: str) -> list[str]:
    run_sha = str(run.get("head_sha") or "")
    if not run_sha:
        return ["run has no head_sha"]
    if run_sha == pr_head_sha:
        return []
    cmp = _gh_json(["api", f"repos/{repo}/compare/{run_sha}...{pr_head_sha}"])
    if not isinstance(cmp, dict):
        return ["compare response unparseable"]
    status = cmp.get("status")
    if status not in ("ahead", "identical"):
        return [
            f"run commit {run_sha[:8]} is not an ancestor of the PR head (compare status {status!r})"
        ]
    files = cmp.get("files")
    if not isinstance(files, list):
        return ["compare response has no files list (cannot show nothing changed since the run)"]
    if len(files) >= 300:
        return [
            "compare lists 300+ files; cannot show nothing under scraper/ changed since the run"
        ]
    for f in files:
        name = str(f.get("filename", "")) if isinstance(f, dict) else ""
        if name.startswith("scraper/") or name == WORKFLOW_PATH:
            return [
                f"{name} changed between the run commit {run_sha[:8]} and the PR head "
                f"{pr_head_sha[:8]}: the proof does not cover the final dependency state"
            ]
    return []


def verify_run(run_id: int, pr: dict[str, str], repo: str) -> list[str]:
    """Return the list of problems with this cited run (empty list = the run is valid proof)."""
    problems: list[str] = []
    run = _gh_json(["api", f"repos/{repo}/actions/runs/{run_id}"])
    if not isinstance(run, dict):
        return ["run response unparseable"]

    path = str(run.get("path") or "").split("@", 1)[0]
    if path != WORKFLOW_PATH:
        problems.append(f"workflow is {path!r}, expected {WORKFLOW_PATH!r}")
    if run.get("event") != "workflow_dispatch":
        problems.append(f"event is {run.get('event')!r}, expected 'workflow_dispatch'")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        problems.append(
            f"run is status={run.get('status')!r} conclusion={run.get('conclusion')!r}, "
            "expected completed/success"
        )
    head_repo = (
        (run.get("head_repository") or {}).get("full_name")
        if isinstance(run.get("head_repository"), dict)
        else None
    )
    if head_repo != repo:
        problems.append(f"run head repository is {head_repo!r}, expected {repo!r}")
    if run.get("head_branch") != pr["head_ref"]:
        problems.append(
            f"run branch is {run.get('head_branch')!r}, expected the PR branch {pr['head_ref']!r}"
        )
    problems.extend(_check_head_relation(run, pr["head_sha"], repo))

    jobs_doc = _gh_json(
        ["api", f"repos/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page=100"]
    )
    jobs = jobs_doc.get("jobs") if isinstance(jobs_doc, dict) else None
    if not isinstance(jobs, list) or not jobs:
        problems.append("run has no jobs list")
        return problems
    job = next(
        (j for j in jobs if isinstance(j, dict) and "self-hosted" in (j.get("labels") or [])),
        None,
    )
    if job is None:
        problems.append("no job on a self-hosted runner (labels lack 'self-hosted')")
        return problems
    if not job.get("runner_name"):
        problems.append("job has no runner_name (never picked up by a runner)")
    steps = {
        str(s.get("name")): s.get("conclusion")
        for s in (job.get("steps") or [])
        if isinstance(s, dict)
    }
    for name in REQUIRED_STEPS:
        if steps.get(name) != "success":
            problems.append(f"step {name!r} conclusion is {steps.get(name)!r}, expected 'success'")

    job_id = job.get("id")
    if not isinstance(job_id, int):
        problems.append("job has no numeric id (cannot read its log)")
        return problems
    log = _gh_text(["api", f"repos/{repo}/actions/jobs/{job_id}/logs"])
    marker = MARKER_RE.search(log)
    if marker is None:
        problems.append("job log has no DEPENDENCY_PROOF marker line (not a dependency proof run)")
        return problems
    scratch = marker.group("path")
    if f"pw-proof-{run_id}" not in scratch or "ms-playwright" in scratch.lower():
        problems.append(f"browsers path {scratch!r} is not the scratch pw-proof-{run_id} path")
    if int(marker.group("n")) < 1:
        problems.append("no browser was installed into the scratch path")
    if marker.group("sha") != str(run.get("head_sha")):
        problems.append("marker head_sha differs from the run's head_sha")
    return problems


# --- decision -----------------------------------------------------------------------------------


def evaluate(pr_number: int, repo: str) -> tuple[bool, list[str]]:
    """(passed, report lines). Raises GuardError when the check cannot be completed."""
    lines: list[str] = []
    files = fetch_pr_files(pr_number, repo)
    guarded = sorted({f for f in files if GUARDED_RE.match(f)})
    if not guarded:
        lines.append(f"PR #{pr_number} changes no scraper/package*.json: no proof required.")
        return True, lines
    lines.append(f"PR #{pr_number} changes scraper dependency files: {', '.join(guarded)}")

    pr = fetch_pr(pr_number, repo)
    run_ids = extract_run_ids(pr["body"], repo)
    if not run_ids:
        lines.append(
            "FAIL: the PR body cites no GitHub Actions run (URL or run ID). Dispatch the proof run "
            "on the PR branch and cite it:\n"
            f"  gh workflow run scrape-tanishq-selfhosted.yml --ref {pr['head_ref']} "
            "-f dependency_proof=true"
        )
        return False, lines

    passed = False
    for run_id in run_ids:
        try:
            problems = verify_run(run_id, pr, repo)
        except GuardError as exc:
            problems = [f"could not verify (failing closed): {exc}"]
        if problems:
            lines.append(f"run {run_id}: NOT valid proof")
            lines.extend(f"  - {p}" for p in problems)
        else:
            lines.append(
                f"run {run_id}: VALID proof (install + real scrape on self-hosted runner, scratch browsers)"
            )
            passed = True
    if not passed:
        lines.append("FAIL: no cited run is valid proof.")
    return passed, lines


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--pr", type=int, required=True, help="PR number to check")
    parser.add_argument(
        "--repo", default=DEFAULT_REPO, help=f"owner/repo (default: {DEFAULT_REPO})"
    )
    args = parser.parse_args()
    try:
        passed, lines = evaluate(args.pr, args.repo)
    except GuardError as exc:
        print(f"FAIL: could not verify PR #{args.pr}, failing closed. Reason: {exc}")
        return 1
    print("\n".join(lines))
    print("RESULT: PASS" if passed else "RESULT: FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
