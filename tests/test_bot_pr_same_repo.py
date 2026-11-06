"""Only PRs whose head is THIS repository are ever treated as bot PRs (2026-11-03).

`gh pr list --head bot/data-sync` matches that branch name in ANY repository; the repo is public and forkable.
A fork PR named bot/data-sync could stall the data sync (taken for "the" PR, so the real one is never opened)
or, where a merge pins to the SHA read from the PR, get the fork's commit merged. Every lookup goes through
scripts/bot_pr_find.sh, and scripts/bot_pr_merge.sh refuses a cross-repository PR as a second layer.

These tests (1) enumerate every PR lookup by branch in the workflows, actions and scripts and fail on one that
bypasses the helper, (2) run the helper against a stub gh, and (3) run the REAL lookup-and-merge code of the
bot-pr-sync action, docs-refresh and both check-price steps with a simulated fork PR named like the bot branch.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
FIND = ROOT / "scripts" / "bot_pr_find.sh"
BASH = shutil.which("bash")
NEEDS_BASH = pytest.mark.skipif(BASH is None, reason="bash not available")
PUSHED = "abc123real"
FORK_SHA = "f0f0f0fork"

# --- (1) enumeration ----------------------------------------------------------------------------------------

SOURCES = ("*.yml", "*.yaml", "*.sh", "*.py")
# a PR looked up by branch name: gh pr list --head, the REST pulls?head= filter, or a branch passed to
# gh pr view/merge/checks/close/update-branch where a number belongs
BRANCH_LOOKUP = [
    re.compile(r"\bgh\s+pr\s+list\b[^\n]*--head\b"),
    re.compile(r"pulls\?[^\s'\"]*\bhead="),
    re.compile(r"\bgh\s+pr\s+(view|merge|checks|close|update-branch)\s+[\"']?(\$\{?BRANCH|bot/)"),
]


def _logical_lines(text: str) -> list[str]:
    """Shell continuation lines joined, so a --head on the next line still counts; comment lines
    (prose that explains the old pitfall) dropped, since they never run."""
    joined = re.sub(r"\\\r?\n\s*", " ", text).splitlines()
    return [ln for ln in joined if not ln.lstrip().startswith("#")]


def lookups_bypassing_the_helper(text: str) -> list[str]:
    return [ln.strip() for ln in _logical_lines(text) if any(p.search(ln) for p in BRANCH_LOOKUP)]


def _files() -> list[Path]:
    out = []
    for base in (ROOT / ".github", ROOT / "scripts"):
        for pat in SOURCES:
            out += [p for p in base.rglob(pat) if p.resolve() != FIND.resolve()]
    return out


def test_no_workflow_action_or_script_looks_up_a_pr_by_branch_except_through_the_helper():
    bad = {}
    for p in _files():
        hits = lookups_bypassing_the_helper(p.read_text(encoding="utf-8", errors="replace"))
        if hits:
            bad[str(p.relative_to(ROOT))] = hits
    assert not bad, f"PR lookups by branch outside scripts/bot_pr_find.sh: {bad}"


@pytest.mark.parametrize(
    "line",
    [
        'PR=$(gh pr list --repo "$R" --head "$BRANCH" --state open --json number)',
        'PR=$(ci_retry gh pr list --repo "$R" --state open \\\n  --head bot/data-sync --json number)',
        'gh api "repos/$R/pulls?head=o:bot/data-sync&state=open"',
        'gh pr view "$BRANCH" --json state',
        "gh pr merge bot/data-sync --squash",
    ],
)
def test_the_scanner_catches_each_shape_of_branch_lookup(line):
    assert lookups_bypassing_the_helper(line), line


def test_the_scanner_ignores_lookups_by_number():
    assert not lookups_bypassing_the_helper(
        'gh pr view "$NUM" --json state\ngh pr merge "${PR_NUM}" --squash'
    )


def test_every_bot_pr_reader_uses_the_helper():
    must = {
        ".github/actions/bot-pr-sync/action.yml": 2,
        ".github/workflows/docs-refresh.yml": 2,
        ".github/workflows/check-price.yml": 2,
        "scripts/bot_pr_sweep.sh": 1,
    }
    for rel, n in must.items():
        assert (ROOT / rel).read_text(encoding="utf-8").count("bot_pr_find.sh") >= n, rel


def test_the_helper_filters_on_the_server_by_owner_and_reads_every_page():
    """Independent review 2026-11-03: with `gh pr list --limit 100`, 100 newer fork PRs named bot/data-sync
    pushed the real PR out of the window and the sync stopped. GitHub's head=OWNER:BRANCH filter never
    returns another owner's PR."""
    src = FIND.read_text(encoding="utf-8")
    assert "pulls?head=${OWNER}:${BRANCH}&" in src and "--paginate" in src
    assert ".head.repo.full_name" in src


