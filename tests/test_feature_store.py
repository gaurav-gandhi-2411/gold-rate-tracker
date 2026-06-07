from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

from ml.feature_store import SCHEMA_VERSION, append_snapshot, load_snapshots

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MACRO_FLOATS = [
    "gold_usd",
    "usd_inr",
    "us_10y_yield",
    "dxy",
    "sensex",
    "vix",
    "crude_wti",
    "tips",
]

_ASOF_DATE_COLS = [f"{col}_asof_date" for col in _MACRO_FLOATS]

_PRICE_FLOATS = ["ibja_pm_916", "ibja_am_916", "tanishq_22k"]

_ALL_COLUMNS = (
    [
        "capture_utc",
        "as_of_date",
        "schema_version",
        "source",
        "partial",
    ]
    + _MACRO_FLOATS
    + _ASOF_DATE_COLS
    + _PRICE_FLOATS
    + [
        "dow",
        "dom",
        "month",
        "is_festival_window",
        "festival_name",
        "days_to_next_festival",
        "duty_change_active",
        "days_since_last_duty_change",
    ]
)


def _make_snapshot(as_of_date: str, **overrides: object) -> dict:
    """Build a fully valid snapshot dict with sensible defaults. Use overrides to vary any field."""
    base: dict = {
        "capture_utc": "2026-06-07T04:00:00Z",
        "as_of_date": as_of_date,
        "schema_version": SCHEMA_VERSION,
        "source": "live_pit",
        "partial": False,
        # macro floats
        "gold_usd": 3200.0,
        "usd_inr": 83.5,
        "us_10y_yield": 4.25,
        "dxy": 104.1,
        "sensex": 72000.0,
        "vix": 14.5,
        "crude_wti": 78.0,
        "tips": 2.1,
        # per-series asof dates
        "gold_usd_asof_date": "2026-06-06",
        "usd_inr_asof_date": "2026-06-06",
        "us_10y_yield_asof_date": "2026-06-06",
        "dxy_asof_date": "2026-06-06",
        "sensex_asof_date": "2026-06-06",
        "vix_asof_date": "2026-06-06",
        "crude_wti_asof_date": "2026-06-06",
        "tips_asof_date": "2026-06-06",
        # prices
        "ibja_pm_916": 74500.0,
        "ibja_am_916": 74400.0,
        "tanishq_22k": 71000.0,
        # calendar
        "dow": 5,
        "dom": 7,
        "month": 6,
        "is_festival_window": False,
        "festival_name": None,
        "days_to_next_festival": 45,
        # duty
        "duty_change_active": False,
        "days_since_last_duty_change": 120,
    }
    base.update(overrides)
    return base


def _store_path(tmp_path: Path) -> Path:
    """Canonical store path scoped to the tmp directory."""
    p = tmp_path / "feature_store" / "snapshots.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# TestIdempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_same_date_second_capture_is_noop(self, tmp_path: Path) -> None:
        path = _store_path(tmp_path)
        snap = _make_snapshot("2026-06-07")

        append_snapshot(snap, path)
        append_snapshot(snap, path)

        df = load_snapshots(path)
        assert len(df) == 1

    def test_different_dates_both_written(self, tmp_path: Path) -> None:
        path = _store_path(tmp_path)

        append_snapshot(_make_snapshot("2026-06-06"), path)
        append_snapshot(_make_snapshot("2026-06-07"), path)

        df = load_snapshots(path)
        assert len(df) == 2
        dates = set(df["as_of_date"].tolist())
        assert dates == {"2026-06-06", "2026-06-07"}

    def test_second_write_with_different_values_does_not_mutate(self, tmp_path: Path) -> None:
        path = _store_path(tmp_path)

        append_snapshot(_make_snapshot("2026-06-07", gold_usd=3200.0), path)
        append_snapshot(_make_snapshot("2026-06-07", gold_usd=9999.0), path)

        df = load_snapshots(path)
        row = df[df["as_of_date"] == "2026-06-07"].iloc[0]
        assert row["gold_usd"] == 3200.0


