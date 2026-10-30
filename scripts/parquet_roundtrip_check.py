"""Prove the installed pyarrow reads, writes and re-reads every parquet file the pipeline keeps.

    python scripts/parquet_roundtrip_check.py data/ibja_rates.parquet ...

For each file: read it, write it to a temporary file with the installed pandas/pyarrow, read that
back, and require the two frames to be identical (values, dtypes, index, column order) and the two
arrow schemas to match (field names and types; file metadata such as `created_by` is ignored).

check-price runs this right after decrypting and before any step writes a parquet, so a library
bump that cannot round-trip the history stops the run before it can re-encrypt a damaged file
(pyarrow 25 -> 26, brief 2026-10-29). The decrypted files are real data and CI logs are public:
the output names files, shapes and which columns differ, never a value.

A file that does not exist (e.g. decrypt failed; that step reports itself) is listed as absent
and is not a failure. Exit 1 if any present file does not round-trip.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def _frames_identical(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    try:
        pd.testing.assert_frame_equal(a, b, check_exact=True)
    except AssertionError:
        return False
    return True


def _differing_columns(a: pd.DataFrame, b: pd.DataFrame) -> list[str]:
    cols = [str(c) for c in a.columns if c not in b.columns or not a[c].equals(b[c])]
    if list(a.columns) != list(b.columns):
        cols.append("<column order>")
    if not a.index.equals(b.index):
        cols.append("<index>")
    return cols


def check(path: Path, tmp_dir: Path) -> tuple[bool, str]:
    if not path.exists():
        return True, f"{path}: absent, not checked"
    before = pd.read_parquet(path)
    out = tmp_dir / path.name
    before.to_parquet(out)
    after = pd.read_parquet(out)
    shape = f"{before.shape[0]} rows x {before.shape[1]} cols"
    schema_ok = pq.read_schema(path).remove_metadata().equals(pq.read_schema(out).remove_metadata())
    frames_ok = _frames_identical(before, after)
    if frames_ok and schema_ok:
        return True, f"{path}: {shape}, identical after write and re-read"
    what = [] if schema_ok else ["arrow schema"]
    if not frames_ok:
        what += _differing_columns(before, after) or ["<dtypes or metadata>"]
    return False, f"{path}: {shape}, DIFFERS in {', '.join(what)}"


def main(argv: list[str]) -> int:
    print(f"pyarrow {pa.__version__}, pandas {pd.__version__}")
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        for arg in argv:
            try:
                good, line = check(Path(arg), Path(tmp))
            except Exception as exc:
                # The class name only: an arrow conversion error can quote a data value, and this
                # log is public.
                good, line = False, f"{arg}: FAILED with {type(exc).__name__}"
            ok &= good
            print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
