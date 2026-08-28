"""Tests for ml/scrape_metrics.py (H3, session dated 2026-08-28)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ml.scrape_metrics import (
    WINDOW_DAYS,
    compute_rolling_success_rate,
    load_outcomes,
    main,
)

NOW = datetime(2026, 8, 28, 4, 0, 0, tzinfo=UTC)


def _record(hours_ago: float, outcome: str = "success", fetch_method: str = "requests") -> dict:
    ts = NOW - timedelta(hours=hours_ago)
    return {"timestamp": ts.isoformat(), "outcome": outcome, "fetch_method": fetch_method}


# ---------------------------------------------------------------------------
# load_outcomes
# ---------------------------------------------------------------------------


def test_load_outcomes_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_outcomes(tmp_path / "does_not_exist.jsonl") == []


def test_load_outcomes_skips_malformed_lines_without_raising(tmp_path: Path) -> None:
    log = tmp_path / "outcomes.jsonl"
    log.write_text(
        '{"timestamp": "2026-08-28T00:00:00Z", "outcome": "success", "fetch_method": "requests"}\n'
        "not valid json at all\n"
        '{"timestamp": "2026-08-28T01:00:00Z", "outcome": "failure", "fetch_method": "none"}\n'
        "\n"  # blank line
    )
    records = load_outcomes(log)
    assert len(records) == 2
    assert records[0]["outcome"] == "success"
    assert records[1]["outcome"] == "failure"


# ---------------------------------------------------------------------------
# compute_rolling_success_rate
# ---------------------------------------------------------------------------


def test_no_records_in_window_returns_none_not_zero() -> None:
    """No data is a distinct state from 0% success -- must not default to 0.0."""
    result = compute_rolling_success_rate([], NOW)
    assert result["n"] == 0
    assert result["success_rate"] is None
    assert result["wilson_ci_low"] is None
    assert result["wilson_ci_high"] is None


def test_all_success_100_percent_rate() -> None:
    records = [_record(h) for h in range(0, 24, 3)]  # 8 records, all success
    result = compute_rolling_success_rate(records, NOW)
    assert result["n"] == 8
    assert result["n_success"] == 8
    assert result["n_failure"] == 0
    assert result["success_rate"] == 1.0


def test_mixed_outcomes_computed_correctly() -> None:
    records = [
        _record(1, "success", "requests"),
        _record(2, "success", "requests"),
        _record(3, "success", "playwright"),
        _record(4, "failure", "none"),
    ]
    result = compute_rolling_success_rate(records, NOW)
    assert result["n"] == 4
    assert result["n_success"] == 3
    assert result["n_failure"] == 1
    assert result["n_requests_path"] == 2
    assert result["n_playwright_fallback"] == 1
    assert result["success_rate"] == 0.75
    assert result["wilson_ci_low"] is not None
    assert result["wilson_ci_low"] < 0.75 < result["wilson_ci_high"]


def test_records_outside_window_excluded() -> None:
    records = [
        _record(1),  # inside
        _record(WINDOW_DAYS * 24 + 10),  # well outside the window
    ]
    result = compute_rolling_success_rate(records, NOW, window_days=WINDOW_DAYS)
    assert result["n"] == 1


def test_records_missing_timestamp_skipped() -> None:
    records = [_record(1), {"outcome": "success", "fetch_method": "requests"}]
    result = compute_rolling_success_rate(records, NOW)
    assert result["n"] == 1


def test_records_unparseable_timestamp_skipped() -> None:
    records = [_record(1), {"timestamp": "not-a-date", "outcome": "success"}]
    result = compute_rolling_success_rate(records, NOW)
    assert result["n"] == 1


def test_custom_window_days_respected() -> None:
    records = [_record(h) for h in [1, 25, 49, 73]]  # spans 3 days
    result_1d = compute_rolling_success_rate(records, NOW, window_days=1)
    result_4d = compute_rolling_success_rate(records, NOW, window_days=4)
    assert result_1d["n"] == 1
    assert result_4d["n"] == 4


# ---------------------------------------------------------------------------
# main() end-to-end
# ---------------------------------------------------------------------------


def test_main_writes_output_file(tmp_path: Path, monkeypatch) -> None:
    import ml.scrape_metrics as sm

    log = tmp_path / "tanishq_scrape_outcomes.jsonl"
    out = tmp_path / "tanishq_scrape_success_rate.json"
    log.write_text(
        json.dumps({"timestamp": NOW.isoformat(), "outcome": "success", "fetch_method": "requests"})
        + "\n"
    )
    monkeypatch.setattr(sm, "OUTCOMES_LOG_PATH", log)
    monkeypatch.setattr(sm, "OUTPUT_PATH", out)

    main(now=NOW)

    written = json.loads(out.read_text())
    assert written["schema_version"] == 1
    assert written["n"] == 1
    assert written["success_rate"] == 1.0
    assert "generated_at_utc" in written
