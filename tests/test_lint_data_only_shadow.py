"""The data-only lint path DECIDES on data-only PRs (GG 2026-10-30; switched from shadow 2026-11-03).

The shadow ran from 2026-10-30 and recorded what it would have decided next to the full result. The
switch condition (.claude/CLAUDE.md, "new checks start non-blocking"; GG 2026-10-30): >= 5 real bot
data PRs over >= 24 h where the scope call was right, validation agreed with the full result and no
code file was classed as data. Met: 40 bot data PRs from 2026-11-01 10:08Z to 2026-11-03 01:05Z,
all agree=yes, 0 files outside the data paths (the PR that switched cites the run IDs).

These pin the deciding state: on a data-only PR the code checks still run but cannot fail the job,
the encryption guard and the data validation decide; on every other run nothing changed.
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
COE = "${{ steps.scope.outputs.data_only == 'true' }}"
DECIDE = "Data-only path -- validate the data (decides on a data-only PR; records otherwise)"
CODE_CHECKS = {
    "precommit": "pre-commit run --all-files",
    "install_ml": "Install ML dependencies",
    "show_versions": "Show installed versions",
    "lock_equal": "Installed versions equal the lock",
    "smoke": "Run smoke tests",
    "pytest_full": "Run full pytest suite",
    "js_registry": "Check every JS test file is referenced by some workflow",
    "test_counts": "Check no test file has lost tests (hollow-run guard)",
    "plain_language": "Check plain-language compliance (U1/U3, docs/PLAIN_LANGUAGE_AUDIT.md)",
    "retailer": "Retailer-language guard (ADR 059 -- no judgmental wording near a retailer name)",
    "computed_claims": "Check for hand-typed computed claims (accuracy/coverage/error)",
    "provenance": "Every .portfolio/metrics.json metric must have a committed artifact",
}


def _wf() -> dict:
    return yaml.safe_load((ROOT / ".github/workflows/lint.yml").read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return _wf()["jobs"]["lint"]["steps"]


def _label(s: dict) -> str:
    return str(s.get("name") or s.get("run") or s.get("uses") or "")


def test_the_job_itself_is_unconditional():
    job = _wf()["jobs"]["lint"]
    assert "if" not in job and "continue-on-error" not in job


def test_scope_is_decided_before_the_first_code_check_with_masters_copy():
    steps = _steps()
    i = next(i for i, s in enumerate(steps) if s.get("id") == "scope")
    first_code = next(i for i, s in enumerate(steps) if s.get("id") in CODE_CHECKS)
    assert i < first_code
    scope = steps[i]
    assert "if" not in scope and "continue-on-error" not in scope
    run = scope["run"]
    assert "git show refs/remotes/origin/master:scripts/ci_scope.py" in run
    assert "git show refs/remotes/origin/master:.github/actions/bot-pr-sync/action.yml" in run
    assert 'git diff --name-status "refs/remotes/origin/master...$HEAD"' in run
    assert '[ "$EVENT" = "pull_request" ] && [ "$BASE_REF" = "master" ]' in run
    # only ever widens to full: anything but an explicit data-only is full
    assert '[ "$MODE" = "data-only" ] || MODE=full' in run
    assert 'echo "data_only=$DATA_ONLY" >> "$GITHUB_OUTPUT"' in run
    assert run.rstrip().endswith("exit 0")


def test_every_code_check_is_non_blocking_only_on_a_data_only_run():
    by_id = {s.get("id"): s for s in _steps()}
    for sid, label in CODE_CHECKS.items():
        s = by_id[sid]
        assert _label(s) == label, sid
        assert s["continue-on-error"] == COE, sid
        assert "if" not in s, sid


def test_the_encryption_guard_and_every_other_step_stay_blocking():
    for s in _steps():
        if s.get("id") in CODE_CHECKS or s.get("name") == DECIDE:
            continue
        assert "continue-on-error" not in s, _label(s)
        assert "if" not in s, _label(s)
    labels = [_label(s) for s in _steps()]
    assert "Check raw data stays encrypted (ADR 060)" in labels
    # every step of the lint job is classified: a new step must be placed deliberately
    known = set(CODE_CHECKS.values()) | {
        DECIDE,
        "Check raw data stays encrypted (ADR 060)",
        "Scope -- data-only or full (master's scope script; undecidable is full)",
        "pip install pre-commit==4.7.0",
    }
    for s in _steps():
        if "uses" in s:
            continue
        assert _label(s) in known, f"unclassified lint step: {_label(s)}"


def test_the_decision_is_the_last_step_runs_always_and_validates():
    d = _steps()[-1]
    assert d["name"] == DECIDE
    assert d["if"] == "always()"
    assert "continue-on-error" not in d  # it decides
    assert d["env"]["DATA_ONLY"] == "${{ steps.scope.outputs.data_only }}"
    assert d["env"]["STEPS"] == "${{ toJSON(steps) }}"
    run = d["run"]
    assert "python scripts/validate_data.py" in run
    assert "pip install -q -c ml/requirements-inference.lock jsonschema pytest" in run
    assert 'if [ "$VALID" != "pass" ]; then' in run


def test_the_forwarded_statuses_still_need_lint_and_pwa_js_to_succeed():
    post = _wf()["jobs"]["post-required-check-status"]
    assert post["needs"] == ["lint", "pwa-js"]
    assert "needs.lint.result == 'success'" in post["if"]


# --- the decision step, executed in bash -------------------------------------------------------
BASH = shutil.which("bash")


def _decide(tmp: Path, data_only: str, validate_exit: int, steps: dict) -> tuple[int, str]:
    """Run the step's own script with stub python: `-m pip` is a no-op, validate_data.py exits
    `validate_exit`, and `-c` (the failed-step list) runs the real interpreter."""
    import sys

    py = Path(sys.executable).as_posix()
    stubs = f"""
