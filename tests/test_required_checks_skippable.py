"""Tests for scripts/check_required_checks_skippable.py (2026-10-09 lesson: a required check that a PR can
skip blocks that PR; a status post that discards its errors is a silent failure)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "rcs", ROOT / "scripts" / "check_required_checks_skippable.py"
)
rcs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rcs)

BOT_OK = "gh workflow run lint.yml --ref x\n"

GOOD = """name: X
on:
  pull_request:
  workflow_dispatch:
jobs:
  chk:
    runs-on: ubuntu-latest
    steps:
      - run: gh api "repos/r/statuses/$SHA" -f state=success -f context=chk
"""


def _use(tmp_path, monkeypatch, text, name="lint.yml"):
    (tmp_path / name).write_text(text, encoding="utf-8")
    monkeypatch.setattr(rcs, "WORKFLOWS", tmp_path)


def test_real_repo_required_checks_are_not_skippable():
    bot = rcs.BOT_ACTION.read_text(encoding="utf-8")
    for ctx in rcs.REQUIRED:
        assert rcs.problems_for(ctx, bot) == [], ctx


def test_a_good_workflow_passes(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch, GOOD)
    assert rcs.problems_for("chk", BOT_OK) == []


def test_path_filter_is_flagged(tmp_path, monkeypatch):
    _use(
        tmp_path,
        monkeypatch,
        GOOD.replace("  pull_request:\n", "  pull_request:\n    paths: ['a/**']\n"),
    )
    assert any("path-filtered" in p for p in rcs.problems_for("chk", BOT_OK))


def test_job_level_if_is_flagged(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch, GOOD.replace("  chk:\n", "  chk:\n    if: github.actor != 'bot'\n"))
    assert any("job-level" in p for p in rcs.problems_for("chk", BOT_OK))


def test_no_dispatch_or_no_forwarding_or_no_bot_dispatch_is_flagged(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch, GOOD.replace("  workflow_dispatch:\n", ""))
    assert any("workflow_dispatch" in p for p in rcs.problems_for("chk", BOT_OK))
    _use(tmp_path, monkeypatch, GOOD.replace("statuses/", "other/"))
    assert any("never forwards" in p for p in rcs.problems_for("chk", BOT_OK))
    _use(tmp_path, monkeypatch, GOOD)
    assert any("bot-pr-sync does not dispatch" in p for p in rcs.problems_for("chk", ""))


def test_status_post_that_hides_errors_is_flagged(tmp_path, monkeypatch):
    for tail in (" >/dev/null", " || true"):
        _use(tmp_path, monkeypatch, GOOD.rstrip("\n") + tail + "\n")
        assert any("discarded" in p for p in rcs.problems_for("chk", BOT_OK)), tail


def test_unknown_check_name_is_flagged(tmp_path, monkeypatch):
    _use(tmp_path, monkeypatch, GOOD)
    assert rcs.problems_for("nope", BOT_OK)
