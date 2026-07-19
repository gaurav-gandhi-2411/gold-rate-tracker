"""Tests for ml.sources.ibja — reads real files via tmp_path, no live network."""

from __future__ import annotations

import json
import re

import pandas as pd
import pytest
from ml.sources.base import SourceStructureError
from ml.sources.ibja import fetch_ibja_calibrated


def _write_calibration(tmp_path, *, valid=True, slope=1.02, intercept=50.0):
    payload = {"valid": valid, "slope": slope, "intercept": intercept, "residual_std": 120.0}
    (tmp_path / "calibration.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_ibja_parquet(tmp_path, *, pm_916=13000.0, date="2026-07-18"):
    df = pd.DataFrame([{"date": date, "pm_916": pm_916, "am_916": pm_916 - 20}])
    df.to_parquet(tmp_path / "ibja_rates.parquet", index=False)


def test_happy_path_computes_calibrated_value(tmp_path):
    _write_calibration(tmp_path, slope=1.0, intercept=0.0)
    _write_ibja_parquet(tmp_path, pm_916=131350.0, date="2026-07-18")  # per 10g
    reading = fetch_ibja_calibrated(data_dir=tmp_path)
    # ibja_per_g = 131350/10 = 13135; calibrated = 1.0*13135 + 0.0 = 13135
    assert reading.rate_22k == 13135.0
    assert reading.source == "ibja"
    assert reading.city is None


def test_slope_and_intercept_applied(tmp_path):
    _write_calibration(tmp_path, slope=1.05, intercept=-200.0)
    _write_ibja_parquet(tmp_path, pm_916=130000.0, date="2026-07-18")
    reading = fetch_ibja_calibrated(data_dir=tmp_path)
    expected = round(1.05 * 13000.0 - 200.0)
    assert reading.rate_22k == float(expected)


def test_missing_calibration_file_raises(tmp_path):
    _write_ibja_parquet(tmp_path)
    with pytest.raises(SourceStructureError, match=re.escape("calibration.json not found")):
        fetch_ibja_calibrated(data_dir=tmp_path)


def test_invalid_json_calibration_raises(tmp_path):
    (tmp_path / "calibration.json").write_text("{not json", encoding="utf-8")
    _write_ibja_parquet(tmp_path)
    with pytest.raises(SourceStructureError, match="not valid JSON"):
        fetch_ibja_calibrated(data_dir=tmp_path)


def test_calibration_valid_false_raises(tmp_path):
    _write_calibration(tmp_path, valid=False)
    _write_ibja_parquet(tmp_path)
    with pytest.raises(SourceStructureError, match="valid is False"):
        fetch_ibja_calibrated(data_dir=tmp_path)


def test_calibration_missing_slope_raises(tmp_path):
    payload = {"valid": True, "intercept": 0.0, "residual_std": 100.0}
    (tmp_path / "calibration.json").write_text(json.dumps(payload), encoding="utf-8")
    _write_ibja_parquet(tmp_path)
    with pytest.raises(SourceStructureError, match="slope/intercept"):
        fetch_ibja_calibrated(data_dir=tmp_path)


def test_missing_parquet_raises(tmp_path):
    _write_calibration(tmp_path)
    with pytest.raises(SourceStructureError, match=re.escape("ibja_rates.parquet not found")):
        fetch_ibja_calibrated(data_dir=tmp_path)


def test_empty_pm_916_column_raises(tmp_path):
    _write_calibration(tmp_path)
    df = pd.DataFrame([{"date": "2026-07-18", "pm_916": None, "am_916": 100.0}])
    df.to_parquet(tmp_path / "ibja_rates.parquet", index=False)
    with pytest.raises(SourceStructureError, match="no non-null pm_916"):
        fetch_ibja_calibrated(data_dir=tmp_path)


def test_latest_row_by_date_used(tmp_path):
    _write_calibration(tmp_path, slope=1.0, intercept=0.0)
    df = pd.DataFrame(
        [
            {"date": "2026-07-10", "pm_916": 100000.0, "am_916": 99000.0},
            {"date": "2026-07-18", "pm_916": 131350.0, "am_916": 131000.0},
        ]
    )
    df.to_parquet(tmp_path / "ibja_rates.parquet", index=False)
    reading = fetch_ibja_calibrated(data_dir=tmp_path)
    assert reading.rate_22k == 13135.0
    assert reading.observed_at.day == 18
