from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
from ml.feature_store import SCHEMA_VERSION, append_snapshot, capture_daily_snapshot, load_snapshots
from ml.feature_store_backfill import patch_missing_macro_series, run_backfill

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

_PRICE_ASOF_DATE_COLS = [f"{col}_asof_date" for col in _PRICE_FLOATS]

_ALL_COLUMNS = [
    "capture_utc",
    "as_of_date",
    "schema_version",
    "source",
    "partial",
    "n_macro_null",
    *_MACRO_FLOATS,
    *_ASOF_DATE_COLS,
    *_PRICE_FLOATS,
    *_PRICE_ASOF_DATE_COLS,
    "dow",
    "dom",
    "month",
    "is_festival_window",
    "festival_name",
    "days_to_next_festival",
    "duty_change_active",
    "days_since_last_duty_change",
]


def _make_snapshot(as_of_date: str, **overrides: object) -> dict:
    """Build a fully valid snapshot dict with sensible defaults. Use overrides to vary any field."""
    base: dict = {
        "capture_utc": "2026-06-07T04:00:00Z",
        "as_of_date": as_of_date,
        "schema_version": SCHEMA_VERSION,
        "source": "live_pit",
        "partial": False,
        "n_macro_null": 0,
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
        # price observation-date stamps (symmetric with macro asof columns)
        "ibja_pm_916_asof_date": "2026-06-06",
        "ibja_am_916_asof_date": "2026-06-06",
        "tanishq_22k_asof_date": "2026-06-06",
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


# ---------------------------------------------------------------------------
# TestCaptureStep
# ---------------------------------------------------------------------------


def _make_mock_macro_parquet(tmp_path: Path) -> Path:
    """Write a minimal macro_cache.parquet with 5 rows and all 8 series."""

    import pandas as pd

    dates = pd.date_range("2026-06-01", periods=5, freq="D", tz="UTC")
    data = {
        "gold_usd": [3200.0] * 5,
        "usd_inr": [85.0] * 5,
        "us_10y_yield": [4.5] * 5,
        "dxy": [104.0] * 5,
        "sensex": [75000.0] * 5,
        "vix": [18.0] * 5,
        "crude_wti": [72.0] * 5,
        "tips": [110.0] * 5,
    }
    df = pd.DataFrame(data, index=dates)
    out = tmp_path / "macro_cache.parquet"
    df.to_parquet(out)
    return out


def _make_mock_ibja_parquet(tmp_path: Path) -> Path:
    """Write a minimal ibja_rates.parquet with one row (correct column names from ibja.py)."""
    import pandas as pd

    row = {
        "date": "2026-06-05",
        "fetched_at": "2026-06-05T10:00:00+00:00",
        "am_999": float("nan"),
        "pm_999": float("nan"),
        "am_995": float("nan"),
        "pm_995": float("nan"),
        "am_916": 72800.0,
        "pm_916": 73000.0,
        "am_750": float("nan"),
        "pm_750": float("nan"),
        "am_585": float("nan"),
        "pm_585": float("nan"),
    }
    df = pd.DataFrame([row])
    out = tmp_path / "ibja_rates.parquet"
    df.to_parquet(out, index=False)
    return out


def _make_mock_prices_json(tmp_path: Path) -> Path:
    """Write a minimal prices.json."""
    import json

    data = [{"timestamp": "2026-06-05T10:00:00Z", "22k": 74000}]
    out = tmp_path / "prices.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    return out


def _make_mock_duty_json(tmp_path: Path, event_date: str = "2000-01-01") -> Path:
    """Write a minimal duty_events.json with one past event."""
    import json

    data = [
        {
            "date": event_date,
            "event_type": "duty_change",
            "direction": "cut",
            "magnitude_pct": None,
            "note": "Mock duty event for tests",
            "source": "test",
        }
    ]
    out = tmp_path / "duty_events.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    return out


class TestCaptureStep:
    def test_capture_writes_row_for_today(self, tmp_path: Path) -> None:
        """capture_daily_snapshot writes exactly one row to a fresh store."""
        import re

        store = _store_path(tmp_path)
        macro = _make_mock_macro_parquet(tmp_path)
        ibja = _make_mock_ibja_parquet(tmp_path)
        prices = _make_mock_prices_json(tmp_path)
        duty = _make_mock_duty_json(tmp_path)

        capture_daily_snapshot(
            store_path=store,
            macro_cache_path=macro,
            ibja_path=ibja,
            prices_path=prices,
            duty_events_path=duty,
        )

        df = load_snapshots(store)
        assert len(df) == 1
        assert re.match(r"\d{4}-\d{2}-\d{2}", df.iloc[0]["as_of_date"])

    def test_capture_is_idempotent(self, tmp_path: Path) -> None:
        """Two calls on the same IST date must yield exactly one stored row."""
        store = _store_path(tmp_path)
        macro = _make_mock_macro_parquet(tmp_path)
        ibja = _make_mock_ibja_parquet(tmp_path)
        prices = _make_mock_prices_json(tmp_path)
        duty = _make_mock_duty_json(tmp_path)

        kwargs: dict = dict(
            store_path=store,
            macro_cache_path=macro,
            ibja_path=ibja,
            prices_path=prices,
            duty_events_path=duty,
        )

        capture_daily_snapshot(**kwargs)
        capture_daily_snapshot(**kwargs)

        df = load_snapshots(store)
        assert len(df) == 1

    def test_capture_partial_flag_when_no_macro(self, tmp_path: Path) -> None:
        """partial=True when macro_cache_path points to a non-existent file."""
        store = _store_path(tmp_path)
        missing_macro = tmp_path / "nonexistent_macro.parquet"
        duty = _make_mock_duty_json(tmp_path)

        capture_daily_snapshot(
            store_path=store,
            macro_cache_path=missing_macro,
            duty_events_path=duty,
        )

        df = load_snapshots(store)
        assert len(df) == 1
        row = df.iloc[0]
        assert row["partial"] is True or row["partial"] == True  # noqa: E712

    def test_capture_partial_false_when_macro_present(self, tmp_path: Path) -> None:
        """partial=False when a valid macro parquet is present."""
        store = _store_path(tmp_path)
        macro = _make_mock_macro_parquet(tmp_path)
        duty = _make_mock_duty_json(tmp_path)

        capture_daily_snapshot(
            store_path=store,
            macro_cache_path=macro,
            duty_events_path=duty,
        )

        df = load_snapshots(store)
        assert len(df) == 1
        row = df.iloc[0]
        assert row["partial"] is False or row["partial"] == False  # noqa: E712

    def test_capture_reads_ibja_pm_916(self, tmp_path: Path) -> None:
        """ibja_pm_916 and ibja_pm_916_asof_date both reflect the IBJA row date (2026-06-05)."""
        store = _store_path(tmp_path)
        ibja = _make_mock_ibja_parquet(tmp_path)
        duty = _make_mock_duty_json(tmp_path)

        capture_daily_snapshot(
            store_path=store,
            macro_cache_path=tmp_path / "nonexistent_macro.parquet",
            ibja_path=ibja,
            duty_events_path=duty,
        )

        df = load_snapshots(store)
        row = df.iloc[0]
        assert abs(float(row["ibja_pm_916"]) - 73000.0) < 1.0
        assert row["ibja_pm_916_asof_date"] == "2026-06-05"
        assert row["ibja_am_916_asof_date"] == "2026-06-05"

    def test_capture_reads_tanishq_price(self, tmp_path: Path) -> None:
        """tanishq_22k and tanishq_22k_asof_date both reflect the prices.json entry date (IST)."""
        store = _store_path(tmp_path)
        prices = _make_mock_prices_json(tmp_path)
        duty = _make_mock_duty_json(tmp_path)

        capture_daily_snapshot(
            store_path=store,
            macro_cache_path=tmp_path / "nonexistent_macro.parquet",
            prices_path=prices,
            duty_events_path=duty,
        )

        df = load_snapshots(store)
        row = df.iloc[0]
        assert abs(float(row["tanishq_22k"]) - 74000.0) < 1.0
        # Mock timestamp "2026-06-05T10:00:00Z" → IST 2026-06-05T15:30:00 → date 2026-06-05
        assert row["tanishq_22k_asof_date"] == "2026-06-05"

    def test_tanishq_asof_uses_ist_date_not_utc(self, tmp_path: Path) -> None:
        """An evening-UTC timestamp must yield the IST calendar date, not the UTC date.

        2026-06-07T19:00:00Z = 2026-06-08T00:30:00+05:30 IST → asof must be 2026-06-08.
        String-slicing the UTC ISO date would incorrectly give 2026-06-07.
        """
        import json

        store = _store_path(tmp_path)
        evening_prices = tmp_path / "prices_evening.json"
        evening_prices.write_text(
            json.dumps([{"timestamp": "2026-06-07T19:00:00Z", "22k": 74000}]),
            encoding="utf-8",
        )
        duty = _make_mock_duty_json(tmp_path)

        capture_daily_snapshot(
            store_path=store,
            macro_cache_path=tmp_path / "nonexistent_macro.parquet",
            prices_path=evening_prices,
            duty_events_path=duty,
        )

        df = load_snapshots(store)
        row = df.iloc[0]
        # IST date is 2026-06-08, NOT 2026-06-07 (the UTC date)
        assert row["tanishq_22k_asof_date"] == "2026-06-08"

    def test_carried_ibja_asof_reflects_observation_date(self, tmp_path: Path) -> None:
        """When the latest IBJA fix predates capture, asof_date < as_of_date (carry detectable)."""
        store = _store_path(tmp_path)
        # Deliberately old IBJA row so carry is always detectable regardless of test run date
        past_ibja_row = {
            "date": "2025-01-15",
            "fetched_at": "2025-01-15T10:00:00+00:00",
            "am_916": 72800.0,
            "pm_916": 73000.0,
            "am_999": float("nan"),
            "pm_999": float("nan"),
            "am_995": float("nan"),
            "pm_995": float("nan"),
            "am_750": float("nan"),
            "pm_750": float("nan"),
            "am_585": float("nan"),
            "pm_585": float("nan"),
        }
        ibja_old = tmp_path / "ibja_old.parquet"
        pd.DataFrame([past_ibja_row]).to_parquet(ibja_old, index=False)
        duty = _make_mock_duty_json(tmp_path)

        capture_daily_snapshot(
            store_path=store,
            macro_cache_path=tmp_path / "nonexistent_macro.parquet",
            ibja_path=ibja_old,
            duty_events_path=duty,
        )

        df = load_snapshots(store)
        row = df.iloc[0]
        assert row["ibja_pm_916_asof_date"] == "2025-01-15"
        # Carry is detectable: asof older than the capture's as_of_date
        assert row["ibja_pm_916_asof_date"] < row["as_of_date"]

    def test_capture_festival_info_included(self, tmp_path: Path) -> None:
        """Festival info keys are present with correct types."""
        store = _store_path(tmp_path)
        duty = _make_mock_duty_json(tmp_path)

        capture_daily_snapshot(
            store_path=store,
            macro_cache_path=tmp_path / "nonexistent_macro.parquet",
            duty_events_path=duty,
        )

        df = load_snapshots(store)
        row = df.iloc[0]
        assert "is_festival_window" in row.index
        assert "festival_name" in row.index
        assert "days_to_next_festival" in row.index
        # is_festival_window must be bool-like
        assert row["is_festival_window"] in (True, False)
        # days_to_next_festival must be a non-negative integer
        assert int(row["days_to_next_festival"]) >= 0

    def test_capture_duty_active_old_event(self, tmp_path: Path) -> None:
        """duty_change_active=False when the only duty event is in the far past."""
        store = _store_path(tmp_path)
        duty = _make_mock_duty_json(tmp_path, event_date="2000-01-01")

        capture_daily_snapshot(
            store_path=store,
            macro_cache_path=tmp_path / "nonexistent_macro.parquet",
            duty_events_path=duty,
        )

        df = load_snapshots(store)
        row = df.iloc[0]
        assert row["duty_change_active"] is False or row["duty_change_active"] == False  # noqa: E712


# ---------------------------------------------------------------------------
# TestBackfill
# ---------------------------------------------------------------------------


def _make_backfill_ibja_parquet(
    tmp_path: Path,
    dates_and_values: list[tuple[str, float | None, float | None]],
) -> Path:
    """Create a mock ibja_rates.parquet with the given dates.

    Parameters
    ----------
    dates_and_values : list of (date_str, pm_916, am_916)
        pm_916 or am_916 may be None to write NaN.
    """
    rows = []
    for date_str, pm_916, am_916 in dates_and_values:
        rows.append(
            {
                "date": date_str,
                "fetched_at": f"{date_str}T10:00:00+00:00",
                "am_916": float("nan") if am_916 is None else float(am_916),
                "pm_916": float("nan") if pm_916 is None else float(pm_916),
            }
        )
    df = pd.DataFrame(rows)
    out = tmp_path / "ibja_rates.parquet"
    df.to_parquet(out, index=False)
    return out


def _make_backfill_macro_df(dates: list[str]) -> pd.DataFrame:
    """Return a mock macro DataFrame with a UTC DatetimeIndex and all 8 series."""
    index = pd.to_datetime(dates, utc=True)
    data = {
        "gold_usd": [3200.0] * len(dates),
        "usd_inr": [85.0] * len(dates),
        "us_10y_yield": [4.5] * len(dates),
        "dxy": [104.0] * len(dates),
        "sensex": [75000.0] * len(dates),
        "vix": [18.0] * len(dates),
        "crude_wti": [72.0] * len(dates),
        "tips": [110.0] * len(dates),
    }
    return pd.DataFrame(data, index=index)


def _make_backfill_duty_json(tmp_path: Path) -> Path:
    """Write a minimal duty_events.json for backfill tests."""
    import json

    data = [
        {
            "date": "2024-07-23",
            "event_type": "duty_change",
            "direction": "cut",
            "magnitude_pct": None,
            "note": "test",
            "source": "test",
        }
    ]
    out = tmp_path / "duty_events.json"
    out.write_text(json.dumps(data), encoding="utf-8")
    return out


class TestBackfill:
    def test_backfill_writes_backfill_yfinance_rows(self, tmp_path: Path) -> None:
        """run_backfill writes rows tagged source='backfill_yfinance' for new IBJA dates."""
        store = _store_path(tmp_path)
        ibja = _make_backfill_ibja_parquet(
            tmp_path,
            [("2025-06-01", 73000.0, 72800.0), ("2025-06-02", 73100.0, 72900.0)],
        )
        macro = _make_backfill_macro_df(["2025-06-01", "2025-06-02"])
        duty = _make_backfill_duty_json(tmp_path)

        n = run_backfill(ibja_path=ibja, store_path=store, duty_events_path=duty, macro_df=macro)

        df = load_snapshots(store)
        assert len(df) == 2
        assert n == 2
        assert (df["source"] == "backfill_yfinance").all()

    def test_backfill_does_not_overwrite_live_pit(self, tmp_path: Path) -> None:
        """run_backfill skips any as_of_date that already has a live_pit row."""
        store = _store_path(tmp_path)
        # Pre-write a live_pit row for 2025-06-01
        append_snapshot(_make_snapshot("2025-06-01", source="live_pit"), store)

        ibja = _make_backfill_ibja_parquet(tmp_path, [("2025-06-01", 73000.0, 72800.0)])
        macro = _make_backfill_macro_df(["2025-06-01"])
        duty = _make_backfill_duty_json(tmp_path)

        n = run_backfill(ibja_path=ibja, store_path=store, duty_events_path=duty, macro_df=macro)

        df = load_snapshots(store)
        assert len(df) == 1
        assert df.iloc[0]["source"] == "live_pit"
        assert n == 0

    def test_backfill_skips_existing_backfill_rows(self, tmp_path: Path) -> None:
        """run_backfill skips dates already present regardless of source."""
        store = _store_path(tmp_path)
        # Pre-write a backfill_yfinance row directly
        append_snapshot(
            _make_snapshot("2025-06-01", source="backfill_yfinance", ibja_pm_916=73000.0),
            store,
        )

        ibja = _make_backfill_ibja_parquet(tmp_path, [("2025-06-01", 73000.0, 72800.0)])
        macro = _make_backfill_macro_df(["2025-06-01"])
        duty = _make_backfill_duty_json(tmp_path)

        n = run_backfill(ibja_path=ibja, store_path=store, duty_events_path=duty, macro_df=macro)

        df = load_snapshots(store)
        assert len(df) == 1
        assert n == 0

    def test_backfill_returns_count_of_new_rows(self, tmp_path: Path) -> None:
        """run_backfill return value equals the number of new rows written."""
        store = _store_path(tmp_path)
        # Pre-populate one date as live_pit so it gets skipped
        append_snapshot(_make_snapshot("2025-06-01", source="live_pit"), store)

        ibja = _make_backfill_ibja_parquet(
            tmp_path,
            [
                ("2025-06-01", 73000.0, 72800.0),
                ("2025-06-02", 73100.0, 72900.0),
                ("2025-06-03", 73200.0, 73000.0),
            ],
        )
        macro = _make_backfill_macro_df(["2025-06-01", "2025-06-02", "2025-06-03"])
        duty = _make_backfill_duty_json(tmp_path)

        n = run_backfill(ibja_path=ibja, store_path=store, duty_events_path=duty, macro_df=macro)

        assert n == 2

    def test_backfill_partial_true_when_no_macro(self, tmp_path: Path) -> None:
        """Written row has partial=True when macro is unavailable."""
        from unittest.mock import patch

        store = _store_path(tmp_path)
        ibja = _make_backfill_ibja_parquet(tmp_path, [("2025-06-01", 73000.0, 72800.0)])
        duty = _make_backfill_duty_json(tmp_path)

        # Patch load_macro_features so it returns None even if the real cache exists
        with patch("ml.macro.load_macro_features", return_value=None):
            n = run_backfill(
                ibja_path=ibja,
                store_path=store,
                duty_events_path=duty,
                macro_df=None,
            )

        assert n == 1
        df = load_snapshots(store)
        row = df.iloc[0]
        assert row["partial"] is True or row["partial"] == True  # noqa: E712

    def test_backfill_only_processes_post_start_date(self, tmp_path: Path) -> None:
        """Dates before start_date are excluded even when pm_916 is non-null."""
        store = _store_path(tmp_path)
        ibja = _make_backfill_ibja_parquet(
            tmp_path,
            [("2024-12-31", 72000.0, 71800.0), ("2025-01-15", 73000.0, 72800.0)],
        )
        macro = _make_backfill_macro_df(["2024-12-31", "2025-01-15"])
        duty = _make_backfill_duty_json(tmp_path)

        n = run_backfill(
            ibja_path=ibja,
            store_path=store,
            duty_events_path=duty,
            start_date="2025-01-01",
            macro_df=macro,
        )

        df = load_snapshots(store)
        assert len(df) == 1
        assert df.iloc[0]["as_of_date"] == "2025-01-15"
        assert n == 1

    def test_backfill_skips_null_pm_916(self, tmp_path: Path) -> None:
        """Dates with null pm_916 in IBJA are not backfilled."""
        store = _store_path(tmp_path)
        ibja = _make_backfill_ibja_parquet(tmp_path, [("2025-06-01", None, 72800.0)])
        macro = _make_backfill_macro_df(["2025-06-01"])
        duty = _make_backfill_duty_json(tmp_path)

        n = run_backfill(ibja_path=ibja, store_path=store, duty_events_path=duty, macro_df=macro)

        df = load_snapshots(store)
        assert len(df) == 0
        assert n == 0


# ---------------------------------------------------------------------------
# TestNMacroNull
# ---------------------------------------------------------------------------


class TestNMacroNull:
    def test_n_macro_null_zero_when_all_macro_present(self, tmp_path: Path) -> None:
        """capture_daily_snapshot sets n_macro_null=0 when all 8 series are available."""
        store = _store_path(tmp_path)
        macro = _make_mock_macro_parquet(tmp_path)
        duty = _make_mock_duty_json(tmp_path)

        capture_daily_snapshot(store_path=store, macro_cache_path=macro, duty_events_path=duty)

        df = load_snapshots(store)
        assert int(df.iloc[0]["n_macro_null"]) == 0

    def test_n_macro_null_eight_when_macro_missing(self, tmp_path: Path) -> None:
        """capture_daily_snapshot sets n_macro_null=8 when macro cache is unavailable."""
        store = _store_path(tmp_path)
        duty = _make_mock_duty_json(tmp_path)

        capture_daily_snapshot(
            store_path=store,
            macro_cache_path=tmp_path / "nonexistent.parquet",
            duty_events_path=duty,
        )

        df = load_snapshots(store)
        assert int(df.iloc[0]["n_macro_null"]) == 8

    def test_n_macro_null_column_always_written(self, tmp_path: Path) -> None:
        """n_macro_null column is present in the parquet after any append_snapshot call."""
        path = _store_path(tmp_path)
        append_snapshot(_make_snapshot("2026-06-07"), path)

        df = load_snapshots(path)
        assert "n_macro_null" in df.columns

    def test_n_macro_null_stored_value(self, tmp_path: Path) -> None:
        """append_snapshot persists the n_macro_null value from the snapshot dict."""
        path = _store_path(tmp_path)
        snap = _make_snapshot(
            "2026-06-07", crude_wti=None, crude_wti_asof_date=None, n_macro_null=1
        )
        append_snapshot(snap, path)

        df = load_snapshots(path)
        assert int(df.iloc[0]["n_macro_null"]) == 1


# ---------------------------------------------------------------------------
# TestPatchMacroSeries
# ---------------------------------------------------------------------------


def _make_mock_price_df(dates: list[str], values: list[float]) -> pd.DataFrame:
    """Return a mock injectable price DataFrame (UTC DatetimeIndex, 'close' column)."""
    return pd.DataFrame(
        {"close": values},
        index=pd.to_datetime(dates, utc=True),
    )


class TestPatchMacroSeries:
    def test_patch_fills_missing_crude_and_tips(self, tmp_path: Path) -> None:
        """patch_missing_macro_series fills null crude_wti/tips on backfill_yfinance rows."""
        path = _store_path(tmp_path)
        snap = _make_snapshot(
            "2025-06-01",
            source="backfill_yfinance",
            crude_wti=None,
            crude_wti_asof_date=None,
            tips=None,
            tips_asof_date=None,
            n_macro_null=2,
        )
        append_snapshot(snap, path)

        crude = _make_mock_price_df(["2025-05-31"], [75.5])
        tips = _make_mock_price_df(["2025-05-31"], [108.2])

        result = patch_missing_macro_series(store_path=path, crude_df=crude, tips_df=tips)

        assert result["crude_patched"] == 1
        assert result["tips_patched"] == 1

        df = load_snapshots(path)
        row = df.iloc[0]
        assert abs(float(row["crude_wti"]) - 75.5) < 0.01
        assert abs(float(row["tips"]) - 108.2) < 0.01
        # All 8 macro series now present — crude and tips filled from mock.
        assert int(row["n_macro_null"]) == 0

    def test_patch_does_not_touch_live_pit(self, tmp_path: Path) -> None:
        """patch_missing_macro_series never modifies live_pit rows."""
        path = _store_path(tmp_path)
        snap = _make_snapshot(
            "2025-06-01",
            source="live_pit",
            crude_wti=None,
            crude_wti_asof_date=None,
            n_macro_null=1,
        )
        append_snapshot(snap, path)

        crude = _make_mock_price_df(["2025-05-31"], [75.5])
        tips = _make_mock_price_df(["2025-05-31"], [108.2])

        result = patch_missing_macro_series(store_path=path, crude_df=crude, tips_df=tips)

        assert result["crude_patched"] == 0
        df = load_snapshots(path)
        assert pd.isna(df.iloc[0]["crude_wti"]), "live_pit crude_wti must remain null"

    def test_patch_returns_correct_counts(self, tmp_path: Path) -> None:
        """Return dict accurately reports how many rows were patched for each series."""
        path = _store_path(tmp_path)
        # Two backfill rows: one missing crude only, one missing both.
        for date_str, crude_val, tips_val in [
            ("2025-06-01", None, 108.0),
            ("2025-06-02", None, None),
        ]:
            snap = _make_snapshot(
                date_str,
                source="backfill_yfinance",
                crude_wti=crude_val,
                crude_wti_asof_date=None if crude_val is None else "2025-05-31",
                tips=tips_val,
                tips_asof_date=None if tips_val is None else "2025-05-31",
                n_macro_null=(
                    1
                    if crude_val is None and tips_val is not None
                    else 2
                    if crude_val is None and tips_val is None
                    else 0
                ),
            )
            append_snapshot(snap, path)

        crude = _make_mock_price_df(["2025-05-31"], [75.0])
        tips = _make_mock_price_df(["2025-05-31"], [109.0])

        result = patch_missing_macro_series(store_path=path, crude_df=crude, tips_df=tips)

        assert result["crude_patched"] == 2
        assert result["tips_patched"] == 1
        assert result["n_macro_null_recomputed"] == 2

    def test_patch_leaves_null_where_no_history(self, tmp_path: Path) -> None:
        """When the injectable df has no data on or before as_of_date, value stays null."""
        path = _store_path(tmp_path)
        snap = _make_snapshot(
            "2025-01-15",
            source="backfill_yfinance",
            crude_wti=None,
            crude_wti_asof_date=None,
            n_macro_null=1,
        )
        append_snapshot(snap, path)

        # Only data from 2025-02-01 onwards — nothing on or before 2025-01-15.
        crude = _make_mock_price_df(["2025-02-01"], [70.0])
        tips = _make_mock_price_df(["2025-02-01"], [105.0])

        result = patch_missing_macro_series(store_path=path, crude_df=crude, tips_df=tips)

        assert result["crude_patched"] == 0
        df = load_snapshots(path)
        assert pd.isna(df.iloc[0]["crude_wti"]), "No history before target date — must stay null"
