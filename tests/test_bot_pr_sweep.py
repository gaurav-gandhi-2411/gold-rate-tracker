"""scripts/bot_pr_sweep.sh + scripts/bot_branches.py: a stranded bot PR on ANY bot branch is merged or paged.

2026-11-01..03: weekly-backtest's bot PR #3424 sat open 46 h with green checks after its sync gave up, and
nothing paged (the alert watched only bot/data-sync). These tests run the real sweep, and through it the real
scripts/bot_pr_merge.sh, against a stub `gh` (nothing touches GitHub), and check that every bot branch named
in a workflow is covered.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SWEEP = ROOT / "scripts" / "bot_pr_sweep.sh"
BASH = shutil.which("bash")
sys.path.insert(0, str(ROOT / "scripts"))
import bot_branches

NOW = 1_793_700_000  # epoch seconds; PR ages below are relative to this
SHA = "fedcba9876"
ALL3 = "lint\npwa-js\nscraper-dependency-guard"


def _iso(minutes_ago: int) -> str:
    out = subprocess.run(
        [BASH or "bash", "-c", f"date -u -d @{NOW - minutes_ago * 60} +%Y-%m-%dT%H:%M:%SZ"],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


# --- the branch list -------------------------------------------------------------------------------


def test_every_bot_branch_named_in_a_workflow_is_swept():
    """Fails when a workflow names a bot/<name> branch the sweep would not look at."""
    found = bot_branches.branches(ROOT)
    named = set()
    for p in (ROOT / ".github" / "workflows").glob("*.yml"):
        named |= set(bot_branches.BRANCH.findall(p.read_text(encoding="utf-8")))
    assert named, "no bot branch named in any workflow: the scan itself is broken"
    assert named <= set(found), f"not swept: {sorted(named - set(found))}"
    assert all(e["allow"] for e in found.values()), found


def test_each_branch_maps_to_the_workflow_that_produces_it():
    found = bot_branches.branches(ROOT)
    assert found["bot/backtest-sync"]["workflows"] == ["weekly-backtest.yml"]
    assert found["bot/data-sync"]["workflows"] == ["check-price.yml"]
    # check-price only reads this branch; the self-hosted scrape opens its PRs
    assert found["bot/tanishq-selfhosted-sync"]["workflows"] == ["scrape-tanishq-selfhosted.yml"]
    assert found["bot/docs-refresh"]["workflows"] == ["docs-refresh.yml"]


def test_allowed_files_come_from_the_action_and_from_docs_refreshs_git_add():
    found = bot_branches.branches(ROOT)
    sync = re.compile(found["bot/backtest-sync"]["allow"])
    assert sync.search("data/backtest.json") and sync.search("og.png")
    assert not sync.search("scripts/x.py") and not sync.search(".github/workflows/lint.yml")
    docs = re.compile(found["bot/docs-refresh"]["allow"])
    assert docs.search("README.md") and docs.search("docs/MODEL_STATUS.md")
    assert not docs.search("docs/ARCHITECTURE.md") and not docs.search("data/prices.json")


def test_a_workflow_that_opens_a_bot_pr_without_a_findable_branch_fails_the_list(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "actions" / "bot-pr-sync").mkdir(parents=True)
    (tmp_path / ".github" / "actions" / "bot-pr-sync" / "action.yml").write_text(
        "runs:\n  steps:\n    - run: BAD_FILES=$(git diff | grep -v -E '^(data/)' || true)\n",
        "utf-8",
    )
    (tmp_path / ".github" / "workflows" / "x.yml").write_text(
        "jobs:\n  j:\n    steps:\n      - run: |\n          BRANCH=bot/x\n"
        "          bash scripts/bot_pr_merge.sh 1 $BRANCH abc\n",
        "utf-8",
    )
    # bot/x opens PRs through bot_pr_merge.sh but its job adds no files: no allowlist, so refuse
    assert bot_branches.main(["--root", str(tmp_path)]) == 2


# --- the sweep, end to end with a stub gh ------------------------------------------------------------


def _sweep(
    tmp: Path,
    *,
    branch: str = "bot/backtest-sync",
    age_min: int = 2700,  # #3424 was 45 h old when found
    busy: str = "0",
    files: str = "data/backtest.json\ndata/coverage_metrics.json",
    statuses: str = ALL3,
    dry: str = "0",
    list_fails: bool = False,
) -> tuple[subprocess.CompletedProcess[str], str]:
    log = (tmp / "calls.log").as_posix()
    st = (tmp / "st").as_posix()
    created = _iso(age_min)
    stubs = f"""
