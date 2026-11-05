"""scripts/ops_log.py and scripts/read_ops_log.py: the ops log that replaced ops pushes."""

from __future__ import annotations

import io
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ops_log
import read_ops_log


def test_record_writes_summary_and_one_line_annotation(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    out = io.StringIO()
    line = ops_log.record(
        "ci-health",
        "check red",
        "first line\nsecond line 100% sure",
        environ={"GITHUB_STEP_SUMMARY": str(summary)},
        stream=out,
    )
    assert line.startswith("::warning title=ops-alert::ci-health: check red -- first line")
    assert "\n" not in line and "100%25 sure" in line  # one line, % escaped for the runner
    assert out.getvalue().strip() == line
    text = summary.read_text(encoding="utf-8")
    assert "### ops-alert: check red" in text and "second line" in text


def test_record_without_summary_file_still_prints_and_never_raises() -> None:
    out = io.StringIO()
    ops_log.record("s", "t", "b", environ={}, stream=out)
    assert "::warning title=ops-alert::s: t -- b" in out.getvalue()
    bad = io.StringIO()
    ops_log.record("s", "t", "b", environ={"GITHUB_STEP_SUMMARY": "/no/such/dir/x.md"}, stream=bad)
    assert "::warning title=ops-alert::" in bad.getvalue()  # a bad summary path cannot fail a run


def test_cli_exits_zero(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert ops_log.main(["--source", "x", "--title", "y", "--body", "z"]) == 0
    assert "ops-alert::x: y -- z" in capsys.readouterr().out


# --- read_ops_log -------------------------------------------------------------------------------


def _fake_gh(pages: dict[str, list[dict]]):
    calls: list[str] = []

    def gh(path: str) -> list[dict]:
        calls.append(path)
        for prefix, data in pages.items():
            if path.startswith(prefix):
                return data
        raise read_ops_log.OpsLogError(f"unexpected {path}")

    gh.calls = calls  # type: ignore[attr-defined]
    return gh


def test_collect_filters_title_paginates_and_sorts() -> None:
    repo = "o/r"
    gh = _fake_gh(
        {
            "repos/o/r/actions/runs?": [  # two pages of runs
                {
                    "workflow_runs": [
                        {"id": 1, "name": "CI health", "created_at": "2026-11-05T08:00:00Z"}
                    ]
                },
                {
                    "workflow_runs": [
                        {"id": 2, "name": "Check price", "created_at": "2026-11-04T09:30:00Z"}
                    ]
                },
            ],
            "repos/o/r/actions/runs/1/jobs": [{"jobs": [{"id": 11, "conclusion": "failure"}]}],
            "repos/o/r/actions/runs/2/jobs": [
                {"jobs": [{"id": 21, "conclusion": "success"}, {"id": 22, "conclusion": "skipped"}]}
            ],
            "repos/o/r/check-runs/11/annotations": [
                [
                    {"title": "ops-alert", "message": "ci-health: red -- x"},
                    {"title": "other", "message": "noise"},
                ]
            ],
            "repos/o/r/check-runs/21/annotations": [
                [{"title": "ops-alert", "message": "a: b -- c"}]
            ],
        }
    )
    rows = read_ops_log.collect(repo, datetime(2026, 11, 1, tzinfo=UTC), gh=gh)
    assert rows == [
        ("2026-11-04 09:30", "Check price", "a: b -- c"),
        ("2026-11-05 08:00", "CI health", "ci-health: red -- x"),
    ]
    assert not any("/check-runs/22/" in c for c in gh.calls)  # skipped jobs are not read


def test_main_fails_closed_when_the_api_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(path: str) -> list[dict]:
        raise read_ops_log.OpsLogError("gh api failed (exit 1): HTTP 401")

    monkeypatch.setattr(read_ops_log, "gh_api", boom)
    monkeypatch.setattr(read_ops_log, "collect", lambda *a, **k: boom("x"))
    assert read_ops_log.main(["--days", "3"]) == 2
    assert "FAILED" in capsys.readouterr().err


def test_main_prints_an_explicit_empty_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(read_ops_log, "collect", lambda *a, **k: [])
    assert read_ops_log.main([]) == 0
    assert "no ops-alert entries in the last 7 days" in capsys.readouterr().out
