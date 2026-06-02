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


# ---------------------------------------------------------------------------
# _classify_sample_direction unit tests
# ---------------------------------------------------------------------------


def test_classify_direction_up():
    """p50 consistently above ibja_last by >0.1% => 'up'."""
    ibja_last = 14_000.0
    # p50 ~14_300, which is 2.14% above ibja_last
    df = pd.DataFrame(
        {
            "date": ["2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22", "2026-05-23"],
            "p10": [14_100.0] * 5,
            "p50": [14_300.0] * 5,
            "p90": [14_500.0] * 5,
        }
    )
    assert cf._classify_sample_direction(df, ibja_last) == "up"


def test_classify_direction_down():
    """p50 consistently below ibja_last by >0.1% => 'down'."""
    ibja_last = 14_000.0
    # p50 ~13_700, which is 2.14% below ibja_last
    df = pd.DataFrame(
        {
            "date": ["2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22", "2026-05-23"],
            "p10": [13_500.0] * 5,
            "p50": [13_700.0] * 5,
            "p90": [13_900.0] * 5,
        }
    )
    assert cf._classify_sample_direction(df, ibja_last) == "down"


def test_classify_direction_neutral_within_threshold():
    """p50 within 0.1% of ibja_last => 'neutral'."""
    ibja_last = 14_000.0
    # p50 = 14_001.0, delta_pct = 0.001/14 ~= 0.0000714, below threshold 0.001
    df = pd.DataFrame(
        {
            "date": ["2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22", "2026-05-23"],
            "p10": [13_990.0] * 5,
            "p50": [14_001.0] * 5,
            "p90": [14_010.0] * 5,
        }
    )
    assert cf._classify_sample_direction(df, ibja_last) == "neutral"


def test_classify_direction_neutral_on_zero_last():
    """ibja_last <= 0 always returns 'neutral'."""
    df = pd.DataFrame(
        {
            "date": ["2026-05-19"],
            "p10": [14_000.0],
            "p50": [14_300.0],
            "p90": [14_500.0],
        }
    )
    assert cf._classify_sample_direction(df, 0.0) == "neutral"
    assert cf._classify_sample_direction(df, -100.0) == "neutral"


# ---------------------------------------------------------------------------
# _aggregate_directions unit tests
# ---------------------------------------------------------------------------


def test_aggregate_all_up():
    assert cf._aggregate_directions(["up", "up", "up"]) == ("up", 1.0)


def test_aggregate_majority_up():
    majority, fraction = cf._aggregate_directions(["up", "down", "up", "down", "up"])
    assert majority == "up"
    assert fraction == 0.6


def test_aggregate_empty():
    assert cf._aggregate_directions([]) == ("neutral", 0.0)


def test_aggregate_2_2_1_split():
    """2-2-1 split: max consensus is 0.4, below the 0.6 gate."""
    _, fraction = cf._aggregate_directions(["up", "up", "down", "down", "neutral"])
    assert fraction < 0.6


def test_aggregate_mixed_5_samples():
    """up,up,down,up,neutral => majority 'up', consensus 0.6 (3/5)."""
    majority, fraction = cf._aggregate_directions(["up", "up", "down", "up", "neutral"])
    assert majority == "up"
    assert fraction == 0.6


# ---------------------------------------------------------------------------
# run_probe — schema v2 and multi-sample fields
# ---------------------------------------------------------------------------


def _stub_pipeline_counted(counter: list[int], horizon: int = 5) -> MagicMock:
    """Stub pipeline that increments counter[0] on each predict_quantiles call."""
    q_values = [14_200.0, 14_380.0, 14_580.0]
    mock = MagicMock()

    def fake_predict_quantiles(inputs, prediction_length, quantile_levels, **kwargs):
        counter[0] += 1
        n_q = len(quantile_levels)
        data = np.zeros((1, prediction_length, n_q), dtype=np.float32)
        for qi in range(n_q):
            data[0, :, qi] = q_values[qi] + qi * 10 * np.arange(prediction_length)
        quantiles = torch.tensor(data)
        mean = torch.tensor(np.mean(data, axis=-1, keepdims=False))
        return quantiles, mean

    mock.predict_quantiles.side_effect = fake_predict_quantiles
    return mock


@patch("ml.chronos_forecast.load_chronos_pipeline")
def test_run_probe_writes_schema_v2(mock_load, tmp_path):
    """Successful probe must write schema_version == 2."""
    mock_load.return_value = _stub_pipeline()
    parquet_path = tmp_path / "ibja.parquet"
    calib_path = tmp_path / "calibration.json"
    out_path = tmp_path / "probe.json"

    _write_stub_parquet(parquet_path)
    _write_stub_calibration(calib_path, valid=False)

    result = cf.run_probe(parquet_path, calib_path, out_path)

    assert result["schema_version"] == 2


