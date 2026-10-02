"""Tests for ml/cadence_digest.py (Y1, audit 2026-09-05; AE3a, audit 2026-09-10)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ml.cadence_digest import (
    build_digest_body,
    load_cadence_metrics,
    load_catchup_log,
    main,
    summarize_catchup_causes,
)


def test_load_missing_file_returns_none(tmp_path: Path) -> None:
    assert load_cadence_metrics(tmp_path / "does_not_exist.json") is None


def test_load_malformed_json_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "cadence_metrics.json"
    path.write_text("not valid json", encoding="utf-8")
    assert load_cadence_metrics(path) is None


def test_load_zero_n_returns_none_not_a_fake_digest(tmp_path: Path) -> None:
    """No data is a distinct state from 'everything is fine' -- must not
    synthesize a digest from an empty metrics file."""
    path = tmp_path / "cadence_metrics.json"
    path.write_text(json.dumps({"n": 0, "median_gap_hours": None}), encoding="utf-8")
    assert load_cadence_metrics(path) is None


def test_load_valid_metrics_returns_dict(tmp_path: Path) -> None:
    path = tmp_path / "cadence_metrics.json"
    payload = {"window_days": 7, "n": 34, "median_gap_hours": 4.8, "as_of": "2026-09-04T19:43:13Z"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = load_cadence_metrics(path)
    assert result == payload


def test_digest_body_states_median_n_and_asof() -> None:
    body = build_digest_body(
        {"median_gap_hours": 4.8, "n": 34, "window_days": 7, "as_of": "2026-09-04T19:43:13Z"}
    )
    assert "4.8h" in body
    assert "n=34" in body
    assert "2026-09-04" in body
    assert "3h" in body  # the design target, stated for comparison


def test_digest_body_includes_p90_when_present() -> None:
    body = build_digest_body(
        {
            "median_gap_hours": 4.8,
            "n": 34,
            "as_of": "2026-09-04T19:43:13Z",
            "p90_gap_hours": 7.3,
        }
    )
    assert "7.3h" in body


def test_digest_body_omits_p90_clause_when_absent_not_fabricated() -> None:
    """If an older cadence_metrics.json (predating the p90 field) is read,
    the digest must not claim a p90 number it doesn't have."""
    body = build_digest_body({"median_gap_hours": 4.8, "n": 34, "as_of": "2026-09-04"})
    assert "Worst case" not in body


def test_main_prints_nothing_actionable_when_no_data(tmp_path: Path, monkeypatch, capsys) -> None:
    import ml.cadence_digest as mod

    monkeypatch.setattr(mod, "CADENCE_METRICS_PATH", tmp_path / "does_not_exist.json")
    monkeypatch.setattr(mod, "CATCHUP_LOG_PATH", tmp_path / "does_not_exist_either.jsonl")
    main()
    out = capsys.readouterr().out
    assert "skipping" in out.lower()
    assert "4.8h" not in out


def test_main_prints_digest_when_data_present(tmp_path: Path, monkeypatch, capsys) -> None:
    import ml.cadence_digest as mod

    path = tmp_path / "cadence_metrics.json"
    path.write_text(
        json.dumps({"median_gap_hours": 4.8, "n": 34, "as_of": "2026-09-04T19:43:13Z"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "CADENCE_METRICS_PATH", path)
    monkeypatch.setattr(mod, "CATCHUP_LOG_PATH", tmp_path / "no_catchups_this_run.jsonl")
    main()
    out = capsys.readouterr().out
    assert "4.8h" in out


# ── AE3a: catch-up cause classification (late vs. dropped) ──


def test_load_catchup_log_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_catchup_log(tmp_path / "does_not_exist.jsonl") == []


def test_load_catchup_log_skips_malformed_lines_without_raising(tmp_path: Path) -> None:
    path = tmp_path / "catchup_dispatch_log.jsonl"
    path.write_text(
        '{"timestamp": "2026-09-10T04:22:00Z", "gap_hours": 5, "cause": "dropped", '
        '"last_schedule_run_age_hours": 8.1}\n'
        "not valid json\n"
        '{"timestamp": "2026-09-09T11:07:00Z", "gap_hours": 4, "cause": "late", '
        '"last_schedule_run_age_hours": 1.2}\n',
        encoding="utf-8",
    )
    records = load_catchup_log(path)
    assert len(records) == 2
    assert records[0]["cause"] == "dropped"
    assert records[1]["cause"] == "late"


def test_summarize_catchup_causes_empty_records_returns_none() -> None:
    assert summarize_catchup_causes([]) is None


def test_summarize_catchup_causes_counts_dropped_vs_late() -> None:
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    records = [
        {"timestamp": "2026-09-09T04:22:00Z", "cause": "dropped"},
        {"timestamp": "2026-09-08T16:07:00Z", "cause": "dropped"},
        {"timestamp": "2026-09-07T04:22:00Z", "cause": "late"},
    ]
    summary = summarize_catchup_causes(records, now=now)
    assert summary == {"total": 3, "dropped": 2, "late": 1, "unknown": 0}


def test_summarize_catchup_causes_excludes_records_outside_window() -> None:
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    records = [
        {"timestamp": "2026-09-09T04:22:00Z", "cause": "dropped"},  # inside 7d window
        {"timestamp": "2026-08-01T00:00:00Z", "cause": "dropped"},  # far outside window
    ]
    summary = summarize_catchup_causes(records, now=now)
    assert summary == {"total": 1, "dropped": 1, "late": 0, "unknown": 0}


def test_summarize_catchup_causes_all_outside_window_returns_none() -> None:
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    records = [{"timestamp": "2026-08-01T00:00:00Z", "cause": "dropped"}]
    assert summarize_catchup_causes(records, now=now) is None


def test_summarize_catchup_causes_counts_unclassified_separately() -> None:
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    records = [
        {"timestamp": "2026-09-09T04:22:00Z", "cause": "unknown"},
        {"timestamp": "2026-09-09T05:00:00Z"},  # missing cause field entirely
    ]
    summary = summarize_catchup_causes(records, now=now)
    assert summary == {"total": 2, "dropped": 0, "late": 0, "unknown": 2}


def test_digest_body_includes_catchup_split_when_present() -> None:
    body = build_digest_body(
        {"median_gap_hours": 4.8, "n": 34, "as_of": "2026-09-04T19:43:13Z"},
        catchup_summary={"total": 5, "dropped": 3, "late": 2, "unknown": 0},
    )
    assert "fired 5x this week" in body
    assert "3 due to the scheduled trigger not firing at all" in body
    assert "2 due to an ordinary slow cycle" in body
    assert "unclassified" not in body


def test_digest_body_reports_unclassified_when_present() -> None:
    body = build_digest_body(
        {"median_gap_hours": 4.8, "n": 34, "as_of": "2026-09-04T19:43:13Z"},
        catchup_summary={"total": 3, "dropped": 1, "late": 1, "unknown": 1},
    )
    assert "1 unclassified" in body


def test_digest_body_omits_catchup_clause_when_none_not_fabricated() -> None:
    """No catch-up firings this week is not itself news -- must not claim
    a split (even '0 dropped, 0 late') the log doesn't actually support."""
    body = build_digest_body(
        {"median_gap_hours": 4.8, "n": 34, "as_of": "2026-09-04T19:43:13Z"}, catchup_summary=None
    )
    assert "Catch-up fired" not in body
