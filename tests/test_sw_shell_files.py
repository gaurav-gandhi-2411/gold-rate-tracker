"""Tests for scripts/sw_shell_files.py -- the set lint.yml's sw-version-guard protects."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "sw_shell_files.py"
_spec = importlib.util.spec_from_file_location("sw_shell_files", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["sw_shell_files"] = mod
_spec.loader.exec_module(mod)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_real_shell_files_include_i18n_js() -> None:
    # The drift this replaces: the old hand-typed guard regex omitted i18n.js, so a PR
    # changing only i18n.js (a Hindi/English copy fix) never demanded a VERSION bump and
    # installed clients stayed on the old copy.
    files = mod.shell_files(REPO_ROOT)
    assert "i18n.js" in files
    assert {"index.html", "app.js", "style.css", "manifest.webmanifest"} <= set(files)


def test_bare_root_entry_is_not_emitted() -> None:
    # An empty line in the output would make the guard's `grep -f` match every path.
    assert "" not in mod.shell_files(REPO_ROOT)


def _repo(tmp_path: Path, sw_body: str, files: list[str]) -> Path:
    (tmp_path / "service-worker.js").write_text(sw_body, encoding="utf-8")
    for f in files:
        p = tmp_path / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    return tmp_path


def test_comments_inside_the_array_are_ignored(tmp_path: Path) -> None:
    sw = 'const SHELL_FILES = [\n  "./",\n  "./a.js",\n  // "./not-a-file.js"\n];\n'
    assert mod.shell_files(_repo(tmp_path, sw, ["a.js"])) == ["a.js"]


def test_missing_array_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(mod.ShellFilesError, match="no `const SHELL_FILES"):
        mod.shell_files(_repo(tmp_path, "const other = [];\n", []))


def test_empty_array_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(mod.ShellFilesError, match="zero files"):
        mod.shell_files(_repo(tmp_path, 'const SHELL_FILES = [\n  "./",\n];\n', []))


def test_listed_but_missing_file_fails_closed(tmp_path: Path) -> None:
    sw = 'const SHELL_FILES = [\n  "./gone.js",\n];\n'
    with pytest.raises(mod.ShellFilesError, match="do not exist"):
        mod.shell_files(_repo(tmp_path, sw, []))


def test_cli_output_is_lf_only_on_every_os() -> None:
    # Windows text-mode stdout would emit \r\n, and the guard's `grep -x` never matches
    # a line ending in \r -- found by running the guard's real shell lines locally.
    import subprocess

    out = subprocess.run(
        [sys.executable, str(_SCRIPT)], capture_output=True, check=True, cwd=REPO_ROOT
    ).stdout
    assert b"\r" not in out
    assert b"i18n.js\n" in out
