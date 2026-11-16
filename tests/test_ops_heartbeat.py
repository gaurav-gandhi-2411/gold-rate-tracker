"""scripts/ops_heartbeat.py: the daily alive line and the 48 h silence note.

No network and no ambient clock: `now` is passed in, and the ops-log read/write are replaced by fakes.
"""

from __future__ import annotations

import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import ops_heartbeat
import ops_log

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def _file(*stamps: str) -> str:
    return "\n".join(
        json.dumps({"ts": s, "source": "x", "title": "t", "body": "b"}) for s in stamps
    )


def test_newest_entry_ignores_unparseable_lines() -> None:
    text = _file("2026-11-12T03:18:39Z", "2026-11-11T07:09:18Z") + "\nnot json\n"
    assert ops_heartbeat.newest_entry(text) == datetime(2026, 11, 12, 3, 18, 39, tzinfo=UTC)
    assert ops_heartbeat.newest_entry("") is None


def test_silent_after_48_hours_and_not_before() -> None:
    silent, note = ops_heartbeat.silence_note(datetime(2026, 11, 12, 3, 18, tzinfo=UTC), NOW)
    assert silent and "OPS LOG SILENT" in note and "104 h" in note
    quiet, note2 = ops_heartbeat.silence_note(datetime(2026, 11, 15, 12, 0, tzinfo=UTC), NOW)
    assert not quiet and "alive" in note2
    assert ops_heartbeat.silence_note(None, NOW)[0] is True


def _run(monkeypatch: pytest.MonkeyPatch, reads: list, tmp_path: Path):
    """Run with a fake reader (successive results from `reads`) and a recording writer."""
    written: list[tuple[str, str, str]] = []
    it = iter(reads)

    def fake_read(repo, gh):
        item = next(it)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(ops_log, "read_remote", fake_read)
    monkeypatch.setattr(ops_log, "record", lambda s, t, b, **kw: written.append((s, t, b)) or "")
    summary = tmp_path / "summary.md"
    env = {"GITHUB_REPOSITORY": "o/r", "GITHUB_STEP_SUMMARY": str(summary)}
    out = io.StringIO()
    rc = ops_heartbeat.run(NOW, environ=env, out=out)
    return rc, written, out.getvalue(), summary.read_text(encoding="utf-8")


def test_silent_log_is_said_plainly_and_the_heartbeat_is_confirmed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    before = ("sha1", _file("2026-11-12T03:18:39Z"))
    after = ("sha2", _file("2026-11-12T03:18:39Z", "2026-11-16T12:00:00Z"))
    rc, written, out, summary = _run(monkeypatch, [before, after], tmp_path)
    assert rc == 0
    assert written and written[0][:2] == ("heartbeat", "alive")
    assert (
        "OPS LOG SILENT" in summary and "::warning title=ops-log-heartbeat::OPS LOG SILENT" in out
    )
    assert "write confirmed" in out


def test_an_unconfirmed_write_is_a_warning_not_a_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    same = ("sha1", _file("2026-11-15T12:00:00Z"))
    rc, _, out, _ = _run(monkeypatch, [same, same], tmp_path)
    assert rc == 0 and "heartbeat write NOT confirmed" in out


def test_an_unreadable_log_is_reported_and_never_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    err = ops_log.OpsLogWriteError("read failed: HTTP 403")
    rc, written, out, summary = _run(monkeypatch, [err, err], tmp_path)
    assert rc == 0 and "OPS LOG UNREADABLE" in summary and written
    assert "NOT confirmed" in out


def test_the_digest_workflow_runs_it_once_a_day_with_write_permission() -> None:
    text = (ROOT / ".github" / "workflows" / "daily-digest.yml").read_text(encoding="utf-8")
    assert "python scripts/ops_heartbeat.py" in text
    assert "contents: write" in text and "OPS_LOG_TOKEN" in text
    # it must sit behind the same once-a-day guard as the note, and never reach ntfy
    step = text.split("python scripts/ops_heartbeat.py")[0].rsplit("- name:", 1)[1]
    assert "steps.guard.outputs.send == 'true'" in step
    assert "continue-on-error: true" in step
