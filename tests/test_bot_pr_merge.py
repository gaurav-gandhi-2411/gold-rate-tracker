"""scripts/bot_pr_merge.sh: the one merge path for every bot PR.

GitHub's native auto-merge did not fire for docs-refresh PRs #2824, #2832 and #2845 although lint, pwa-js and
scraper-dependency-guard were all `success` (80 min, 4 h 53 min, 6 h 30 min); bot-pr-sync therefore polls
and merges itself. These tests run the real script in bash with a stub `gh`; nothing touches GitHub.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "bot_pr_merge.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

ALL3 = "lint\npwa-js\nscraper-dependency-guard"


def _posix(p: Path) -> str:
    return p.as_posix()


def _run(
    tmp: Path,
    *,
    pushed: str = "abc123",
    head: str = "abc123",
    green_on: str | None = None,
    statuses: str = ALL3,
    checks: str = "",
    states: str = "OPEN",
    merge_state: str = "CLEAN",
    merge_fails: int = 0,
    wait_min: str = "1",
    late: int = 0,
    parent: str = "abc123",
    wait_sec: str = "",
    cancelled_urls: str = "",
    files: str = "data/tanishq_scrape_success_rate.json",
    branch: str = "bot/x",
    dispatch_fails: bool = False,
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the script. `states` is the sequence of PR states, one per poll (the last repeats);
    statuses/checks are the already-filtered names the real `--jq` filters would print for `green_on`."""
    log = _posix(tmp / "calls.log")
    st = _posix(tmp / "st")
    green_sha = green_on or head
    Path(st + ".head").write_text(head)
    stubs = f"""
sleep() {{ :; }}
cnt() {{ n=$(cat "{st}.$1" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "{st}.$1"; echo $n; }}
gh() {{
  echo "gh $*" >> "{log}"
  cur=$(cat "{st}.head")
  case "$1 $2" in
    "pr view")
      case "$*" in
        *"--json state"*)
          n=$(cnt state); i=0
          for s in {states.replace(",", " ")}; do i=$((i + 1)); last=$s; if [ "$i" -eq "$n" ]; then echo $s; exit 0; fi; done
          echo $last ;;
        *"--json headRefOid"*)
          if [ -f "{st}.pending" ]; then
            n=$(cnt late)
            if [ "$n" -ge 2 ]; then echo newhead999 > "{st}.head"; cur=newhead999; fi
          fi
          echo "$cur" ;;
        *"--json files"*) printf '%s\\n' "{files}" ;;
        *"--json mergeStateStatus"*)
          if [ "{merge_state}" = BEHIND ] && [ ! -f "{st}.updated" ]; then echo BEHIND; else echo CLEAN; fi ;;
      esac ;;
    "pr update-branch") touch "{st}.updated"; if [ "{late}" = 1 ]; then : > "{st}.pending"; else echo newhead999 > "{st}.head"; fi ;;
    "pr merge")
      n=$(cnt merge)
      if [ "$n" -le {merge_fails} ]; then echo "gh: merge blocked" >&2; return 1; fi ;;
    "workflow run")
      case "$*" in *docs-refresh.yml*|*generate-og-image.yml*) if [ "{1 if dispatch_fails else 0}" = 1 ]; then return 1; fi ;; esac ;;
    "api repos/o/r/commits/newhead999") echo "{parent}" ;;
    "api repos/o/r/commits/{green_sha}/status?per_page=100") printf '%s\\n' "{statuses}" ;;
    "api repos/o/r/commits/{green_sha}/check-runs?per_page=100")
      case "$*" in
        *cancelled*) printf '%s\\n' "{cancelled_urls}" ;;
        *) printf '%s\\n' "{checks}" ;;
      esac ;;
    "run rerun") ;;
    api*) ;;
  esac
  return 0
}}
export -f gh sleep cnt
"""
    script = tmp / "run.sh"
    script.write_bytes(
        (stubs + f'cd "{_posix(ROOT)}"\nbash "{_posix(SCRIPT)}" 7 {branch} "{pushed}"\n').encode()
    )
    env = {
        **os.environ,
        "GITHUB_REPOSITORY": "o/r",
        "CI_RETRY_BASE": "0",
        "POLL_SECONDS": "0",
        "WAIT_MINUTES": wait_min,
        **({"WAIT_SECONDS": wait_sec} if wait_sec else {}),
    }
    r = subprocess.run([BASH, str(script)], capture_output=True, text=True, env=env, timeout=120)
    return r, (tmp / "calls.log").read_text() if (tmp / "calls.log").exists() else ""


