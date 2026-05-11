"""Lightning callbacks for training infrastructure."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytorch_lightning as pl
import torch


class GPUStepCallback(pl.Callback):
    """Records GPU memory and utilization at every training step.

    Writes logs/gpu-utilization-{model_name}-{run_ts}.csv incrementally.
    Call .summary() after training to get MLflow-ready aggregate metrics.
    """

    FIELDS: ClassVar[list[str]] = [
        "step",
        "timestamp_iso",
        "memory_allocated_mb",
        "memory_reserved_mb",
        "gpu_util_pct",
    ]

    def __init__(self, model_name: str, logs_dir: Path, run_ts: str | None = None) -> None:
        super().__init__()
        self.model_name = model_name
        self.logs_dir = Path(logs_dir)
        self.run_ts = run_ts or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        self._csv_path = self.logs_dir / f"gpu-utilization-{model_name}-{self.run_ts}.csv"
        self._rows: list[dict[str, Any]] = []
        self._nvml_handle: Any = None
        self._nvml_ok = False
        self._step = 0

    def setup(self, trainer: Any, pl_module: Any, stage: str | None = None) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        try:
            import pynvml

            pynvml.nvmlInit()
            self._nvml_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            self._nvml_ok = True
        except Exception:
            self._nvml_ok = False
        with open(self._csv_path, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=self.FIELDS).writeheader()

    def _gpu_util(self) -> float:
        if not self._nvml_ok:
            return 0.0
        try:
            import pynvml

            rates = pynvml.nvmlDeviceGetUtilizationRates(self._nvml_handle)
            return float(rates.gpu)
        except Exception:
            return 0.0

    def teardown(self, trainer: Any, pl_module: Any, stage: str | None = None) -> None:
        """Release pynvml handle so the callback is picklable after training."""
        if self._nvml_ok:
            try:
                import pynvml

                pynvml.nvmlShutdown()
            except Exception:
                pass
        self._nvml_handle = None
        self._nvml_ok = False

    def on_train_batch_end(
        self, trainer: Any, pl_module: Any, outputs: Any, batch: Any, batch_idx: int
    ) -> None:
        self._step += 1
        cuda_ok = torch.cuda.is_available()
        row = {
            "step": self._step,
            "timestamp_iso": datetime.now(UTC).isoformat(),
            "memory_allocated_mb": round(torch.cuda.memory_allocated(0) / 1e6, 2)
            if cuda_ok
            else 0.0,
            "memory_reserved_mb": round(torch.cuda.memory_reserved(0) / 1e6, 2) if cuda_ok else 0.0,
            "gpu_util_pct": round(self._gpu_util(), 1),
        }
        self._rows.append(row)
        with open(self._csv_path, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=self.FIELDS).writerow(row)

    @property
    def csv_path(self) -> Path:
        return self._csv_path

    def summary(self) -> dict[str, float]:
        """Aggregate statistics suitable for MLflow metrics logging."""
        if not self._rows:
            return {
                "gpu_memory_peak_mb": 0.0,
                "gpu_memory_mean_mb": 0.0,
                "gpu_util_peak_pct": 0.0,
                "gpu_util_mean_pct": 0.0,
            }
        mem = [r["memory_allocated_mb"] for r in self._rows]
        util = [r["gpu_util_pct"] for r in self._rows]
        return {
            "gpu_memory_peak_mb": round(max(mem), 2),
            "gpu_memory_mean_mb": round(sum(mem) / len(mem), 2),
            "gpu_util_peak_pct": round(max(util), 1),
            "gpu_util_mean_pct": round(sum(util) / len(util), 1),
        }
