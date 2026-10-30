"""The required lint job tests what production runs: it installs the lock and proves every pin."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_installed_matches_lock as cim


def _fake_installed(monkeypatch, have: dict[str, str]) -> None:
    monkeypatch.setattr(cim.prv, "installed", lambda name: have.get(name))


def test_exact_pins_pass_and_a_cpu_label_is_only_a_note(monkeypatch):
    _fake_installed(monkeypatch, {"pandas": "3.0.6", "torch": "2.13.0+cpu"})
    problems, notes = cim.mismatches({"pandas": "3.0.6", "torch": "2.13.0"})
    assert problems == []
    assert notes == ["torch==2.13.0+cpu (lock pins 2.13.0; local label only)"]


def test_a_newer_or_missing_package_is_a_mismatch(monkeypatch):
    _fake_installed(monkeypatch, {"pandas": "3.1.0", "torch": "2.15.0+cpu"})
    problems, _ = cim.mismatches({"pandas": "3.0.6", "torch": "2.13.0", "pyarrow": "25.0.1"})
    assert problems == [
        "pandas==3.1.0 (lock pins 3.0.6)",
        "pyarrow: NOT INSTALLED (lock pins 25.0.1)",
        "torch==2.15.0+cpu (lock pins 2.13.0)",
    ]


def test_main_exit_codes(tmp_path, monkeypatch, capsys):
    lock = tmp_path / "x.lock"
    lock.write_text("pandas==3.0.6\n    # via x\n", encoding="utf-8")
    _fake_installed(monkeypatch, {"pandas": "3.0.6"})
    assert cim.main(["--lock", str(lock)]) == 0
    assert "1 of 1 lock pins installed exactly" in capsys.readouterr().out
    _fake_installed(monkeypatch, {"pandas": "3.1.0"})
    assert cim.main(["--lock", str(lock)]) == 1
    assert cim.main(["--lock", str(tmp_path / "missing.lock")]) == 2
    empty = tmp_path / "empty.lock"
    empty.write_text("# nothing\n", encoding="utf-8")
    assert cim.main(["--lock", str(empty)]) == 2


# --- the wiring --------------------------------------------------------------------------------
def _jobs() -> dict:
    wf = yaml.safe_load((ROOT / ".github/workflows/lint.yml").read_text(encoding="utf-8"))
    return wf["jobs"]


def _production_install() -> str:
    wf = yaml.safe_load((ROOT / ".github/workflows/check-price.yml").read_text(encoding="utf-8"))
    step = next(
        s for s in wf["jobs"]["check"]["steps"] if s.get("name") == "Install ML dependencies"
    )
    return step["run"]


def _python(steps: list[dict]) -> list[str]:
    return [str(s["with"]["python-version"]) for s in steps if "setup-python" in str(s.get("uses"))]


def test_required_lint_installs_the_lock_exactly_as_check_price_does():
    steps = _jobs()["lint"]["steps"]
    install = next(s for s in steps if s.get("name") == "Install ML dependencies")
    assert install["run"] == _production_install()
    # the same Python as production (check-price: 3.12) for everything after pre-commit
    assert _python(steps)[-1] == "3.12"
    i = steps.index(install)
    assert steps[i + 1]["name"] == "Show installed versions"
    proof = steps[i + 2]
    assert proof["name"] == "Installed versions equal the lock"
    assert proof["run"].strip() == "python scripts/check_installed_matches_lock.py"
    assert "continue-on-error" not in proof
    assert all("requirements.txt" not in str(s.get("run", "")) for s in steps)


def test_check_price_runs_on_the_python_the_lock_is_compiled_for():
    header = (ROOT / "ml/requirements-inference.lock").read_text(encoding="utf-8").splitlines()[1]
    assert "--python-version 3.12" in header
    wf = yaml.safe_load((ROOT / ".github/workflows/check-price.yml").read_text(encoding="utf-8"))
    assert _python(wf["jobs"]["check"]["steps"]) == ["3.12"]


def test_the_newest_versions_job_is_an_early_warning_that_cannot_block():
    job = _jobs()["newest-allowed-versions"]
    runs = " ".join(str(s.get("run", "")) for s in job["steps"])
    assert "pip install -q --upgrade -r ml/requirements.txt" in runs
    assert "pytest" in runs and '-m "not integration"' in runs
    # not a required context: the check name GitHub reports (the job's `name`, else its key) is not
    # one of the required contexts the repo's skippable-check guard tracks
    import check_required_checks_skippable as skippable

    reported = job.get("name", "newest-allowed-versions")
    assert reported == "newest-allowed-versions"
    assert reported not in skippable.REQUIRED
    assert "continue-on-error" not in job  # it reports red when it fails; it just never blocks


def test_a_lock_line_the_parser_would_skip_is_cannot_check(tmp_path, monkeypatch, capsys):
    lock = tmp_path / "x.lock"
    lock.write_text(
        "# header\npandas==3.0.6\n    # via x\ntorch[cpu]==2.13.0\nfoo @ https://x/foo.whl\n",
        encoding="utf-8",
    )
    _fake_installed(monkeypatch, {"pandas": "3.0.6"})
    assert cim.unparsed_lines(lock) == ["torch[cpu]==2.13.0", "foo @ https://x/foo.whl"]
    assert cim.main(["--lock", str(lock)]) == 2
    assert "not a plain name==version pin: torch[cpu]==2.13.0" in capsys.readouterr().out


def test_the_real_lock_has_no_line_the_check_would_skip():
    assert cim.unparsed_lines(ROOT / "ml/requirements-inference.lock") == []