@patch("ml.chronos_forecast.load_chronos_pipeline")
def test_run_probe_calls_forecast_num_samples_times(mock_load, tmp_path):
    """predict_quantiles must be called exactly DEFAULT_NUM_SAMPLES times."""
    call_counter: list[int] = [0]
    mock_load.return_value = _stub_pipeline_counted(call_counter)
    parquet_path = tmp_path / "ibja.parquet"
    calib_path = tmp_path / "calibration.json"
    out_path = tmp_path / "probe.json"

    _write_stub_parquet(parquet_path)
    _write_stub_calibration(calib_path, valid=False)

    result = cf.run_probe(parquet_path, calib_path, out_path)

    assert result["status"] == "success"
    assert call_counter[0] == cf.DEFAULT_NUM_SAMPLES


@patch("ml.chronos_forecast.load_chronos_pipeline")
def test_run_probe_sample_directions_length(mock_load, tmp_path):
    """num_samples == DEFAULT_NUM_SAMPLES and sample_directions has matching length."""
    mock_load.return_value = _stub_pipeline()
    parquet_path = tmp_path / "ibja.parquet"
    calib_path = tmp_path / "calibration.json"
    out_path = tmp_path / "probe.json"

    _write_stub_parquet(parquet_path)
    _write_stub_calibration(calib_path, valid=False)

    result = cf.run_probe(parquet_path, calib_path, out_path)

    assert result["num_samples"] == cf.DEFAULT_NUM_SAMPLES
    assert len(result["sample_directions"]) == cf.DEFAULT_NUM_SAMPLES
    assert all(d in ("up", "down", "neutral") for d in result["sample_directions"])


def _stub_pipeline_alternating(up_count: int, down_count: int, horizon: int = 5) -> MagicMock:
    """Return a pipeline that alternates between up-trending and down-trending samples.

    First `up_count` calls return p50 well above ibja_last; remaining return below.
    ibja_last from _make_ibja_series(25) ends around 14_200..14_600; we use p50
    values deliberately outside that range.
    """
    call_idx: list[int] = [0]
    mock = MagicMock()

    def fake_predict_quantiles(inputs, prediction_length, quantile_levels, **kwargs):
        idx = call_idx[0]
        call_idx[0] += 1
        n_q = len(quantile_levels)
        data = np.zeros((1, prediction_length, n_q), dtype=np.float32)
        if idx < up_count:
            # Up: p50 ~ 16_000 (well above any realistic ibja_last ~14k)
            base_values = [15_500.0, 16_000.0, 16_500.0]
        else:
            # Down: p50 ~ 12_000 (well below ibja_last ~14k)
            base_values = [11_500.0, 12_000.0, 12_500.0]
        for qi in range(n_q):
            data[0, :, qi] = base_values[qi]
        quantiles = torch.tensor(data)
        mean = torch.tensor(np.mean(data, axis=-1, keepdims=False))
        return quantiles, mean

    mock.predict_quantiles.side_effect = fake_predict_quantiles
    return mock


@patch("ml.chronos_forecast.load_chronos_pipeline")
def test_run_probe_single_sample_direction_up(mock_load, tmp_path):
    """Single-sample probe: pipeline returning 'up' forecast => majority_direction='up'.

    ADR 020: DEFAULT_NUM_SAMPLES=1; multi-sample alternating stubs are no longer relevant.
    """
    mock_load.return_value = _stub_pipeline_alternating(up_count=1, down_count=0)
    parquet_path = tmp_path / "ibja.parquet"
    calib_path = tmp_path / "calibration.json"
    out_path = tmp_path / "probe.json"

    _write_stub_parquet(parquet_path)
    _write_stub_calibration(calib_path, valid=False)

    result = cf.run_probe(parquet_path, calib_path, out_path)

    assert result["majority_direction"] == "up"
    assert result["num_samples"] == 1
    assert result["sample_directions"] == ["up"]


@patch("ml.chronos_forecast.load_chronos_pipeline")
def test_run_probe_single_sample_direction_down(mock_load, tmp_path):
    """Single-sample probe: pipeline returning 'down' forecast => majority_direction='down'."""
    mock_load.return_value = _stub_pipeline_alternating(up_count=0, down_count=1)
    parquet_path = tmp_path / "ibja.parquet"
    calib_path = tmp_path / "calibration.json"
    out_path = tmp_path / "probe.json"

    _write_stub_parquet(parquet_path)
    _write_stub_calibration(calib_path, valid=False)

    result = cf.run_probe(parquet_path, calib_path, out_path)

    assert result["majority_direction"] == "down"
    assert result["num_samples"] == 1
    assert result["sample_directions"] == ["down"]


