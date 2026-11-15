"""ml/inference.py writes next_fix.down_since while the next-fix model is down (when it went down)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import ml.inference as inf
import pytest

from tests.test_inference_main import _disable_fusion, _make_backtest, _make_prices

NOW = datetime(2026, 2, 10, 12, 0, tzinfo=UTC)  # pinned: the fixture prices end on 2026-02-09
DOWN = {"active": False, "reason": "error: ZeroDivisionError"}


def test_down_since_is_now_when_there_is_no_usable_previous_forecast() -> None:
    assert inf._down_since(None, NOW) == NOW.isoformat()
    assert inf._down_since("not a dict", NOW) == NOW.isoformat()
    assert inf._down_since({}, NOW) == NOW.isoformat()
    # the previous forecast was active: a new streak starts now
    assert inf._down_since({"next_fix": {"active": True}, "predicted_at": "x"}, NOW) == (
        NOW.isoformat()
    )
    # a previous forecast from before next_fix existed says nothing about the model being down
    assert inf._down_since({"predicted_at": "2026-02-10T06:00:00+00:00"}, NOW) == NOW.isoformat()


def test_down_since_keeps_the_start_of_the_streak() -> None:
    start = "2026-02-09T21:00:00+00:00"
    prev = {"predicted_at": "2026-02-10T09:00:00+00:00", "next_fix": {**DOWN, "down_since": start}}
    assert inf._down_since(prev, NOW) == start
    prev["next_fix"]["down_since"] = "garbage"  # a damaged key fails soft to now, never raises
    assert inf._down_since(prev, NOW) == NOW.isoformat()


def test_down_since_falls_back_to_the_previous_forecasts_time_for_an_old_inactive_block() -> None:
    prev = {"predicted_at": "2026-02-10T09:00:00+00:00", "next_fix": dict(DOWN)}
    assert inf._down_since(prev, NOW) == "2026-02-10T09:00:00+00:00"


@pytest.fixture
def _repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(inf, "DATA_DIR", tmp_path)
    _disable_fusion(monkeypatch)
    # ml.drivers stamps computed_at from the real clock; this test is about next_fix, so drop it
    monkeypatch.setattr(inf, "compute_driver_attribution", lambda **_: None)
    (tmp_path / "prices.json").write_text(json.dumps(_make_prices(40)))
    (tmp_path / "backtest.json").write_text(json.dumps(_make_backtest(35)))
    (tmp_path / "chronos_probe.json").write_text(json.dumps({"status": "failed"}))
    (tmp_path / "calibration.json").write_text(json.dumps({"valid": False}))
    return tmp_path


def _next_fix(repo: Path) -> dict:
    return json.loads((repo / "forecast.json").read_text())["next_fix"]


def test_main_stamps_the_start_of_the_outage_and_keeps_it_across_runs(
    _repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(inf, "_next_fix_block", lambda *a, **k: dict(DOWN))
    inf.main(now=NOW)
    assert _next_fix(_repo)["down_since"] == NOW.isoformat()
    inf.main(now=NOW + timedelta(hours=3))  # still down: the start does not move
    assert _next_fix(_repo)["down_since"] == NOW.isoformat()
    ok = {
        "active": True,
        "predicted_22k": 14500,
        "lower": 14400,
        "upper": 14600,
        "model_version": "m",
    }
    monkeypatch.setattr(inf, "_next_fix_block", lambda *a, **k: dict(ok))
    inf.main(now=NOW + timedelta(hours=6))  # back: no key at all
    assert "down_since" not in _next_fix(_repo)
    monkeypatch.setattr(inf, "_next_fix_block", lambda *a, **k: dict(DOWN))
    inf.main(now=NOW + timedelta(hours=9))  # down again: a new streak
    assert _next_fix(_repo)["down_since"] == (NOW + timedelta(hours=9)).isoformat()


def test_main_fails_soft_when_the_previous_forecast_is_unreadable(
    _repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(inf, "_next_fix_block", lambda *a, **k: dict(DOWN))
    (_repo / "forecast.json").write_text("{truncated", encoding="utf-8")
    inf.main(now=NOW)
    assert _next_fix(_repo)["down_since"] == NOW.isoformat()


def test_the_forecast_schema_and_range_check_accept_the_extra_key(
    _repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # scripts/validate_data.py validates forecast.json against this schema (no next_fix property,
    # no additionalProperties: false), so the new key cannot fail the data merge gate
    import jsonschema
    from scripts.validate_data import check_ranges

    from tests.test_schema_contracts import FORECAST_SCHEMA

    monkeypatch.setattr(inf, "_next_fix_block", lambda *a, **k: dict(DOWN))
    inf.main(now=NOW)
    forecast = json.loads((_repo / "forecast.json").read_text())
    assert "down_since" in forecast["next_fix"]
    jsonschema.validate(forecast, FORECAST_SCHEMA)
    assert check_ranges([], forecast) == []
