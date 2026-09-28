"""Tests for scripts/check_test_counts.py (AP5, 2026-09-21): the guard against hollow test runs."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_test_counts.py"
_spec = importlib.util.spec_from_file_location("check_test_counts", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules["check_test_counts"] = mod
_spec.loader.exec_module(mod)

_REPO = Path(__file__).resolve().parent.parent

_PY = "def test_a():\n    pass\n\n\nclass TestX:\n    def test_b(self):\n        pass\n\n    def helper(self):\n        pass\n"
_MJS = 'import test from "node:test";\ntest("one", () => {});\ntest("two", () => {});\n  it("three", () => {});\n'


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(_PY, encoding="utf-8")
    (tmp_path / "tests" / "test_y.js").write_text(_MJS, encoding="utf-8")
    (tmp_path / "worker-deadman" / "test").mkdir(parents=True)
    (tmp_path / "worker-deadman" / "test" / "w.test.mjs").write_text(_MJS, encoding="utf-8")
    return tmp_path


def _run(root: Path, *extra: str) -> int:
    sys.argv = ["check_test_counts.py", "--repo-root", str(root), *extra]
    return mod.main()


def test_counts_python_defs_including_class_methods_but_not_helpers(tmp_path: Path):
    assert mod.count_python_tests(_tree(tmp_path) / "tests" / "test_x.py") == 2


def test_counts_js_test_and_it_lines(tmp_path: Path):
    assert mod.count_js_tests(_tree(tmp_path) / "tests" / "test_y.js") == 3


def test_baseline_then_check_passes(tmp_path: Path):
    root = _tree(tmp_path)
    assert _run(root, "--update") == 0
    assert _run(root) == 0


def test_a_truncated_test_file_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """The 2026-09-21 incident shape: a test file emptied, the rest still 'passes'."""
    root = _tree(tmp_path)
    assert _run(root, "--update") == 0
    (root / "worker-deadman" / "test" / "w.test.mjs").write_text("", encoding="utf-8")
    assert _run(root) == 1
    out = capsys.readouterr().out
    assert "w.test.mjs: 0 test(s), baseline is 3" in out


def test_partial_truncation_fails(tmp_path: Path):
    root = _tree(tmp_path)
    assert _run(root, "--update") == 0
    (root / "tests" / "test_x.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    assert _run(root) == 1


def test_a_deleted_test_file_fails(tmp_path: Path):
    root = _tree(tmp_path)
    assert _run(root, "--update") == 0
    (root / "tests" / "test_y.js").unlink()
    assert _run(root) == 1


def test_a_new_unlisted_test_file_fails(tmp_path: Path):
    """An unlisted file is unguarded, so adding one must touch the baseline."""
    root = _tree(tmp_path)
    assert _run(root, "--update") == 0
    (root / "tests" / "test_new.py").write_text("def test_z():\n    pass\n", encoding="utf-8")
    assert _run(root) == 1


def test_adding_tests_passes_without_a_baseline_edit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    root = _tree(tmp_path)
    assert _run(root, "--update") == 0
    (root / "tests" / "test_x.py").write_text(
        _PY + "\n\ndef test_more():\n    pass\n", encoding="utf-8"
    )
    assert _run(root) == 0
    assert "raise it with --update" in capsys.readouterr().out


def test_lowering_is_possible_only_through_an_explicit_update(tmp_path: Path):
    root = _tree(tmp_path)
    assert _run(root, "--update") == 0
    (root / "tests" / "test_x.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    assert _run(root) == 1
    assert _run(root, "--update") == 0  # the explicit, reviewable act
    assert _run(root) == 0


def test_empty_tree_and_missing_or_malformed_baseline_fail_closed(tmp_path: Path):
    assert _run(tmp_path) == 2  # no test files at all: refuse to pass an empty sweep
    root = _tree(tmp_path)
    assert _run(root) == 2  # baseline missing
    base = root / mod.BASELINE_REL
    base.mkdir(parents=True)
    assert _run(root) == 2  # baseline directory empty
    (base / "tests").mkdir()
    (base / "tests" / "test_x.py.count").write_text("{not a number", encoding="utf-8")
    assert _run(root) == 2  # malformed entry


def test_baseline_is_one_file_per_test_file(tmp_path: Path):
    root = _tree(tmp_path)
    assert _run(root, "--update") == 0
    base = root / mod.BASELINE_REL
    assert sorted(p.relative_to(base).as_posix() for p in base.rglob("*.count")) == [
        "tests/test_x.py.count",
        "tests/test_y.js.count",
        "worker-deadman/test/w.test.mjs.count",
    ]
    assert (base / "tests" / "test_x.py.count").read_text(encoding="utf-8") == "2\n"


def test_adding_a_test_file_touches_only_its_own_baseline_file(tmp_path: Path):
    """The reason for the per-file layout: a new test file must not edit any shared baseline file."""
    root = _tree(tmp_path)
    assert _run(root, "--update") == 0
    base = root / mod.BASELINE_REL
    before = {p: p.read_bytes() for p in base.rglob("*.count")}
    (root / "tests" / "test_new.py").write_text("def test_z():\n    pass\n", encoding="utf-8")
    assert _run(root, "--update") == 0
    after = {p: p.read_bytes() for p in base.rglob("*.count")}
    assert set(after) - set(before) == {base / "tests" / "test_new.py.count"}
    assert all(after[p] == b for p, b in before.items())


def test_update_is_the_only_way_to_drop_a_deleted_file_from_the_baseline(tmp_path: Path):
    root = _tree(tmp_path)
    assert _run(root, "--update") == 0
    (root / "tests" / "test_y.js").unlink()
    assert _run(root) == 1
    assert _run(root, "--update") == 0
    assert not (root / mod.BASELINE_REL / "tests" / "test_y.js.count").exists()
    assert _run(root) == 0


def test_two_branches_adding_different_test_files_merge_without_conflict(tmp_path: Path):
    """End-to-end with real git: the 2026-09-26 merge-train conflict shape no longer conflicts."""
    root = _tree(tmp_path)
    assert _run(root, "--update") == 0

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )

    assert git("init", "-q", "-b", "main").returncode == 0
    git("add", "-A")
    assert git("commit", "-q", "-m", "base").returncode == 0
    for branch, name in (("a", "test_a_new.py"), ("b", "test_b_new.py")):
        git("checkout", "-q", "-b", branch, "main")
        (root / "tests" / name).write_text("def test_q():\n    pass\n", encoding="utf-8")
        assert _run(root, "--update") == 0
        git("add", "-A")
        assert git("commit", "-q", "-m", branch).returncode == 0
    git("checkout", "-q", "main")
    assert git("merge", "-q", "--no-edit", "a").returncode == 0
    merged = git("merge", "-q", "--no-edit", "b")
    assert merged.returncode == 0, merged.stdout + merged.stderr
    assert _run(root) == 0


def test_the_committed_baseline_matches_the_real_repo():
    """The guard must be green on the tree it ships in, and cover the real Worker tests."""
    current = mod.collect_counts(_REPO)
    baseline = mod.read_baseline(_REPO / mod.BASELINE_REL)
    failures, _ = mod.compare(current, baseline)
    assert failures == []
    assert "worker-deadman/test/pr_trigger_health.test.mjs" in baseline


def test_the_real_incident_shape_is_caught_on_a_copy_of_the_real_tree(tmp_path: Path):
    """Empty the real pr_trigger_health test on a copy of the real tests and require a failure."""
    for rel in ("tests", "worker-deadman/test", "scripts"):
        shutil.copytree(
            _REPO / rel,
            tmp_path / rel,
            ignore=shutil.ignore_patterns("__pycache__", "node_modules"),
        )
    victim = tmp_path / "worker-deadman" / "test" / "pr_trigger_health.test.mjs"
    assert victim.stat().st_size > 1000
    victim.write_text("", encoding="utf-8")
    current = mod.collect_counts(tmp_path)
    baseline = mod.read_baseline(_REPO / mod.BASELINE_REL)
    failures, _ = mod.compare(
        current,
        {
            k: v
            for k, v in baseline.items()
            if k in current or k == "worker-deadman/test/pr_trigger_health.test.mjs"
        },
    )
    assert any("pr_trigger_health.test.mjs: 0 test(s)" in f for f in failures)
