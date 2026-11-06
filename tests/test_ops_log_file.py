"""The ops-log FILE: scripts/ops_log.py appends one JSON line per entry to ops_log.jsonl on the
`ops-log` branch (so the weekly read is one API call), and every job that records can write it.

A fake `gh` models the GitHub contents/git-data API (sha check, 404s, 409 conflicts); nothing here
touches the network or a real repository.
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ops_log

NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)
ENV = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_REPOSITORY": "o/r",
    "GITHUB_RUN_ID": "99",
    "GITHUB_WORKFLOW": "Check Gold Price",
    "GH_TOKEN": "t",
}


class FakeGh:
    """A tiny stateful stand-in for `gh api` (same (args, stdin) -> (rc, out, err) contract)."""

    def __init__(self, text: str | None = None, branch: bool = True, conflicts: int = 0) -> None:
        self.text = text  # file content, None = no file
        self.branch = branch
        self.conflicts = conflicts  # PUTs to refuse with 409 (another writer got in first)
        self.sha_n = 1
        self.calls: list[list[str]] = []
        self.commit_messages: list[str] = []
        self.orphan_parents: list | None = None
        self.always_fail: str | None = None  # stderr to return for every PUT

    def _sha(self) -> str:
        return f"sha{self.sha_n}"

    def __call__(self, args: list[str], stdin: str | None = None) -> tuple[int, str, str]:
        self.calls.append(args)
        path = next(a for a in args if a.startswith("repos/"))
        method = args[args.index("-X") + 1] if "-X" in args else "GET"
        body = json.loads(stdin) if stdin else {}
        if method == "GET" and "/contents/" in path:
            if not self.branch or self.text is None:
                return 1, "", "gh: Not Found (HTTP 404)"
            enc = base64.b64encode(self.text.encode()).decode()
            meta = {"sha": self._sha(), "encoding": "base64", "content": enc}
            return 0, json.dumps(meta), ""
        if method == "GET" and "/git/ref/" in path:
            return (0, "{}", "") if self.branch else (1, "", "gh: Not Found (HTTP 404)")
        if method == "PUT":
            if self.always_fail:
                return 1, "", self.always_fail
            if self.conflicts > 0:
                self.conflicts -= 1
                self.text = (self.text or "") + '{"ts": "2026-11-06T11:59:00Z"}\n'  # their write
                self.sha_n += 1
                return 1, "", "gh: is at sha but expected (HTTP 409)"
            if body.get("sha", None) != (self._sha() if self.text is not None else None):
                return 1, "", "gh: sha does not match (HTTP 422)"
            assert body["branch"] == "ops-log"
            self.text = base64.b64decode(body["content"]).decode()
            self.commit_messages.append(body["message"])
            self.sha_n += 1
            return 0, "{}", ""
        if method == "POST" and path.endswith("/git/trees"):
            self.pending_text = body["tree"][0]["content"]
            return 0, json.dumps({"sha": "tree1"}), ""
        if method == "POST" and path.endswith("/git/commits"):
            self.orphan_parents = body["parents"]
            self.commit_messages.append(body["message"])
            return 0, json.dumps({"sha": "commit1"}), ""
        if method == "POST" and path.endswith("/git/refs"):
            assert body["ref"] == "refs/heads/ops-log"
            self.branch, self.text = True, self.pending_text
            return 0, "{}", ""
        raise AssertionError(f"unexpected call {args}")


def _no_sleep(_: float) -> None:
    return None


def _lines(fake: FakeGh) -> list[dict]:
    assert fake.text is not None
    return [json.loads(x) for x in fake.text.splitlines()]


def _entry(title: str = "t") -> str:
    return ops_log.entry_json("src", title, "a\nb  c", ENV, NOW)


def test_entry_has_the_documented_fields_one_line_and_capped() -> None:
    e = json.loads(ops_log.entry_json("ci-health", "T", "x\n" + "y" * 900, ENV, NOW))
    assert list(e) == ["ts", "source", "title", "body", "workflow", "run_id", "run_url"]
    assert e["ts"] == "2026-11-06T12:00:00Z" and e["workflow"] == "Check Gold Price"
    assert e["run_url"] == "https://github.com/o/r/actions/runs/99"
    assert len(e["body"]) == 500 and "\n" not in e["body"]


def test_append_to_existing_file_keeps_old_lines_and_adds_one() -> None:
    fake = FakeGh(text='{"ts": "2026-11-05T00:00:00Z", "title": "old"}\n')
    ops_log.append_entry(_entry("new"), "o/r", "ops-log: src: new [skip ci]", fake, _no_sleep, NOW)
    rows = _lines(fake)
    assert [r["title"] for r in rows] == ["old", "new"]
    assert fake.commit_messages == ["ops-log: src: new [skip ci]"]


def test_missing_branch_is_created_as_an_orphan_with_the_file() -> None:
    fake = FakeGh(text=None, branch=False)
    ops_log.append_entry(_entry(), "o/r", "ops-log: src: t [skip ci]", fake, _no_sleep, NOW)
    assert fake.orphan_parents == []  # no parent: an orphan branch
    assert fake.branch and len(_lines(fake)) == 1


def test_existing_branch_without_the_file_creates_the_file() -> None:
    fake = FakeGh(text=None, branch=True)
    ops_log.append_entry(_entry(), "o/r", "m", fake, _no_sleep, NOW)
    assert len(_lines(fake)) == 1 and fake.orphan_parents is None


def test_conflict_then_success_rereads_and_keeps_the_other_writers_line() -> None:
    fake = FakeGh(text='{"ts": "2026-11-05T00:00:00Z", "title": "old"}\n', conflicts=2)
    sleeps: list[float] = []
    ops_log.append_entry(_entry("mine"), "o/r", "m", fake, sleeps.append, NOW)
    rows = _lines(fake)
    assert rows[-1]["title"] == "mine" and len(rows) == 4  # old + 2 concurrent + mine
    assert len(sleeps) == 2 and all(s > 0 for s in sleeps)  # backed off between tries


def test_persistent_conflict_gives_up_after_five_tries() -> None:
    fake = FakeGh(text="", conflicts=99)
    with pytest.raises(ops_log.OpsLogWriteError, match="gave up after 5"):
        ops_log.append_entry(_entry(), "o/r", "m", fake, _no_sleep, NOW)
    assert sum(1 for c in fake.calls if "PUT" in c) == 5


def test_a_403_is_not_retried() -> None:
    fake = FakeGh(text="")
    fake.always_fail = "gh: Resource not accessible by integration (HTTP 403)"
    with pytest.raises(ops_log.OpsLogWriteError):
        ops_log.append_entry(_entry(), "o/r", "m", fake, _no_sleep, NOW)
    assert sum(1 for c in fake.calls if "PUT" in c) == 1


def test_prune_drops_lines_older_than_120_days_and_keeps_unparseable_ones() -> None:
    old = (NOW - timedelta(days=121)).strftime("%Y-%m-%dT%H:%M:%SZ")
    new = (NOW - timedelta(days=119)).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [json.dumps({"ts": old}), json.dumps({"ts": new}), "not json"]
    assert ops_log.prune(lines, NOW) == [json.dumps({"ts": new}), "not json"]


def test_append_prunes_on_write() -> None:
    old = (NOW - timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fake = FakeGh(text=json.dumps({"ts": old, "title": "ancient"}) + "\n")
    ops_log.append_entry(_entry("fresh"), "o/r", "m", fake, _no_sleep, NOW)
    assert [r["title"] for r in _lines(fake)] == ["fresh"]


def test_record_still_prints_annotation_and_warns_but_never_raises_when_the_file_fails() -> None:
    fake = FakeGh(text="")
    fake.always_fail = "gh: Server Error (HTTP 500)"
    out = io.StringIO()
    line = ops_log.record("s", "t", "b", environ=ENV, stream=out, gh=fake)
    text = out.getvalue()
    assert line in text and "ops_log: warning: ops-log file not written" in text
    assert text.count("\n") == 2  # the annotation and exactly one warning line


def test_record_writes_the_file_in_actions_and_skips_pull_requests_and_the_off_switch() -> None:
    fake = FakeGh(text="")
    ops_log.record("s", "t", "b", environ=ENV, stream=io.StringIO(), gh=fake)
    assert len(_lines(fake)) == 1
    for extra in (
        {"GITHUB_EVENT_NAME": "pull_request"},
        {"OPS_LOG_NO_FILE": "1"},
        {"GITHUB_ACTIONS": ""},
    ):
        quiet = FakeGh(text="")
        ops_log.record("s", "t", "b", environ={**ENV, **extra}, stream=io.StringIO(), gh=quiet)
        assert quiet.calls == [], extra


def test_record_without_a_token_warns_and_does_not_call_gh() -> None:
    fake = FakeGh(text="")
    env = {k: v for k, v in ENV.items() if k != "GH_TOKEN"}
    out = io.StringIO()
    ops_log.record("s", "t", "b", environ=env, stream=out, gh=fake)
    assert fake.calls == [] and "no GH_TOKEN" in out.getvalue()


# --- every recording job can write the file ----------------------------------------------------

WF_DIR = ROOT / ".github" / "workflows"
RECORD_MARKERS = ("scripts/ops_log.py", "og_run_alert.py", "-m ml.notifications")


def _recording_jobs() -> list[tuple[str, str, dict, dict]]:
    out = []
    for p in sorted(WF_DIR.glob("*.y*ml")):
        wf = yaml.safe_load(p.read_text(encoding="utf-8"))
        for jid, job in (wf.get("jobs") or {}).items():
            if _direct(job) or _via_action(job):
                out.append((p.name, jid, wf, job))
    return out


def _direct(job: dict) -> bool:
    """A step's own `run:` text calls a python recorder (comments elsewhere do not count)."""
    return any(
        m in str(step.get("run", "")) for step in job.get("steps", []) for m in RECORD_MARKERS
    )


