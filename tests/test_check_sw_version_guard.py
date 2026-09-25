"""Tests for scripts/check_sw_version_guard.py.

evaluate() is pure (no git, no filesystem) so every branch of the legacy-vs-actions rule
is covered directly. A handful of end-to-end tests exercise main() against a real,
throwaway git repo to check the `git diff` wiring itself, not just the decision table.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_sw_version_guard.py"
REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("check_sw_version_guard", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["check_sw_version_guard"] = mod
_spec.loader.exec_module(mod)


# --- legacy mode: identical behaviour to the bash it replaces ---------------------------


def test_legacy_precached_unchanged_always_ok() -> None:
    result = mod.evaluate(
        "legacy", precached_changed=False, sw_changed=False, version_line_changed=False
    )
    assert result.ok


def test_legacy_precached_changed_sw_not_touched_fails() -> None:
    result = mod.evaluate(
        "legacy", precached_changed=True, sw_changed=False, version_line_changed=False
    )
    assert not result.ok
    assert "wasn't touched" in result.message


def test_legacy_shell_change_without_version_bump_fails() -> None:
    # This is the exact case the task asks to prove: a PR changes a precached shell file,
    # touches service-worker.js too (e.g. to add a comment) but never bumps VERSION.
    result = mod.evaluate(
        "legacy", precached_changed=True, sw_changed=True, version_line_changed=False
    )
    assert not result.ok
    assert "VERSION const wasn't" in result.message


def test_legacy_shell_change_with_version_bump_passes() -> None:
    result = mod.evaluate(
        "legacy", precached_changed=True, sw_changed=True, version_line_changed=True
    )
    assert result.ok


# --- actions mode: the new rule -- VERSION must never be hand-edited -------------------


def test_actions_mode_hand_edited_version_fails() -> None:
    result = mod.evaluate(
        "actions", precached_changed=False, sw_changed=True, version_line_changed=True
    )
    assert not result.ok
    assert "do not hand-edit" in result.message


def test_actions_mode_shell_change_without_touching_version_passes() -> None:
    # In actions mode a PR is free to change shell files without touching VERSION at all --
    # the build stamps it later.
    result = mod.evaluate(
        "actions", precached_changed=True, sw_changed=False, version_line_changed=False
    )
    assert result.ok


def test_actions_mode_sw_changed_for_other_reasons_passes() -> None:
    result = mod.evaluate(
        "actions", precached_changed=False, sw_changed=True, version_line_changed=False
    )
    assert result.ok


# --- unknown mode fails closed (rule 98a) -----------------------------------------------


@pytest.mark.parametrize("mode", ["", "legacyy", "ACTIONS", "true", "1"])
def test_unknown_mode_fails_closed(mode: str) -> None:
    result = mod.evaluate(
        mode, precached_changed=False, sw_changed=False, version_line_changed=False
    )
    assert not result.ok
    assert "unknown PAGES_BUILD_MODE" in result.message


# --- end-to-end against a real throwaway git repo (checks the git-diff wiring) ----------


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(  # noqa: E731 -- tiny local test helper
        ["git", *a], cwd=repo, capture_output=True, check=True, text=True
    )
    run("init", "-q")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    (repo / "service-worker.js").write_text(
        'const VERSION = "v1";\nconst SHELL_FILES = [\n  "./",\n  "./index.html",\n];\n',
        encoding="utf-8",
    )
    (repo / "index.html").write_text("<html>v1</html>\n", encoding="utf-8")
    (repo / "data").mkdir()
    (repo / "data" / "prices.json").write_text('{"22k": 1}\n', encoding="utf-8")
    run("add", "-A")
    run("commit", "-q", "-m", "base")
    run("branch", "-q", "-m", "master")
    return repo


def _run_guard(
    repo: Path, base: str = "master", mode: str | None = None
) -> subprocess.CompletedProcess[str]:
    args = [sys.executable, str(_SCRIPT), "--repo-root", str(repo), "--base", base]
    if mode is not None:
        args += ["--mode", mode]
    return subprocess.run(args, cwd=repo, capture_output=True, text=True)


def test_e2e_legacy_shell_change_without_version_bump_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "checkout", "-q", "-b", "pr"], cwd=repo, capture_output=True, check=True)
    (repo / "index.html").write_text("<html>v2</html>\n", encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-q", "-am", "shell change"], cwd=repo, capture_output=True, check=True
    )

    result = _run_guard(repo, mode="legacy")
    assert result.returncode != 0
    assert "wasn't touched" in result.stderr


def test_e2e_legacy_shell_change_with_version_bump_passes(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "checkout", "-q", "-b", "pr"], cwd=repo, capture_output=True, check=True)
    (repo / "index.html").write_text("<html>v2</html>\n", encoding="utf-8")
    (repo / "service-worker.js").write_text(
        'const VERSION = "v2";\nconst SHELL_FILES = [\n  "./",\n  "./index.html",\n];\n',
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "commit", "-q", "-am", "shell change + bump"],
        cwd=repo,
        capture_output=True,
        check=True,
    )

    result = _run_guard(repo, mode="legacy")
    assert result.returncode == 0


def test_e2e_actions_mode_hand_edited_version_fails(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "checkout", "-q", "-b", "pr"], cwd=repo, capture_output=True, check=True)
    (repo / "service-worker.js").write_text(
        'const VERSION = "v2";\nconst SHELL_FILES = [\n  "./",\n  "./index.html",\n];\n',
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "commit", "-q", "-am", "hand bump"], cwd=repo, capture_output=True, check=True
    )

    result = _run_guard(repo, mode="actions")
    assert result.returncode != 0
    assert "do not hand-edit" in result.stderr


def test_e2e_non_shell_file_change_never_requires_version_bump(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    subprocess.run(["git", "checkout", "-q", "-b", "pr"], cwd=repo, capture_output=True, check=True)
    (repo / "data" / "prices.json").write_text('{"22k": 2}\n', encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-q", "-am", "data only"], cwd=repo, capture_output=True, check=True
    )

    for mode in ("legacy", "actions"):
        result = _run_guard(repo, mode=mode)
        assert result.returncode == 0, f"mode={mode}: {result.stderr}"
