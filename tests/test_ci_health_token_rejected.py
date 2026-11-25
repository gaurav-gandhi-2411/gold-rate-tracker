"""An expired CI_HEALTH_PAT says so once and does not turn every ci-health run red.

The step that uses the token is the first one in .github/workflows/ci-health.yml. These tests run THAT
step's own shell text (extracted from the workflow, not a copy) against a stub `gh` that answers the
branch-protection call with a simulated 401 and with a 403 and a 500, and run
scripts/ci_health_token_rejected.py's once-per-7-days memory against a fake ops-log read.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ci_health_token_rejected as mod

NOW = datetime(2026, 12, 11, 12, 0, tzinfo=UTC)  # the day after the token's expiry
BASH = shutil.which("bash")
NEEDS_BASH = pytest.mark.skipif(BASH is None, reason="bash not available")
STEP = "Check required status checks on master's most recent lint.yml run"


def _step_script() -> str:
    wf = yaml.safe_load((ROOT / ".github/workflows/ci-health.yml").read_text(encoding="utf-8"))
    steps = {s.get("name", ""): s for s in wf["jobs"]["check"]["steps"]}
    return steps[STEP]["run"]


def _run_step(tmp_path: Path, gh_body: str) -> subprocess.CompletedProcess[str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text("#!/usr/bin/env bash\n" + gh_body + "\n", encoding="utf-8")
    shim = bindir / "python3"
    shim.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    for f in (gh, shim):
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    script = tmp_path / "step.sh"
    script.write_text(_step_script(), encoding="utf-8", newline="\n")
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_WORKSPACE": str(ROOT),
        "GITHUB_REPOSITORY": "o/r",
        "RUNNER_TEMP": str(tmp_path),
        "GH_TOKEN": "x",
        "OPS_LOG_NO_FILE": "1",  # print the annotation; write no file
        "CI_RETRY_BASE": "0",
    }
    assert BASH is not None
    return subprocess.run(
        [BASH, str(script)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


PROTECTION_401 = r"""
case "$*" in
  *branches/master/protection*) echo "gh: Bad credentials (HTTP 401)" >&2; exit 1 ;;
  *) echo "gh: Not Found (HTTP 404)" >&2; exit 1 ;;
esac
"""


@NEEDS_BASH
def test_a_rejected_token_is_said_once_and_the_step_does_not_fail(tmp_path: Path) -> None:
    r = _run_step(tmp_path, PROTECTION_401)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "::warning title=ci-health-token::GitHub rejected CI_HEALTH_PAT (HTTP 401)" in out
    assert out.count("::warning title=ops-alert::ci-health: " + mod.TITLE) == 1
    assert "failing closed" not in out and "::error::" not in out


@NEEDS_BASH
@pytest.mark.parametrize("error", ["HTTP 403: Resource not accessible", "HTTP 500: Server Error"])
def test_any_other_failure_still_fails_closed(tmp_path: Path, error: str) -> None:
    body = f'echo "gh: {error}" >&2; exit 1'
    r = _run_step(tmp_path, body)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "failing closed" in out and "ci-health-token" not in out


def _contents(lines: list[dict]) -> tuple[int, str, str]:
    text = "\n".join(json.dumps(e) for e in lines)
    meta = {"sha": "s", "encoding": "base64", "content": base64.b64encode(text.encode()).decode()}
    return 0, json.dumps(meta), ""


def _entry(ts: str, title: str = mod.TITLE) -> dict:
    return {"ts": ts, "source": "ci-health", "title": title, "body": "b"}


def _run_mod(read: tuple[int, str, str], capsys: pytest.CaptureFixture[str]) -> str:
    wrote: list[str] = []

    def gh(args: list[str], stdin: str | None = None) -> tuple[int, str, str]:
        if any(a == "-X" or "PUT" in a for a in args):  # a write
            wrote.append("w")
            return 0, "{}", ""
        return read

    env = {"GITHUB_REPOSITORY": "o/r", "OPS_LOG_NO_FILE": ""}
    assert mod.run(NOW, environ=env, gh=gh) == 0
    out = capsys.readouterr().out
    return out + ("WROTE" if wrote else "")


def test_a_second_rejection_within_seven_days_is_not_written_again(capsys) -> None:
    recent = _contents([_entry("2026-12-08T03:00:00Z")])
    out = _run_mod(recent, capsys)
    assert "already in the ops log within 7 days" in out and "ops-alert" not in out


def test_an_old_or_other_entry_does_not_count_and_the_message_is_written(capsys) -> None:
    old = _contents([_entry("2026-12-01T03:00:00Z"), _entry("2026-12-10T03:00:00Z", "other")])
    out = _run_mod(old, capsys)
    assert "::warning title=ops-alert::ci-health: " + mod.TITLE in out


def test_an_unreadable_ops_log_still_writes_the_message(capsys) -> None:
    out = _run_mod((1, "", "HTTP 403: nope"), capsys)
    assert "::warning title=ops-alert::ci-health: " + mod.TITLE in out


def test_the_message_carries_the_renewal_pointer_and_no_secret_value() -> None:
    assert "docs/FOR_GG.md section 0" in mod.BODY and "token-expiry.yml" in mod.BODY
    assert "ghp_" not in mod.BODY and "github_pat_" not in mod.BODY
