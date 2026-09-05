"""Tests for ml/cadence_digest.py (Y1, audit 2026-09-05)."""

from __future__ import annotations

import json
from pathlib import Path

from ml.cadence_digest import build_digest_body, load_cadence_metrics, main


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
    main()
    out = capsys.readouterr().out
    assert "4.8h" in out
