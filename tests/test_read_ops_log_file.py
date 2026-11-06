"""scripts/read_ops_log.py default path: one gh call reads ops_log.jsonl from the ops-log branch.

The clock is never read here: `since` is passed in, and main() has the file reader and the
annotation walk replaced by fakes.
"""

from __future__ import annotations

import base64
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import read_ops_log

SINCE = datetime(2026, 11, 1, tzinfo=UTC)


def _line(ts: str, title: str) -> str:
    entry = {"ts": ts, "source": "s", "title": title, "body": "b", "workflow": "W"}
    return json.dumps(entry)


def _gh_with(text: str):
    calls: list[list[str]] = []

    def gh(args: list[str], stdin: str | None = None) -> tuple[int, str, str]:
        calls.append(args)
        enc = base64.b64encode(text.encode()).decode()
        return 0, json.dumps({"sha": "x", "encoding": "base64", "content": enc}), ""

    gh.calls = calls  # type: ignore[attr-defined]
    return gh


def _gh_failing(stderr: str):
    return lambda args, stdin=None: (1, "", stderr)


def test_read_file_filters_to_the_window_sorts_and_uses_one_call() -> None:
    text = "\n".join(
        [
            _line("2026-11-05T08:00:00Z", "newer"),
            _line("2026-10-20T08:00:00Z", "too old"),
            _line("2026-11-02T09:30:00Z", "older"),
        ]
    )
    gh = _gh_with(text)
    rows = read_ops_log.read_file("o/r", SINCE, gh=gh)
    assert rows == [
        "2026-11-02 09:30  W  s: older -- b",
        "2026-11-05 08:00  W  s: newer -- b",
    ]
    assert len(gh.calls) == 1


def test_read_file_lists_an_unparseable_line_raw_instead_of_dropping_it() -> None:
    rows = read_ops_log.read_file("o/r", SINCE, gh=_gh_with("garbage {\n"))
    assert rows is not None and rows[0].startswith("(unparseable line) garbage")


def test_read_file_missing_is_none_and_unreadable_raises() -> None:
    assert read_ops_log.read_file("o/r", SINCE, gh=_gh_failing("gh: Not Found (HTTP 404)")) is None
    with pytest.raises(read_ops_log.OpsLogError, match="HTTP 500"):
        read_ops_log.read_file("o/r", SINCE, gh=_gh_failing("gh: Server Error (HTTP 500)"))


def test_main_prints_file_entries_without_walking_annotations(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(read_ops_log, "read_file", lambda repo, since: ["row one"])
    monkeypatch.setattr(
        read_ops_log, "collect", lambda *a, **k: pytest.fail("the annotation walk must not run")
    )
    assert read_ops_log.main(["--days", "7"]) == 0
    out = capsys.readouterr().out
    assert "1 entries in the last 7 days" in out and "row one" in out


def test_main_falls_back_to_the_walk_when_the_file_does_not_exist_yet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(read_ops_log, "read_file", lambda repo, since: None)
    monkeypatch.setattr(read_ops_log, "emitting_workflows", lambda: ["a.yml"])
    monkeypatch.setattr(
        read_ops_log, "collect", lambda *a, **k: [("2026-11-05 08:00", "a.yml", "walked")]
    )
    assert read_ops_log.main([]) == 0
    out = capsys.readouterr().out
    assert "no ops-log file yet, falling back" in out and "walked" in out


def test_main_fails_closed_when_the_file_is_unreadable_and_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(repo: str, since: datetime) -> list[str]:
        raise read_ops_log.OpsLogError("read failed: HTTP 403")

    monkeypatch.setattr(read_ops_log, "read_file", boom)
    monkeypatch.setattr(
        read_ops_log, "collect", lambda *a, **k: pytest.fail("no silent fallback on an error")
    )
    assert read_ops_log.main([]) == 2
    assert "FAILED, cannot read the ops-log file" in capsys.readouterr().err


def test_from_annotations_forces_the_walk(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        read_ops_log, "read_file", lambda *a, **k: pytest.fail("the file must not be read")
    )
    monkeypatch.setattr(read_ops_log, "emitting_workflows", lambda: ["a.yml"])
    monkeypatch.setattr(read_ops_log, "collect", lambda *a, **k: [])
    assert read_ops_log.main(["--from-annotations"]) == 0
    assert "no ops-alert entries" in capsys.readouterr().out
