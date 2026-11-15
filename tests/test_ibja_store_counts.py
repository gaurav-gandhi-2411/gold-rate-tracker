"""scripts/ibja_store_counts.py: aggregates only, and the zero-cells rule fails closed."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ibja_store_counts as isc


def _df(**cols) -> pd.DataFrame:
    return pd.DataFrame({"date": ["2026-11-09", "2026-11-10"], **cols})


def test_clean_store_passes_and_counts_nulls_per_column(tmp_path, capsys):
    p = tmp_path / "s.parquet"
    _df(am_916=[100.0, 101.0], pm_916=[102.0, float("nan")]).to_parquet(p)
    assert isc.main(["--store", str(p), "--date", "2026-11-10"]) == 0
    out = capsys.readouterr().out
    assert "zero_cells=0" in out and "null_cells pm_916=1" in out
    assert "{'pm_916': 'null'}" in out


def test_zero_negative_and_inf_cells_fail_the_rule(tmp_path, capsys):
    p = tmp_path / "s.parquet"
    _df(am_916=[0.0, 101.0], pm_916=[-5.0, float("inf")]).to_parquet(p)
    assert isc.main(["--store", str(p)]) == 1
    assert "zero_cells=3" in capsys.readouterr().out


def test_no_rate_value_is_ever_printed(tmp_path, capsys):
    p = tmp_path / "s.parquet"
    _df(am_916=[12345.67, 7654.32], pm_916=[1.0, 2.0]).to_parquet(p)
    isc.main(["--store", str(p), "--date", "2026-11-09"])
    out = capsys.readouterr().out
    assert "12345" not in out and "7654" not in out


def test_missing_day_and_unreadable_store(tmp_path, capsys):
    p = tmp_path / "s.parquet"
    _df(pm_916=[1.0, 2.0]).to_parquet(p)
    isc.main(["--store", str(p), "--date", "2030-01-01"])
    assert "row absent" in capsys.readouterr().out
    assert isc.main(["--store", str(tmp_path / "nope.parquet")]) == 2  # never a silent pass


def test_workflow_runs_after_decrypt_and_is_read_only():
    wf = (
        Path(__file__).resolve().parent.parent / ".github/workflows/ibja-store-counts.yml"
    ).read_text(encoding="utf-8")
    assert wf.index("decrypt-data") < wf.index("scripts/ibja_store_counts.py")
    assert "contents: read" in wf and "contents: write" not in wf and "upload-artifact" not in wf
