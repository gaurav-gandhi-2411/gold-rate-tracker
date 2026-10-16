"""Behaviour tests for scripts/ci_retry.sh and where the workflows use it.

2026-10-12: one GitHub `HTTP 502` in one un-retried call stranded a bot data PR for two hours. The
helper retries only transient failures, a bounded number of times, and a persistent outage still
fails. These tests EXECUTE the helper (and the bot-PR sync step) in bash with stub commands; they do
not only read the workflow text.
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
HELPER = ROOT / "scripts" / "ci_retry.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


def _posix(p: Path) -> str:
    return p.as_posix()


def _script_file(tmp: Path, script: str) -> str:
    """The script goes in a file: a long multi-line -c argument is mangled by Windows quoting."""
    f = tmp / "script.sh"
    f.write_bytes(script.encode("utf-8"))  # LF only
    return _posix(f)


def _run(script: str, tmp: Path, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "CI_RETRY_BASE": "0", "TMPDIR": str(tmp)}
    return subprocess.run(
        [BASH or "bash", _script_file(tmp, script)],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=ROOT,
    )


def _flaky(tmp: Path, fail_times: int, message: str, code: int = 1) -> str:
    """Bash for a stub command `flaky`: fails `fail_times` times with `message`, then prints OK.
    Calls are counted in a file, because ci_retry runs the command in a subshell."""
    counter = _posix(tmp / "calls")
    return f"""
