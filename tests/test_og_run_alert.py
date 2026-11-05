"""The share-card workflow renders from master's tip and pages once per run of failures, plus recovery."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import og_run_alert as a

WF = (ROOT / ".github/workflows/generate-og-image.yml").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("current", "previous", "expect"),
    [
        ("failure", [], None),  # one failure is not an incident
        ("failure", ["success", "failure"], None),
        ("failure", ["failure", "success"], "failed twice"),  # the second in a row opens it
        ("failure", ["failure"], "failed twice"),  # history begins with one failure
        ("failure", ["failure", "failure", "success"], None),  # already paged
        ("success", ["failure", "failure"], "refreshing again"),  # recovery after a paged incident
        ("success", ["failure", "success"], None),  # one failure was never paged, so no recovery
        ("success", [], None),
        ("cancelled", ["failure"], None),
    ],
)
def test_decide(current: str, previous: list[str], expect: str | None) -> None:
    msg = a.decide(current, previous)
    if expect is None:
        assert msg is None
    else:
        assert msg is not None and expect in msg["title"]


def test_a_run_of_failures_pages_exactly_once_and_recovers_once() -> None:
    history: list[str] = []  # newest first
    sent = []
    for outcome in ["success", "failure", "failure", "failure", "failure", "success", "success"]:
        msg = a.decide(outcome, history)
        if msg:
            sent.append(msg["priority"])
        history.insert(0, outcome)
    assert sent == ["high", "default"]


def test_only_success_and_failure_count() -> None:
    runs = {
        "workflow_runs": [
            {"id": 1, "conclusion": "skipped"},
            {"id": 2, "conclusion": "failure"},
            {"id": 3, "conclusion": "cancelled"},
            {"id": 4, "conclusion": "success"},
        ]
    }
    assert a.conclusions(runs) == ["failure", "success"]
    for garbage in ("garbage", {"message": "Not Found"}, {"workflow_runs": None}):
        with pytest.raises(ValueError):
            a.conclusions(garbage)


@pytest.mark.parametrize("content", [None, "not json", '{"message": "Bad credentials"}'])
def test_unreadable_history_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str | None
) -> None:
    f = tmp_path / "runs.json"
    if content is not None:
        f.write_text(content, encoding="utf-8")
    assert a.main(["--current", "failure", "--previous", str(f), "--dry-run"]) == 0
    assert "PAGE: Gold Tracker: share card failed (run history unreadable)" in (
        capsys.readouterr().out
    )
    assert a.main(["--current", "success", "--previous", str(f), "--dry-run"]) == 0
    assert "no page" in capsys.readouterr().out


def test_a_due_entry_goes_to_the_ops_log_and_never_to_ntfy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("NTFY_TOPIC", "topic-x")
    assert not hasattr(a, "send") and not hasattr(a, "urllib")  # no ntfy path left in the module
    f = tmp_path / "runs.json"
    f.write_text(json.dumps([{"conclusion": "failure"}]), encoding="utf-8")
    assert a.main(["--current", "failure", "--previous", str(f)]) == 0
    out = capsys.readouterr().out
    assert "::warning title=ops-alert::og_run_alert: Gold Tracker: share card failed twice" in out
    assert "failed twice" in summary.read_text(encoding="utf-8")


def test_main_reads_the_api_shape(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = tmp_path / "runs.json"
    f.write_text(
        json.dumps({"workflow_runs": [{"id": 9, "conclusion": "failure"}]}), encoding="utf-8"
    )
    a.main(["--current", "failure", "--previous", str(f), "--dry-run"])
    assert "PAGE: Gold Tracker: share card failed twice" in capsys.readouterr().out


def test_the_workflow_renders_from_master_tip() -> None:
    checkout = re.search(r"- uses: actions/checkout@\S+.*\n\s+with:\n\s+ref: master\n", WF)
    assert checkout, "generate-og-image must check out master's tip, not the event's commit"


def test_the_workflow_runs_the_alert_on_every_outcome() -> None:
    step = WF.split("- name: Record in the ops log if the card keeps failing", 1)
    assert len(step) == 2, "alert step missing"
    body = step[1]
    assert "if: always()" in body.split("run:", 1)[0]
    assert "JOB_STATUS: ${{ job.status }}" in body
    assert "NTFY_TOPIC" not in body  # ops log, not ntfy (2026-11-06)
    assert "select(.id != ${GITHUB_RUN_ID})" in body
    assert "scripts/og_run_alert.py --current" in body
    # the file gh api writes is the file the script reads
    written = re.search(r'> "(\$\{RUNNER_TEMP\}/[^"]+)"', body)
    read = re.search(r'--previous "([^"]+)"', body)
    assert written and read and written.group(1) == read.group(1)
    # it is the last step, so it sees every earlier step's outcome
    assert "- name:" not in body and "- uses:" not in body
