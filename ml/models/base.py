"""Common forecaster interface and shared utilities."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass
class ForecastResult:
    point: float
    lower_q10: float
    upper_q90: float
    target_time: str  # ISO 8601
    feature_count: int
    model_version: str


class BaseForecaster(ABC):
    """All forecasters implement this interface."""

    name: str  # set by subclass

    @abstractmethod
    def fit(self, history: pd.DataFrame, macro: pd.DataFrame, **kwargs) -> dict[str, Any]:
        """Fit on history. Returns training metadata dict for MLflow logging."""

    @abstractmethod
    def predict(self, history: pd.DataFrame, macro: pd.DataFrame) -> ForecastResult:
        """Predict next reading."""

    @abstractmethod
    def export_onnx(self, path: Path) -> None:
        """Export to ONNX. Raises NotImplementedError for non-neural models (LightGBM)."""

    @abstractmethod
    def save_native(self, dir: Path) -> None:
        """Save native checkpoint (.pt for PyTorch, .lgbm for LightGBM)."""

    @classmethod
    @abstractmethod
    def load_native(cls, dir: Path) -> BaseForecaster:
        """Reload from native checkpoint."""
