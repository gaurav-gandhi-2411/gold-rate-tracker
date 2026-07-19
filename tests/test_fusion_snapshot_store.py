"""Tests for ml.fusion_snapshot_store — idempotent PIT append behavior."""

from __future__ import annotations

from ml.fusion_snapshot_store import append_snapshot_rows, load_snapshots

_ROW_A = {
    "capture_utc": "2026-07-19T12:00:00Z",
    "as_of_date": "2026-07-19",
    "schema_version": 1,
    "source": "ibja",
    "city": None,
    "rate_22k": 13135.0,
    "observed_at": "2026-07-18T11:30:00Z",
    "attribution": "IBJA-calibrated estimate",
}
_ROW_B = {
    "capture_utc": "2026-07-19T12:00:00Z",
    "as_of_date": "2026-07-19",
    "schema_version": 1,
    "source": "kalyan",
    "city": "Bangalore",
    "rate_22k": 13135.0,
    "observed_at": "2026-07-19T10:30:00Z",
    "attribution": "Kalyan Jewellers — BANGALORE board rate",
}


def test_load_snapshots_empty_when_no_file(tmp_path):
    df = load_snapshots(tmp_path / "nonexistent.parquet")
    assert df.empty
    assert "source" in df.columns


def test_append_and_load_roundtrip(tmp_path):
    store = tmp_path / "fusion_snapshots.parquet"
    n = append_snapshot_rows([_ROW_A, _ROW_B], store)
    assert n == 2
    df = load_snapshots(store)
    assert len(df) == 2
    assert set(df["source"]) == {"ibja", "kalyan"}


def test_append_empty_rows_is_noop(tmp_path):
    store = tmp_path / "fusion_snapshots.parquet"
    n = append_snapshot_rows([], store)
    assert n == 0
    assert not store.exists()


def test_duplicate_capture_source_city_is_skipped(tmp_path):
    store = tmp_path / "fusion_snapshots.parquet"
    append_snapshot_rows([_ROW_A], store)
    n_second = append_snapshot_rows([_ROW_A], store)  # exact same (source, city, capture_utc)
    assert n_second == 0
    assert len(load_snapshots(store)) == 1


def test_same_source_different_capture_utc_is_appended(tmp_path):
    store = tmp_path / "fusion_snapshots.parquet"
    append_snapshot_rows([_ROW_A], store)
    later = dict(_ROW_A, capture_utc="2026-07-19T18:00:00Z")
    n = append_snapshot_rows([later], store)
    assert n == 1
    assert len(load_snapshots(store)) == 2


def test_same_capture_different_city_both_kept(tmp_path):
    # kalyan/Bangalore and kalyan/Chennai at the same capture_utc are distinct rows.
    store = tmp_path / "fusion_snapshots.parquet"
    chennai_row = dict(_ROW_B, city="Chennai")
    n = append_snapshot_rows([_ROW_B, chennai_row], store)
    assert n == 2
    df = load_snapshots(store)
    assert set(df["city"]) == {"Bangalore", "Chennai"}