sleep() {{ :; }}
curl() {{ echo "curl $*" >> "{log}"; }}
gh() {{
  echo "gh $*" >> "{log}"
  case "$1 $2" in
    "pr list")
      if [ "{1 if list_fails else 0}" = 1 ]; then return 1; fi
      case "$*" in *"--head {branch} "*) echo "3424 {SHA} {created}" ;; esac ;;
    "run list") echo "{busy}" ;;
    "pr view")
      case "$*" in
        *"--json state"*) if [ -f "{st}.merged" ]; then echo MERGED; else echo OPEN; fi ;;
        *"--json headRefOid"*) echo "{SHA}" ;;
        *"--json mergeStateStatus"*) echo CLEAN ;;
        *"--json files"*) printf '%s\\n' "{files}" ;;
      esac ;;
    "pr merge") touch "{st}.merged" ;;
    "api repos/o/r/pulls/3424/files?per_page=100") printf '%s\\n' "{files}" ;;
    "api repos/o/r/commits/{SHA}/status?per_page=100") printf '%s\\n' "{statuses}" ;;
    "api repos/o/r/commits/{SHA}/check-runs?per_page=100") ;;
    *) ;;
  esac
  return 0
}}
export -f gh sleep curl
"""
    script = tmp / "run.sh"
    script.write_bytes((stubs + f'bash "{SWEEP.as_posix()}"\n').encode())
    env = {
        **os.environ,
        "GITHUB_REPOSITORY": "o/r",
        "CI_RETRY_BASE": "0",
        "POLL_SECONDS": "0",
        "WAIT_SECONDS": "1",  # bot_pr_merge.sh's tests-only deadline: a red PR gives up at once
        "NOW": str(NOW),
        "DRY_RUN": dry,
        "NTFY_TOPIC": "t",
        "PYTHON": sys.executable,
    }
    r = subprocess.run(
        [BASH or "bash", str(script)], capture_output=True, text=True, env=env, timeout=180
    )
    return r, (tmp / "calls.log").read_text() if (tmp / "calls.log").exists() else ""


NEEDS_BASH = pytest.mark.skipif(BASH is None, reason="bash not available")


@NEEDS_BASH
def test_a_stranded_green_weekly_backtest_pr_is_merged_pinned_to_its_head(tmp_path):
    r, log = _sweep(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    # the shared merge path, every required context checked on that head, merge pinned to it
    assert f"gh pr merge 3424 --repo o/r --squash --match-head-commit {SHA}" in log
    assert f"api repos/o/r/commits/{SHA}/status?per_page=100" in log
    assert "MERGED stranded bot/backtest-sync PR #3424." in r.stdout
    assert "curl" not in log  # merged: nothing to page about


@NEEDS_BASH
def test_every_branch_is_listed_not_only_data_sync(tmp_path):
    _, log = _sweep(tmp_path)
    for b in bot_branches.branches(ROOT):
        assert f"--head {b} " in log, b


@NEEDS_BASH
def test_a_pr_whose_producing_run_is_still_going_is_left_to_that_run(tmp_path):
    r, log = _sweep(tmp_path, busy="1")
    assert r.returncode == 0 and "pr merge" not in log and "curl" not in log
    assert "a producing run is queued or running" in r.stdout


@NEEDS_BASH
def test_a_young_pr_is_left_alone(tmp_path):
    r, log = _sweep(tmp_path, age_min=5)
    assert r.returncode == 0 and "pr merge" not in log and "run list" not in log


@NEEDS_BASH
def test_a_pr_touching_code_is_never_merged_and_pages(tmp_path):
    r, log = _sweep(tmp_path, files="data/backtest.json\nscripts/evil.py")
    assert r.returncode == 1 and "pr merge" not in log
    assert "scripts/evil.py" in r.stdout and "PAGE: bot/backtest-sync PR #3424" in r.stdout
    assert "curl" in log and "ntfy.sh/t" in log


@NEEDS_BASH
def test_red_required_checks_never_merge_and_page_when_old(tmp_path):
    r, log = _sweep(tmp_path, statuses="lint\npwa-js")  # scraper-dependency-guard missing
    assert r.returncode == 1 and "pr merge" not in log
    assert "the merge path did not merge it" in r.stdout and "curl" in log


@NEEDS_BASH
def test_red_checks_on_a_pr_younger_than_the_alert_age_do_not_page_yet(tmp_path):
    r, log = _sweep(tmp_path, statuses="lint", age_min=30)
    assert r.returncode == 0 and "pr merge" not in log and "curl" not in log
    assert "pages at 60 min" in r.stdout


@NEEDS_BASH
def test_dry_run_merges_nothing(tmp_path):
    r, log = _sweep(tmp_path, dry="1")
    assert r.returncode == 0 and "pr merge" not in log
    assert "DRY RUN: bot/backtest-sync #3424" in r.stdout


@NEEDS_BASH
def test_an_unreadable_pr_list_pages_instead_of_reading_as_none(tmp_path):
    r, log = _sweep(tmp_path, list_fails=True)
    assert r.returncode == 1 and "could not list open" in r.stdout and "pr merge" not in log