def _via_action(job: dict) -> bool:
    return any(step.get("uses") == "./.github/actions/bot-pr-sync" for step in job.get("steps", []))


def test_the_recording_job_scan_finds_the_known_senders() -> None:
    names = {n for n, _, _, _ in _recording_jobs()}
    assert {
        "ci-health.yml",
        "notify-gg.yml",
        "check-price.yml",
        "scrape-tanishq-selfhosted.yml",
    } <= names


@pytest.mark.parametrize(
    "name,jid,wf,job", _recording_jobs(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_every_recording_job_has_contents_write(name: str, jid: str, wf: dict, job: dict) -> None:
    perms = job.get("permissions", wf.get("permissions", {}))
    assert perms.get("contents") == "write", f"{name}:{jid} records but cannot write ops-log"


@pytest.mark.parametrize(
    "name,jid,wf,job", _recording_jobs(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_direct_recorders_get_the_workflow_token(name: str, jid: str, wf: dict, job: dict) -> None:
    if not _direct(
        job
    ):  # bot-pr-sync callers: the action's own token (the bot PAT) writes the file
        return
    text = json.dumps(job)
    assert "OPS_LOG_TOKEN" in text or "GH_TOKEN" in text, f"{name}:{jid} has no token for gh"


def test_no_workflow_runs_on_a_push_to_the_ops_log_branch() -> None:
    """Every `on: push` must name branches that exclude ops-log (the file commits are [skip ci] too)."""
    for p in sorted(WF_DIR.glob("*.y*ml")):
        wf = yaml.safe_load(p.read_text(encoding="utf-8"))
        on = wf.get(True, wf.get("on")) or {}
        if not isinstance(on, dict) or "push" not in on:
            assert "push" not in (on if isinstance(on, (list, str)) else []), p.name
            continue
        push = on["push"] or {}
        branches = push.get("branches")
        ignore = push.get("branches-ignore")
        assert branches or ignore, f"{p.name}: unfiltered push trigger would fire on ops-log"
        if branches:
            assert "ops-log" not in branches and not any("*" in b for b in branches), p.name


# --- the bash fallback for hosts without python ------------------------------------------------

BASH = shutil.which("bash")


def _bash_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    summary = tmp_path / "summary.md"
    return {
        **os.environ,
        "OPS_LOG_PYTHON": "no-such-python-here",  # models a runner with no python3 on PATH
        "OPS_LOG_NO_FILE": "1",
        "GITHUB_STEP_SUMMARY": str(summary),
        "GITHUB_REPOSITORY": "o/r",
        "CI_RETRY_BASE": "0",
        **extra,
    }


@pytest.mark.skipif(BASH is None, reason="bash is needed")
def test_page_without_python_prints_the_annotation_and_writes_the_summary(tmp_path: Path) -> None:
    script = (
        f'source "{(ROOT / "scripts" / "ops_log_fallback.sh").as_posix()}"\n'
        "OPS_LOG_PY_UNUSED=1\n"
        f'HERE="{(ROOT / "scripts").as_posix()}"\n'
    )
    # take page() verbatim from the real script so the test cannot drift from it
    src = (ROOT / "scripts" / "bot_branch_push.sh").read_text(encoding="utf-8")
    page = src[src.index("page() {") : src.index("rescue() {")]
    test_sh = tmp_path / "page_test.sh"
    test_sh.write_text(script + page + 'page "100% stuck\nsecond line"\n', encoding="utf-8")
    proc = subprocess.run(
        [BASH, str(test_sh)],
        capture_output=True,
        text=True,
        env=_bash_env(tmp_path),
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert (
        "::warning title=ops-alert::bot_branch_push: a Tanishq reading needed rescuing -- "
        "100%25 stuck second line https://github.com/o/r/branches"
    ) in proc.stdout
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "### ops-alert: a Tanishq reading needed rescuing" in summary


@pytest.mark.skipif(BASH is None, reason="bash is needed")
def test_bash_fallback_appends_one_json_line_through_gh(tmp_path: Path) -> None:
    """A stub gh serves ops_log.jsonl and records the PUT; the pushed content is valid JSON lines."""
    store = tmp_path / "store.jsonl"
    old = (datetime.now(UTC) - timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%SZ")
    keep = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.write_text(
        f'{{"ts": "{old}", "title": "ancient"}}\n{{"ts": "{keep}", "title": "recent"}}\n',
        encoding="utf-8",
    )
    putlog = tmp_path / "put.log"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_bytes(
        (
            "#!/bin/sh\n"
            'case "$*" in\n'
            f'  *"-X PUT"*) echo "$*" >> "{putlog.as_posix()}"; exit 0 ;;\n'
            f'  *Accept*) cat "{store.as_posix()}" ;;\n'
            "  *) echo sha1 ;;\n"
            "esac\n"
        ).encode()
    )
    gh.chmod(0o755)
    script = tmp_path / "append.sh"
    fallback = (ROOT / "scripts" / "ops_log_fallback.sh").as_posix()
    script.write_text(
        f'source "{fallback}"\nops_log_fallback src "a title" "say \\"hi\\" \\\\ ok"\n',
        encoding="utf-8",
    )
    env = _bash_env(
        tmp_path,
        PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}",
        GITHUB_ACTIONS="true",
        GH_TOKEN="x",
        GITHUB_RUN_ID="7",
        GITHUB_WORKFLOW="W",
    )
    env.pop("OPS_LOG_NO_FILE")
    proc = subprocess.run(
        [BASH, str(script)], capture_output=True, text=True, env=env, timeout=60, check=False
    )
    assert proc.returncode == 0, proc.stderr
    put = putlog.read_text(encoding="utf-8")
    assert "-f branch=ops-log" in put and "-f sha=sha1" in put and "[skip ci]" in put
    content = put.split("-f content=")[1].split(" ")[0]
    rows = [json.loads(x) for x in base64.b64decode(content).decode().splitlines()]
    assert [r["title"] for r in rows] == ["recent", "a title"]  # the 200-day line was pruned
    assert rows[1]["body"] == 'say "hi" \\ ok' and rows[1]["run_url"].endswith(
        "/o/r/actions/runs/7"
    )