def test_all_three_required_contexts_green_on_the_pushed_head_merge_it_pinned_to_that_head(
    tmp_path,
):
    r, log = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "gh pr merge 7 --repo o/r --squash --match-head-commit abc123" in log
    assert "Merged." in r.stdout


def test_contexts_may_arrive_as_check_runs_or_as_statuses(tmp_path):
    r, log = _run(tmp_path, statuses="scraper-dependency-guard", checks="lint\npwa-js")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "--match-head-commit abc123" in log


def test_a_missing_required_context_never_merges_and_fails_loudly_naming_it(tmp_path):
    r, log = _run(tmp_path, statuses="lint\npwa-js", wait_min="0")
    assert r.returncode == 1
    assert "gh pr merge" not in log
    assert "::error::" in r.stdout and "scraper-dependency-guard" in r.stdout


def test_a_head_that_is_not_the_commit_this_run_pushed_is_never_trusted_or_merged(tmp_path):
    # the PR head lags / moved: even with every context green on it, only `pushed` may be merged
    r, log = _run(tmp_path, pushed="abc123", head="zzz999", wait_min="0")
    assert r.returncode == 1
    assert "gh pr merge" not in log and "commits/zzz999" not in log
    assert "not the commit this run verified" in r.stdout


def test_a_failed_merge_attempt_is_retried_on_the_next_poll(tmp_path):
    r, log = _run(tmp_path, merge_fails=2)
    assert r.returncode == 0, r.stdout + r.stderr
    assert log.count("gh pr merge") == 3


def test_a_pr_merged_by_someone_else_mid_wait_is_success_without_our_merge(tmp_path):
    r, log = _run(tmp_path, statuses="", states="OPEN,MERGED")
    assert r.returncode == 0 and "Merged." in r.stdout
    assert "gh pr merge" not in log


def test_a_pr_closed_without_merging_fails(tmp_path):
    r, log = _run(tmp_path, states="OPEN,CLOSED", statuses="")
    assert r.returncode == 1 and "closed without merging" in r.stdout
    assert "gh pr merge" not in log


def test_a_pr_that_falls_behind_is_updated_redispatched_with_its_number_and_pinned_to_the_new_head(
    tmp_path,
):
    r, log = _run(tmp_path, head="abc123", green_on="newhead999", merge_state="BEHIND")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "gh pr update-branch 7" in log
    assert "gh workflow run lint.yml --repo o/r --ref bot/x" in log
    assert "gh workflow run scraper-dependency-guard.yml --repo o/r --ref bot/x -f pr=7" in log
    assert "gh pr merge 7 --repo o/r --squash --match-head-commit newhead999" in log


def test_no_pushed_sha_refuses_to_merge(tmp_path):
    r, log = _run(tmp_path, pushed="")
    assert r.returncode == 2 and "gh pr merge" not in log


def test_every_network_read_in_the_script_is_retried_and_the_merge_is_pinned():
    text = SCRIPT.read_text(encoding="utf-8")
    code = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    for ln in code:
        if re.search(r"\bgh (api|pr view)\b", ln):
            assert "ci_retry" in ln, ln
    assert "--match-head-commit" in text and "::error::" in text