# --- shared stub gh -----------------------------------------------------------------------------------------


def _stub(tmp: Path, *, prs: str, real_created: str = "2026-11-03T00:00:00Z") -> str:
    """Bash stubs. `prs`: which open PRs exist on the branch: 'fork', 'real', 'fork,real', '' (none).
    PR 9001 is a fork's (head = FORK_SHA), PR 42 the real bot PR (head = PUSHED). `gh pr create` opens 42."""
    log = (tmp / "calls.log").as_posix()
    st = (tmp / "st").as_posix()
    (tmp / "st.prs").write_text(prs, encoding="utf-8")
    return f"""
sleep() {{ :; }}
curl() {{ echo "curl $*" >> "{log}"; }}
python3() {{ echo "python3 $*" >> "{log}"; }}
git() {{ case "$1" in rev-parse) echo {PUSHED} ;; *) echo "git $*" >> "{log}" ;; esac; }}
gh() {{
  echo "gh $*" >> "{log}"
  local prs; prs=$(cat "{st}.prs")
  case "$1 $2" in
    "api repos/o/r/pulls?head="*)
      # rows as scripts/bot_pr_find.sh formats them: number, head, created, draft, head repository.
      # GitHub's head=o:<branch> filter would not return the fork's PR at all; it is returned here anyway,
      # so the helper's own check is what is tested.
      case ",$prs," in *,fork,*) printf '9001\\t{FORK_SHA}\\t2026-11-01T00:00:00Z\\tfalse\\tattacker/r\\n' ;; esac
      case ",$prs," in *,real,*) printf '42\\t{PUSHED}\\t{real_created}\\tfalse\\to/r\\n' ;; esac ;;
    "pr create") echo "fork,real" > "{st}.prs" ;;
    "pr view")
      case "$3 $*" in
        "9001 "*"--json isCrossRepository"*) echo "true attacker r" ;;
        *"--json isCrossRepository"*) echo "false o r" ;;
        *"--json state"*) if [ -f "{st}.merged" ]; then echo MERGED; else echo OPEN; fi ;;
        "9001 "*"--json headRefOid"*) echo {FORK_SHA} ;;
        *"--json headRefOid"*) echo {PUSHED} ;;
        *"--json mergeStateStatus"*) echo CLEAN ;;
        *"--json files"*) echo data/prices.json ;;
      esac ;;
    "pr merge") touch "{st}.merged" ;;
    "api repos/o/r/commits/{PUSHED}/status?per_page=100") printf 'lint\\npwa-js\\nscraper-dependency-guard\\n' ;;
    "api repos/o/r/pulls/42/files") echo data/prices.json ;;
    "api repos/o/r/pulls/9001/files") echo data/prices.json ;;
    "api repos/o/r/commits/{FORK_SHA}/check-runs?per_page=100") echo 2 ;;
    "api repos/o/r/commits/{PUSHED}/check-runs?per_page=100") echo 2 ;;
    *) ;;
  esac
  return 0
}}
export -f gh git sleep curl python3
"""


def _run(tmp: Path, body: str, *, prs: str, extra_env: dict[str, str] | None = None, **kw: str):
    script = tmp / "run.sh"
    script.write_bytes((_stub(tmp, prs=prs, **kw) + body + "\n").encode())
    env = {
        **os.environ,
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_WORKSPACE": ROOT.as_posix(),
        "CI_RETRY_BASE": "0",
        "POLL_SECONDS": "0",
        "WAIT_SECONDS": "1",
        "NTFY_TOPIC": "t",
        **(extra_env or {}),
    }
    r = subprocess.run(
        [BASH or "bash", str(script)], capture_output=True, text=True, env=env, timeout=180
    )
    log = (tmp / "calls.log").read_text() if (tmp / "calls.log").exists() else ""
    return r, log


