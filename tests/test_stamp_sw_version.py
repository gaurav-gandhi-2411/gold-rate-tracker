"""Tests for scripts/stamp_sw_version.py.

Fixtures copy the REAL repo's service-worker.js + every SHELL_FILES entry into an
isolated tmp_path tree (never mutating the actual checkout), so test (a) below exercises
the actual precached set this repo ships, not a synthetic stand-in.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "stamp_sw_version.py"
REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("stamp_sw_version", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["stamp_sw_version"] = mod
_spec.loader.exec_module(mod)


def _copy_real_shell_tree(dest: Path) -> Path:
    """Copy service-worker.js and every real SHELL_FILES entry into an isolated root."""
    dest.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "service-worker.js", dest / "service-worker.js")
    for f in mod.shell_files(REPO_ROOT):
        src = REPO_ROOT / f
        d = dest / f
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, d)
    return dest


# --- (a) one-byte change in EVERY shell file changes the version -----------------------


def test_one_byte_change_in_every_shell_file_changes_version(tmp_path: Path) -> None:
    baseline_dir = _copy_real_shell_tree(tmp_path / "baseline")
    baseline_version = mod.compute_version(baseline_dir)
    files = mod.shell_files(REPO_ROOT)
    assert len(files) >= 10  # sanity: real repo currently precaches well over this

    for f in files:
        case_dir = _copy_real_shell_tree(tmp_path / f"case-{f.replace('/', '_')}")
        target = case_dir / f
        data = bytearray(target.read_bytes())
        data[0] = (data[0] + 1) % 256  # single-byte flip, wraps safely for any byte value
        target.write_bytes(bytes(data))
        version = mod.compute_version(case_dir)
        assert version != baseline_version, f"one-byte change in {f!r} did not change VERSION"


# --- (b) a non-shell file change does NOT change the version ---------------------------


def test_non_shell_file_change_does_not_affect_version(tmp_path: Path) -> None:
    case_dir = _copy_real_shell_tree(tmp_path / "case")
    baseline_version = mod.compute_version(case_dir)

    (case_dir / "data").mkdir()
    (case_dir / "data" / "prices.json").write_text('{"22k": 1}', encoding="utf-8")
    assert mod.compute_version(case_dir) == baseline_version

    (case_dir / "data" / "prices.json").write_text('{"22k": 999999}', encoding="utf-8")
    assert mod.compute_version(case_dir) == baseline_version


# --- (c) deterministic across runs; explicitly NOT line-ending-stable ------------------


def test_deterministic_across_runs(tmp_path: Path) -> None:
    case_dir = _copy_real_shell_tree(tmp_path / "case")
    assert mod.compute_version(case_dir) == mod.compute_version(case_dir)


def test_not_line_ending_stable(tmp_path: Path) -> None:
    # Documented, intentional property (see module docstring): compute_version hashes
    # exact bytes, so a line-ending-only edit to a shell file changes VERSION, because it
    # changes the bytes an installed client would actually fetch.
    case_dir = _copy_real_shell_tree(tmp_path / "case")
    baseline_version = mod.compute_version(case_dir)

    target = case_dir / "app.js"  # confirmed CRLF in the real repo
    original = target.read_bytes()
    assert b"\r\n" in original
    flipped = original.replace(b"\r\n", b"\n")
    assert flipped != original  # sanity: the flip actually changed bytes
    target.write_bytes(flipped)

    assert mod.compute_version(case_dir) != baseline_version


# --- (d) a missing shell file fails closed, non-zero exit ------------------------------


def test_compute_version_raises_on_missing_shell_file(tmp_path: Path) -> None:
    case_dir = _copy_real_shell_tree(tmp_path / "case")
    files = mod.shell_files(REPO_ROOT)
    (case_dir / files[0]).unlink()
    with pytest.raises(mod.StampError, match="do not exist"):
        mod.compute_version(case_dir)


def test_cli_missing_shell_file_is_nonzero_exit(tmp_path: Path) -> None:
    case_dir = _copy_real_shell_tree(tmp_path / "case")
    files = mod.shell_files(REPO_ROOT)
    (case_dir / files[0]).unlink()
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--repo-root", str(case_dir), "--check"],
        capture_output=True,
    )
    assert result.returncode != 0
    assert b"FAIL" in result.stderr


# --- (e) stamp() replaces exactly one VERSION line; rest is byte-identical -------------


def test_stamp_replaces_only_version_line_rest_byte_identical(tmp_path: Path) -> None:
    case_dir = _copy_real_shell_tree(tmp_path / "case")
    original = (case_dir / "service-worker.js").read_bytes()
    version = mod.compute_version(case_dir)

    mod.stamp(case_dir, version)
    updated = (case_dir / "service-worker.js").read_bytes()

    expected, count = mod._VERSION_LINE.subn(f'const VERSION = "{version}";'.encode(), original)
    assert count == 1  # exactly one VERSION line existed to replace
    assert updated == expected  # nothing outside that substring moved


def test_stamp_preserves_crlf_line_terminators(tmp_path: Path) -> None:
    case_dir = _copy_real_shell_tree(tmp_path / "case")
    original = (case_dir / "service-worker.js").read_bytes()
    assert b"\r\n" in original  # sanity: the real file is CRLF, per repo convention

    version = mod.compute_version(case_dir)
    mod.stamp(case_dir, version)
    updated = (case_dir / "service-worker.js").read_bytes()
    assert updated.count(b"\r\n") == original.count(b"\r\n")
    assert b"\n" not in updated.replace(b"\r\n", b"")  # no bare LF introduced


# --- CLI --check leaves the file untouched and prints "sh-" prefixed version -----------


def test_cli_check_mode_does_not_write(tmp_path: Path) -> None:
    case_dir = _copy_real_shell_tree(tmp_path / "case")
    original = (case_dir / "service-worker.js").read_bytes()

    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--repo-root", str(case_dir), "--check"],
        capture_output=True,
        check=True,
    )
    assert (case_dir / "service-worker.js").read_bytes() == original
    printed = result.stdout.decode().strip()
    assert printed.startswith("sh-")
    assert len(printed) == len("sh-") + 16


def test_cli_default_mode_writes(tmp_path: Path) -> None:
    case_dir = _copy_real_shell_tree(tmp_path / "case")
    original = (case_dir / "service-worker.js").read_bytes()

    subprocess.run(
        [sys.executable, str(_SCRIPT), "--repo-root", str(case_dir)],
        capture_output=True,
        check=True,
    )
    assert (case_dir / "service-worker.js").read_bytes() != original
