"""FOR_GG item 11: on a data-only PR only the tests that read data/ can fail it.

Covers the three parts: the recording plugin (scripts/data_reads_plugin.py, run for real in a
throwaway pytest session), the static scan (scripts/select_data_tests.py) and the decision
(scripts/data_test_decision.py), including that an unusable record is an error, never a pass.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import data_test_decision as dtd
import select_data_tests as sdt

PROJECT_TESTS = """
import subprocess
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


def test_reads_data():
    assert (DATA / "prices.json").read_text() == "{}"


def test_reads_nothing():
    assert 1 + 1 == 2


def test_fails_reading_data():
    assert (DATA / "prices.json").read_text() == "changed"


def test_skipped():
    import pytest
    pytest.skip("not here")


def test_git_show_of_data():
    subprocess.run(["git", "show", "HEAD:data/prices.json"], capture_output=True)
"""

# a file that never touches data/: its failure says nothing about a data-only PR
PURE_TESTS = """
def test_fails_reading_nothing():
    assert False
"""

# review finding: a value loaded once and cached is read only by the FIRST test that needs it
CACHED_TESTS = """
import functools
from pathlib import Path


@functools.lru_cache
def load():
    return (Path(__file__).resolve().parent.parent / "da" "ta" / "prices.json").read_text()


def test_first_loads():
    assert load()


def test_second_uses_the_cache_and_fails():
    assert load() == "changed"
"""

# a module-level load: the read happens while the file is imported (collected), before any test
IMPORT_TESTS = """
from pathlib import Path

VALUE = (Path(__file__).resolve().parent.parent / "da" "ta" / "prices.json").read_text()


def test_fails_on_the_imported_value():
    assert VALUE == "changed"
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "prices.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text(PROJECT_TESTS, encoding="utf-8")
    (tmp_path / "tests" / "test_pure.py").write_text(PURE_TESTS, encoding="utf-8")
    (tmp_path / "tests" / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(ROOT / 'scripts')!r})\n"
        "import data_reads_plugin\n\n"
        "def pytest_configure(config):\n"
        "    root = Path(__file__).resolve().parent.parent\n"
        "    config.pluginmanager.register(\n"
        "        data_reads_plugin.DataReads(root, root / 'reads.json'), 'data_reads_plugin')\n",
        encoding="utf-8",
    )
    return tmp_path


def _run(project: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "DATA_READS_OUT"}
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return json.loads((project / "reads.json").read_text(encoding="utf-8"))


def test_the_plugin_records_reads_and_outcomes_per_test(project: Path):
    rec = _run(project)
    t = {k.split("::")[1]: v for k, v in rec["tests"].items()}
    assert t["test_reads_data"] == {"outcome": "passed", "data_reads": ["data/prices.json"]}
    assert t["test_reads_nothing"] == {"outcome": "passed", "data_reads": []}
    assert t["test_fails_reading_data"]["outcome"] == "failed"
    assert t["test_fails_reading_data"]["data_reads"] == ["data/prices.json"]
    assert t["test_fails_reading_nothing"] == {"outcome": "failed", "data_reads": []}
    assert t["test_skipped"]["outcome"] == "skipped"
    assert t["test_git_show_of_data"]["data_reads"][0].startswith("git:")
    assert rec["version"] == 1 and rec["collect_errors"] == [] and rec["exitstatus"] == 1
    assert rec["collect_reads"] == {}


def test_only_failures_in_data_reading_tests_block(project: Path):
    rec = _run(project)
    verdict, blocking, non_blocking, n_reading = dtd.decide(rec, set())
    assert verdict == "fail" and n_reading == 3  # data, failing data, git show
    assert [b.split(" ")[0].split("::")[1] for b in blocking] == ["test_fails_reading_data"]
    assert non_blocking == ["tests/test_pure.py::test_fails_reading_nothing"]
    # a file the static scan selects blocks on any failure (reads through a child process)
    _, blocking, non_blocking, _ = dtd.decide(rec, {"tests/test_pure.py"})
    assert len(blocking) == 2 and non_blocking == []


def test_a_failure_on_a_cached_read_blocks(project: Path):
    (project / "tests" / "test_cached.py").write_text(CACHED_TESTS, encoding="utf-8")
    rec = _run(project)
    second = rec["tests"]["tests/test_cached.py::test_second_uses_the_cache_and_fails"]
    assert second == {"outcome": "failed", "data_reads": []}  # the read was the first test's
    _, blocking, non_blocking, _ = dtd.decide(rec, set())
    assert any(b.startswith("tests/test_cached.py::test_second") for b in blocking), blocking
    assert non_blocking == ["tests/test_pure.py::test_fails_reading_nothing"]


