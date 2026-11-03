"""The data-only lint path runs in SHADOW: it records, the full checks decide (GG 2026-10-30).

Pins the shadow state required by the "new checks start non-blocking" rule in .claude/CLAUDE.md. The
switch to deciding is a separate PR that must change these assertions, citing >= 5 real bot data
PRs over >= 24 h of clean shadow lines.

The scope decision is its own non-blocking step (`id: scope`), so the switch can read it before the
first code check. It is the step a switch would trust, so its own bash is executed here against a
real git repository (verifier finding on #3506, 2026-11-03: string checks let an unconditional
data_only=true, a `-- data/` pathspec, a `--diff-filter=AM` and the PR's own scope script through).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SHADOW = "Data-only path (shadow) -- what it would have decided; the checks above still decide"
SCOPE = "Scope -- data-only or full (master's scope script; undecidable is full)"
BASH = shutil.which("bash")
GIT = shutil.which("git")


def _steps() -> list[dict]:
    wf = yaml.safe_load((ROOT / ".github/workflows/lint.yml").read_text(encoding="utf-8"))
    return wf["jobs"]["lint"]["steps"]


def _step(name: str) -> dict:
    return next(s for s in _steps() if s.get("name") == name)


def test_the_shadow_is_the_last_step_runs_always_and_can_never_fail_the_job():
    shadow = _steps()[-1]
    assert shadow["name"] == SHADOW
    assert shadow["if"] == "always()"
    assert shadow["continue-on-error"] is True
    assert shadow["timeout-minutes"] <= 5
    assert shadow["run"].rstrip().endswith("exit 0")
    assert "set +e" in shadow["run"]
    # the real result of every check above, captured after all of them ran
    assert shadow["env"]["FULL_RESULT"] == "${{ job.status }}"


def test_the_full_checks_are_unchanged_so_they_still_decide():
    wf = yaml.safe_load((ROOT / ".github/workflows/lint.yml").read_text(encoding="utf-8"))
    job = wf["jobs"]["lint"]
    assert "if" not in job and "continue-on-error" not in job
    # no condition of ANY kind on a check: a code step made conditional would let the shadow decide.
    # Only the scope step may be non-blocking, and only unconditionally (it decides nothing yet).
    for s in _steps()[:-1]:
        assert "if" not in s, s.get("name") or s.get("run")
        if s.get("id") == "scope":
            assert s["continue-on-error"] is True
        else:
            assert "continue-on-error" not in s, s.get("name") or s.get("run")
    labels = " | ".join(str(s.get("name") or s.get("run") or "") for s in _steps())
    for must in (
        "pre-commit run --all-files",
        "Install ML dependencies",
        "Installed versions equal the lock",
        "Run smoke tests",
        "Run full pytest suite",
        "Check raw data stays encrypted (ADR 060)",
        "Check no test file has lost tests (hollow-run guard)",
    ):
        assert must in labels, must


def test_scope_runs_before_the_first_code_check_never_blocks_and_never_touches_origin_master():
    steps = _steps()
    names = [s.get("name") or s.get("run") for s in steps]
    scope = _step(SCOPE)
    assert scope["id"] == "scope"
    assert scope["continue-on-error"] is True
    assert scope["timeout-minutes"] <= 3
    assert names.index(SCOPE) < names.index("pip install pre-commit==4.7.0")
    run = scope["run"]
    # a private ref: tests/test_drift.py looks for origin/master and must see the checkout as before
    assert "refs/remotes/origin/master" not in run
    assert '"+refs/heads/master:refs/ci-scope/master"' in run
    # without it git also updates origin/master from the remote's configured fetch refspec
    assert "--refmap=" in run
    assert "git show refs/ci-scope/master:scripts/ci_scope.py" in run
    assert "git show refs/ci-scope/master:.github/actions/bot-pr-sync/action.yml" in run
    assert run.rstrip().endswith("exit 0")


def test_the_shadow_reads_the_scope_step_and_logs_one_comparable_line():
    shadow = _step(SHADOW)
    assert shadow["env"]["DATA_ONLY"] == "${{ steps.scope.outputs.data_only }}"
    assert shadow["env"]["APPLIES"] == "${{ steps.scope.outputs.applies }}"
    run = shadow["run"]
    assert "python scripts/validate_data.py" in run
    assert "pip install -q -c ml/requirements-inference.lock jsonschema pytest" in run
    line = (
        "DATA-ONLY SHADOW: scope=$MODE validate=$VALID data_tests=$DTESTS full=$FULL_RESULT "
        "agree=$AGREE reason=$REASON"
    )
    assert line in run
    assert "GITHUB_STEP_SUMMARY" in run
    assert 'python scripts/data_test_decision.py --reads "$RUNNER_TEMP/data_reads.json"' in run


def test_the_full_suite_records_data_reads_only_on_a_data_only_pr():
    # FOR_GG item 11: recording is switched on by an env var that is empty unless the scope step
    # said data-only, so a code PR's full suite runs exactly as before
    env = _step("Run full pytest suite")["env"]
    assert env == {
        "DATA_READS_OUT": "${{ steps.scope.outputs.data_only == 'true' && "
        "format('{0}/data_reads.json', runner.temp) || '' }}"
    }


# --- the scope step's own bash, on a real repository ----------------------------------------------
NEEDS = pytest.mark.skipif(BASH is None or GIT is None, reason="needs bash and git (CI has both)")


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(
        [
            GIT or "git",
            "-c",
            "commit.gpgsign=false",
            "-c",
            f"core.hooksPath={cwd / '.nohooks'}",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        },
    )
    return r.stdout.strip()


def _write(base: Path, rel: str, text: str) -> None:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _origin(tmp: Path) -> Path:
    """Master with the REAL scope script and bot-pr-sync allowlist, some data and some code."""
    o = tmp / "origin"
    o.mkdir()
    _git(o, "init", "-q", "-b", "master")
    _git(o, "config", "uploadpack.allowAnySHA1InWant", "true")
    for rel in ("scripts/ci_scope.py", ".github/actions/bot-pr-sync/action.yml"):
        _write(o, rel, (ROOT / rel).read_text(encoding="utf-8"))
    for rel in ("data/a.json", "data/old.json"):
        _write(o, rel, "{}\n")
    _write(o, "app.js", "x\n")
    _git(o, "add", "-A")
    _git(o, "commit", "-q", "-m", "base")
    return o


def _pr(tmp: Path, origin: Path, change, orphan: bool = False) -> tuple[Path, str]:
    """A fresh clone with a PR branch made by `change(work)`, pushed to origin. Returns (work, sha)."""
    work = tmp / "work"
    _git(tmp, "clone", "-q", origin.as_uri(), str(work))
    if orphan:
        _git(work, "checkout", "-q", "--orphan", "pr")
        _git(work, "rm", "-q", "-r", "-f", ".")
    else:
        _git(work, "checkout", "-q", "-b", "pr")
    change(work)
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "pr")
    _git(work, "push", "-q", "origin", "pr")
    return work, _git(work, "rev-parse", "HEAD")


def _scope(
    tmp: Path,
    work: Path,
    sha: str,
    event: str = "pull_request",
    ref: str = "1/merge",
    github_sha: str | None = None,
) -> dict[str, str]:
    out = tmp / "out.txt"
    out.write_text("", encoding="utf-8")
    rt = tmp / "rt"
    rt.mkdir(exist_ok=True)
    script = (
        f'python() {{ "{Path(sys.executable).as_posix()}" "$@"; }}\nexport -f python\n'
        f'cd "{work.as_posix()}"\n' + _step(SCOPE)["run"]
    )
    f = tmp / "scope.sh"
    f.write_bytes(script.encode("utf-8"))
    # Git Bash on Windows would rewrite `refs/...:.github/...` as a path list; exclude only args
    # starting with refs/ (no-op on Linux, where CI runs)
    env = {
        **os.environ,
        "MSYS2_ARG_CONV_EXCL": "refs/",
        "EVENT": event,
        "BASE_REF": "master" if event == "pull_request" else "",
        "PR_HEAD": sha if event == "pull_request" else "",
        "REF_NAME": ref,
        "GITHUB_SHA": github_sha or sha,
        "GITHUB_OUTPUT": str(out),
        "RUNNER_TEMP": str(rt),
        "GITHUB_STEP_SUMMARY": str(tmp / "s"),
    }
    r = subprocess.run(
        [BASH or "bash", f.as_posix()], capture_output=True, text=True, timeout=120, env=env
    )
    assert r.returncode == 0, r.stdout + r.stderr
    pairs = (ln.split("=", 1) for ln in out.read_text(encoding="utf-8").splitlines() if "=" in ln)
    return dict(pairs)


def _case(tmp: Path, change, orphan: bool = False) -> dict[str, str]:
    work, sha = _pr(tmp, _origin(tmp), change, orphan=orphan)
    return _scope(tmp, work, sha)


@NEEDS
def test_scope_a_data_only_pr_is_data_only(tmp_path):
    def change(w):
        _write(w, "data/a.json", '{"x": 1}\n')
        _write(w, "data/new.json", "{}\n")

    got = _case(tmp_path, change)
    assert got["data_only"] == "true" and got["applies"] == "true", got


@NEEDS
@pytest.mark.parametrize("what", ["code", "data and code", "code deleted", "data renamed"])
def test_scope_any_code_delete_or_rename_is_full(tmp_path, what):
    def change(w):
        if what in ("code", "data and code"):
            _write(w, "app.js", "y\n")
        if what in ("data and code", "code deleted"):
            _write(w, "data/a.json", '{"x": 1}\n')  # a data change next to it hides nothing
        if what == "code deleted":
            (w / "app.js").unlink()
        if what == "data renamed":
            (w / "data/old.json").rename(w / "data/renamed.json")

    assert _case(tmp_path, change)["data_only"] == "false"


@NEEDS
def test_scope_no_merge_base_is_full(tmp_path):
    got = _case(tmp_path, lambda w: _write(w, "data/x.json", "{}\n"), orphan=True)
    assert got["data_only"] == "false"
    assert got["reason"] == "change list or master's scope script unavailable"


@NEEDS
def test_scope_uses_masters_script_and_allowlist_never_the_checkout(tmp_path):
    origin = _origin(tmp_path)
    work, sha = _pr(tmp_path, origin, lambda w: _write(w, "scripts/x.json", "{}\n"))
    # the checkout (the PR's tree in CI) says data-only for anything; master's copies must decide
    _write(work, "scripts/ci_scope.py", "print('mode=data-only')\nprint('reason: checkout copy')\n")
    action = work / ".github/actions/bot-pr-sync/action.yml"
    action.write_text(
        action.read_text(encoding="utf-8").replace("^(data/", "^(scripts/|data/"), encoding="utf-8"
    )
    assert _scope(tmp_path, work, sha)["data_only"] == "false"


@NEEDS
def test_scope_is_from_the_merge_base_and_leaves_origin_master_alone(tmp_path):
    origin = _origin(tmp_path)
    work, sha = _pr(tmp_path, origin, lambda w: _write(w, "data/a.json", '{"x": 1}\n'))
    before = _git(work, "rev-parse", "refs/remotes/origin/master")
    _write(origin, "app.js", "master moved on\n")  # a two-dot diff would list this code change
    _git(origin, "commit", "-q", "-am", "master moves")
    assert _scope(tmp_path, work, sha)["data_only"] == "true"
    assert _git(work, "rev-parse", "refs/remotes/origin/master") == before


@NEEDS
@pytest.mark.parametrize("pr_data_only", [True, False])
def test_scope_judges_the_pr_head_it_is_given_not_the_checkout(tmp_path, pr_data_only):
    # In CI the checkout is refs/pull/N/merge (and GITHUB_SHA is that merge commit), not the PR
    # head. Here the checkout carries the opposite kind of change to the PR head, so a diff
    # against HEAD or GITHUB_SHA instead of PR_HEAD gives the wrong answer (verifier L1, PR A).
    origin = _origin(tmp_path)
    pr_file, other = ("data/a.json", "app.js") if pr_data_only else ("app.js", "data/a.json")
    work, sha = _pr(tmp_path, origin, lambda w: _write(w, pr_file, "pr\n"))
    _write(work, other, "checkout only\n")
    _git(work, "commit", "-q", "-am", "checkout differs from the PR head")
    checkout = _git(work, "rev-parse", "HEAD")
    got = _scope(tmp_path, work, sha, github_sha=checkout)
    assert got["data_only"] == ("true" if pr_data_only else "false"), got


@NEEDS
def test_scope_dispatch_on_a_branch_applies_and_other_events_do_not(tmp_path):
    origin = _origin(tmp_path)
    work, sha = _pr(tmp_path, origin, lambda w: _write(w, "data/a.json", '{"x": 1}\n'))
    got = _scope(tmp_path, work, sha, event="workflow_dispatch", ref="bot/data-sync")
    assert got["applies"] == "true" and got["data_only"] == "true", got
    for event, ref in (("push", "master"), ("schedule", "master"), ("workflow_dispatch", "master")):
        got = _scope(tmp_path, work, sha, event=event, ref=ref)
        assert got["applies"] == "false" and got["data_only"] == "false", (event, got)


DATA = {"APPLIES": "true", "DATA_ONLY": "true", "SCOPE_REASON": "all data"}


@pytest.mark.skipif(BASH is None, reason="needs bash (CI has it)")
@pytest.mark.parametrize(
    ("scope_env", "valid_rc", "tests_rc", "full", "expect"),
    [
        ({"APPLIES": "false", "DATA_ONLY": "false"}, 0, 0, "success", "nothing to record"),
        (
            DATA,
            0,
            0,
            "success",
            "DATA-ONLY SHADOW: scope=data-only validate=pass data_tests=pass full=success "
            "agree=yes reason=all data",
        ),
        (
            DATA,
            1,
            0,
            "success",
            "DATA-ONLY SHADOW: scope=data-only validate=fail data_tests=pass full=success "
            "agree=no reason=all data",
        ),
        # a data-reading test failed: the data-only decision fails, as the full checks did
        (
            DATA,
            0,
            1,
            "failure",
            "DATA-ONLY SHADOW: scope=data-only validate=pass data_tests=fail full=failure "
            "agree=yes reason=all data",
        ),
        # the 2026-10-29 shape: a linter fails the full checks, the data and its tests are fine
        (
            DATA,
            0,
            0,
            "failure",
            "DATA-ONLY SHADOW: scope=data-only validate=pass data_tests=pass full=failure "
            "agree=no reason=all data",
        ),
        # no usable record of the tests: the data-only path was never tried, so it counts neither
        # way (review finding: the 2026-10-29 shape, a linter failing before the suite ran, would
        # otherwise score as agreement)
        (
            DATA,
            0,
            2,
            "success",
            "DATA-ONLY SHADOW: scope=data-only validate=pass data_tests=error full=success "
            "agree=n/a reason=all data",
        ),
        (
            DATA,
            0,
            2,
            "failure",
            "DATA-ONLY SHADOW: scope=data-only validate=pass data_tests=error full=failure "
            "agree=n/a reason=all data",
        ),
        # a failed or timed-out scope step leaves its outputs empty: recorded as full, never data-only
        (
            {"APPLIES": "", "DATA_ONLY": "", "SCOPE_OUTCOME": "failure"},
            0,
            0,
            "success",
            "DATA-ONLY SHADOW: scope=full validate=pass data_tests=n/a full=success agree=n/a "
            "reason=scope step did not finish (outcome: failure)",
        ),
    ],
)
def test_the_shadow_step_reads_the_scope_outputs(
    tmp_path, scope_env, valid_rc, tests_rc, full, expect
):
    stub = (
        'python() { case "$*" in '
        f"*validate_data*) return {valid_rc} ;; *data_test_decision*) return {tests_rc} ;; "
        "*) return 0 ;; esac; }\n"
        "export -f python\n"
    )
    f = tmp_path / "shadow.sh"
    f.write_bytes((stub + _step(SHADOW)["run"]).encode("utf-8"))
    env = {
        **os.environ,
        "SCOPE_REASON": "",
        "SCOPE_OUTCOME": "success",
        "FULL_RESULT": full,
        **scope_env,
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "s"),
    }
    r = subprocess.run(
        [BASH or "bash", f.as_posix()], capture_output=True, text=True, timeout=60, env=env
    )
    assert r.returncode == 0, r.stderr
    assert expect in r.stdout, r.stdout
