"""Tests for ml/cadence_metrics.py (R2, audit 2026-09-04)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ml.cadence_metrics import WINDOW_DAYS, compute_median_gap, load_log, main

NOW = datetime(2026, 9, 4, 6, 0, 0, tzinfo=UTC)


def _record(hours_ago: float) -> dict:
    return {"timestamp": (NOW - timedelta(hours=hours_ago)).isoformat()}


# ---------------------------------------------------------------------------
# load_log
# ---------------------------------------------------------------------------


def test_load_log_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_log(tmp_path / "does_not_exist.jsonl") == []


def test_load_log_skips_malformed_lines_without_raising(tmp_path: Path) -> None:
    log = tmp_path / "run_cadence_log.jsonl"
    log.write_text(
        '{"timestamp": "2026-09-04T00:00:00Z"}\n'
        "not valid json at all\n"
        '{"timestamp": "2026-09-04T03:00:00Z"}\n'
        "\n"  # blank line
    )
    records = load_log(log)
    assert len(records) == 2


# ---------------------------------------------------------------------------
# compute_median_gap
# ---------------------------------------------------------------------------


def test_fewer_than_two_records_in_window_returns_none_not_zero() -> None:
    """No/one record is a distinct state from a 0h gap -- must not default to 0."""
    result = compute_median_gap([_record(1)], NOW)
    assert result["n"] == 0
    assert result["median_gap_hours"] is None
    assert result["as_of"] is None


def test_empty_records_returns_none() -> None:
    result = compute_median_gap([], NOW)
    assert result["n"] == 0
    assert result["median_gap_hours"] is None


def test_regular_3h_cadence_computes_3h_median() -> None:
    records = [_record(h) for h in (0, 3, 6, 9, 12, 15, 18)]
    result = compute_median_gap(records, NOW)
    assert result["n"] == 6
    assert result["median_gap_hours"] == 3.0
    assert result["as_of"] == NOW.isoformat()


def test_degraded_cadence_with_one_dropped_tick() -> None:
    # 3h nominal cadence but one tick dropped, creating a 6h gap.
    records = [_record(h) for h in (0, 3, 6, 12, 15, 18)]
    result = compute_median_gap(records, NOW)
    assert result["n"] == 5
    # gaps (oldest->newest): 3,3,6,3,3 -> sorted 3,3,3,3,6 -> median 3
    assert result["median_gap_hours"] == 3.0


def test_records_outside_window_excluded() -> None:
    records = [_record(1), _record(4), _record(24 * 30)]  # last one far outside 7d window
    result = compute_median_gap(records, NOW, window_days=7)
    assert result["n"] == 1


def test_records_missing_timestamp_are_skipped() -> None:
    records = [_record(1), {"timestamp": None}, {"no_such_field": True}, _record(4)]
    result = compute_median_gap(records, NOW)
    assert result["n"] == 1


def test_unparseable_timestamp_is_skipped() -> None:
    records = [_record(1), {"timestamp": "not-a-date"}, _record(4)]
    result = compute_median_gap(records, NOW)
    assert result["n"] == 1


def test_window_days_default_is_seven() -> None:
    assert WINDOW_DAYS == 7


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_writes_output_file(tmp_path: Path, monkeypatch) -> None:
    import ml.cadence_metrics as mod

    log_path = tmp_path / "run_cadence_log.jsonl"
    output_path = tmp_path / "cadence_metrics.json"
    log_path.write_text(
        "\n".join(json.dumps(_record(h)) for h in (0, 3, 6)) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(mod, "CADENCE_LOG_PATH", log_path)
    monkeypatch.setattr(mod, "OUTPUT_PATH", output_path)

    main(now=NOW)

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["n"] == 2
    assert written["median_gap_hours"] == 3.0
    assert written["schema_version"] == 1
    assert "generated_at_utc" in written


def test_main_handles_missing_log_file(tmp_path: Path, monkeypatch) -> None:
    import ml.cadence_metrics as mod

    output_path = tmp_path / "cadence_metrics.json"
    monkeypatch.setattr(mod, "CADENCE_LOG_PATH", tmp_path / "does_not_exist.jsonl")
    monkeypatch.setattr(mod, "OUTPUT_PATH", output_path)

    main(now=NOW)

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["n"] == 0
    assert written["median_gap_hours"] is None
