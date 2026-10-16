"""A blocked bot-PR sync must queue Tanishq readings, never overwrite them.

2026-10-14/15: the data token expired, the data PR could not merge, and every self-hosted scrape
force-pushed its single reading over the previous unmerged one on bot/tanishq-selfhosted-sync: three
readings were destroyed. scripts/bot_branch_push.sh (with scripts/carry_forward_readings.js) now rebuilds
the branch as master + this run's reading + the readings already queued on it.

These tests run the REAL scripts (bash, node, git) against a local bare repository standing in for GitHub;
only `curl` (the ntfy page) is a stub. The 401 / wiring tests run the bot-pr-sync step with stub gh/git.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(BASH is None or NODE is None, reason="bash and node are needed")

BRANCH = "bot/tanishq-selfhosted-sync"
MSG = "chore: append Tanishq reading (self-hosted scrape) [skip ci]"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "core.autocrlf",
    "GIT_CONFIG_VALUE_0": "false",
}


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, **GIT_ENV},
        check=False,
    )
    if check and r.returncode != 0:
        raise AssertionError(f"git {args} failed: {r.stderr}")
    return r.stdout


def _reading(ts: str, price: int) -> dict:
    return {"timestamp": ts, "22k": price, "24k": price + 1240, "18k": price - 2480, "source": "t"}


def _write_prices(repo: Path, rows: list[dict]) -> None:
    (repo / "data" / "prices.json").write_bytes((json.dumps(rows, indent=2) + "\n").encode())


def _prices(repo: Path, ref: str | None = None) -> list[dict]:
    if ref:
        return json.loads(_git(repo, "show", f"{ref}:data/prices.json"))
    return json.loads((repo / "data" / "prices.json").read_text())


# mixed line ends on purpose: the real outcomes log has them, and they must survive
OUTCOMES_SEED = (
    b'{"timestamp": "2026-10-14T01:00:00Z", "outcome": "success"}\r\n'
    b'{"timestamp": "2026-10-14T05:00:00Z", "outcome": "success"}\n'
)


def _outcome_line(ts: str) -> bytes:
    return f'{{"timestamp": "{ts}", "outcome": "success"}}\n'.encode()


@pytest.fixture
def world(tmp_path: Path) -> dict:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True, capture_output=True)
    (seed / "data").mkdir()
    _write_prices(seed, [_reading("2026-10-14T01:00:00.000Z", 13500), _reading("2026-10-14T05:00:00.000Z", 13510)])
    (seed / "data" / "tanishq_scrape_outcomes.jsonl").write_bytes(OUTCOMES_SEED)
    (seed / "data" / "tanishq_selfhosted_health.json").write_text('{"consecutive_job_failures": 0}\n')
    _git(seed, "checkout", "-q", "-b", "master")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "seed")
    _git(seed, "push", "-q", "origin", "master")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    curl_log = tmp_path / "curl.log"
    stub = bindir / "curl"
    stub.write_bytes(f'#!/bin/sh\necho "curl $*" >> "{curl_log.as_posix()}"\n'.encode())
    stub.chmod(0o755)
    return {"tmp": tmp_path, "origin": origin, "bin": bindir, "curl_log": curl_log, "n": 0}


def _scrape(w: dict, ts: str, price: int, *, health: int = 0, clone_from: str = "master") -> subprocess.CompletedProcess:
    """One self-hosted run: fresh checkout of master, append a reading, commit, push the bot branch."""
    w["n"] += 1
    repo = w["tmp"] / f"run{w['n']}"
    subprocess.run(["git", "clone", "-q", str(w["origin"]), str(repo)], check=True, capture_output=True)
    _git(repo, "checkout", "-q", clone_from)
    rows = _prices(repo)
    rows.append(_reading(ts, price))
    _write_prices(repo, rows)
    log = repo / "data" / "tanishq_scrape_outcomes.jsonl"
    log.write_bytes(log.read_bytes() + _outcome_line(ts[:19] + "Z"))
    (repo / "data" / "tanishq_selfhosted_health.json").write_text(f'{{"consecutive_job_failures": {health}}}\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", MSG)
    env = {
        **os.environ,
        **GIT_ENV,
        "PATH": f"{w['bin']}{os.pathsep}{os.environ['PATH']}",
        "CI_RETRY_BASE": "0",
        "NTFY_TOPIC": "topic",
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_RUN_ID": str(1000 + w["n"]),
    }
    return subprocess.run(
        [BASH, str(ROOT / "scripts" / "bot_branch_push.sh"), BRANCH],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def _merge_branch_into_master(w: dict) -> None:
    """What a successful bot PR does to master (the branch is a descendant of master, so a plain ff)."""
    _git(w["origin"], "update-ref", "refs/heads/master", f"refs/heads/{BRANCH}")


def _origin_prices(w: dict, ref: str) -> list[str]:
    return [r["timestamp"] for r in _prices(w["origin"], ref)]


def _outcome_ts(w: dict, ref: str) -> list[str]:
    raw = subprocess.run(
        ["git", "show", f"{ref}:data/tanishq_scrape_outcomes.jsonl"], cwd=w["origin"], capture_output=True, check=True
    ).stdout
    return [json.loads(x)["timestamp"] for x in raw.decode().splitlines() if x.strip()]


T1, T2, T3, T4 = (
    "2026-10-15T02:30:00.000Z",
    "2026-10-15T05:10:00.000Z",
    "2026-10-15T06:04:00.000Z",
    "2026-10-15T09:00:00.000Z",
)


def test_three_runs_during_a_blocked_sync_all_reach_master_in_order_once(world: dict) -> None:
    for i, (ts, price) in enumerate([(T1, 13601), (T2, 13620), (T3, 13635)]):
        r = _scrape(world, ts, price, health=i)  # nobody merges the branch: the sync is blocked
        assert r.returncode == 0, r.stdout + r.stderr
    base = ["2026-10-14T01:00:00.000Z", "2026-10-14T05:00:00.000Z"]
    # the branch accumulated all three, none overwritten
    assert _origin_prices(world, f"refs/heads/{BRANCH}") == [*base, T1, T2, T3]
    assert _origin_prices(world, "refs/heads/master") == base

    _merge_branch_into_master(world)  # recovery: the PR merges
    assert _origin_prices(world, "refs/heads/master") == [*base, T1, T2, T3]
    assert _outcome_ts(world, "refs/heads/master")[2:] == [t[:19] + "Z" for t in (T1, T2, T3)]
    health = json.loads(_git(world["origin"], "show", "master:data/tanishq_selfhosted_health.json"))
    assert health["consecutive_job_failures"] == 2  # the newest run's record

    # the next scrape after recovery adds only itself: no duplicates, nothing resurrected
    r = _scrape(world, T4, 13650)
    assert r.returncode == 0, r.stdout + r.stderr
    _merge_branch_into_master(world)
    assert _origin_prices(world, "refs/heads/master") == [*base, T1, T2, T3, T4]
    outs = _outcome_ts(world, "refs/heads/master")
    assert len(outs) == len(set(outs)) == 6


def test_existing_line_ends_of_the_outcomes_log_are_not_rewritten(world: dict) -> None:
    _scrape(world, T1, 13601)
    raw = subprocess.run(
        ["git", "show", f"refs/heads/{BRANCH}:data/tanishq_scrape_outcomes.jsonl"],
        cwd=world["origin"],
        capture_output=True,
        check=True,
    ).stdout
    assert raw.startswith(OUTCOMES_SEED)  # the CRLF line is still CRLF, the LF line still LF
    assert raw.endswith(_outcome_line(T1[:19] + "Z"))


def test_a_master_that_moved_during_the_scrape_does_not_lose_the_reading(world: dict) -> None:
    # this run checked out master, then another reading was merged to master before it pushed
    world["n"] += 1
    repo = world["tmp"] / "late"
    subprocess.run(["git", "clone", "-q", str(world["origin"]), str(repo)], check=True, capture_output=True)
    other = world["tmp"] / "other"
    subprocess.run(["git", "clone", "-q", str(world["origin"]), str(other)], check=True, capture_output=True)
    rows = _prices(other) + [_reading(T1, 13600)]
    _write_prices(other, rows)
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "other reading merged first")
    _git(other, "push", "-q", "origin", "master")

    _write_prices(repo, _prices(repo) + [_reading(T2, 13620)])  # same spot in the file: a rebase would conflict
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", MSG)
    env = {**os.environ, **GIT_ENV, "CI_RETRY_BASE": "0", "PATH": f"{world['bin']}{os.pathsep}{os.environ['PATH']}"}
    r = subprocess.run(
        [BASH, str(ROOT / "scripts" / "bot_branch_push.sh"), BRANCH], cwd=repo, capture_output=True, text=True, env=env
    )
    assert r.returncode == 0, r.stdout + r.stderr
    got = _origin_prices(world, f"refs/heads/{BRANCH}")
    assert got[-2:] == [T1, T2] and len(got) == len(set(got)) == 4


def test_a_reading_already_on_master_pushes_nothing(world: dict) -> None:
    r = _scrape(world, "2026-10-14T05:00:00.000Z", 13510)
    assert r.returncode == 0 and "Nothing to push" in r.stdout
    assert _git(world["origin"], "branch", "--list", BRANCH).strip() == ""


def test_an_unreadable_queued_branch_is_kept_and_the_run_goes_to_a_rescue_branch_and_pages(world: dict) -> None:
    assert _scrape(world, T1, 13601).returncode == 0
    bad = world["tmp"] / "corrupt"
    subprocess.run(["git", "clone", "-q", "-b", BRANCH, str(world["origin"]), str(bad)], check=True, capture_output=True)
    (bad / "data" / "prices.json").write_text("{ not json")
    _git(bad, "add", "-A")
    _git(bad, "commit", "-q", "-m", "corrupt")
    _git(bad, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    tip = _git(world["origin"], "rev-parse", f"refs/heads/{BRANCH}").strip()

    r = _scrape(world, T2, 13620)
    assert r.returncode == 1
    assert "::error::" in r.stdout
    assert _git(world["origin"], "rev-parse", f"refs/heads/{BRANCH}").strip() == tip  # untouched
    rescue = f"{BRANCH}-rescue-{1000 + world['n']}"
    assert T2 in _origin_prices(world, f"refs/heads/{rescue}")  # this run's reading is kept
    assert "ntfy.sh/topic" in world["curl_log"].read_text()  # and GG is paged


def test_an_unexpected_changed_file_on_the_branch_is_never_dropped(world: dict) -> None:
    assert _scrape(world, T1, 13601).returncode == 0
    odd = world["tmp"] / "odd"
    subprocess.run(["git", "clone", "-q", "-b", BRANCH, str(world["origin"]), str(odd)], check=True, capture_output=True)
    (odd / "data" / "something_else.json").write_text("{}")
    _git(odd, "add", "-A")
    _git(odd, "commit", "-q", "-m", "odd")
    _git(odd, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    r = _scrape(world, T2, 13620)
    assert r.returncode == 1
    assert _git(world["origin"], "show", f"{BRANCH}:data/something_else.json") == "{}"  # still there
    assert world["curl_log"].exists()


def test_an_unreachable_remote_is_not_read_as_no_branch(world: dict) -> None:
    world["n"] += 1
    repo = world["tmp"] / "dead"
    subprocess.run(["git", "clone", "-q", str(world["origin"]), str(repo)], check=True, capture_output=True)
    _write_prices(repo, _prices(repo) + [_reading(T1, 13600)])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", MSG)
    first = _git(repo, "rev-parse", "HEAD")
    _git(repo, "remote", "set-url", "origin", str(world["tmp"] / "does-not-exist.git"))
    env = {**os.environ, **GIT_ENV, "CI_RETRY_BASE": "0", "CI_RETRY_ATTEMPTS": "1", "NTFY_TOPIC": "topic",
           "PATH": f"{world['bin']}{os.pathsep}{os.environ['PATH']}"}
    r = subprocess.run(
        [BASH, str(ROOT / "scripts" / "bot_branch_push.sh"), BRANCH], cwd=repo, capture_output=True, text=True, env=env
    )
    assert r.returncode != 0
    assert _git(repo, "rev-parse", "HEAD") == first  # nothing rebuilt or rewritten on the failure path


# --- the bot-pr-sync step wires it in -------------------------------------------------------------
def _sync_script() -> str:
    action = yaml.safe_load((ROOT / ".github/actions/bot-pr-sync/action.yml").read_text("utf-8"))
    return str(action["runs"]["steps"][0]["run"])


def _run_step(tmp: Path, *, carry: str, token: str, push_exit: int, push_out: str = "") -> tuple[subprocess.CompletedProcess, str]:
    ws = tmp / "ws"
    (ws / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "ci_retry.sh", ws / "scripts" / "ci_retry.sh")
    log = tmp / "calls.log"
    (ws / "scripts" / "bot_branch_push.sh").write_bytes(
        f'#!/usr/bin/env bash\necho "push-script $*" >> "{log.as_posix()}"\necho "{push_out}"\nexit {push_exit}\n'.encode()
    )
    stubs = f"""