def test_an_asynchronous_branch_update_is_adopted_only_when_the_new_head_is_a_child_of_the_verified_commit(
    tmp_path,
):
    # update-branch returns before the new head exists; it appears two reads later, with abc123 as its parent
    r, log = _run(
        tmp_path,
        head="abc123",
        green_on="newhead999",
        merge_state="BEHIND",
        late=1,
        parent="abc123",
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "--match-head-commit newhead999" in log


def test_a_new_head_that_is_not_a_child_of_the_verified_commit_is_never_merged(tmp_path):
    r, log = _run(
        tmp_path,
        head="abc123",
        green_on="newhead999",
        merge_state="BEHIND",
        late=1,
        parent="foreign000",
        wait_sec="3",
    )
    assert r.returncode == 1
    assert "gh pr merge" not in log


URL = "https://github.com/o/r/actions/runs/38957199971/job/117162938955"


def test_a_refused_merge_with_a_cancelled_required_check_run_reruns_that_run_once_per_head(
    tmp_path,
):
    # PR #2864 (2026-10-16): every context green, merge refused by "base branch policy"; the cause was a
    # CANCELLED pull_request run of scraper-dependency-guard. Two refusals, one rerun.
    r, log = _run(tmp_path, merge_fails=2, cancelled_urls=URL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert log.count("gh pr merge") == 3
    assert log.count("gh run rerun 38957199971 --repo o/r") == 1
    assert "Re-running cancelled required-check run 38957199971" in r.stdout
    # the query only looks at the REQUIRED contexts' runs that were cancelled or timed out
    assert '"lint","pwa-js","scraper-dependency-guard"' in log and "cancelled" in log


def test_no_rerun_when_nothing_is_cancelled(tmp_path):
    r, log = _run(tmp_path, merge_fails=1)
    assert r.returncode == 0 and "gh run rerun" not in log


def test_no_rerun_before_a_merge_was_refused(tmp_path):
    r, log = _run(tmp_path, cancelled_urls=URL)
    assert r.returncode == 0 and "gh run rerun" not in log


# --- 2026-10-18: a merged data PR asks docs-refresh to run (workflow_run never fires for bot-dispatched runs) ---

DISPATCH = "gh workflow run docs-refresh.yml --repo o/r --ref master"


def _metric_sources() -> list[str]:
    """Every file an injected marker reads, enumerated from README.md and docs/ the way the script does."""
    pat = re.compile(r"<!--METRIC:([^#<>]+)#")
    found: set[str] = set()
    for p in [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]:
        found.update(pat.findall(p.read_text(encoding="utf-8")))
    return sorted(found)


def test_the_enumeration_of_metric_sources_is_not_empty_and_all_under_data():
    srcs = _metric_sources()
    assert len(srcs) >= 5 and all(s.startswith("data/") and s.endswith(".json") for s in srcs)


@pytest.mark.parametrize("source", _metric_sources())
def test_a_merged_pr_that_changed_an_injected_metric_source_dispatches_docs_refresh(
    tmp_path, source
):
    r, log = _run(tmp_path, files=f"scripts/x.py\n{source}")
    assert r.returncode == 0, r.stdout + r.stderr
    assert DISPATCH in log
    assert log.index("gh pr merge") < log.index(DISPATCH)  # only after the merge


def test_a_merged_pr_that_changed_no_metric_source_dispatches_nothing(tmp_path):
    r, log = _run(tmp_path, files="app.js\ndata/gold_prices_snapshot_unrelated.json")
    assert r.returncode == 0 and "docs-refresh.yml" not in log
    assert "not dispatching docs-refresh" in r.stdout


def test_the_docs_refresh_pr_itself_never_dispatches_another_refresh(tmp_path):
    srcs = _metric_sources()
    r, log = _run(tmp_path, branch="bot/docs-refresh", files="\n".join(srcs))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "gh pr merge" in log and "docs-refresh.yml" not in log
    assert "not dispatching another docs-refresh" in r.stdout


def test_an_unreadable_file_list_still_dispatches_because_the_refresh_is_idempotent(tmp_path):
    r, log = _run(tmp_path, files="")
    assert r.returncode == 0 and DISPATCH in log


def test_a_failed_dispatch_warns_but_does_not_fail_the_merge(tmp_path):
    r, log = _run(tmp_path, dispatch_fails=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "gh pr merge" in log and "::warning::could not dispatch docs-refresh.yml" in r.stdout


def test_a_pr_merged_by_someone_else_mid_wait_also_dispatches(tmp_path):
    r, log = _run(tmp_path, statuses="", states="OPEN,MERGED")
    assert r.returncode == 0 and "gh pr merge" not in log
    assert DISPATCH in log


def test_nothing_is_dispatched_when_the_pr_is_not_merged(tmp_path):
    r, log = _run(tmp_path, statuses="lint\npwa-js", wait_min="0")
    assert r.returncode == 1 and "docs-refresh.yml" not in log


OG = "gh workflow run generate-og-image.yml --repo o/r --ref master"


def test_the_data_sync_pr_also_dispatches_generate_og_image_after_the_merge(tmp_path):
    r, log = _run(tmp_path, branch="bot/data-sync")
    assert r.returncode == 0, r.stdout + r.stderr
    assert OG in log and log.index("gh pr merge") < log.index(OG)


@pytest.mark.parametrize(
    "branch", ["bot/og-image", "bot/docs-refresh", "bot/tanishq-selfhosted-sync"]
)
def test_no_other_bot_pr_dispatches_generate_og_image(tmp_path, branch):
    r, log = _run(tmp_path, branch=branch)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "generate-og-image.yml" not in log


def test_a_failed_og_dispatch_warns_but_does_not_fail_the_merge(tmp_path):
    r, _log = _run(tmp_path, branch="bot/data-sync", dispatch_fails=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "::warning::could not dispatch generate-og-image.yml" in r.stdout


def test_nothing_is_dispatched_when_the_pr_is_closed_unmerged(tmp_path):
    r, log = _run(tmp_path, states="OPEN,CLOSED", statuses="")
    assert r.returncode == 1 and "docs-refresh.yml" not in log
