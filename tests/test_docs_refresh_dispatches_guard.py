"""A force-pushed bot branch gets `action_required` for its pull_request guard run, so docs-refresh must
dispatch the required scraper-dependency-guard itself, with the PR number (PR #2810 sat BLOCKED 2 h on
2026-10-15 for lack of it), and must merge through the shared bot-PR merge path because GitHub's native
auto-merge did not fire on the PRs after that (#2824, #2832, #2845: 80 min to 6.5 h with every required
context green). The merge logic itself is tested in tests/test_bot_pr_merge.py."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _script() -> str:
    wf = yaml.safe_load((ROOT / ".github/workflows/docs-refresh.yml").read_text(encoding="utf-8"))
    steps = [s for j in wf["jobs"].values() for s in j["steps"] if "run" in s]
    return next(s["run"] for s in steps if "gh workflow run lint.yml" in s["run"])


def test_docs_refresh_dispatches_lint_and_the_required_guard_for_its_pr():
    run = _script()
    assert 'gh workflow run lint.yml --repo "${GITHUB_REPOSITORY}" --ref "${BRANCH}"' in run
    guard = 'gh workflow run scraper-dependency-guard.yml --repo "${GITHUB_REPOSITORY}" --ref "${BRANCH}" -f pr="${EXISTING_PR}"'
    assert guard in run
    # the PR number is known (and the PR exists) before the dispatch
    assert run.index("EXISTING_PR=$(") < run.index("scraper-dependency-guard.yml")


def test_the_guard_workflow_accepts_a_dispatched_pr_number_and_forwards_a_status():
    text = (ROOT / ".github/workflows/scraper-dependency-guard.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text and "pr:" in text and "statuses: write" in text


def test_docs_refresh_merges_through_the_shared_script_with_the_commit_it_pushed():
    run = _script()
    call = 'bash "${GITHUB_WORKSPACE}/scripts/bot_pr_merge.sh" "$EXISTING_PR" "$BRANCH" "$(git rev-parse HEAD)"'
    assert call in run
    # after the push of that commit and after both dispatches
    assert run.index("git push --force") < run.index("scripts/bot_pr_merge.sh")
    assert run.index("scraper-dependency-guard.yml") < run.index("scripts/bot_pr_merge.sh")
    # no private copy of the merge loop is left here
    assert (
        "DEADLINE" not in run
        and 'gh pr merge "$EXISTING_PR" --repo "${GITHUB_REPOSITORY}" --squash' not in run
    )


def test_the_job_timeout_covers_the_merge_wait():
    wf = yaml.safe_load((ROOT / ".github/workflows/docs-refresh.yml").read_text(encoding="utf-8"))
    assert wf["jobs"]["refresh"]["timeout-minutes"] >= 30
