"""scripts/parquet_roundtrip_check.py and its check-price step (pyarrow 25 -> 26, brief 2026-10-29)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import parquet_roundtrip_check as prc


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-10-27", "2026-10-28", "2026-10-29"]),
            "pm_916": [11850.0, None, 11912.5],
            "source": ["ibja", "ibja", "ibja"],
        }
    )


def test_a_real_parquet_round_trips_identically(tmp_path):
    src = tmp_path / "src" / "ibja_rates.parquet"
    src.parent.mkdir()
    _frame().to_parquet(src, index=False)
    out = tmp_path / "out"
    out.mkdir()
    good, line = prc.check(src, out)
    assert good and "3 rows x 3 cols, identical" in line


def test_a_file_written_by_pyarrow_directly_passes_with_a_schema_note(tmp_path):
    # 2026-10-30 regression: the first version compared the original file's arrow schema with a
    # pandas rewrite and stopped check-price on two files whose data round-tripped identically.
    src = tmp_path / "fusion_snapshots.parquet"
    table = pa.table(
        {
            "ts": pa.array([1793347036000, 1793350636000], type=pa.timestamp("ms", tz="UTC")),
            "source": pa.array(["kalyan", "tanishq"], type=pa.string()),
            "rate": pa.array([13090.0, 13245.0], type=pa.float64()),
        }
    )
    pq.write_table(table, src)
    out = tmp_path / "out"
    out.mkdir()
    good, line = prc.check(src, out)
    assert good, line
    assert "identical after write and re-read" in line
    # the v1 trigger: pandas rewrites a pyarrow `string` column as `large_string`; reported, types only
    assert (
        "note: this writer's schema differs from the file's: source: string -> large_string" in line
    )
    assert "13090" not in line and "kalyan" not in line


def test_an_unstable_rewrite_schema_fails_and_names_the_field(tmp_path, monkeypatch):
    src = tmp_path / "ibja_rates.parquet"
    _frame().to_parquet(src, index=False)
    real_schema = pq.read_schema

    def drifting(path, *a, **k):  # the second rewrite comes back with a different type
        s = real_schema(path, *a, **k)
        if str(path).endswith(".2.parquet"):
            i = s.get_field_index("pm_916")
            s = s.set(i, pa.field("pm_916", pa.float32()))
        return s

    monkeypatch.setattr(prc.pq, "read_schema", drifting)
    out = tmp_path / "out"
    out.mkdir()
    good, line = prc.check(src, out)
    assert not good and "rewrite schema (pm_916: double -> float)" in line


def test_schema_changes_lists_fields_by_name_and_type():
    a = pa.schema([("x", pa.string()), ("y", pa.int64())])
    b = pa.schema([("x", pa.large_string()), ("z", pa.int64())])
    assert prc.schema_changes(a, b) == [
        "x: string -> large_string",
        "y: int64 -> absent",
        "z: absent -> int64",
    ]
    assert prc.schema_changes(a, a) == []


def test_an_absent_file_is_reported_and_not_a_failure(tmp_path):
    good, line = prc.check(tmp_path / "missing.parquet", tmp_path)
    assert good and "absent" in line


def test_a_difference_names_the_column_and_never_a_value(tmp_path, monkeypatch):
    src = tmp_path / "ibja_rates.parquet"
    _frame().to_parquet(src, index=False)
    real_read = pd.read_parquet
    calls = {"n": 0}

    def lossy_read(path, *a, **k):  # the second read (the re-read) loses precision
        calls["n"] += 1
        df = real_read(path, *a, **k)
        if calls["n"] == 2:
            df["pm_916"] = df["pm_916"].round(0)
        return df

    monkeypatch.setattr(prc.pd, "read_parquet", lossy_read)
    out = tmp_path / "out"
    out.mkdir()
    good, line = prc.check(src, out)
    assert not good and "DIFFERS in pm_916" in line
    assert "11912" not in line and "11850" not in line


def test_main_exits_one_when_any_file_differs(tmp_path, monkeypatch):
    monkeypatch.setattr(prc, "check", lambda p, t: (p.name != "bad.parquet", p.name))
    assert prc.main([str(tmp_path / "ok.parquet"), str(tmp_path / "bad.parquet")]) == 1
    assert prc.main([str(tmp_path / "ok.parquet")]) == 0


def test_an_exception_fails_the_check_and_prints_only_its_class(tmp_path, monkeypatch, capsys):
    def boom(p, t):
        raise ValueError("Could not convert '11912.5' with type str")

    monkeypatch.setattr(prc, "check", boom)
    assert prc.main([str(tmp_path / "ibja_rates.parquet")]) == 1
    out = capsys.readouterr().out
    assert "FAILED with ValueError" in out and "11912" not in out


def _step() -> tuple[list[dict], int]:
    wf = yaml.safe_load((ROOT / ".github/workflows/check-price.yml").read_text(encoding="utf-8"))
    steps = wf["jobs"]["check"]["steps"]
    i = next(
        i
        for i, s in enumerate(steps)
        if s.get("name") == "Parquet round trip with the installed pyarrow"
    )
    return steps, i


def test_the_step_runs_after_decrypt_and_before_any_other_step():
    steps, i = _step()
    assert steps[i - 1].get("id") == "decrypt"
    assert steps[i + 1]["name"] == "Fetch macro features"
    # observe first: the step cannot stop the run until a real-data run shows every file passing
    # (2026-10-30 false positive); turning that on is its own PR, before pyarrow 26 (#3322)
    assert steps[i].get("continue-on-error") is True


def test_the_step_covers_every_encrypted_and_every_tracked_parquet():
    steps, i = _step()
    listed = set(steps[i]["run"].split()[2:])
    sys.path.insert(0, str(ROOT / "scripts"))
    import data_crypt

    encrypted = {p for p in data_crypt.REGISTRY if p.endswith(".parquet")}
    tracked = set(
        subprocess.run(
            ["git", "ls-files", "data/*.parquet", "data/**/*.parquet"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    )
    assert encrypted and tracked
    assert encrypted | tracked <= listed, sorted((encrypted | tracked) - listed)
