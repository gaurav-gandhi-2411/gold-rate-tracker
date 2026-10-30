"""The data-only lint path runs in SHADOW: it records, the full checks decide (GG 2026-10-30).

Pins the shadow state required by the "new checks start non-blocking" rule in .claude/CLAUDE.md. The
switch to deciding is a separate PR that must change these assertions, citing >= 5 real bot data
PRs over >= 24 h of clean shadow lines.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SHADOW = "Data-only path (shadow) -- what it would have decided; the checks above still decide"


def _steps() -> list[dict]:
    wf = yaml.safe_load((ROOT / ".github/workflows/lint.yml").read_text(encoding="utf-8"))
    return wf["jobs"]["lint"]["steps"]


def test_the_shadow_is_the_last_step_runs_always_and_can_never_fail_the_job():
    shadow = _steps()[-1]
    assert shadow["name"] == SHADOW
    assert shadow["if"] == "always()"
    assert shadow["continue-on-error"] is True
    assert shadow["run"].rstrip().endswith("exit 0")
    assert "set +e" in shadow["run"]
    # the real result of every check above, captured after all of them ran
    assert shadow["env"]["FULL_RESULT"] == "${{ job.status }}"


def test_the_full_checks_are_unchanged_so_they_still_decide():
    for s in _steps()[:-1]:
        assert "scope" not in str(s.get("if", "")), s.get("name") or s.get("run")
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


def test_the_shadow_decides_with_masters_copy_and_logs_one_comparable_line():
    run = _steps()[-1]["run"]
    assert "git show refs/remotes/origin/master:scripts/ci_scope.py" in run
    assert "git show refs/remotes/origin/master:.github/actions/bot-pr-sync/action.yml" in run
    assert '--action "$RUNNER_TEMP/action.yml"' in run
    assert '[ "$EVENT" = "pull_request" ] && [ "$BASE_REF" = "master" ]' in run
    assert '[ "$MODE" = "data-only" ] || MODE=full' in run
    assert "python scripts/validate_data.py" in run
    assert "pip install -q -c ml/requirements-inference.lock jsonschema pytest" in run
    line = "DATA-ONLY SHADOW: scope=$MODE validate=$VALID full=$FULL_RESULT agree=$AGREE reason=$REASON"
    assert line in run
    assert "GITHUB_STEP_SUMMARY" in run
