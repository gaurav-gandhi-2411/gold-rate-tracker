"""A force-pushed bot branch gets `action_required` for its pull_request guard run, so docs-refresh must
dispatch the required scraper-dependency-guard itself, with the PR number (PR #2810 sat BLOCKED 2 h on
2026-10-15 for lack of it)."""

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


# --- the workflow merges the PR itself once the three required contexts are green -------------------
def _run_refresh(tmp_path, *, green: int, wait_min: str = "1"):
    """Run the real step script with stub git/gh/date helpers; returns (result, call log)."""
    import os
    import shutil
    import subprocess

    bash = shutil.which("bash")
    log = tmp_path / "calls.log"
    merged = tmp_path / "merged"
    script = (tmp_path / "step.sh").as_posix()
    stubs = f"""
git() {{ case "$1 $2" in "diff --staged") return 1;; esac; echo "git $*" >> "{log.as_posix()}"; return 0; }}
sleep() {{ :; }}
gh() {{
  echo "gh $*" >> "{log.as_posix()}"
  case "$1 $2" in
    "pr list") echo 4242 ;;
    "pr view")
      case "$*" in
        *headRefOid*) echo abc123 ;;
        *) if [ -f "{merged.as_posix()}" ]; then echo MERGED; else echo OPEN; fi ;;
      esac ;;
    "api repos/o/r/commits/abc123/status") echo {green} ;;
    "pr merge") if [ "$3" = "4242" ] && [[ "$*" == *--squash* ]] && [[ "$*" != *--auto* ]]; then touch "{merged.as_posix()}"; fi ;;
  esac
  return 0
}}
export -f git sleep gh
"""
    (tmp_path / "step.sh").write_bytes((stubs + _script()).encode("utf-8"))
    env = {
        **os.environ,
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_WORKSPACE": str(ROOT),
        "GH_TOKEN": "x",
        "DOCS_REFRESH_WAIT_MIN": wait_min,
        "CI_RETRY_BASE": "0",
    }
    r = subprocess.run(
        [bash, script], capture_output=True, text=True, env=env, timeout=120, cwd=ROOT
    )
    return r, (log.read_text() if log.exists() else "")


import shutil as _shutil

import pytest

_needs_bash = pytest.mark.skipif(_shutil.which("bash") is None, reason="bash not available")


@_needs_bash
def test_green_required_contexts_make_the_workflow_merge_the_pr_itself(tmp_path):
    r, log = _run_refresh(tmp_path, green=3)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "gh pr merge 4242 --repo o/r --squash" in log and "Merged." in r.stdout


@_needs_bash
def test_it_does_not_merge_while_a_required_context_is_missing_and_then_fails_loudly(tmp_path):
    r, log = _run_refresh(tmp_path, green=2, wait_min="0")
    assert r.returncode == 1 and "was not merged after 0 minutes" in r.stdout
    assert "gh pr merge 4242 --repo o/r --squash" not in log


def test_the_job_timeout_covers_the_merge_wait():
    wf = yaml.safe_load((ROOT / ".github/workflows/docs-refresh.yml").read_text(encoding="utf-8"))
    assert wf["jobs"]["refresh"]["timeout-minutes"] >= 30
