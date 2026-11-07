"""The data-only lint path decides (GG 2026-10-30 option C; switched on 2026-11-07).

On a PR the Scope step calls data-only, the required `lint` job skips the code-only checks and is
decided by the lock install, the validation of data/, the tests that read data/ and the raw-data
guard. Every other run (code PRs, pushes, the schedule, a failed or undecidable scope) runs
everything, as before. This file pins that split:

  * every step of the job is classified ALWAYS / CODE_ONLY / DATA_ONLY in the table below, and a new
    step that is not in the table fails the test, so a future check cannot be silently skipped on
    data PRs (or silently added to them);
  * each step's own `if:` is evaluated for the possible values of the scope output;
  * the validate and data-test steps' own bash is executed with stubs (a failure fails the step,
    an unusable selection runs the FULL suite);
  * the workflow reads the committed measurement (the selector itself: tests/test_select_data_tests_measured.py).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="needs bash (CI has it)")

SCOPE = "Scope -- data-only or full (master's scope script; undecidable is full)"
RECORD = "Data-only path (decision record) -- one line per run; records, never decides"
VALIDATE = "Validate the data (scripts/validate_data.py) -- decides a data-only PR"
DATA_TESTS = (
    "Run the tests that read data/ (data-only PR; selection fails closed to the full suite)"
)

# Run on every run, data-only or not.
ALWAYS = {
    "actions/checkout",
    "actions/setup-python 3.11",
    SCOPE,
    "actions/setup-python 3.12",
    "Install ML dependencies",
    "Show installed versions",
    "Installed versions equal the lock",
    "Check raw data stays encrypted (ADR 060)",
    RECORD,
}
# Skipped on a data-only run (the code cannot have changed); run on every other run.
CODE_ONLY = {
    "pip install pre-commit==4.7.0",
    "pre-commit run --all-files",
    "Run smoke tests",
    "Run full pytest suite",
    "Check every JS test file is referenced by some workflow",
    "Check no test file has lost tests (hollow-run guard)",
    "Check plain-language compliance (U1/U3, docs/PLAIN_LANGUAGE_AUDIT.md)",
    "Retailer-language guard (ADR 059 -- no judgmental wording near a retailer name)",
    "Check for hand-typed computed claims (accuracy/coverage/error)",
    "Every .portfolio/metrics.json metric must have a committed artifact",
}
# Run only on a data-only run; they decide it.
DATA_ONLY = {VALIDATE, DATA_TESTS}


def _steps() -> list[dict]:
    wf = yaml.safe_load((ROOT / ".github/workflows/lint.yml").read_text(encoding="utf-8"))
    return wf["jobs"]["lint"]["steps"]


def _label(step: dict) -> str:
    if step.get("name"):
        return str(step["name"])
    if "run" in step:
        return str(step["run"]).strip()
    uses = str(step["uses"]).split("@")[0]
    version = (step.get("with") or {}).get("python-version")
    return f"{uses} {version}" if version else uses


def _step(label: str) -> dict:
    return next(s for s in _steps() if _label(s) == label)


def _runs(step: dict, data_only: str) -> bool:
    """Whether GitHub runs `step` when every earlier step succeeded, for a scope output value.

    Only the three `if:` forms the job uses are understood; anything else fails the test, so a new
    kind of condition has to be modelled here on purpose.
    """
    cond = step.get("if")
    if cond is None or cond == "always()":
        return True
    if cond == "steps.scope.outputs.data_only != 'true'":
        return data_only != "true"
    if cond == "steps.scope.outputs.data_only == 'true'":
        return data_only == "true"
    raise AssertionError(f"unmodelled condition {cond!r} on {_label(step)!r}")


def test_every_step_is_classified_exactly_once_and_the_table_has_no_stale_entry():
    labels = [_label(s) for s in _steps()]
    assert len(labels) == len(set(labels)), "step labels must be unique"
    tables = [ALWAYS, CODE_ONLY, DATA_ONLY]
    assert sum(len(t) for t in tables) == len(ALWAYS | CODE_ONLY | DATA_ONLY), "in two tables"
    unclassified = set(labels) - (ALWAYS | CODE_ONLY | DATA_ONLY)
    assert not unclassified, (
        f"classify {sorted(unclassified)} in tests/test_lint_data_only_switch.py: is it code-only "
        "(skipped on a data-only PR) or always?"
    )
    assert not (ALWAYS | CODE_ONLY | DATA_ONLY) - set(labels), "stale entry in the table"


def test_on_a_data_only_run_exactly_the_designed_steps_run():
    ran = {_label(s) for s in _steps() if _runs(s, "true")}
    skipped = {_label(s) for s in _steps() if not _runs(s, "true")}
    assert ran == ALWAYS | DATA_ONLY
    assert skipped == CODE_ONLY


@pytest.mark.parametrize("data_only", ["false", "", "True", "maybe"])
def test_every_other_scope_value_runs_everything_as_before(data_only):
    # '' is what a failed, timed-out or undecidable scope step leaves behind (no output at all)
    ran = {_label(s) for s in _steps() if _runs(s, data_only)}
    assert ran == ALWAYS | CODE_ONLY
    assert DATA_ONLY.isdisjoint(ran)


def test_the_steps_that_ran_before_the_switch_still_run_unconditionally_on_a_full_run():
    # the full-scope checks, by name, as they were on master before this change
    before = {
        "pip install pre-commit==4.7.0",
        "pre-commit run --all-files",
        "Install ML dependencies",
        "Show installed versions",
        "Installed versions equal the lock",
        "Run smoke tests",
        "Run full pytest suite",
        "Check raw data stays encrypted (ADR 060)",
        "Check every JS test file is referenced by some workflow",
        "Check no test file has lost tests (hollow-run guard)",
        "Check plain-language compliance (U1/U3, docs/PLAIN_LANGUAGE_AUDIT.md)",
        "Retailer-language guard (ADR 059 -- no judgmental wording near a retailer name)",
        "Check for hand-typed computed claims (accuracy/coverage/error)",
        "Every .portfolio/metrics.json metric must have a committed artifact",
    }
    full = {_label(s) for s in _steps() if _runs(s, "false")}
    assert before <= full
    # and none of them became non-blocking
    for label in before:
        assert "continue-on-error" not in _step(label), label
        assert "timeout-minutes" not in _step(label) or label == SCOPE, label


def test_only_the_scope_and_record_steps_may_be_non_blocking_and_the_job_has_no_condition():
    wf = yaml.safe_load((ROOT / ".github/workflows/lint.yml").read_text(encoding="utf-8"))
    job = wf["jobs"]["lint"]
    assert "if" not in job and "continue-on-error" not in job
    for s in _steps():
        if _label(s) in (SCOPE, RECORD):
            assert s["continue-on-error"] is True
        else:
            assert "continue-on-error" not in s, _label(s)
    assert _step(RECORD)["if"] == "always()"
    assert _label(_steps()[-1]) == RECORD  # last, so it sees every earlier step's outcome


def test_a_failed_scope_leaves_no_data_only_output_so_everything_runs():
    # the scope step is non-blocking, and only ever writes data_only=true on an explicit data-only
    # answer (executed in tests/test_lint_data_only_shadow.py); the skip conditions read '' as full
    scope = _step(SCOPE)
    assert scope["continue-on-error"] is True and scope["id"] == "scope"
    assert scope["run"].count("data_only=true") == 0  # written through $DATA_ONLY only
    assert '[ "$MODE" = "data-only" ] || MODE=full' in scope["run"]


def test_the_raw_data_guard_and_the_lock_proof_run_on_data_only_runs_without_a_condition():
    for label in (
        "Check raw data stays encrypted (ADR 060)",
        "Installed versions equal the lock",
        "Install ML dependencies",
    ):
        assert "if" not in _step(label), label


# --- the data-only steps' own bash ------------------------------------------------------------------
def _run_bash(tmp_path: Path, script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    f = tmp_path / "step.sh"
    f.write_bytes(script.encode("utf-8"))
    full_env = {**os.environ, "RUNNER_TEMP": str(tmp_path), **env}
    # `-e` and `-o pipefail`-less, as GitHub's default `bash -e {0}` shell
    return subprocess.run(
        [BASH or "bash", "-e", f.as_posix()],
        capture_output=True,
        text=True,
        timeout=60,
        env=full_env,
    )


STUBS = (
    'python() { case "$*" in\n'
    '  *select_data_tests*) printf "%s" "$STUB_SELECT_OUT"; return "$STUB_SELECT_RC" ;;\n'
    '  *validate_data*) return "$STUB_VALIDATE_RC" ;;\n'
    "esac; return 0; }\n"
    'pytest() { echo "$*" >> "$PYTEST_LOG"; return "$STUB_PYTEST_RC"; }\n'
    "export -f python pytest\n"
)


@needs_bash
@pytest.mark.parametrize(("rc", "fails"), [(0, False), (1, True), (2, True)])
def test_validate_data_failing_or_unable_to_run_fails_the_job(tmp_path, rc, fails):
    r = _run_bash(
        tmp_path,
        STUBS + _step(VALIDATE)["run"],
        {"STUB_VALIDATE_RC": str(rc), "PYTEST_LOG": str(tmp_path / "p.log")},
    )
    assert (r.returncode != 0) is fails, r.stdout + r.stderr


def _data_tests(
    tmp_path: Path, select_rc: int, select_out: str, pytest_rc: int
) -> tuple[subprocess.CompletedProcess[str], str]:
    log = tmp_path / "p.log"
    r = _run_bash(
        tmp_path,
        STUBS + _step(DATA_TESTS)["run"],
        {
            "STUB_SELECT_RC": str(select_rc),
            "STUB_SELECT_OUT": select_out,
            "STUB_PYTEST_RC": str(pytest_rc),
            "PYTEST_LOG": str(log),
        },
    )
    return r, log.read_text(encoding="utf-8") if log.exists() else ""


@needs_bash
def test_the_selected_files_are_what_pytest_runs_not_the_whole_suite(tmp_path):
    r, log = _data_tests(tmp_path, 0, "tests/test_a.py\ntests/test_b.py\n", 0)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "-m not integration tests/test_a.py tests/test_b.py -v" in log
    assert " tests/ " not in log + " "
    assert "running 2 selected test files" in r.stdout


@needs_bash
def test_a_failing_selected_test_fails_the_step(tmp_path):
    r, log = _data_tests(tmp_path, 0, "tests/test_a.py\n", 1)
    assert r.returncode != 0
    assert "tests/test_a.py" in log


@needs_bash
@pytest.mark.parametrize(
    ("select_rc", "select_out"),
    [(2, "tests/test_a.py\n"), (1, "tests/test_a.py\n"), (0, ""), (2, "")],
    ids=["selector-error", "selector-failed", "empty-selection", "error-and-empty"],
)
def test_selection_that_cannot_be_computed_runs_the_full_suite(tmp_path, select_rc, select_out):
    r, log = _data_tests(tmp_path, select_rc, select_out, 0)
    assert r.returncode == 0, r.stdout + r.stderr
    assert log.strip() == "-m not integration tests/ -v"
    assert "FULL suite" in r.stdout
    # and a failure of that full run still fails the step
    r, _ = _data_tests(tmp_path, select_rc, select_out, 1)
    assert r.returncode != 0


@needs_bash
def test_a_selector_that_prints_crlf_paths_still_selects_the_right_files(tmp_path):
    # Windows runners print \r\n; the path handed to pytest must not carry the \r
    r, log = _data_tests(tmp_path, 0, "tests/test_a.py\r\n", 0)
    assert r.returncode == 0
    assert log.strip() == "-m not integration tests/test_a.py -v"


@needs_bash
@pytest.mark.parametrize(
    ("env", "expect"),
    [
        (
            {
                "DATA_ONLY": "true",
                "VALIDATE": "success",
                "DATA_TESTS": "success",
                "RESULT": "success",
            },
            "DATA-ONLY DECISION: scope=data-only validate=success data_tests=success "
            "result=success reason=all data",
        ),
        (
            {
                "DATA_ONLY": "true",
                "VALIDATE": "failure",
                "DATA_TESTS": "skipped",
                "RESULT": "failure",
            },
            "DATA-ONLY DECISION: scope=data-only validate=failure data_tests=skipped "
            "result=failure reason=all data",
        ),
        (
            {
                "DATA_ONLY": "false",
                "VALIDATE": "skipped",
                "DATA_TESTS": "skipped",
                "RESULT": "success",
            },
            "DATA-ONLY DECISION: scope=full validate=skipped data_tests=skipped "
            "result=success reason=all data",
        ),
        (
            {"DATA_ONLY": "", "SCOPE_REASON": "", "SCOPE_OUTCOME": "failure", "RESULT": "success"},
            "scope=full validate= data_tests= result=success "
            "reason=scope step did not finish (outcome: failure)",
        ),
        ({"APPLIES": "false", "DATA_ONLY": "false"}, "nothing to record"),
    ],
)
def test_the_record_line_reads_the_scope_and_the_deciding_steps(tmp_path, env, expect):
    base = {
        "APPLIES": "true",
        "SCOPE_REASON": "all data",
        "SCOPE_OUTCOME": "success",
        "VALIDATE": "",
        "DATA_TESTS": "",
        "RESULT": "",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "s"),
    }
    r = _run_bash(tmp_path, _step(RECORD)["run"], {**base, **env})
    assert r.returncode == 0, r.stderr
    assert expect in r.stdout, r.stdout


def test_the_record_step_cannot_decide_it_is_non_blocking_and_exits_zero():
    s = _step(RECORD)
    assert s["continue-on-error"] is True and s["timeout-minutes"] <= 5
    assert s["run"].rstrip().endswith("exit 0") and "set +e" in s["run"]


# --- the workflow reads the committed measurement -----------------------------------------------------
def test_the_data_test_step_reads_the_committed_measurement():
    assert (ROOT / "tests" / "data_reading_tests.txt").is_file()
    assert "--measured tests/data_reading_tests.txt" in _step(DATA_TESTS)["run"]