def test_a_failure_on_a_value_read_at_import_blocks(project: Path):
    (project / "tests" / "test_imported.py").write_text(IMPORT_TESTS, encoding="utf-8")
    rec = _run(project)
    assert rec["collect_reads"] == {"tests/test_imported.py": ["data/prices.json"]}
    _, blocking, _, _ = dtd.decide(rec, set())
    assert any(b.startswith("tests/test_imported.py::") for b in blocking), blocking


def test_a_collection_error_blocks(project: Path):
    (project / "tests" / "test_broken.py").write_text("import nope_not_a_module\n", "utf-8")
    rec = _run(project)
    verdict, blocking, _, _ = dtd.decide(rec, set())
    assert verdict == "fail" and any(b.startswith("collection error:") for b in blocking)


@pytest.mark.parametrize("status", [2, 3, 4, 5, None])
def test_a_run_that_stopped_early_blocks_even_with_no_failure(status):
    # review finding: an interrupted or internal-error run writes a valid but partial record
    rec = {
        "version": 1,
        "exitstatus": status,
        "tests": {"tests/test_x.py::t": {"outcome": "passed", "data_reads": ["data/a"]}},
    }
    verdict, blocking, _, _ = dtd.decide(rec, set())
    assert verdict == "fail" and blocking[0].startswith("pytest stopped early")


@pytest.mark.parametrize(
    "record",
    [{}, {"version": 1, "tests": {}}, {"version": 2, "tests": {"a": {}}}, {"tests": None}],
)
def test_an_unusable_record_is_an_error_never_a_pass(record: dict):
    with pytest.raises(ValueError):
        dtd.decide(record, set())


def test_the_cli_exit_codes(tmp_path: Path, capsys):
    rec = tmp_path / "r.json"
    assert dtd.main(["--reads", str(tmp_path / "missing.json")]) == 2
    rec.write_text("not json", encoding="utf-8")
    assert dtd.main(["--reads", str(rec)]) == 2
    ok = {
        "version": 1,
        "exitstatus": 0,
        "tests": {"tests/test_x.py::t": {"outcome": "passed", "data_reads": []}},
    }
    rec.write_text(json.dumps(ok), encoding="utf-8")
    assert dtd.main(["--reads", str(rec)]) == 0
    bad = {
        "version": 1,
        "exitstatus": 1,
        "tests": {"tests/test_x.py::t": {"outcome": "failed", "data_reads": ["data/a"]}},
    }
    rec.write_text(json.dumps(bad), encoding="utf-8")
    assert dtd.main(["--reads", str(rec)]) == 1
    assert "BLOCKING: tests/test_x.py::t" in capsys.readouterr().out


def test_the_plugin_never_raises_on_odd_audit_events(tmp_path: Path):
    import data_reads_plugin

    p = data_reads_plugin.DataReads.__new__(data_reads_plugin.DataReads)
    p.data, p.current, p.reads = str(tmp_path), "t", {}
    for args in [(), (None,), (3,), (b"\xff",), (object(),)]:
        p._hook("open", args)
    p._hook("subprocess.Popen", ())
    p._hook("subprocess.Popen", ("git", None))


def test_recording_is_on_only_when_the_lint_job_asks(pytestconfig):
    on = pytestconfig.pluginmanager.get_plugin("data_reads_plugin") is not None
    assert on == bool(os.environ.get("DATA_READS_OUT"))


def test_the_static_scan_ignores_prose_and_selects_code(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_prose.py").write_text(
        '"""Reads data/prices.json in production."""\n# data/x.json\ndef test_a():\n    pass\n',
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_join.py").write_text(
        'from pathlib import Path\nP = Path(".") / "data" / "x.json"\n', encoding="utf-8"
    )
    (tmp_path / "tests" / "test_git.py").write_text(
        'CMD = ["git", "show", "HEAD:data/x.json"]\n', encoding="utf-8"
    )
    (tmp_path / "tests" / "test_testdata.py").write_text('P = "test_data/x.json"\n', "utf-8")
    assert sdt.select(tmp_path) == ["tests/test_git.py", "tests/test_join.py"]


def test_the_static_scan_follows_a_module_constant_into_data(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "ml").mkdir()
    (tmp_path / "ml" / "store.py").write_text(
        'from pathlib import Path\nSTORE_PATH = Path(__file__).parent.parent / "data" / "s.json"\n',
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_uses.py").write_text(
        "from ml import store\n\ndef test_a():\n    store.STORE_PATH.read_text()\n", "utf-8"
    )
    assert sdt.select(tmp_path) == ["tests/test_uses.py"]


def test_the_real_scan_runs_and_selects_known_data_readers():
    chosen = set(sdt.select(ROOT))
    assert "tests/test_nextfix_p3.py" in chosen  # reads data/nextfix_p3_oos.json
    assert "tests/test_rare_bot_sync_wait.py" not in chosen  # reads only workflows