flaky() {{
  n=$(cat "{counter}" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "{counter}"
  if [ "$n" -le {fail_times} ]; then echo "{message}" >&2; echo "partial-stdout"; return {code}; fi
  echo OK
}}
"""


def _calls(tmp: Path) -> int:
    f = tmp / "calls"
    return int(f.read_text().strip()) if f.exists() else 0


def _use(tmp: Path, fail_times: int, message: str, code: int = 1) -> str:
    return f'source "{_posix(HELPER)}"\n' + _flaky(tmp, fail_times, message, code)


def test_success_first_try_passes_stdout_through(tmp_path: Path) -> None:
    r = _run(_use(tmp_path, 0, "") + 'V=$(ci_retry flaky); echo "got=$V"', tmp_path)
    assert r.returncode == 0 and "got=OK" in r.stdout
    assert _calls(tmp_path) == 1


@pytest.mark.parametrize("fails", [1, 2, 4])
def test_transient_failures_are_absorbed_up_to_four(tmp_path: Path, fails: int) -> None:
    script = (
        _use(tmp_path, fails, "gh: Server Error (HTTP 502)") + 'V=$(ci_retry flaky); echo "got=$V"'
    )
    r = _run(script, tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "got=OK"  # a failed attempt's stdout never leaks into the value
    assert _calls(tmp_path) == fails + 1
    assert r.stderr.count("transient failure") == fails


@pytest.mark.parametrize("fails", [5, 9])
def test_five_transient_failures_in_a_row_still_fail(tmp_path: Path, fails: int) -> None:
    script = _use(tmp_path, fails, "HTTP 503 Service Unavailable", code=4) + "ci_retry flaky"
    r = _run(script, tmp_path)
    assert r.returncode == 4  # the command's own exit code, never 0
    assert _calls(tmp_path) == 5  # bounded
    assert "gave up after 5 attempts" in r.stderr
    assert "HTTP 503" in r.stderr  # the real error is still shown


def test_no_pause_after_the_last_attempt(tmp_path: Path) -> None:
    script = (
        _use(tmp_path, 9, "HTTP 502")
        + 'sleep() { echo "$1" >> "'
        + _posix(tmp_path / "sleeps")
        + '"; }\nCI_RETRY_BASE=7 ci_retry flaky'
    )
    _run(script, tmp_path)
    assert (tmp_path / "sleeps").read_text().split() == ["7", "14", "21", "28"]  # 4 pauses, 5 tries


@pytest.mark.parametrize(
    "msg", ["gh: Not Found (HTTP 404)", "HTTP 422: Validation Failed", "bad flag"]
)
def test_a_non_transient_failure_is_not_retried(tmp_path: Path, msg: str) -> None:
    r = _run(_use(tmp_path, 9, msg, code=3) + "ci_retry flaky", tmp_path)
    assert r.returncode == 3
    assert _calls(tmp_path) == 1
    assert msg in r.stderr


def test_attempts_can_be_lowered(tmp_path: Path) -> None:
    r = _run(_use(tmp_path, 9, "HTTP 502") + "CI_RETRY_ATTEMPTS=2 ci_retry flaky", tmp_path)
    assert r.returncode != 0 and _calls(tmp_path) == 2


# --- the bot-PR sync step, executed with stub gh / git -------------------------------------------
def _sync_script() -> str:
    action = yaml.safe_load((ROOT / ".github/actions/bot-pr-sync/action.yml").read_text("utf-8"))
    return str(action["runs"]["steps"][0]["run"])


def _sync_stubs(tmp: Path, *, list_fails: int, dispatch_fails: int, token: str = "good") -> str:
    log, st = _posix(tmp / "gh.log"), _posix(tmp / "state")
    return f"""
cnt() {{ n=$(cat "{st}.$1" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "{st}.$1"; echo $n; }}
git() {{ case "$1" in diff) ;; *) echo "git $*" >> "{log}";; esac; return 0; }}
export -f git
gh() {{
  echo "gh $*" >> "{log}"
  case "$1 $2" in
    "pr list")
      n=$(cnt list)
      if [ "$n" -le {list_fails} ]; then echo "gh: Bad Gateway (HTTP 502)" >&2; return 1; fi
      if [ -f "{st}.created" ]; then echo 4242; fi ;;
    "pr create") touch "{st}.created" ;;
    "workflow run")
      n=$(cnt dispatch)
      if [ "$n" -le {dispatch_fails} ]; then echo "gh: Server Error (HTTP 502)" >&2; return 1; fi ;;
    "pr view") echo MERGED ;;
    "api user")
      if [ "{token}" = "bad" ]; then echo "HTTP 401: Bad credentials (https://api.github.com/user)" >&2; return 1; fi
      echo bot-user ;;
  esac
  return 0
}}
curl() {{ echo "curl $*" >> "{log}"; return 0; }}
export -f gh cnt curl
"""


def _run_sync(
    tmp: Path, *, list_fails: int, dispatch_fails: int, token: str = "good", ntfy: str = ""
) -> subprocess.CompletedProcess[str]:
    script = _sync_stubs(tmp, list_fails=list_fails, dispatch_fails=dispatch_fails, token=token) + (
        f'cd "{_posix(ROOT)}"\n' + _sync_script()
    )
    env = {
        "GH_TOKEN": "x",
        "BRANCH": "bot/test",
        "PR_TITLE": "t",
        "PR_BODY": "b",
        "WAIT_MINUTES": "1",
        "NTFY_TOPIC": ntfy,
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_WORKSPACE": str(ROOT),
        "CI_RETRY_BASE": "0",
    }
    return subprocess.run(
        [BASH or "bash", _script_file(tmp, script)],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, **env},
    )


def test_sync_step_survives_one_502_on_list_and_on_each_dispatch(tmp_path: Path) -> None:
    r = _run_sync(tmp_path, list_fails=1, dispatch_fails=1)
    log = (tmp_path / "gh.log").read_text()
    assert r.returncode == 0, r.stderr
    assert "Merged." in r.stdout
    assert log.count("gh pr create") == 1  # created once, after the list recovered
    # the first lint dispatch failed with a 502 and was repeated; the guard dispatch then succeeded
    assert log.count("gh workflow run lint.yml") == 2
    assert log.count("gh workflow run scraper-dependency-guard.yml") == 1


def test_sync_step_fails_closed_when_the_list_never_recovers(tmp_path: Path) -> None:
    r = _run_sync(tmp_path, list_fails=99, dispatch_fails=0)
    log = (tmp_path / "gh.log").read_text()
    assert r.returncode != 0
    assert "gh pr create" not in log  # never opens a second PR on a failed listing
    assert "workflow run" not in log
    assert log.count("gh pr list") == 5


# --- where the workflows use it: no bare network call on the bot-PR and data paths ----------------
# a command position: line start, after `$(`, `if `, `if ! `, `|| `, `&& `, `; ` or `{ `
CALL = re.compile(
    r"(?:^|\$\(|\bif (?:! )?|\|\| |&& |; |\{ )"
    r"(?:gh (?:api|pr (?:create|list|merge)|workflow run)|git (?:push|fetch))\b"
)
FILES = [
    ".github/actions/bot-pr-sync/action.yml",
    ".github/workflows/check-price.yml",
    ".github/workflows/docs-refresh.yml",
    ".github/workflows/scrape-tanishq-selfhosted.yml",
    ".github/workflows/ci-health.yml",
]
# Bare calls that are deliberate, each with the reason it is not wrapped.
ALLOWED = (
    'gh pr merge "${PR_NUM}" --squash',  # the poll loop re-attempts every 10 s for WAIT_MINUTES
    'gh pr merge "$EXISTING_PR"',  # docs-refresh: same poll loop, re-attempted every 15 s
    "gh pr update-branch",  # best-effort inside the poll loop (|| true)
    'gh workflow run lint.yml --ref "${BRANCH}" || true',  # best-effort re-dispatch in the poll loop
    'gh workflow run scraper-dependency-guard.yml --ref "${BRANCH}" -f pr="${PR_NUM}" || true',
    'gh api "repos/${GITHUB_REPOSITORY}/commits/${CURRENT_SHA}/check-runs"',  # polled every 10 s
)


def _run_scripts(rel: str) -> list[str]:
    doc = yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))
    steps = list(doc.get("runs", {}).get("steps", []))
    for job in (doc.get("jobs") or {}).values():
        steps += job.get("steps", [])
    return [str(s["run"]) for s in steps if isinstance(s, dict) and "run" in s]


def _bare_calls(rel: str) -> list[str]:
    out = []
    for script in _run_scripts(rel):
        for raw in script.splitlines():
            line = raw.strip()
            if (
                line.startswith(("#", "echo", "|| echo"))
                or "ci_retry" in line
                or not CALL.search(line)
            ):
                continue
            out.append(line)
    return out


@pytest.mark.parametrize("rel", FILES)
def test_no_unwrapped_network_call_on_the_bot_pr_and_data_paths(rel: str) -> None:
    bare = [ln for ln in _bare_calls(rel) if not any(a in ln for a in ALLOWED)]
    assert bare == [], f"{rel}: wrap these in ci_retry (scripts/ci_retry.sh) or allow them: {bare}"


@pytest.mark.parametrize("rel", FILES)
def test_each_file_that_calls_ci_retry_loads_it(rel: str) -> None:
    text = (ROOT / rel).read_text(encoding="utf-8")
    assert "ci_retry " in text
    assert "scripts/ci_retry.sh" in text


def test_progress_lines_stay_out_of_a_captured_response(tmp_path: Path) -> None:
    """ci-health parses the response as JSON: a recovered 502 must not put text in front of it."""
    errf = _posix(tmp_path / "err.txt")
    script = (
        _use(tmp_path, 1, "gh: Bad Gateway (HTTP 502)")
        + f'R=$(ci_retry flaky 2>"{errf}"); echo "R=[$R]"'
    )
    r = _run(script, tmp_path)
    assert "R=[OK]" in r.stdout
    assert "transient failure" in (tmp_path / "err.txt").read_text()


def test_ci_health_does_not_merge_stderr_into_the_response() -> None:
    text = (ROOT / ".github/workflows/ci-health.yml").read_text(encoding="utf-8")
    assert (
        'ci_retry gh api "repos/${GITHUB_REPOSITORY}/branches/master/protection" 2>&1' not in text
    )


def test_warnings_of_a_successful_call_are_kept(tmp_path: Path) -> None:
    script = 'source "' + _posix(HELPER) + '"\nw() { echo out; echo warn >&2; }\nci_retry w'
    r = _run(script, tmp_path)
    assert r.stdout.strip() == "out" and "warn" in r.stderr


@pytest.mark.parametrize(
    "msg",
    [
        "fatal: the remote end hung up unexpectedly",
        "Recv failure: Connection was reset",
        "dial tcp: no such host",
        "schannel: failed to receive handshake",
    ],
)
def test_more_transient_shapes_are_retried(tmp_path: Path, msg: str) -> None:
    r = _run(_use(tmp_path, 1, msg) + "ci_retry flaky", tmp_path)
    assert r.returncode == 0 and _calls(tmp_path) == 2


def test_sync_step_stops_loudly_and_pages_when_the_token_is_rejected(tmp_path: Path) -> None:
    """2026-10-14: an expired token (HTTP 401) stopped every data sync for a day with no alarm."""
    r = _run_sync(tmp_path, list_fails=0, dispatch_fails=0, token="bad", ntfy="topic")
    log = (tmp_path / "gh.log").read_text()
    assert r.returncode != 0
    assert "was rejected by GitHub (HTTP 401" in r.stdout + r.stderr
    assert log.count("curl ") == 1 and "ntfy.sh/topic" in log  # one page
    assert "pr list" not in log and "pr create" not in log and "workflow run" not in log
    assert "git push" not in log  # stopped before pushing anything


def test_sync_step_without_a_ntfy_topic_still_fails_on_a_rejected_token(tmp_path: Path) -> None:
    r = _run_sync(tmp_path, list_fails=0, dispatch_fails=0, token="bad")
    assert r.returncode != 0 and "curl " not in (tmp_path / "gh.log").read_text()


def test_a_good_token_probe_changes_nothing(tmp_path: Path) -> None:
    r = _run_sync(tmp_path, list_fails=0, dispatch_fails=0, ntfy="topic")
    assert r.returncode == 0 and "curl " not in (tmp_path / "gh.log").read_text()
