"""Tests for scripts/prepare_direction_eval_publish.py (published direction
numbers must never go backwards to an older computation)."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "prepare_direction_eval_publish.py"
_spec = importlib.util.spec_from_file_location("prepare_direction_eval_publish", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules["prepare_direction_eval_publish"] = mod
_spec.loader.exec_module(mod)

BASE = {
    "run_sha": "b" * 40,
    "run_is_on_master": True,
    "newer_evaluator_commits": [],
    "master_source_sha": "a" * 40,
    "master_source_is_descendant_of_run": False,
    "run_generated_at": "2026-09-23T12:00:00+00:00",
    "master_generated_at": "2026-09-23T11:00:00+00:00",
}


class TestDecidePublish:
    def test_newest_run_publishes(self) -> None:
        assert mod.decide_publish(**BASE).publish is True

    def test_refuses_when_newer_evaluator_commit_on_master(self) -> None:
        # The 2026-09-23 incident: the older-code run must stand down.
        d = mod.decide_publish(**BASE | {"newer_evaluator_commits": ["c" * 40]})
        assert d.publish is False

    def test_refuses_when_master_numbers_from_newer_code(self) -> None:
        d = mod.decide_publish(**BASE | {"master_source_is_descendant_of_run": True})
        assert d.publish is False

    def test_refuses_same_code_later_run(self) -> None:
        d = mod.decide_publish(
            **BASE
            | {"master_source_sha": "b" * 40, "master_generated_at": "2026-09-23T13:00:00+00:00"}
        )
        assert d.publish is False

    def test_same_code_newer_run_publishes(self) -> None:
        assert mod.decide_publish(**BASE | {"master_source_sha": "b" * 40}).publish is True

    def test_legacy_master_without_source_uses_time(self) -> None:
        assert mod.decide_publish(**BASE | {"master_source_sha": None}).publish is True
        later = BASE | {
            "master_source_sha": None,
            "master_generated_at": "2026-09-24T00:00:00+00:00",
        }
        assert mod.decide_publish(**later).publish is False

    def test_refuses_run_not_on_master(self) -> None:
        assert mod.decide_publish(**BASE | {"run_is_on_master": False}).publish is False


# ---------------------------------------------------------------------------
# Integration: real git repos, the incident replayed
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    env = os.environ | {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def _write_outputs(repo: Path, gen: str, src: str | None) -> None:
    (repo / "data").mkdir(exist_ok=True)
    (repo / "data" / "direction_baseline.json").write_text(
        json.dumps({"generated_at_utc": gen, "source_sha": src}), encoding="utf-8"
    )
    hist = repo / "data" / "direction_eval_history.jsonl"
    old = hist.read_text(encoding="utf-8") if hist.exists() else ""
    hist.write_text(old + json.dumps({"generated_at_utc": gen}) + "\n", encoding="utf-8")


def _run(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT)], cwd=cwd, capture_output=True, text=True, check=False
    )


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "master")
    (seed / "ml" / "direction").mkdir(parents=True)
    (seed / "ml" / "direction" / "evaluate.py").write_text("v1\n", encoding="utf-8")
    _write_outputs(seed, "2026-09-23T10:00:00+00:00", None)
    _git(seed, "add", ".")
    _git(seed, "commit", "-q", "-m", "v1")
    bare = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(seed), str(bare))
    return bare


def _clone(origin: Path, dest: Path) -> Path:
    _git(origin.parent, "clone", "-q", str(origin), str(dest))
    return dest


def test_older_code_run_stands_down_newer_publishes_without_conflict(origin: Path) -> None:
    root = origin.parent
    # Run A checks out v1 (old evaluator).
    run_a = _clone(origin, root / "run_a")
    sha_a = _git(run_a, "rev-parse", "HEAD")
    # Meanwhile the evaluator fix (v2) lands on master.
    dev = _clone(origin, root / "dev")
    (dev / "ml" / "direction" / "evaluate.py").write_text("v2\n", encoding="utf-8")
    _git(dev, "commit", "-q", "-am", "v2 evaluator")
    _git(dev, "push", "-q", "origin", "master")
    sha_b = _git(dev, "rev-parse", "HEAD")

    # A finishes with old-code numbers: must NOT publish.
    _write_outputs(run_a, "2026-09-23T11:00:00+00:00", sha_a)
    res_a = _run(run_a)
    assert res_a.returncode == 0, res_a.stderr
    assert res_a.stdout.strip() == "publish=false"
    assert "newer evaluator" in res_a.stderr

    # B (v2) finishes: publishes, on top of master, history appended.
    run_b = _clone(origin, root / "run_b")
    assert _git(run_b, "rev-parse", "HEAD") == sha_b
    _write_outputs(run_b, "2026-09-23T11:30:00+00:00", sha_b)
    res_b = _run(run_b)
    assert res_b.stdout.strip() == "publish=true", res_b.stderr
    hist = (run_b / "data" / "direction_eval_history.jsonl").read_text(encoding="utf-8")
    lines = [json.loads(x)["generated_at_utc"] for x in hist.splitlines()]
    assert lines == ["2026-09-23T10:00:00+00:00", "2026-09-23T11:30:00+00:00"]
    assert _git(run_b, "rev-parse", "HEAD") == _git(run_b, "rev-parse", "origin/master")


def test_refuses_when_history_tail_is_not_this_run(origin: Path) -> None:
    run = _clone(origin, origin.parent / "run")
    (run / "data" / "direction_baseline.json").write_text(
        json.dumps({"generated_at_utc": "2026-09-23T12:00:00+00:00"}), encoding="utf-8"
    )
    res = _run(run)
    assert res.returncode != 0
