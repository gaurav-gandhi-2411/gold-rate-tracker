"""Tests for scripts/check_test_registry_complete.py (AJ2, audit 2026-09-11/12)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "check_test_registry_complete.py"
)
_spec = importlib.util.spec_from_file_location("check_test_registry_complete", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["check_test_registry_complete"] = mod
_spec.loader.exec_module(mod)


def _make_repo(tmp_path: Path, test_files: list[str], workflow_text: str) -> Path:
    for rel in test_files:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// test file\n", encoding="utf-8")
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    (workflows_dir / "lint.yml").write_text(workflow_text, encoding="utf-8")
    return tmp_path


def test_referenced_file_is_not_flagged(tmp_path: Path) -> None:
    repo = _make_repo(
        tmp_path,
        ["tests/test_foo.js"],
        "run: node --test tests/test_foo.js\n",
    )
    files = mod.find_all_js_test_files(repo)
    text = mod.read_all_workflow_text(repo / ".github" / "workflows")
    unreferenced = mod.find_unreferenced_test_files(files, text, {})
    assert unreferenced == []


def test_unreferenced_file_is_flagged(tmp_path: Path) -> None:
    repo = _make_repo(
        tmp_path,
        ["tests/test_foo.js", "tests/test_orphan.js"],
        "run: node --test tests/test_foo.js\n",
    )
    files = mod.find_all_js_test_files(repo)
    text = mod.read_all_workflow_text(repo / ".github" / "workflows")
    unreferenced = mod.find_unreferenced_test_files(files, text, {})
    assert [p.as_posix() for p in unreferenced] == ["tests/test_orphan.js"]


def test_known_exclusion_is_not_flagged_even_if_unreferenced(tmp_path: Path) -> None:
    repo = _make_repo(
        tmp_path,
        ["tests/test_foo.js", "tests/test_excluded.js"],
        "run: node --test tests/test_foo.js\n",
    )
    files = mod.find_all_js_test_files(repo)
    text = mod.read_all_workflow_text(repo / ".github" / "workflows")
    unreferenced = mod.find_unreferenced_test_files(
        files, text, {"tests/test_excluded.js": "documented reason"}
    )
    assert unreferenced == []


def test_bare_filename_reference_counts_for_working_directory_scoped_steps(tmp_path: Path) -> None:
    # scraper-canary.yml uses `working-directory: scraper` + a bare filename
    # (`node --test test_scrape.js`), not the repo-root-relative path --
    # this must still count as referenced.
    repo = _make_repo(
        tmp_path,
        ["scraper/test_scrape.js"],
        "working-directory: scraper\nrun: node --test test_scrape.js\n",
    )
    files = mod.find_all_js_test_files(repo)
    text = mod.read_all_workflow_text(repo / ".github" / "workflows")
    unreferenced = mod.find_unreferenced_test_files(files, text, {})
    assert unreferenced == []


def test_sweeps_every_naming_convention_in_use() -> None:
    # rule 85b: a sweep pattern must cover every shape, not just the first
    # example found. This repo genuinely uses all four.
    assert set(mod.JS_TEST_FILENAME_PATTERNS) == {
        "test_*.js",
        "test_*.mjs",
        "*.test.js",
        "*.test.mjs",
    }


def test_node_modules_and_git_dirs_are_excluded(tmp_path: Path) -> None:
    repo = _make_repo(
        tmp_path,
        ["node_modules/somepkg/test_helper.js", "tests/test_real.js"],
        "run: node --test tests/test_real.js\n",
    )
    files = mod.find_all_js_test_files(repo)
    assert Path("tests/test_real.js") in files
    assert not any("node_modules" in p.as_posix() for p in files)


def test_no_workflow_files_raises_rather_than_passing_vacuously(tmp_path: Path) -> None:
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    with pytest.raises(mod.RegistryCheckError, match=r"no \.yml files"):
        mod.read_all_workflow_text(tmp_path / ".github" / "workflows")


def test_missing_workflows_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(mod.RegistryCheckError):
        mod.read_all_workflow_text(tmp_path / ".github" / "workflows")


def test_real_repo_has_no_orphaned_js_test_files() -> None:
    # Integration-style: run against THIS repo's real state. Updated by this
    # same PR (fix/wire-registry-check-into-lint, AJ2b) once
    # test_tier_degradation_visible.js and test_vol_regime.js were added to
    # lint.yml's explicit list -- was pinned to that 2-file gap set in the
    # prior PR (feat/test-registry-completeness-check) before the fix
    # landed. If this starts failing again, a new orphaned test file has
    # appeared -- investigate before updating this assertion.
    repo_root = Path(__file__).resolve().parent.parent
    files = mod.find_all_js_test_files(repo_root)
    text = mod.read_all_workflow_text(repo_root / ".github" / "workflows")
    unreferenced = mod.find_unreferenced_test_files(files, text, mod.KNOWN_EXCLUSIONS)
    assert unreferenced == [], (
        f"Newly orphaned JS test file(s): {[p.as_posix() for p in unreferenced]}"
    )