# --- (2) the helper ----------------------------------------------------------------------------------------

FIND_CALL = f'bash "{FIND.as_posix()}" bot/data-sync'


@NEEDS_BASH
def test_the_helper_returns_only_the_same_repository_pr(tmp_path):
    r, _ = _run(tmp_path, FIND_CALL, prs="fork,real")
    assert r.returncode == 0 and r.stdout.split() == ["42", PUSHED, "2026-11-03T00:00:00Z", "false"]
    assert "ignoring PR #9001" in r.stderr


@NEEDS_BASH
def test_the_helper_finds_nothing_when_only_a_fork_pr_is_open(tmp_path):
    r, _ = _run(tmp_path, FIND_CALL + " --first-number", prs="fork")
    assert r.returncode == 0 and r.stdout.strip() == ""


@NEEDS_BASH
def test_the_helper_first_number(tmp_path):
    r, _ = _run(tmp_path, FIND_CALL + " --first-number", prs="fork,real")
    assert r.stdout.strip() == "42"


@NEEDS_BASH
def test_an_unreadable_list_fails_the_helper_instead_of_reading_as_none(tmp_path):
    r, _ = _run(tmp_path, "gh() { return 1; }; export -f gh; " + FIND_CALL, prs="")
    assert r.returncode != 0 and r.stdout == ""


@NEEDS_BASH
@pytest.mark.parametrize(
    "row",
    [
        "7\\tsha\\tc\\tfalse\\tnull",  # head repository deleted (a deleted fork)
        "7\\tsha\\tc\\tfalse\\tother/r",  # another owner
        "7\\tsha\\tc\\tfalse\\to/fork-name",  # another repository name
        "7\\tsha\\tc\\tfalse\\to/r-extra",  # a prefix match is not a match
        "7\\tsha\\tc\\tfalse\\t",  # field missing
        "7\\tsha\\tc\\to/r",  # a field missing shifts the rest: still dropped
    ],
)
def test_anything_short_of_this_exact_repository_is_dropped(tmp_path, row):
    body = f"gh() {{ printf '{row}\\n'; }}; export -f gh; {FIND_CALL}"
    r, _ = _run(tmp_path, body, prs="")
    assert r.returncode == 0 and r.stdout == "", r.stdout


@NEEDS_BASH
def test_owner_case_does_not_matter(tmp_path):
    body = f"gh() {{ printf '7\\tsha\\tc\\tfalse\\tO/R\\n'; }}; export -f gh; {FIND_CALL}"
    r, _ = _run(tmp_path, body, prs="")
    assert r.stdout.split()[0] == "7"


@NEEDS_BASH
def test_the_query_sent_asks_github_for_this_owners_branch_only(tmp_path):
    _, log = _run(tmp_path, FIND_CALL, prs="real")
    assert (
        "gh api repos/o/r/pulls?head=o:bot/data-sync&base=master&state=open&per_page=100 --paginate"
        in log
    )


# --- (3) the real workflow code with a simulated fork PR named bot/data-sync ---------------------------------


def _slice(text: str, start: str, end: str) -> str:
    lines = text.splitlines()
    i = next(n for n, ln in enumerate(lines) if ln.strip().startswith(start))
    j = next(n for n in range(i, len(lines)) if end in lines[n])
    return "\n".join(ln.strip() for ln in lines[i : j + 1])


def _action_lookup_and_merge() -> str:
    """The bot-pr-sync action's own lines, from finding the PR to bot_pr_merge.sh."""
    action = yaml.safe_load((ROOT / ".github/actions/bot-pr-sync/action.yml").read_text("utf-8"))
    run = next(s["run"] for s in action["runs"]["steps"] if "bot_pr_merge.sh" in str(s.get("run")))
    return _slice(run, "PR_NUM=$(", 'scripts/bot_pr_merge.sh"')


def _step(workflow: str, needle: str) -> str:
    wf = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text("utf-8"))
    for job in wf["jobs"].values():
        for s in job.get("steps") or []:
            if needle in str(s.get("run") or ""):
                return str(s["run"])
    raise AssertionError(f"no step in {workflow} containing {needle!r}")