git() {{ case "$1" in diff) ;; *) echo "git $*" >> "{log.as_posix()}";; esac; return 0; }}
gh() {{
  echo "gh $*" >> "{log.as_posix()}"
  case "$1 $2" in
    "pr list") echo 4242 ;;
    "pr view") echo MERGED ;;
    "api user") if [ "{token}" = "bad" ]; then echo "HTTP 401: Bad credentials" >&2; return 1; fi; echo bot ;;
  esac
  return 0
}}
curl() {{ echo "curl $*" >> "{log.as_posix()}"; return 0; }}
export -f git gh curl
"""
    script = tmp / "run.sh"
    script.write_bytes((stubs + _sync_script()).encode())
    env = {
        **os.environ,
        "GH_TOKEN": "x",
        "BRANCH": "bot/test",
        "PR_TITLE": "t",
        "PR_BODY": "b",
        "WAIT_MINUTES": "1",
        "NTFY_TOPIC": "topic",
        "CARRY_FORWARD": carry,
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_WORKSPACE": str(ws),
        "RUNNER_TEMP": str(tmp),
        "CI_RETRY_BASE": "0",
    }
    r = subprocess.run([BASH, str(script)], capture_output=True, text=True, env=env, timeout=120)
    return r, (log.read_text() if log.exists() else "")


def test_a_rejected_token_still_queues_the_reading_when_carry_forward_is_on(tmp_path: Path) -> None:
    r, log = _run_step(tmp_path, carry="true", token="bad", push_exit=0)
    assert r.returncode != 0 and "was rejected by GitHub (HTTP 401" in r.stdout + r.stderr
    assert "push-script bot/test" in log  # the reading is queued on the bot branch
    assert log.count("curl ") == 1  # one page for the token
    assert "pr create" not in log and "workflow run" not in log


def test_a_rejected_token_pushes_nothing_when_carry_forward_is_off(tmp_path: Path) -> None:
    r, log = _run_step(tmp_path, carry="false", token="bad", push_exit=0)
    assert r.returncode != 0 and "push-script" not in log and "git push" not in log


def test_carry_forward_replaces_the_force_push_and_no_rebase(tmp_path: Path) -> None:
    r, log = _run_step(tmp_path, carry="true", token="good", push_exit=0, push_out="Pushed.")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "push-script bot/test" in log
    assert "git push --force" not in log and "git rebase" not in log
    assert "gh pr list" in log  # the normal PR flow follows


def test_a_failed_queue_push_stops_the_step_before_any_pr(tmp_path: Path) -> None:
    r, log = _run_step(tmp_path, carry="true", token="good", push_exit=7)
    assert r.returncode == 7 and "gh pr" not in log


def test_nothing_new_means_no_pr_and_success(tmp_path: Path) -> None:
    r, log = _run_step(tmp_path, carry="true", token="good", push_exit=0, push_out="Nothing to push: every reading is already on master.")
    assert r.returncode == 0 and "gh pr" not in log


def test_without_carry_forward_the_old_force_push_and_rebase_are_unchanged(tmp_path: Path) -> None:
    r, log = _run_step(tmp_path, carry="false", token="good", push_exit=0)
    assert r.returncode == 0
    assert "git push --force origin HEAD:refs/heads/bot/test" in log and "git rebase origin/master" in log
    assert "push-script" not in log


def test_only_the_selfhosted_tanishq_sync_turns_carry_forward_on() -> None:
    on = []
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        doc = yaml.safe_load(wf.read_text("utf-8"))
        for job in (doc.get("jobs") or {}).values():
            for step in job.get("steps", []):
                if str(step.get("uses", "")).endswith("bot-pr-sync") and str((step.get("with") or {}).get("carry-forward")) == "true":
                    on.append(wf.name)
    assert on == ["scrape-tanishq-selfhosted.yml"]