@patch("ml.chronos_forecast.load_chronos_pipeline")
def test_direction_consensus_is_constant_1_0_on_success(mock_load, tmp_path):
    """direction_consensus must be exactly 1.0 on a successful probe — ADR 020 schema contract.

    Model is deterministic; any successful probe returning direction_consensus != 1.0 is a bug.
    This test is the Phi8A schema contract guard: if the field is ever re-wired to a computed
    non-1.0 value, this test will catch it.
    """
    mock_load.return_value = _stub_pipeline_alternating(up_count=1, down_count=0)
    parquet_path = tmp_path / "ibja.parquet"
    calib_path = tmp_path / "calibration.json"
    out_path = tmp_path / "probe.json"

    _write_stub_parquet(parquet_path)
    _write_stub_calibration(calib_path, valid=False)

    result = cf.run_probe(parquet_path, calib_path, out_path)

    assert result["status"] == "success"
    assert result["direction_consensus"] == 1.0, (
        "direction_consensus must be constant 1.0 on success (ADR 020: deterministic model). "
        "If this changed, a consumer has re-wired the field — review before merging."
    )


def _stub_pipeline_neutral(ibja_last: float, horizon: int = 5) -> MagicMock:
    """Return a pipeline whose p50 equals ibja_last exactly (produces 'neutral')."""
    mock = MagicMock()

    def fake_predict_quantiles(inputs, prediction_length, quantile_levels, **kwargs):
        n_q = len(quantile_levels)
        data = np.zeros((1, prediction_length, n_q), dtype=np.float32)
        # p10, p50, p90 all set to ibja_last so direction is exactly 0 => neutral
        for qi in range(n_q):
            data[0, :, qi] = ibja_last
        quantiles = torch.tensor(data)
        mean = torch.tensor(np.mean(data, axis=-1, keepdims=False))
        return quantiles, mean

    mock.predict_quantiles.side_effect = fake_predict_quantiles
    return mock


@patch("ml.chronos_forecast.load_chronos_pipeline")
def test_run_probe_all_neutral(mock_load, tmp_path):
    """Single neutral sample => majority_direction='neutral', direction_consensus=1.0."""
    parquet_path = tmp_path / "ibja.parquet"
    calib_path = tmp_path / "calibration.json"
    out_path = tmp_path / "probe.json"

    _write_stub_parquet(parquet_path)
    _write_stub_calibration(calib_path, valid=False)

    # Determine ibja_last_value by reading the parquet we just wrote
    series = _make_ibja_series(25)
    ibja_last = float(series.iloc[-1])  # pm_916 / 10 will be this value in run_probe
    mock_load.return_value = _stub_pipeline_neutral(ibja_last)

    result = cf.run_probe(parquet_path, calib_path, out_path)

    assert result["majority_direction"] == "neutral"
    assert result["direction_consensus"] == 1.0


def test_run_probe_insufficient_context_has_default_fields(tmp_path):
    """Failure path (insufficient_context) must expose new fields with defaults."""
    parquet_path = tmp_path / "ibja.parquet"
    calib_path = tmp_path / "calibration.json"
    out_path = tmp_path / "probe.json"

    _write_stub_parquet(parquet_path, n=7)  # below _MIN_CONTEXT_DAYS=8
    _write_stub_calibration(calib_path, valid=False)

    result = cf.run_probe(parquet_path, calib_path, out_path)

    assert result["status"] == "insufficient_context"
    assert result["num_samples"] == 0
    assert result["sample_directions"] == []
    assert result["majority_direction"] == "neutral"
    assert result["direction_consensus"] == 0.0


@patch("ml.chronos_forecast.load_chronos_pipeline")
def test_run_probe_wall_clock_sanity(mock_load, tmp_path):
    """wall_clock_ms total must be present, >0, and < 5000 with mocked pipeline."""
    mock_load.return_value = _stub_pipeline()
    parquet_path = tmp_path / "ibja.parquet"
    calib_path = tmp_path / "calibration.json"
    out_path = tmp_path / "probe.json"

    _write_stub_parquet(parquet_path)
    _write_stub_calibration(calib_path, valid=False)

    result = cf.run_probe(parquet_path, calib_path, out_path)

    total_ms = result["wall_clock_ms"]["total"]
    assert total_ms > 0
    assert total_ms < 5000  # generous bound for CI variability; mock should be <<100ms
