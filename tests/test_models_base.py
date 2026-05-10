"""
Unit tests for ml/models/base.py.

Tests:
  1. ForecastResult dataclass — field access, default construction
  2. BaseForecaster — cannot instantiate abstract class
  3. BaseForecaster — concrete subclass satisfies interface
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from ml.models.base import BaseForecaster, ForecastResult


# ---------------------------------------------------------------------------
# 1. ForecastResult
# ---------------------------------------------------------------------------


class TestForecastResult:
    def test_construction(self):
        r = ForecastResult(
            point=7200.0,
            lower_q10=7100.0,
            upper_q90=7300.0,
            target_time="2024-06-01T00:00:00+00:00",
            feature_count=43,
            model_version="tft-20240601",
        )
        assert r.point == 7200.0
        assert r.lower_q10 == 7100.0
        assert r.upper_q90 == 7300.0
        assert r.feature_count == 43

    def test_fields_are_accessible(self):
        r = ForecastResult(1.0, 0.5, 1.5, "2024-01-01T00:00:00Z", 10, "v1")
        assert hasattr(r, "point")
        assert hasattr(r, "lower_q10")
        assert hasattr(r, "upper_q90")
        assert hasattr(r, "target_time")
        assert hasattr(r, "feature_count")
        assert hasattr(r, "model_version")


# ---------------------------------------------------------------------------
# 2. BaseForecaster is abstract
# ---------------------------------------------------------------------------


class TestBaseForecasterIsAbstract:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            BaseForecaster()  # type: ignore[abstract]

    def test_partial_subclass_still_abstract(self):
        class PartialImpl(BaseForecaster):
            name = "partial"

            def fit(self, history, macro, **kwargs):
                return {}

        with pytest.raises(TypeError):
            PartialImpl()


# ---------------------------------------------------------------------------
# 3. Concrete subclass satisfies interface
# ---------------------------------------------------------------------------


class _ConcreteForecaster(BaseForecaster):
    """Minimal implementation for testing only."""

    name = "test_forecaster"

    def fit(self, history: pd.DataFrame, macro: pd.DataFrame, **kwargs) -> dict[str, Any]:
        return {"n_train": len(history)}

    def predict(self, history: pd.DataFrame, macro: pd.DataFrame) -> ForecastResult:
        return ForecastResult(
            point=0.0,
            lower_q10=-1.0,
            upper_q90=1.0,
            target_time="2024-01-01T00:00:00Z",
            feature_count=0,
            model_version="test-v1",
        )

    def export_onnx(self, path: Path) -> None:
        raise NotImplementedError("test model has no ONNX export")

    def save_native(self, dir: Path) -> None:
        pass

    @classmethod
    def load_native(cls, dir: Path) -> "_ConcreteForecaster":
        return cls()


class TestConcreteForecaster:
    def test_instantiation(self):
        f = _ConcreteForecaster()
        assert f.name == "test_forecaster"

    def test_fit_returns_dict(self):
        f = _ConcreteForecaster()
        history = pd.DataFrame({"22k": [6000, 6100, 6200]})
        macro = pd.DataFrame()
        result = f.fit(history, macro)
        assert isinstance(result, dict)
        assert result["n_train"] == 3

    def test_predict_returns_forecast_result(self):
        f = _ConcreteForecaster()
        history = pd.DataFrame({"22k": [6000]})
        macro = pd.DataFrame()
        result = f.predict(history, macro)
        assert isinstance(result, ForecastResult)

    def test_export_onnx_raises_not_implemented(self):
        f = _ConcreteForecaster()
        with pytest.raises(NotImplementedError):
            f.export_onnx(Path("/tmp/test.onnx"))

    def test_load_native_returns_instance(self):
        loaded = _ConcreteForecaster.load_native(Path("/tmp"))
        assert isinstance(loaded, _ConcreteForecaster)