PRELUDE = f'set -eo pipefail\nsource "{ROOT.as_posix()}/scripts/ci_retry.sh"\nBRANCH=bot/data-sync\nPR_TITLE=t\nPR_BODY=b\n'


@NEEDS_BASH
def test_bot_pr_sync_with_a_fork_pr_listed_first_still_merges_its_own_pr(tmp_path):
    r, log = _run(tmp_path, PRELUDE + _action_lookup_and_merge(), prs="fork,real")
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"gh pr merge 42 --repo o/r --squash --match-head-commit {PUSHED}" in log
    assert "9001" not in log.replace("ignoring", "")  # never viewed, dispatched for, or merged
    assert "gh pr create" not in log


@NEEDS_BASH
def test_bot_pr_sync_with_only_a_fork_pr_open_opens_and_merges_its_own(tmp_path):
    # before 2026-11-03 the fork PR was taken for "the" PR: no PR opened, the sync waited on a foreign head
    r, log = _run(tmp_path, PRELUDE + _action_lookup_and_merge(), prs="fork")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "gh pr create --base master --head bot/data-sync" in log
    assert f"gh pr merge 42 --repo o/r --squash --match-head-commit {PUSHED}" in log
    assert "pr merge 9001" not in log and "pr view 9001" not in log


@NEEDS_BASH
def test_docs_refresh_with_a_fork_pr_listed_first_merges_its_own_pr(tmp_path):
    run = _step("docs-refresh.yml", "bot_pr_merge.sh")
    body = PRELUDE.replace("bot/data-sync", "bot/docs-refresh") + _slice(
        run, "EXISTING_PR=$(", 'scripts/bot_pr_merge.sh"'
    )
    r, log = _run(tmp_path, body, prs="fork,real")
    assert r.returncode == 0, r.stdout + r.stderr
    # docs-refresh also asks GitHub's own auto-merge first; either way only PR 42 is acted on
    assert "gh pr merge 42 --repo o/r --auto --squash" in log and "-f pr=42" in log
    assert "Merged." in r.stdout
    assert "pr merge 9001" not in log and "pr view 9001" not in log and "pr=9001" not in log


@NEEDS_BASH
def test_check_price_tanishq_stranded_merge_never_touches_a_fork_pr(tmp_path):
    run = _step("check-price.yml", "pulls/${NUM}/merge")
    r, log = _run(tmp_path, run, prs="fork")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "No open Tanishq sync PR." in r.stdout
    assert "-X PUT" not in log and "pulls/9001" not in log


@NEEDS_BASH
def test_check_price_tanishq_stranded_merge_still_merges_the_real_pr(tmp_path):
    run = _step("check-price.yml", "pulls/${NUM}/merge")
    r, log = _run(
        tmp_path,
        run,
        prs="fork,real",
        real_created="2026-11-01T00:00:00Z",
        extra_env={"DISPATCH_TOKEN": "x"},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "api -X PUT repos/o/r/pulls/42/merge -f merge_method=squash -f sha=" + PUSHED in log
    assert "pulls/9001" not in log


@NEEDS_BASH
def test_check_price_stuck_pr_alert_ignores_a_fork_pr(tmp_path):
    run = _step("check-price.yml", "bot/data-sync PR #${PR_NUM} has been open")
    r, log = _run(tmp_path, run, prs="fork")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "No open bot/data-sync PR" in r.stdout and "curl" not in log


@NEEDS_BASH
def test_check_price_stuck_pr_alert_still_pages_about_the_real_pr(tmp_path):
    run = _step("check-price.yml", "bot/data-sync PR #${PR_NUM} has been open")
    r, log = _run(tmp_path, run, prs="fork,real", real_created="2026-11-01T00:00:00Z")
    assert r.returncode == 0, r.stdout + r.stderr
    # the page is an ops-log entry since #3602 (ntfy carries gold-price information only): no curl
    page = log.split("python3 scripts/ops_log.py", 1)[-1]
    assert "python3 scripts/ops_log.py" in log and "curl" not in log
    assert "bot/data-sync PR #42 has been open" in page and "9001" not in page
