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
        *"--json headRefOid"*) echo "$cur" ;;
        *"--json mergeStateStatus"*)
          if [ "{merge_state}" = BEHIND ] && [ ! -f "{st}.updated" ]; then echo BEHIND; else echo CLEAN; fi ;;
      esac ;;
    "pr update-branch") touch "{st}.updated"; echo newhead999 > "{st}.head" ;;
    "pr merge")
      n=$(cnt merge)
      if [ "$n" -le {merge_fails} ]; then echo "gh: merge blocked" >&2; return 1; fi ;;
    "workflow run") ;;
    "api repos/o/r/commits/{green_sha}/status?per_page=100") printf '%s\\n' "{statuses}" ;;
    "api repos/o/r/commits/{green_sha}/check-runs?per_page=100") printf '%s\\n' "{checks}" ;;
    api*) ;;
  esac
  return 0
}}
export -f gh sleep cnt
"""
    script = tmp / "run.sh"
    script.write_bytes(
        (stubs + f'cd "{_posix(ROOT)}"\nbash "{_posix(SCRIPT)}" 7 bot/x "{pushed}"\n').encode()
    )
    env = {
        **os.environ,
        "GITHUB_REPOSITORY": "o/r",
        "CI_RETRY_BASE": "0",
        "POLL_SECONDS": "0",
        "WAIT_MINUTES": wait_min,
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
