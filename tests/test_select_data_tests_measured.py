"""scripts/select_data_tests.py --measured: the static scan UNION the committed measurement.

The data-only lint path (docs/RUNBOOK.md, "What runs on a data-only PR") has no full run to measure,
so the last measurement is committed as tests/data_reading_tests.txt and unioned with the static scan.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_selector():
    spec = importlib.util.spec_from_file_location(
        "sel_under_test", ROOT / "scripts" / "select_data_tests.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_repo(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "ml").mkdir()
    (tmp_path / "tests/test_reads.py").write_text('P = "data/prices.json"\n', encoding="utf-8")
    (tmp_path / "tests/test_plain.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    (tmp_path / "tests/test_measured_only.py").write_text(
        "def test_y():\n    pass\n", encoding="utf-8"
    )
    return tmp_path


def test_selection_is_the_static_scan_union_the_measured_list_and_drops_missing_files(tmp_path):
    sel = _load_selector()
    root = _fake_repo(tmp_path)
    measured = root / "m.txt"
    measured.write_text(
        "# measured\n\ntests\\test_measured_only.py  # via a default path\n"
        "tests/test_gone.py\ntests/test_reads.py\n",
        encoding="utf-8",
    )
    assert sel.select(root) == ["tests/test_reads.py"]
    assert sel.select_with_measured(root, measured) == [
        "tests/test_measured_only.py",
        "tests/test_reads.py",
    ]


def test_an_unreadable_measured_list_is_an_error_never_an_empty_list(tmp_path, capsys):
    sel = _load_selector()
    root = _fake_repo(tmp_path)
    assert sel.main(["--root", str(root), "--measured", str(root / "missing.txt")]) == 2
    assert "run the full suite" in capsys.readouterr().err
    # no test files at all: also an error
    empty = tmp_path / "empty"
    (empty / "tests").mkdir(parents=True)
    (empty / "m.txt").write_text("", encoding="utf-8")
    assert sel.main(["--root", str(empty), "--measured", str(empty / "m.txt")]) == 2


def test_the_cli_prints_the_union_one_path_per_line(tmp_path, capsys):
    sel = _load_selector()
    root = _fake_repo(tmp_path)
    (root / "m.txt").write_text("tests/test_measured_only.py\n", encoding="utf-8")
    assert sel.main(["--root", str(root), "--measured", str(root / "m.txt")]) == 0
    assert capsys.readouterr().out.split() == [
        "tests/test_measured_only.py",
        "tests/test_reads.py",
    ]


def test_without_the_flag_the_cli_is_the_static_scan_alone(tmp_path, capsys):
    sel = _load_selector()
    root = _fake_repo(tmp_path)
    assert sel.main(["--root", str(root)]) == 0
    assert capsys.readouterr().out.split() == ["tests/test_reads.py"]


def test_the_committed_measurement_is_well_formed_and_lists_only_existing_files():
    sel = _load_selector()
    listing = ROOT / "tests" / "data_reading_tests.txt"
    paths = [
        ln.split("#", 1)[0].strip()
        for ln in listing.read_text(encoding="utf-8").splitlines()
        if ln.split("#", 1)[0].strip()
    ]
    assert paths, "an empty measurement would add nothing"
    assert paths == sorted(set(paths))
    assert [p for p in paths if not (ROOT / p).is_file()] == []
    assert set(sel.select(ROOT)) <= set(sel.select_with_measured(ROOT, listing))
