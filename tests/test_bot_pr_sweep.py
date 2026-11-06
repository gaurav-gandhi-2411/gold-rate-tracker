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


def _temp_repo(root: Path) -> Path:
    """A minimal repo whose one bot workflow is covered (main() returns 0); returns its workflows dir."""
    wfs = root / ".github" / "workflows"
    wfs.mkdir(parents=True)
    action = root / ".github" / "actions" / "bot-pr-sync"
    action.mkdir(parents=True)
    (action / "action.yml").write_text(
        "runs:\n  steps:\n    - run: BAD_FILES=$(git diff | grep -v -E '^(data/)' || true)\n",
        "utf-8",
    )
    (wfs / "ok.yml").write_text(
        "jobs:\n  j:\n    steps:\n      - uses: ./.github/actions/bot-pr-sync\n"
        "        with:\n          branch: bot/ok\n",
        "utf-8",
    )
    assert bot_branches.main(["--root", str(root)]) == 0
    return wfs


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
    fork: str = "false",
    open_state: str = "OPEN",  # what `gh pr view --json state` says while unmerged
    branch_list: str | None = None,  # replaces bot_branches.py's output (one TSV line)
) -> tuple[subprocess.CompletedProcess[str], str]:
    log = (tmp / "calls.log").as_posix()
    st = (tmp / "st").as_posix()
    created = _iso(age_min)
    fake_py = ""
    if branch_list is not None:
        (tmp / "branches.tsv").write_text(branch_list + "\n", encoding="utf-8")
        fake_py = f'fakepy() {{ cat "{(tmp / "branches.tsv").as_posix()}"; }}\nexport -f fakepy\n'
    stubs = f"""
sleep() {{ :; }}
curl() {{ echo "curl $*" >> "{log}"; }}
gh() {{
  echo "gh $*" >> "{log}"
  case "$1 $2" in
    "api repos/o/r/pulls?head="*)
      if [ "{1 if list_fails else 0}" = 1 ]; then return 1; fi
      # the row scripts/bot_pr_find.sh's --jq formats: number, head, created, draft, head repository
      case "$*" in *"head=o:{branch}&"*)
        if [ "{fork}" = true ]; then printf '3424\\t{SHA}\\t{created}\\tfalse\\tattacker/r\\n'
        else printf '3424\\t{SHA}\\t{created}\\tfalse\\to/r\\n'; fi ;;
      esac ;;
    "run list") if [ "{busy}" = FAIL ]; then return 1; fi; echo "{busy}" ;;
    "pr view")
      case "$*" in
        *"--json isCrossRepository"*) echo "false o r" ;;
        *"--json state"*) if [ -f "{st}.merged" ]; then echo MERGED; else echo {open_state}; fi ;;
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
    script.write_bytes((stubs + fake_py + f'bash "{SWEEP.as_posix()}"\n').encode())
    env = {
        **os.environ,
        "GITHUB_REPOSITORY": "o/r",
        "CI_RETRY_BASE": "0",
        "POLL_SECONDS": "0",
        "WAIT_SECONDS": "1",  # bot_pr_merge.sh's tests-only deadline: a red PR gives up at once
        "NOW": str(NOW),
        "DRY_RUN": dry,
        "OPS_LOG_PYTHON": sys.executable,  # the stubbed PYTHON may be a shell function
        "PYTHON": "fakepy" if branch_list is not None else sys.executable,
    }
    r = subprocess.run(
        [BASH or "bash", str(script)], capture_output=True, text=True, env=env, timeout=180
    )
    return r, (tmp / "calls.log").read_text() if (tmp / "calls.log").exists() else ""


NEEDS_BASH = pytest.mark.skipif(BASH is None, reason="bash not available")

# a "page" is an ops-log entry now (ntfy carries gold-price information only, 2026-11-06)
ANNOTATION = "::warning title=ops-alert::bot-pr-sweep: bot PR stuck unmerged -- "


def _paged(r: subprocess.CompletedProcess[str], log: str) -> bool:
    """One ops-log annotation on stdout, and no curl (so no ntfy) was invoked."""
    return r.stdout.count(ANNOTATION) >= 1 and "curl" not in log


def _quiet(r: subprocess.CompletedProcess[str], log: str) -> bool:
    return ANNOTATION not in r.stdout and "curl" not in log


@NEEDS_BASH
def test_a_stranded_green_weekly_backtest_pr_is_merged_pinned_to_its_head(tmp_path):
    r, log = _sweep(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    # the shared merge path, every required context checked on that head, merge pinned to it
    assert f"gh pr merge 3424 --repo o/r --squash --match-head-commit {SHA}" in log
    assert f"api repos/o/r/commits/{SHA}/status?per_page=100" in log
    assert "MERGED stranded bot/backtest-sync PR #3424." in r.stdout
    assert _quiet(r, log)  # merged: nothing to page about


@NEEDS_BASH
def test_every_branch_is_listed_not_only_data_sync(tmp_path):
    _, log = _sweep(tmp_path)
    for b in bot_branches.branches(ROOT):
        assert f"pulls?head=o:{b}&" in log, b


@NEEDS_BASH
def test_a_pr_whose_producing_run_is_still_going_is_left_to_that_run(tmp_path):
    r, log = _sweep(tmp_path, busy="1", age_min=100)
    assert r.returncode == 0 and "pr merge" not in log and _quiet(r, log)
    assert "a producing run is queued or running" in r.stdout
    assert "--workflow weekly-backtest.yml --branch master" in log


@NEEDS_BASH
def test_a_producer_busy_for_hours_pages_but_never_merges(tmp_path):
    # e.g. the self-hosted Tanishq runner offline: its run sits queued, the PR must not go silent
    r, log = _sweep(tmp_path, busy="1", age_min=400)
    assert r.returncode == 1 and "pr merge" not in log and _paged(r, log)
    assert "still open past 360 min" in r.stdout


@NEEDS_BASH
def test_unreadable_producer_runs_page_when_old_and_never_merge(tmp_path):
    r, log = _sweep(tmp_path, busy="FAIL")
    assert r.returncode == 1 and "pr merge" not in log and "could not be read" in r.stdout


@NEEDS_BASH
def test_unreadable_producer_runs_on_a_young_pr_neither_merge_nor_page(tmp_path):
    r, log = _sweep(tmp_path, busy="FAIL", age_min=30)
    assert r.returncode == 0 and "pr merge" not in log and _quiet(r, log)


@NEEDS_BASH
def test_a_fork_pr_on_a_bot_branch_name_is_never_seen(tmp_path):
    # dropped by scripts/bot_pr_find.sh: not merged, not paged (a fork must not be able to page GG hourly)
    r, log = _sweep(tmp_path, fork="true")
    assert r.returncode == 0 and "pr merge" not in log and "curl" not in log
    assert "run list" not in log and "ignoring PR #3424" in r.stderr


@NEEDS_BASH
def test_an_invalid_allowlist_regex_blocks_the_merge(tmp_path):
    # grep -E exits 2 on a broken pattern and prints nothing: that must not read as "no bad files"
    r, log = _sweep(tmp_path, branch_list="bot/backtest-sync\tweekly-backtest.yml\t^(data/|og")
    assert r.returncode == 1 and "pr merge" not in log
    assert "not a valid regex" in r.stdout


def test_the_file_list_includes_the_old_name_of_a_renamed_file():
    # a rename scripts/x.py -> data/x.py deletes code from master; its old name must meet the allowlist
    assert ".previous_filename // empty" in SWEEP.read_text(encoding="utf-8")


@NEEDS_BASH
def test_a_pr_closed_while_the_sweep_ran_does_not_page(tmp_path):
    r, log = _sweep(tmp_path, statuses="lint", open_state="CLOSED")
    assert r.returncode == 0 and "pr merge" not in log and _quiet(r, log)
    assert "CLOSED by something else meanwhile" in r.stdout


@NEEDS_BASH
def test_a_young_pr_is_left_alone(tmp_path):
    r, log = _sweep(tmp_path, age_min=5)
    assert r.returncode == 0 and "pr merge" not in log and "run list" not in log


@NEEDS_BASH
def test_a_pr_touching_code_is_never_merged_and_pages(tmp_path):
    r, log = _sweep(tmp_path, files="data/backtest.json\nscripts/evil.py")
    assert r.returncode == 1 and "pr merge" not in log
    assert "scripts/evil.py" in r.stdout and "PAGE: bot/backtest-sync PR #3424" in r.stdout
    assert _paged(r, log)


@NEEDS_BASH
def test_red_required_checks_never_merge_and_page_when_old(tmp_path):
    r, log = _sweep(tmp_path, statuses="lint\npwa-js")  # scraper-dependency-guard missing
    assert r.returncode == 1 and "pr merge" not in log
    assert "the merge path did not merge it" in r.stdout and _paged(r, log)


@NEEDS_BASH
def test_red_checks_on_a_pr_younger_than_the_alert_age_do_not_page_yet(tmp_path):
    r, log = _sweep(tmp_path, statuses="lint", age_min=30)
    assert r.returncode == 0 and "pr merge" not in log and _quiet(r, log)
    assert "pages at 60 min" in r.stdout


@NEEDS_BASH
def test_dry_run_merges_nothing_and_still_pages_about_an_old_stranded_pr(tmp_path):
    r, log = _sweep(tmp_path, dry="1")
    assert r.returncode == 1 and "pr merge" not in log and _paged(r, log)
    assert "DRY RUN: bot/backtest-sync #3424" in r.stdout and "this run is a dry run" in r.stdout


@NEEDS_BASH
def test_dry_run_does_not_page_about_a_young_stranded_pr(tmp_path):
    r, log = _sweep(tmp_path, dry="1", age_min=30)
    assert r.returncode == 0 and "pr merge" not in log and _quiet(r, log)


def test_scheduled_runs_are_report_only_until_a_separate_pr_switches_them():
    """Project rule (2026-10-30): a new check starts non-blocking; flipping this is a deliberate PR."""
    wf = (ROOT / ".github" / "workflows" / "bot-pr-sweep.yml").read_text(encoding="utf-8")
    assert "github.event_name == 'schedule' || github.event.inputs.dry_run == 'true'" in wf


def test_a_bot_pr_sync_branch_that_is_not_a_literal_fails_the_list(tmp_path):
    wfs = _temp_repo(tmp_path)
    (wfs / "y.yml").write_text(
        "jobs:\n  j:\n    steps:\n      - uses: ./.github/actions/bot-pr-sync\n"
        "        with:\n          branch: ${{ format('bot/{0}', 'new') }}\n",
        "utf-8",
    )
    assert bot_branches.main(["--root", str(tmp_path)]) == 2


def test_a_non_draft_gh_pr_create_outside_the_bot_paths_fails_the_list(tmp_path):
    wfs = _temp_repo(tmp_path)
    (wfs / "z.yml").write_text(
        "jobs:\n  j:\n    steps:\n      - run: gh pr create --base master --head auto/new\n",
        "utf-8",
    )
    assert bot_branches.main(["--root", str(tmp_path)]) == 2
    (wfs / "z.yml").write_text(
        "jobs:\n  j:\n    steps:\n      - run: gh pr create --draft --head auto/new\n", "utf-8"
    )
    assert bot_branches.main(["--root", str(tmp_path)]) == 0  # a person merges a draft


@NEEDS_BASH
def test_an_unreadable_pr_list_pages_instead_of_reading_as_none(tmp_path):
    r, log = _sweep(tmp_path, list_fails=True)
    assert r.returncode == 1 and "could not list open" in r.stdout and "pr merge" not in log


def test_the_sweep_script_and_workflow_never_touch_ntfy():
    for path in ("scripts/bot_pr_sweep.sh", ".github/workflows/bot-pr-sweep.yml"):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "ntfy" not in text.lower().replace("ntfy carries", "") and "NTFY" not in text, path
        assert "curl" not in text, path


def test_the_sweep_records_through_ops_log_py():
    text = SWEEP.read_text(encoding="utf-8")
    assert '"${HERE}/ops_log.py" --source bot-pr-sweep' in text
