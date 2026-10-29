"""scripts/print_runtime_versions.py: CI logs show the version of every runtime dependency."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import print_runtime_versions as prv


def test_names_come_from_requirements_ignoring_comments_and_specifiers(tmp_path):
    req = tmp_path / "r.txt"
    req.write_text("# header\npandas>=3.0.6\n\nscikit-learn>=1.9.1  # why\n", encoding="utf-8")
    assert prv.requirement_names(req) == ["pandas", "scikit-learn"]


def test_every_real_requirement_is_reported():
    names = prv.requirement_names(ROOT / "ml/requirements.txt")
    for key in ("cryptography", "pyarrow", "pandas", "numpy", "scikit-learn"):
        assert key in names


def test_lock_pins_keep_local_versions_and_canonical_names(tmp_path):
    lock = tmp_path / "x.lock"
    lock.write_text("torch==2.13.0+cpu\n    # via x\nScikit_Learn==1.9.1\n", encoding="utf-8")
    assert prv.lock_pins(lock) == {"torch": "2.13.0+cpu", "scikit-learn": "1.9.1"}


def test_report_marks_a_difference_a_missing_package_and_a_package_not_in_the_lock(monkeypatch):
    have = {"torch": "2.13.0+cpu", "pandas": "3.0.6"}
    monkeypatch.setattr(prv, "installed", lambda name: have.get(name))
    rows = prv.report(["torch", "pandas", "nope"], {"torch": "2.13.0", "pandas": "3.0.6"})
    assert rows[0].startswith("python ")
    assert rows[1] == "torch==2.13.0+cpu  (lock pins 2.13.0: DIFFERS)"
    assert rows[2] == "pandas==3.0.6"
    assert rows[3] == "nope: NOT INSTALLED  (not in lock)"


def test_main_always_exits_zero(tmp_path, capsys):
    req = tmp_path / "r.txt"
    req.write_text("surely-not-installed-pkg>=1\n", encoding="utf-8")
    assert prv.main(["--requirements", str(req)]) == 0
    assert "surely-not-installed-pkg: NOT INSTALLED" in capsys.readouterr().out


def _steps(workflow: str, job: str) -> list[dict]:
    wf = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8"))
    return wf["jobs"][job]["steps"]


def _index(steps: list[dict], name: str) -> int:
    return next(i for i, s in enumerate(steps) if s.get("name") == name)


def test_check_price_logs_versions_against_the_lock_right_after_installing():
    steps = _steps("check-price.yml", "check")
    i = _index(steps, "Show installed versions")
    assert i == _index(steps, "Install ML dependencies") + 1
    assert "--lock ml/requirements-inference.lock" in steps[i]["run"]


def test_lint_logs_versions_against_the_lock_right_after_installing():
    steps = _steps("lint.yml", "lint")
    i = _index(steps, "Show installed versions")
    assert i == _index(steps, "Install ML dependencies") + 1
    assert "--lock ml/requirements-inference.lock" in steps[i]["run"]
