"""Tests for ml.chronos_forecast — ChronosBoltPipeline mocked, no real download."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import ml.chronos_forecast as cf
import numpy as np
import pandas as pd
import pytest
import torch
from ml.calibration import CalibrationParams

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STUB_PARAMS = CalibrationParams(
    slope=1.02,
    intercept=50.0,
    fit_date="2026-05-19",
    n_observations=35,
    residual_std=10.0,
    r_squared=0.998,
    huber_epsilon=1.35,
)


def _make_ibja_series(n: int = 25, last_date: str = "2026-05-18") -> pd.Series:
    """Synthetic date-indexed IBJA-level series (INR/g)."""
    rng = np.random.default_rng(42)
    base = pd.Timestamp(last_date) - pd.Timedelta(days=n - 1)
    dates = [str((base + pd.Timedelta(days=i)).date()) for i in range(n)]
    values = 14_000.0 + rng.normal(0, 200, n).cumsum()
    return pd.Series(values, index=dates, name="pm_916_per_g")


def _stub_pipeline(horizon: int = 5, q_values: list[float] | None = None) -> MagicMock:
    """Return a mock pipeline whose predict_quantiles gives deterministic output."""
    if q_values is None:
        # p10, p50, p90 per step — gently increasing spread with widening horizon
        q_values = [14_200.0, 14_380.0, 14_580.0]

    mock = MagicMock()

    def fake_predict_quantiles(inputs, prediction_length, quantile_levels, **kwargs):
        n_q = len(quantile_levels)
        data = np.zeros((1, prediction_length, n_q), dtype=np.float32)
        for qi in range(n_q):
            data[0, :, qi] = q_values[qi] + qi * 10 * np.arange(prediction_length)
        quantiles = torch.tensor(data)
        mean = torch.tensor(np.mean(data, axis=-1, keepdims=False))
        return quantiles, mean

    mock.predict_quantiles.side_effect = fake_predict_quantiles
    return mock


# ---------------------------------------------------------------------------
# forecast_ibja
# ---------------------------------------------------------------------------


def test_forecast_ibja_shape():
    pipeline = _stub_pipeline()
    series = _make_ibja_series(25)
    df = cf.forecast_ibja(pipeline, series, horizon=5)
    assert df.shape == (5, 4)  # date + p10 + p50 + p90
    assert list(df.columns) == ["date", "p10", "p50", "p90"]


def test_forecast_ibja_date_continuity():
    pipeline = _stub_pipeline()
    series = _make_ibja_series(25, last_date="2026-05-18")
    df = cf.forecast_ibja(pipeline, series, horizon=5)
    last_input = pd.Timestamp("2026-05-18")
    for i, row in df.iterrows():
        expected = (last_input + timedelta(days=i + 1)).strftime("%Y-%m-%d")
        assert row["date"] == expected, f"row {i}: expected {expected}, got {row['date']}"


def test_forecast_ibja_quantile_monotonicity():
    """p10 <= p50 <= p90 for every forecast step."""
    pipeline = _stub_pipeline(q_values=[14_100.0, 14_380.0, 14_650.0])
    series = _make_ibja_series(25)
    df = cf.forecast_ibja(pipeline, series, horizon=5)
    assert (df["p10"] <= df["p50"]).all(), "p10 > p50 found"
    assert (df["p50"] <= df["p90"]).all(), "p50 > p90 found"


def test_forecast_ibja_horizon_respected():
    pipeline = _stub_pipeline()
    series = _make_ibja_series(25)
    for h in [1, 3, 7]:
        df = cf.forecast_ibja(pipeline, series, horizon=h)
        assert len(df) == h


def test_forecast_ibja_raises_on_insufficient_context():
    pipeline = _stub_pipeline()
    series = _make_ibja_series(7)  # below _MIN_CONTEXT_DAYS=8
    with pytest.raises(ValueError, match="insufficient IBJA history"):
        cf.forecast_ibja(pipeline, series, horizon=5)


def test_forecast_ibja_accepts_exactly_min_context():
    pipeline = _stub_pipeline()
    series = _make_ibja_series(8)
    df = cf.forecast_ibja(pipeline, series, horizon=5)
    assert len(df) == 5


def test_forecast_ibja_calls_predict_quantiles_once():
    pipeline = _stub_pipeline()
    series = _make_ibja_series(25)
    cf.forecast_ibja(pipeline, series, horizon=5)
    pipeline.predict_quantiles.assert_called_once()


# ---------------------------------------------------------------------------
# chronos_to_tanishq
# ---------------------------------------------------------------------------


def test_chronos_to_tanishq_applies_calibration():
    ibja_fc = pd.DataFrame(
        {
            "date": ["2026-05-19", "2026-05-20"],
            "p10": [14_000.0, 14_050.0],
            "p50": [14_400.0, 14_450.0],
            "p90": [14_800.0, 14_850.0],
        }
    )
    result = cf.chronos_to_tanishq(ibja_fc, _STUB_PARAMS)
    # slope=1.02, intercept=50
    assert abs(result["p50"].iloc[0] - (1.02 * 14_400.0 + 50.0)) < 1e-3
    assert abs(result["p10"].iloc[0] - (1.02 * 14_000.0 + 50.0)) < 1e-3


def test_chronos_to_tanishq_preserves_schema():
    ibja_fc = pd.DataFrame(
        {
            "date": ["2026-05-19"],
            "p10": [14_000.0],
            "p50": [14_400.0],
            "p90": [14_800.0],
        }
    )
    result = cf.chronos_to_tanishq(ibja_fc, _STUB_PARAMS)
    assert list(result.columns) == ["date", "p10", "p50", "p90"]
    assert result["date"].iloc[0] == "2026-05-19"


def test_chronos_to_tanishq_monotonicity_preserved():
    """If p10 <= p50 <= p90 in IBJA forecast, it must hold after calibration (slope > 0)."""
    ibja_fc = pd.DataFrame(
        {
            "date": ["2026-05-19", "2026-05-20"],
            "p10": [14_000.0, 14_050.0],
            "p50": [14_400.0, 14_450.0],
            "p90": [14_800.0, 14_850.0],
        }
    )
    result = cf.chronos_to_tanishq(ibja_fc, _STUB_PARAMS)
    assert (result["p10"] <= result["p50"]).all()
    assert (result["p50"] <= result["p90"]).all()


# ---------------------------------------------------------------------------
# run_probe
# ---------------------------------------------------------------------------


def _write_stub_calibration(path: Path, valid: bool = False) -> None:
    payload = {
        "slope": 1.02,
        "intercept": 50.0,
        "fit_date": "2026-05-19",
        "n_observations": 35 if valid else 21,
        "residual_std": 10.0,
        "r_squared": 0.998,
        "huber_epsilon": 1.35,
        "valid": valid,
        "schema_version": 1,
    }
    path.write_text(json.dumps(payload))


def _write_stub_parquet(path: Path, n: int = 25) -> None:
    series = _make_ibja_series(n)
    df = pd.DataFrame(
        {
            "date": series.index.tolist(),
            "pm_916": (series * 10).tolist(),
            "fetched_at": ["2026-05-18T00:00:00+00:00"] * n,
        }
    )
    df.to_parquet(path, index=False)


@patch("ml.chronos_forecast.load_chronos_pipeline")
def test_run_probe_success(mock_load, tmp_path):
    mock_load.return_value = _stub_pipeline()
    parquet_path = tmp_path / "ibja.parquet"
    calib_path = tmp_path / "calibration.json"
    out_path = tmp_path / "probe.json"

    _write_stub_parquet(parquet_path)
    _write_stub_calibration(calib_path, valid=False)

    result = cf.run_probe(parquet_path, calib_path, out_path)

    assert result["status"] == "success"
    assert out_path.exists()
    assert len(result["ibja_forecast"]) == 5
    assert result["calibration_applied"] is False  # calib is invalid stub


@patch("ml.chronos_forecast.load_chronos_pipeline")
def test_run_probe_calibration_applied_when_valid(mock_load, tmp_path):
    mock_load.return_value = _stub_pipeline()
    parquet_path = tmp_path / "ibja.parquet"
    calib_path = tmp_path / "calibration.json"
    out_path = tmp_path / "probe.json"

    _write_stub_parquet(parquet_path)
    _write_stub_calibration(calib_path, valid=True)

    result = cf.run_probe(parquet_path, calib_path, out_path)

    assert result["status"] == "success"
    assert result["calibration_applied"] is True
    assert result["tanishq_forecast"] is not None
    assert len(result["tanishq_forecast"]) == 5


def test_run_probe_insufficient_context(tmp_path):
    parquet_path = tmp_path / "ibja.parquet"
    calib_path = tmp_path / "calibration.json"
    out_path = tmp_path / "probe.json"

    _write_stub_parquet(parquet_path, n=7)  # below _MIN_CONTEXT_DAYS=8
    _write_stub_calibration(calib_path, valid=False)

    result = cf.run_probe(parquet_path, calib_path, out_path)

    assert result["status"] == "insufficient_context"
    assert out_path.exists()  # always written even on failure


def test_run_probe_missing_parquet_gives_insufficient(tmp_path):
    calib_path = tmp_path / "calibration.json"
    out_path = tmp_path / "probe.json"
    _write_stub_calibration(calib_path, valid=False)

    result = cf.run_probe(tmp_path / "nonexistent.parquet", calib_path, out_path)

    assert result["status"] == "insufficient_context"
    assert out_path.exists()


@patch("ml.chronos_forecast.load_chronos_pipeline")
def test_run_probe_model_load_failure(mock_load, tmp_path):
    mock_load.side_effect = RuntimeError("model not found")
    parquet_path = tmp_path / "ibja.parquet"
    calib_path = tmp_path / "calibration.json"
    out_path = tmp_path / "probe.json"

    _write_stub_parquet(parquet_path)
    _write_stub_calibration(calib_path)

    result = cf.run_probe(parquet_path, calib_path, out_path)

    assert result["status"] == "model_load_failed"
    assert out_path.exists()


@patch("ml.chronos_forecast.load_chronos_pipeline")
def test_run_probe_output_has_required_keys(mock_load, tmp_path):
    mock_load.return_value = _stub_pipeline()
    parquet_path = tmp_path / "ibja.parquet"
    calib_path = tmp_path / "calibration.json"
    out_path = tmp_path / "probe.json"

    _write_stub_parquet(parquet_path)
    _write_stub_calibration(calib_path)

    result = cf.run_probe(parquet_path, calib_path, out_path)

    required = {
        "probed_at",
        "status",
        "wall_clock_ms",
        "ibja_context_days",
        "ibja_last_date",
        "ibja_last_value",
        "horizon",
        "ibja_forecast",
        "calibration_applied",
        "calibration_valid",
        "model_version",
        "schema_version",
    }
    assert required <= set(result.keys())


@patch("ml.chronos_forecast.load_chronos_pipeline")
def test_run_probe_wall_clock_keys_present(mock_load, tmp_path):
    mock_load.return_value = _stub_pipeline()
    parquet_path = tmp_path / "ibja.parquet"
    calib_path = tmp_path / "calibration.json"
    out_path = tmp_path / "probe.json"

    _write_stub_parquet(parquet_path)
    _write_stub_calibration(calib_path)

    result = cf.run_probe(parquet_path, calib_path, out_path)

    assert {"pipeline_load", "forecast", "calibration", "total"} <= set(
        result["wall_clock_ms"].keys()
    )


@pytest.mark.integration
def test_run_probe_real_model(tmp_path):
    """Requires real model download — skipped by default, run with -m integration."""
    parquet_path = tmp_path / "ibja.parquet"
    calib_path = tmp_path / "calibration.json"
    out_path = tmp_path / "probe.json"

    _write_stub_parquet(parquet_path, n=25)
    _write_stub_calibration(calib_path, valid=False)

    result = cf.run_probe(parquet_path, calib_path, out_path)
    assert result["status"] == "success"
    assert result["wall_clock_ms"]["total"] > 0