# ---------------------------------------------------------------------------
# TestImmutability
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_prior_rows_unchanged_after_new_date_capture(self, tmp_path: Path) -> None:
        path = _store_path(tmp_path)

        append_snapshot(_make_snapshot("2026-06-06", gold_usd=3100.0), path)
        first_row = load_snapshots(path).iloc[0].to_dict()

        append_snapshot(_make_snapshot("2026-06-07", gold_usd=3200.0), path)

        df = load_snapshots(path)
        reloaded_row = df[df["as_of_date"] == "2026-06-06"].iloc[0].to_dict()

        for col, original_val in first_row.items():
            reloaded_val = reloaded_row[col]
            # NaN != NaN by definition, so check both sides
            if isinstance(original_val, float) and math.isnan(original_val):
                assert isinstance(reloaded_val, float) and math.isnan(reloaded_val), (
                    f"Column '{col}': expected NaN, got {reloaded_val!r}"
                )
            else:
                assert reloaded_val == original_val, (
                    f"Column '{col}': expected {original_val!r}, got {reloaded_val!r}"
                )

    def test_immutability_across_multiple_appends(self, tmp_path: Path) -> None:
        path = _store_path(tmp_path)
        dates = [f"2026-06-0{d}" for d in range(1, 6)]
        written_rows: list[dict] = []

        for date in dates:
            snap = _make_snapshot(date, gold_usd=float(3000 + int(date[-1])))
            append_snapshot(snap, path)
            written_rows.append(snap.copy())

            df = load_snapshots(path)
            # All previously written rows must still be value-identical.
            for prev in written_rows:
                stored = df[df["as_of_date"] == prev["as_of_date"]].iloc[0]
                assert stored["gold_usd"] == prev["gold_usd"], (
                    f"Mutation detected on '{prev['as_of_date']}' after appending '{date}'"
                )

        df = load_snapshots(path)
        assert len(df) == 5


# ---------------------------------------------------------------------------
# TestSchema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_all_required_columns_present(self, tmp_path: Path) -> None:
        path = _store_path(tmp_path)
        append_snapshot(_make_snapshot("2026-06-07"), path)

        df = load_snapshots(path)
        for col in _ALL_COLUMNS:
            assert col in df.columns, f"Missing expected column: '{col}'"

    def test_schema_version_matches_constant(self, tmp_path: Path) -> None:
        path = _store_path(tmp_path)
        append_snapshot(_make_snapshot("2026-06-07", schema_version=SCHEMA_VERSION), path)

        df = load_snapshots(path)
        assert int(df.iloc[0]["schema_version"]) == SCHEMA_VERSION

    def test_source_field_live_pit_by_default(self, tmp_path: Path) -> None:
        path = _store_path(tmp_path)
        append_snapshot(_make_snapshot("2026-06-07", source="live_pit"), path)

        df = load_snapshots(path)
        assert df.iloc[0]["source"] == "live_pit"


# ---------------------------------------------------------------------------
# TestPartialFlag
# ---------------------------------------------------------------------------


class TestPartialFlag:
    def test_partial_flag_true_when_macro_missing(self, tmp_path: Path) -> None:
        path = _store_path(tmp_path)
        null_macros: dict = {col: None for col in _MACRO_FLOATS}
        append_snapshot(_make_snapshot("2026-06-07", partial=True, **null_macros), path)

        df = load_snapshots(path)
        row = df.iloc[0]
        assert row["partial"] is True or row["partial"] == True  # noqa: E712 — parquet may return numpy bool
        for col in _MACRO_FLOATS:
            assert pd.isna(row[col]), f"Expected NaN for macro column '{col}', got {row[col]!r}"

    def test_partial_flag_false_when_macro_present(self, tmp_path: Path) -> None:
        path = _store_path(tmp_path)
        append_snapshot(_make_snapshot("2026-06-07", partial=False, gold_usd=3200.0), path)

        df = load_snapshots(path)
        row = df.iloc[0]
        assert row["partial"] is False or row["partial"] == False  # noqa: E712
        assert row["gold_usd"] == 3200.0
