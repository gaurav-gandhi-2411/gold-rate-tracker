"""Tests for ml.drift — no live requests, no filesystem side effects."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import ml.drift as drift


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_forecast(path: Path, predicted_22k: float = 14400.0, model_version: str = "naive_flat_hold") -> None:
    payload = {
        "predicted_at": "2026-05-30T00:00:00+00:00",
        "target_time": "2026-05-31T00:00:00+00:00",
        "predicted_22k": predicted_22k,
        "model_version": model_version,
    }
    path.write_text(json.dumps(payload))


def _write_prices(path: Path, price_22k: float = 14450.0, timestamp: str = "2026-05-31T06:00:00.000Z") -> None:
    path.write_text(json.dumps([{"timestamp": timestamp, "22k": price_22k}]))


def _write_backtest(path: Path, mae_5d_avg_naive: float = 249.53) -> None:
    path.write_text(json.dumps({"mae_5d_avg_naive": mae_5d_avg_naive, "n_folds": 165}))


# ---------------------------------------------------------------------------
# baseline_mae propagation
# ---------------------------------------------------------------------------


def test_run_drift_check_writes_baseline_mae_from_backtest(tmp_path, monkeypatch):
    """Each new drift entry includes baseline_mae sourced from backtest.json."""
    monkeypatch.setattr(drift, "DRIFT_METRICS_PATH", tmp_path / "drift_metrics.json")
    monkeypatch.setattr(drift, "FORECAST_PATH", tmp_path / "forecast.json")
    monkeypatch.setattr(drift, "PRICES_PATH", tmp_path / "prices.json")
    monkeypatch.setattr(drift, "BACKTEST_PATH", tmp_path / "backtest.json")

    _write_forecast(tmp_path / "forecast.json")
    _write_prices(tmp_path / "prices.json")
    _write_backtest(tmp_path / "backtest.json", mae_5d_avg_naive=249.53)

    entry = drift.run_drift_check()

    assert entry is not None
    assert "baseline_mae" in entry
    assert entry["baseline_mae"] == 249.53


def test_run_drift_check_omits_baseline_mae_when_backtest_absent(tmp_path, monkeypatch):
    """When backtest.json is absent, baseline_mae is not written (no fabrication)."""
    monkeypatch.setattr(drift, "DRIFT_METRICS_PATH", tmp_path / "drift_metrics.json")
    monkeypatch.setattr(drift, "FORECAST_PATH", tmp_path / "forecast.json")
    monkeypatch.setattr(drift, "PRICES_PATH", tmp_path / "prices.json")
    monkeypatch.setattr(drift, "BACKTEST_PATH", tmp_path / "backtest.json")

    _write_forecast(tmp_path / "forecast.json")
    _write_prices(tmp_path / "prices.json")
    # No backtest.json written

    entry = drift.run_drift_check()

    assert entry is not None
    assert "baseline_mae" not in entry


def test_run_drift_check_baseline_mae_uses_naive_field(tmp_path, monkeypatch):
    """baseline_mae takes the mae_5d_avg_naive value, not Chronos or other fields."""
    monkeypatch.setattr(drift, "DRIFT_METRICS_PATH", tmp_path / "drift_metrics.json")
    monkeypatch.setattr(drift, "FORECAST_PATH", tmp_path / "forecast.json")
    monkeypatch.setattr(drift, "PRICES_PATH", tmp_path / "prices.json")
    monkeypatch.setattr(drift, "BACKTEST_PATH", tmp_path / "backtest.json")

    _write_forecast(tmp_path / "forecast.json")
    _write_prices(tmp_path / "prices.json")
    (tmp_path / "backtest.json").write_text(
        json.dumps({"mae_5d_avg_naive": 300.0, "mae_5d_avg_chronos": 350.0, "n_folds": 180})
    )

    entry = drift.run_drift_check()

    assert entry is not None
    assert entry["baseline_mae"] == 300.0


def test_run_drift_check_baseline_mae_persisted_in_file(tmp_path, monkeypatch):
    """baseline_mae is written to drift_metrics.json, readable back."""
    monkeypatch.setattr(drift, "DRIFT_METRICS_PATH", tmp_path / "drift_metrics.json")
    monkeypatch.setattr(drift, "FORECAST_PATH", tmp_path / "forecast.json")
    monkeypatch.setattr(drift, "PRICES_PATH", tmp_path / "prices.json")
    monkeypatch.setattr(drift, "BACKTEST_PATH", tmp_path / "backtest.json")

    _write_forecast(tmp_path / "forecast.json")
    _write_prices(tmp_path / "prices.json")
    _write_backtest(tmp_path / "backtest.json", mae_5d_avg_naive=249.53)

    drift.run_drift_check()

    saved = json.loads((tmp_path / "drift_metrics.json").read_text())
    assert len(saved) == 1
    assert saved[0]["baseline_mae"] == 249.53
    assert saved[0]["model_version"] == "naive_flat_hold"


def test_run_drift_check_residual_correct(tmp_path, monkeypatch):
    """Residual = actual - forecast (positive when price rose)."""
    monkeypatch.setattr(drift, "DRIFT_METRICS_PATH", tmp_path / "drift_metrics.json")
    monkeypatch.setattr(drift, "FORECAST_PATH", tmp_path / "forecast.json")
    monkeypatch.setattr(drift, "PRICES_PATH", tmp_path / "prices.json")
    monkeypatch.setattr(drift, "BACKTEST_PATH", tmp_path / "backtest.json")

    _write_forecast(tmp_path / "forecast.json", predicted_22k=14400.0)
    _write_prices(tmp_path / "prices.json", price_22k=14550.0)
    _write_backtest(tmp_path / "backtest.json")

    entry = drift.run_drift_check()

    assert entry is not None
    assert entry["residual"] == pytest.approx(150.0)
