"""MLflow tracking utilities for gold-rate-tracker.

A single MLflowTracker class exposes a `run` context manager that logs
params, metrics, artifacts, and tags atomically. Falls back to a no-op
logger when MLflow is unreachable, so training never fails because tracking
failed.
"""

from __future__ import annotations

import os
import socket
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import mlflow
import structlog

log = structlog.get_logger()


def get_tracking_uri() -> str:
    """Determine MLflow tracking URI from env, with sensible defaults.

    Priority:
    1. MLFLOW_TRACKING_URI env var
    2. http://localhost:5001 (default — port 5001 avoids conflicts with other
       local MLflow instances on the standard 5000)
    """
    return os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5001")


def is_mlflow_reachable(uri: str, timeout: float = 2.0) -> bool:
    """Check if MLflow server responds. Used to gracefully degrade in CI."""
    try:
        if "://" in uri:
            uri = uri.split("://", 1)[1]
        host, _, port_str = uri.partition(":")
        port = int(port_str) if port_str else 5001
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


class MLflowTracker:
    """MLflow tracking interface with graceful no-op fallback."""

    def __init__(
        self,
        experiment_name: str,
        tracking_uri: str | None = None,
        enabled: bool | None = None,
    ):
        self.tracking_uri = tracking_uri or get_tracking_uri()
        self.experiment_name = experiment_name

        if enabled is None:
            enabled = is_mlflow_reachable(self.tracking_uri)

        self.enabled = enabled

        if self.enabled:
            mlflow.set_tracking_uri(self.tracking_uri)
            mlflow.set_experiment(experiment_name)
            log.info("mlflow.enabled", uri=self.tracking_uri, experiment=experiment_name)
        else:
            log.warning(
                "mlflow.disabled",
                reason="server unreachable",
                uri=self.tracking_uri,
            )

    @contextmanager
    def run(
        self,
        run_name: str,
        tags: dict[str, Any] | None = None,
        nested: bool = False,
    ) -> Iterator["MLflowRunHandle"]:
        """Context manager. If MLflow is disabled, yields a no-op handle."""
        if not self.enabled:
            yield MLflowRunHandle(active=False)
            return

        with mlflow.start_run(run_name=run_name, nested=nested) as active_run:
            if tags:
                mlflow.set_tags(tags)
            handle = MLflowRunHandle(active=True, run_id=active_run.info.run_id)
            log.info("mlflow.run.start", run_id=handle.run_id, run_name=run_name)
            try:
                yield handle
            finally:
                log.info("mlflow.run.end", run_id=handle.run_id, run_name=run_name)


class MLflowRunHandle:
    """Wraps a single MLflow run. No-op when active=False."""

    def __init__(self, active: bool, run_id: str | None = None):
        self.active = active
        self.run_id = run_id

    def log_params(self, params: dict[str, Any]) -> None:
        if not self.active:
            return
        safe = {k: str(v)[:250] for k, v in params.items()}
        mlflow.log_params(safe)

    def log_metrics(self, metrics: dict[str, float], step: int | None = None) -> None:
        if not self.active:
            return
        mlflow.log_metrics(metrics, step=step)

    def log_artifact(self, local_path: Path, artifact_path: str | None = None) -> None:
        if not self.active:
            return
        mlflow.log_artifact(str(local_path), artifact_path=artifact_path)

    def log_artifacts(self, local_dir: Path, artifact_path: str | None = None) -> None:
        if not self.active:
            return
        mlflow.log_artifacts(str(local_dir), artifact_path=artifact_path)

    def set_tags(self, tags: dict[str, Any]) -> None:
        if not self.active:
            return
        mlflow.set_tags({k: str(v) for k, v in tags.items()})


def get_git_sha() -> str:
    """Return short git SHA for tagging runs."""
    import subprocess
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"
