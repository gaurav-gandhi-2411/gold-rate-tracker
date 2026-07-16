"""Shared utilities for training: target normalizer, parity check, GPU monitor."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class TargetNormalizer:
    """Z-score normalizer for the regression target. Saved alongside the model."""

    mean: float
    std: float
    fitted_on_n: int
    method: str = "z-score"

    def normalize(self, x: np.ndarray) -> np.ndarray:
        return (x - self.mean) / self.std

    def denormalize(self, x: np.ndarray) -> np.ndarray:
        return x * self.std + self.mean

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> TargetNormalizer:
        return cls(**json.loads(path.read_text()))

    @classmethod
    def fit(cls, y: np.ndarray) -> TargetNormalizer:
        return cls(mean=float(y.mean()), std=float(y.std()), fitted_on_n=len(y))


def compute_naive_baseline_mae(y_true: np.ndarray, prev_y: np.ndarray) -> float:
    """MAE of predicting prev_y as next y. The 'predict last value' baseline."""
    return float(np.mean(np.abs(y_true - prev_y)))


def compute_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """Standard regression metrics."""
    abs_err = np.abs(y_true - y_pred)
    pct_err = np.where(y_true != 0, abs_err / np.abs(y_true), 0)
    direction_correct = np.mean(np.sign(y_true) == np.sign(y_pred))
    return {
        "mae": float(abs_err.mean()),
        "mape": float(pct_err.mean() * 100),
        "rmse": float(np.sqrt(((y_true - y_pred) ** 2).mean())),
        "direction_accuracy": float(direction_correct),
        "n_samples": len(y_true),
    }


def gpu_snapshot() -> dict[str, Any]:
    """Capture current GPU state for logging. Returns dict if no GPU available."""
    try:
        import torch

        if not torch.cuda.is_available():
            return {"cuda_available": False}
        return {
            "cuda_available": True,
            "device_name": torch.cuda.get_device_name(0),
            "memory_allocated_mb": torch.cuda.memory_allocated(0) / 1e6,
            "memory_reserved_mb": torch.cuda.memory_reserved(0) / 1e6,
            "torch_version": torch.__version__,
        }
    except ImportError:
        return {"cuda_available": False, "torch_installed": False}


def verify_pytorch_onnx_parity(
    pt_predictions: np.ndarray,
    onnx_predictions: np.ndarray,
    tolerance: float = 1e-3,
) -> dict[str, float]:
    """Verify ONNX export hasn't corrupted predictions. Raises ValueError on failure."""
    diff = np.abs(pt_predictions - onnx_predictions)
    max_diff = float(diff.max())
    mean_diff = float(diff.mean())
    if max_diff > tolerance:
        raise ValueError(
            f"ONNX/PyTorch parity failure: max_abs_diff={max_diff} > tolerance={tolerance}. "
            "ONNX export is corrupted. Common causes: unsupported ops, wrong opset, "
            "fp16 numerical drift."
        )
    return {"max_abs_diff": max_diff, "mean_abs_diff": mean_diff, "tolerance": tolerance}
