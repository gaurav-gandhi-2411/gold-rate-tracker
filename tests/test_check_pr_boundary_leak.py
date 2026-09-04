"""Tests for scripts/check_pr_boundary_leak.py (X2, audit 2026-09-05)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_pr_boundary_leak.py"
_spec = importlib.util.spec_from_file_location("check_pr_boundary_leak", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["check_pr_boundary_leak"] = mod
_spec.loader.exec_module(mod)


def _proc(returncode=0, stdout="", stderr=""):
    class _Result:
        pass

    r = _Result()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


# ---------------------------------------------------------------------------
# _gh_json / _git -- fail closed on any subprocess failure
# ---------------------------------------------------------------------------


def test_gh_json_raises_on_nonzero_exit():
    with (
        patch("subprocess.run", return_value=_proc(returncode=1, stderr="not found")),
        pytest.raises(mod.BoundaryLeakError, match="failed"),
    ):
        mod._gh_json(["pr", "view", "999"])


def test_gh_json_raises_on_unparseable_output():
    with (
        patch("subprocess.run", return_value=_proc(returncode=0, stdout="not json")),
        pytest.raises(mod.BoundaryLeakError, match="unparseable"),
    ):
        mod._gh_json(["pr", "view", "999"])


def test_git_raises_on_nonzero_exit():
    with (
        patch("subprocess.run", return_value=_proc(returncode=128, stderr="bad ref")),
        pytest.raises(mod.BoundaryLeakError, match="failed"),
    ):
        mod._git(["rev-parse", "bogus"])


# ---------------------------------------------------------------------------
# check_branch_base
# ---------------------------------------------------------------------------


def test_branch_base_clean_ancestry_passes():
    """The exact non-leak case: branch's divergence point's parent IS an
    ancestor of origin/master."""
    pr_view = _proc(
        returncode=0,
        stdout=json.dumps({"headRefName": "fix/x", "headRefOid": "headsha123"}),
    )
    calls = {"n": 0}

    def fake_run(args, **kwargs):
        calls["n"] += 1
        if args[:2] == ["gh", "pr"]:
            return pr_view
        if args[:2] == ["git", "fetch"]:
            return _proc(returncode=0)
        if args[:2] == ["git", "log"]:
            return _proc(returncode=0, stdout="commit1\ncommit2\n")
        if args[:2] == ["git", "rev-parse"]:
            return _proc(returncode=0, stdout="parentsha\n")
        if args[:2] == ["git", "merge-base"]:
            return _proc(returncode=0)  # is-ancestor: true
        raise AssertionError(f"unexpected call: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        errors = mod.check_branch_base(1234, "owner/repo", "master")
    assert errors == []


def test_branch_base_leaked_ancestry_fails():
    """The exact leak case: divergence point's parent is NOT an ancestor of
    origin/master -- the branch was built on another branch."""
    pr_view = _proc(
        returncode=0,
        stdout=json.dumps({"headRefName": "fix/y", "headRefOid": "headsha456"}),
    )

    def fake_run(args, **kwargs):
        if args[:2] == ["gh", "pr"]:
            return pr_view
        if args[:2] == ["git", "fetch"]:
            return _proc(returncode=0)
        if args[:2] == ["git", "log"]:
            return _proc(returncode=0, stdout="commitA\n")
        if args[:2] == ["git", "rev-parse"]:
            return _proc(returncode=0, stdout="otherbranchparent\n")
        if args[:2] == ["git", "merge-base"]:
            return _proc(returncode=1)  # is-ancestor: false -- THE LEAK
        raise AssertionError(f"unexpected call: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        errors = mod.check_branch_base(1234, "owner/repo", "master")
    assert len(errors) == 1
    assert "NOT an ancestor" in errors[0]


def test_branch_base_no_unique_commits_passes():
    pr_view = _proc(
        returncode=0,
        stdout=json.dumps({"headRefName": "fix/z", "headRefOid": "headsha789"}),
    )

    def fake_run(args, **kwargs):
        if args[:2] == ["gh", "pr"]:
            return pr_view
        if args[:2] == ["git", "fetch"]:
            return _proc(returncode=0)
        if args[:2] == ["git", "log"]:
            return _proc(returncode=0, stdout="")  # nothing unique
        raise AssertionError(f"unexpected call: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        errors = mod.check_branch_base(1234, "owner/repo", "master")
    assert errors == []


# ---------------------------------------------------------------------------
# check_boundary_overlap
# ---------------------------------------------------------------------------


def test_boundary_overlap_detects_shared_file():
    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "diff"] and args[3] == "100":
            return _proc(returncode=0, stdout="worker-deadman/src/deadman.mjs\nREADME.md\n")
        if args[:3] == ["gh", "pr", "diff"] and args[3] == "200":
            return _proc(returncode=0, stdout="worker-deadman/src/deadman.mjs\n")
        if args[:2] == ["gh", "pr"] and "list" in args:
            return _proc(returncode=0, stdout=json.dumps([{"number": 200}]))
        raise AssertionError(f"unexpected call: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        errors = mod.check_boundary_overlap(100, "owner/repo")
    assert len(errors) == 1
    assert "#200" in errors[0]
    assert "deadman.mjs" in errors[0]


def test_boundary_overlap_no_shared_files_passes():
    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "diff"] and args[3] == "100":
            return _proc(returncode=0, stdout="README.md\n")
        if args[:3] == ["gh", "pr", "diff"] and args[3] == "200":
            return _proc(returncode=0, stdout="ml/metrics.py\n")
        if args[:2] == ["gh", "pr"] and "list" in args:
            return _proc(returncode=0, stdout=json.dumps([{"number": 200}]))
        raise AssertionError(f"unexpected call: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        errors = mod.check_boundary_overlap(100, "owner/repo")
    assert errors == []


def test_boundary_overlap_skips_self():
    """A boundary-gated PR checking itself must not flag against its own files."""

    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "diff"] and args[3] == "100":
            return _proc(returncode=0, stdout="README.md\n")
        if args[:2] == ["gh", "pr"] and "list" in args:
            return _proc(returncode=0, stdout=json.dumps([{"number": 100}]))
        raise AssertionError(f"unexpected call: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        errors = mod.check_boundary_overlap(100, "owner/repo")
    assert errors == []


def test_boundary_overlap_no_other_gated_prs_passes():
    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "diff"] and args[3] == "100":
            return _proc(returncode=0, stdout="README.md\n")
        if args[:2] == ["gh", "pr"] and "list" in args:
            return _proc(returncode=0, stdout=json.dumps([]))
        raise AssertionError(f"unexpected call: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        errors = mod.check_boundary_overlap(100, "owner/repo")
    assert errors == []


def test_boundary_overlap_fails_closed_on_diff_failure():
    """A gh call failing mid-check must raise, never be treated as 'no files'."""

    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "diff"] and args[3] == "100":
            return _proc(returncode=1, stderr="rate limited")
        raise AssertionError(f"unexpected call: {args}")

    with patch("subprocess.run", side_effect=fake_run), pytest.raises(mod.BoundaryLeakError):
        mod.check_boundary_overlap(100, "owner/repo")
