"""scripts/read_ops_log.py default path: one gh call reads ops_log.jsonl from the ops-log branch.

No ambient clock: `since` is passed in, and main() runs with `_now` pinned and the file reader and
the annotation walk replaced by fakes. No test here spawns a subprocess, so no GITHUB_* variable
(CI sets GITHUB_EVENT_NAME=pull_request on PR runs) is relied on.
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
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)  # the pinned "today" for main()


def _line(ts: str, title: str) -> str:
    entry = {"ts": ts, "source": "s", "title": title, "body": "b", "workflow": "W"}
    return json.dumps(entry)


def _gh_with(text: str):
    calls: list[list[str]] = []

    def gh(args: list[str], stdin: str | None = None, timeout: float = 60):
        calls.append(args)
        enc = base64.b64encode(text.encode()).decode()
        return 0, json.dumps({"sha": "x", "encoding": "base64", "content": enc}), ""

    gh.calls = calls  # type: ignore[attr-defined]
    return gh


def _gh_failing(stderr: str):
    return lambda args, stdin=None, timeout=60: (1, "", stderr)


def test_read_file_filters_to_the_window_sorts_and_uses_one_call() -> None:
    text = "\n".join(
        [
            _line("2026-11-05T08:00:00Z", "newer"),
            _line("2026-10-20T08:00:00Z", "too old"),
            _line("2026-11-02T09:30:00Z", "older"),
        ]
    )
    gh = _gh_with(text)
    rows, first = read_ops_log.read_file("o/r", SINCE, gh=gh)
    assert rows == [
        "2026-11-02 09:30  W  s: older -- b",
        "2026-11-05 08:00  W  s: newer -- b",
    ]
    assert first == "2026-10-20T08:00:00Z"  # the earliest entry in the file, in or out of window
    assert len(gh.calls) == 1


def test_read_file_lists_an_unparseable_line_raw_instead_of_dropping_it() -> None:
    rows, first = read_ops_log.read_file("o/r", SINCE, gh=_gh_with("garbage {\n"))
    assert rows[0].startswith("(unparseable line) garbage") and first is None


def test_read_file_missing_is_none_and_unreadable_raises() -> None:
    assert read_ops_log.read_file("o/r", SINCE, gh=_gh_failing("gh: Not Found (HTTP 404)")) is None
    with pytest.raises(read_ops_log.OpsLogError, match="HTTP 500"):
        read_ops_log.read_file("o/r", SINCE, gh=_gh_failing("gh: Server Error (HTTP 500)"))
    with pytest.raises(read_ops_log.OpsLogError, match="HTTP 403"):
        read_ops_log.read_file("o/r", SINCE, gh=_gh_failing("gh: Forbidden (HTTP 403)"))


def test_a_file_over_1mb_is_read_through_the_raw_media_type() -> None:
    """Above 1 MB the JSON form has no content (encoding none); the reader asks for raw bytes."""
    raw = "\n".join(_line("2026-11-05T08:00:00Z", f"t{n}") for n in range(3)) + "\n"
    calls: list[list[str]] = []

    def gh(args: list[str], stdin: str | None = None, timeout: float = 60):
        calls.append(args)
        if any("raw" in a for a in args):
            return 0, raw, ""
        return 0, json.dumps({"sha": "x", "encoding": "none", "content": "", "size": 1_200_000}), ""

    rows, _ = read_ops_log.read_file("o/r", SINCE, gh=gh)
    assert len(rows) == 3 and rows[-1].endswith("s: t2 -- b")
    assert any("Accept: application/vnd.github.raw+json" in a for c in calls for a in c)


def test_main_prints_entries_oldest_first_without_walking_annotations(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[datetime] = []

    def fake(repo: str, since: datetime):
        seen.append(since)
        return ["row one", "row two"], "2026-10-01T00:00:00Z"

    monkeypatch.setattr(read_ops_log, "_now", lambda: NOW)
    monkeypatch.setattr(read_ops_log, "read_file", fake)
    monkeypatch.setattr(
        read_ops_log, "collect", lambda *a, **k: pytest.fail("the annotation walk must not run")
    )
    assert read_ops_log.main(["--days", "7"]) == 0
    out = capsys.readouterr().out
    assert "2 entries in the last 7 days" in out and out.index("row one") < out.index("row two")
    assert "NOTE" not in out  # the file starts before the window: complete
    assert seen == [datetime(2026, 11, 1, 12, 0, tzinfo=UTC)]  # pinned now minus 7 days


def test_a_window_before_the_files_first_entry_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(read_ops_log, "_now", lambda: NOW)
    monkeypatch.setattr(read_ops_log, "read_file", lambda r, s: (["row"], "2026-11-07T00:36:01Z"))
    assert read_ops_log.main(["--days", "7"]) == 0
    out = capsys.readouterr().out
    assert "NOTE: the file begins at 2026-11-07T00:36:01Z" in out and "--from-annotations" in out
    # a window that starts after the first entry is complete: no note
    assert read_ops_log.main(["--days", "1"]) == 0  # since = 2026-11-07T12:00Z, after 00:36
    assert "NOTE" not in capsys.readouterr().out


def test_an_empty_file_says_it_has_no_entry_yet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(read_ops_log, "_now", lambda: NOW)
    monkeypatch.setattr(read_ops_log, "read_file", lambda r, s: ([], None))
    assert read_ops_log.main([]) == 0
    assert "NOTE: the file begins at no entry yet" in capsys.readouterr().out


def test_main_falls_back_to_the_walk_when_the_file_does_not_exist_yet(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(read_ops_log, "_now", lambda: NOW)
    monkeypatch.setattr(read_ops_log, "read_file", lambda r, s: None)
    monkeypatch.setattr(read_ops_log, "emitting_workflows", lambda: ["a.yml"])
    monkeypatch.setattr(
        read_ops_log, "collect", lambda *a, **k: [("2026-11-05 08:00", "a.yml", "walked")]
    )
    assert read_ops_log.main([]) == 0
    out = capsys.readouterr().out
    assert "no ops-log file yet, falling back to the annotation walk" in out and "walked" in out


def test_main_fails_closed_when_the_file_is_unreadable_and_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(repo: str, since: datetime):
        raise read_ops_log.OpsLogError("read failed: HTTP 403")

    monkeypatch.setattr(read_ops_log, "_now", lambda: NOW)
    monkeypatch.setattr(read_ops_log, "read_file", boom)
    monkeypatch.setattr(
        read_ops_log, "collect", lambda *a, **k: pytest.fail("no silent fallback on an error")
    )
    assert read_ops_log.main([]) == 2
    assert "FAILED, cannot read the ops-log file" in capsys.readouterr().err


def test_from_annotations_forces_the_walk(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(read_ops_log, "_now", lambda: NOW)
    monkeypatch.setattr(
        read_ops_log, "read_file", lambda *a, **k: pytest.fail("the file must not be read")
    )
    monkeypatch.setattr(read_ops_log, "emitting_workflows", lambda: ["a.yml"])
    monkeypatch.setattr(read_ops_log, "collect", lambda *a, **k: [])
    assert read_ops_log.main(["--from-annotations"]) == 0
    assert "no ops-alert entries" in capsys.readouterr().out
