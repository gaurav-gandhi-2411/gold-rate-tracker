"""scripts/parquet_roundtrip_check.py and its check-price step (pyarrow 25 -> 26, brief 2026-10-29)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
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
    assert "continue-on-error" not in steps[i]  # a failed round trip must stop the run


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