python() {{
  case "$1" in
    -m) return 0 ;;
    scripts/validate_data.py) echo "validate stub"; return {validate_exit} ;;
    *) "{py}" "$@" ;;
  esac
}}
export -f python
"""
    script = stubs + _steps()[-1]["run"]
    f = tmp / "decide.sh"
    f.write_bytes(script.encode("utf-8"))
    summary = tmp / "summary.md"
    env = {
        **os.environ,
        "DATA_ONLY": data_only,
        "FULL_RESULT": "success",
        "STEPS": json.dumps(steps),
        "RUNNER_TEMP": str(tmp),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    r = subprocess.run(
        [BASH or "bash", f.as_posix()],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=ROOT,
    )
    return r.returncode, r.stdout


MYPY_RED = {
    "scope": {"outputs": {"data_only": "true"}, "outcome": "success", "conclusion": "success"},
    "precommit": {"outputs": {}, "outcome": "failure", "conclusion": "success"},
    "pytest_full": {"outputs": {}, "outcome": "success", "conclusion": "success"},
}


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_a_data_only_pr_with_valid_data_passes_even_when_masters_code_checks_fail(tmp_path):
    # the 2026-10-29 shape: mypy red on master's code, a bot data PR with good data
    code, out = _decide(tmp_path, "true", 0, MYPY_RED)
    assert code == 0, out
    assert "DATA-ONLY PATH: scope=data-only validate=pass code_checks_failed=precommit" in out
    assert "::warning::code checks failed on master's code" in out


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize(("validate_exit", "word"), [(1, "fail"), (2, "error")])
def test_a_data_only_pr_with_bad_or_unvalidatable_data_fails(tmp_path, validate_exit, word):
    code, out = _decide(tmp_path, "true", validate_exit, {})
    assert code == 1, out
    assert f"validate={word}" in out and "::error::the data did not validate" in out


@pytest.mark.skipif(BASH is None, reason="bash not available")
@pytest.mark.parametrize("data_only", ["false", ""])
def test_a_full_run_or_an_undecided_scope_only_records(tmp_path, data_only):
    # "" is what a scope step that failed or timed out leaves: the full checks decided
    code, out = _decide(tmp_path, data_only, 1, MYPY_RED)
    assert code == 0, out
    assert "scope=full; the checks above decided" in out
    assert "validate stub" not in out
