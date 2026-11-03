"""Prove the installed pyarrow reads, writes and re-reads every parquet file the pipeline keeps.

    python scripts/parquet_roundtrip_check.py data/ibja_rates.parquet ...

For each file: read it (frame A), write A with the installed pandas/pyarrow (file 1), read file 1
back (frame B), write B again (file 2). Two things must hold:

- the data survives: A and B are identical (values, dtypes, index, column order);
- the installed writer is stable on its own output: files 1 and 2 have the same arrow schema.

The arrow schema of the ORIGINAL file is not compared with file 1. A file's stored schema can
legitimately differ from a fresh rewrite while the data is identical (it was written by an older
library version or from different in-memory dtypes, e.g. int64 vs double for an all-null-then-filled
column). Comparing them stopped check-price on 2026-10-30 with nothing wrong (run 41045036359,
reverted in #3353). That difference is printed as a
note -- field names and arrow types only -- and never fails the run.

check-price runs this right after decrypting and before any step writes a parquet, so a library
bump that cannot round-trip the history is caught before it can re-encrypt a damaged file
(pyarrow 25 -> 26, brief 2026-10-29). The decrypted files are real data and CI logs are public:
the output names files, shapes, columns and arrow types, never a value.

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


def schema_changes(a: pa.Schema, b: pa.Schema) -> list[str]:
    """Field-level differences, as `name: type_a -> type_b` (types and names only, no values)."""
    a, b = a.remove_metadata(), b.remove_metadata()
    if a.equals(b):
        return []
    out = []
    for name in dict.fromkeys(a.names + b.names):
        ta = str(a.field(name).type) if name in a.names else "absent"
        tb = str(b.field(name).type) if name in b.names else "absent"
        if ta != tb:
            out.append(f"{name}: {ta} -> {tb}")
    if a.names != b.names and not out:
        out.append("<field order>")
    return out or ["<nullability or field metadata>"]


def check(path: Path, tmp_dir: Path) -> tuple[bool, str]:
    if not path.exists():
        return True, f"{path}: absent, not checked"
    before = pd.read_parquet(path)
    first = tmp_dir / f"{path.stem}.1.parquet"
    before.to_parquet(first)
    after = pd.read_parquet(first)
    second = tmp_dir / f"{path.stem}.2.parquet"
    after.to_parquet(second)
    shape = f"{before.shape[0]} rows x {before.shape[1]} cols"

    frames_ok = _frames_identical(before, after)
    unstable = schema_changes(pq.read_schema(first), pq.read_schema(second))
    note = schema_changes(pq.read_schema(path), pq.read_schema(first))
    note_text = f" (note: this writer's schema differs from the file's: {'; '.join(note)})"
    suffix = note_text if note else ""
    if frames_ok and not unstable:
        return True, f"{path}: {shape}, identical after write and re-read{suffix}"
    what = [] if frames_ok else (_differing_columns(before, after) or ["<dtypes or metadata>"])
    if unstable:
        what.append(f"rewrite schema ({'; '.join(unstable)})")
    return False, f"{path}: {shape}, DIFFERS in {', '.join(what)}{suffix}"


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
