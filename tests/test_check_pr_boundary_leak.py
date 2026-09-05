"""Tests for scripts/check_pr_boundary_leak.py (X2/Z1, audit 2026-09-05)."""

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
# check_foreign_commits -- Z1's replacement for the ancestry check
# ---------------------------------------------------------------------------


def test_foreign_commits_no_match_passes():
    """This PR's commits share no patch-id with any other open PR's commits."""

    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "view"] and args[3] == "100":
            return _proc(returncode=0, stdout=json.dumps({"headRefOid": "head100"}))
        if args[:2] == ["git", "fetch"]:
            return _proc(returncode=0)
        if args[:2] == ["git", "log"] and "head100" in args[2]:
            return _proc(returncode=0, stdout="commitA\n")
        if args[:2] == ["git", "log"] and "head200" in args[2]:
            return _proc(returncode=0, stdout="commitB\n")
        if args[:2] == ["git", "show"] and args[2] == "commitA":
            return _proc(returncode=0, stdout="diff for commitA")
        if args[:2] == ["git", "show"] and args[2] == "commitB":
            return _proc(returncode=0, stdout="diff for commitB")
        if args[:2] == ["git", "patch-id"]:
            # distinguish by which diff was piped in via kwargs["input"]
            inp = kwargs.get("input", "")
            if "commitA" in inp:
                return _proc(returncode=0, stdout="pidA fullshaA\n")
            return _proc(returncode=0, stdout="pidB fullshaB\n")
        if args[:2] == ["gh", "pr"] and "list" in args:
            return _proc(
                returncode=0,
                stdout=json.dumps(
                    [{"number": 200, "baseRefName": "master", "headRefOid": "head200"}]
                ),
            )
        raise AssertionError(f"unexpected call: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        errors = mod.check_foreign_commits(100, "owner/repo", "master")
    assert errors == []


def test_foreign_commits_shared_patch_id_fails():
    """The exact leak case: this PR's own commit shares patch content with a
    commit on another currently open PR -- must fail regardless of any label."""

    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "view"] and args[3] == "100":
            return _proc(returncode=0, stdout=json.dumps({"headRefOid": "head100"}))
        if args[:2] == ["git", "fetch"]:
            return _proc(returncode=0)
        if args[:2] == ["git", "log"]:
            return _proc(returncode=0, stdout="leakedcommit\n")
        if args[:2] == ["git", "show"]:
            return _proc(returncode=0, stdout="identical diff content")
        if args[:2] == ["git", "patch-id"]:
            # same patch-id regardless of which commit -- simulates identical content
            return _proc(returncode=0, stdout="sharedpid abcdefabcdef\n")
        if args[:2] == ["gh", "pr"] and "list" in args:
            return _proc(
                returncode=0,
                stdout=json.dumps(
                    [{"number": 406, "baseRefName": "master", "headRefOid": "head406"}]
                ),
            )
        raise AssertionError(f"unexpected call: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        errors = mod.check_foreign_commits(100, "owner/repo", "master")
    assert len(errors) == 1
    assert "#406" in errors[0]


def test_foreign_commits_no_unique_commits_passes():
    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "view"]:
            return _proc(returncode=0, stdout=json.dumps({"headRefOid": "head100"}))
        if args[:2] == ["git", "fetch"]:
            return _proc(returncode=0)
        if args[:2] == ["git", "log"]:
            return _proc(returncode=0, stdout="")
        raise AssertionError(f"unexpected call: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        errors = mod.check_foreign_commits(100, "owner/repo", "master")
    assert errors == []


def test_foreign_commits_skips_self_in_other_pr_list():
    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "view"]:
            return _proc(returncode=0, stdout=json.dumps({"headRefOid": "head100"}))
        if args[:2] == ["git", "fetch"]:
            return _proc(returncode=0)
        if args[:2] == ["git", "log"]:
            return _proc(returncode=0, stdout="commitA\n")
        if args[:2] == ["git", "show"]:
            return _proc(returncode=0, stdout="diff")
        if args[:2] == ["git", "patch-id"]:
            return _proc(returncode=0, stdout="pid sha\n")
        if args[:2] == ["gh", "pr"] and "list" in args:
            # only "other" PR in the list is this same PR -- must not compare against self
            return _proc(
                returncode=0,
                stdout=json.dumps(
                    [{"number": 100, "baseRefName": "master", "headRefOid": "head100"}]
                ),
            )
        raise AssertionError(f"unexpected call: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        errors = mod.check_foreign_commits(100, "owner/repo", "master")
    assert errors == []


def test_foreign_commits_empty_patch_id_not_an_error():
    """A commit with no patch content of its own (e.g. an empty merge commit)
    must be skipped, not crash the check."""

    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "view"]:
            return _proc(returncode=0, stdout=json.dumps({"headRefOid": "head100"}))
        if args[:2] == ["git", "fetch"]:
            return _proc(returncode=0)
        if args[:2] == ["git", "log"]:
            return _proc(returncode=0, stdout="emptymerge\n")
        if args[:2] == ["git", "show"]:
            return _proc(returncode=0, stdout="")
        if args[:2] == ["git", "patch-id"]:
            return _proc(returncode=0, stdout="")  # empty -- no patch content
        raise AssertionError(f"unexpected call: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        errors = mod.check_foreign_commits(100, "owner/repo", "master")
    assert errors == []


# ---------------------------------------------------------------------------
# check_file_level_residue
# ---------------------------------------------------------------------------


def test_file_level_residue_all_files_explained_passes():
    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "view"]:
            return _proc(returncode=0, stdout=json.dumps({"headRefOid": "head100"}))
        if args[:3] == ["gh", "pr", "diff"]:
            return _proc(returncode=0, stdout="a.py\nb.py\n")
        if args[:2] == ["git", "fetch"]:
            return _proc(returncode=0)
        if args[:2] == ["git", "log"]:
            return _proc(returncode=0, stdout="commitA\n")
        if args[:2] == ["git", "show"]:
            return _proc(returncode=0, stdout="a.py\nb.py\n")
        raise AssertionError(f"unexpected call: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        errors = mod.check_file_level_residue(100, "owner/repo", "master")
    assert errors == []


def test_file_level_residue_unexplained_file_fails():
    """A file in the full diff that no unique commit's own diff explains --
    e.g. hidden inside a merge commit's combined-diff view."""

    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "view"]:
            return _proc(returncode=0, stdout=json.dumps({"headRefOid": "head100"}))
        if args[:3] == ["gh", "pr", "diff"]:
            return _proc(returncode=0, stdout="a.py\nsneaked_in.py\n")
        if args[:2] == ["git", "fetch"]:
            return _proc(returncode=0)
        if args[:2] == ["git", "log"]:
            return _proc(returncode=0, stdout="commitA\n")
        if args[:2] == ["git", "show"]:
            return _proc(returncode=0, stdout="a.py\n")  # does not mention sneaked_in.py
        raise AssertionError(f"unexpected call: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        errors = mod.check_file_level_residue(100, "owner/repo", "master")
    assert len(errors) == 1
    assert "sneaked_in.py" in errors[0]


def test_file_level_residue_no_commits_but_files_changed_fails():
    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "view"]:
            return _proc(returncode=0, stdout=json.dumps({"headRefOid": "head100"}))
        if args[:3] == ["gh", "pr", "diff"]:
            return _proc(returncode=0, stdout="a.py\n")
        if args[:2] == ["git", "fetch"]:
            return _proc(returncode=0)
        if args[:2] == ["git", "log"]:
            return _proc(returncode=0, stdout="")
        raise AssertionError(f"unexpected call: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        errors = mod.check_file_level_residue(100, "owner/repo", "master")
    assert len(errors) == 1


def test_file_level_residue_no_commits_no_files_passes():
    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "pr", "view"]:
            return _proc(returncode=0, stdout=json.dumps({"headRefOid": "head100"}))
        if args[:3] == ["gh", "pr", "diff"]:
            return _proc(returncode=0, stdout="")
        if args[:2] == ["git", "fetch"]:
            return _proc(returncode=0)
        if args[:2] == ["git", "log"]:
            return _proc(returncode=0, stdout="")
        raise AssertionError(f"unexpected call: {args}")

    with patch("subprocess.run", side_effect=fake_run):
        errors = mod.check_file_level_residue(100, "owner/repo", "master")
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
