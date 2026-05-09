"""Tests for ml/tracking.py — MLflow tracking utilities."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
import requests
from ml.tracking import (
    MLflowRunHandle,
    MLflowTracker,
    get_tracking_uri,
    is_mlflow_reachable,
)

MLFLOW_URI = "http://localhost:5001"


# ---------------------------------------------------------------------------
# get_tracking_uri
# ---------------------------------------------------------------------------


def test_get_tracking_uri_default():
    env = {k: v for k, v in os.environ.items() if k != "MLFLOW_TRACKING_URI"}
    with patch.dict(os.environ, env, clear=True):
        uri = get_tracking_uri()
    assert uri == "http://localhost:5001"


def test_get_tracking_uri_respects_env_var():
    with patch.dict(os.environ, {"MLFLOW_TRACKING_URI": "http://custom:9999"}):
        uri = get_tracking_uri()
    assert uri == "http://custom:9999"


# ---------------------------------------------------------------------------
# is_mlflow_reachable
# ---------------------------------------------------------------------------


def test_is_mlflow_reachable_closed_port():
    # Port 1 is never open; should return False quickly
    assert is_mlflow_reachable("http://127.0.0.1:1", timeout=0.5) is False


def test_is_mlflow_reachable_bad_uri():
    assert is_mlflow_reachable("not-a-valid-uri", timeout=0.5) is False


def test_is_mlflow_reachable_unreachable_host():
    assert is_mlflow_reachable("http://192.0.2.1:5001", timeout=0.3) is False


# ---------------------------------------------------------------------------
# MLflowTracker — disabled (no-op) mode
# ---------------------------------------------------------------------------


def test_tracker_disabled_skips_mlflow_setup():
    import mlflow

    with (
        patch.object(mlflow, "set_tracking_uri") as mock_uri,
        patch.object(mlflow, "set_experiment") as mock_exp,
    ):
        MLflowTracker("test-exp", enabled=False)
        mock_uri.assert_not_called()
        mock_exp.assert_not_called()


def test_tracker_disabled_run_yields_noop_handle():
    tracker = MLflowTracker("test-exp", enabled=False)
    with tracker.run("my-run") as handle:
        assert isinstance(handle, MLflowRunHandle)
        assert handle.active is False
        assert handle.run_id is None


def test_tracker_disabled_run_noop_does_not_start_mlflow_run():
    import mlflow

    tracker = MLflowTracker("test-exp", enabled=False)
    with patch.object(mlflow, "start_run") as mock_start:
        with tracker.run("my-run"):
            pass
        mock_start.assert_not_called()


# ---------------------------------------------------------------------------
# MLflowRunHandle — no-op when active=False
# ---------------------------------------------------------------------------


def test_run_handle_inactive_log_params_no_call():
    import mlflow

    handle = MLflowRunHandle(active=False)
    with patch.object(mlflow, "log_params") as mock_fn:
        handle.log_params({"lr": 0.001})
        mock_fn.assert_not_called()


def test_run_handle_inactive_log_metrics_no_call():
    import mlflow

    handle = MLflowRunHandle(active=False)
    with patch.object(mlflow, "log_metrics") as mock_fn:
        handle.log_metrics({"val_loss": 1.23})
        mock_fn.assert_not_called()


def test_run_handle_inactive_set_tags_no_call():
    import mlflow

    handle = MLflowRunHandle(active=False)
    with patch.object(mlflow, "set_tags") as mock_fn:
        handle.set_tags({"model": "nbeats"})
        mock_fn.assert_not_called()


def test_run_handle_inactive_log_artifact_no_call():
    from pathlib import Path

    import mlflow

    handle = MLflowRunHandle(active=False)
    with patch.object(mlflow, "log_artifact") as mock_fn:
        handle.log_artifact(Path("some/file.json"))
        mock_fn.assert_not_called()


# ---------------------------------------------------------------------------
# Integration test — requires MLflow at localhost:5001
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_integration_log_and_retrieve_run():
    """Start a run, log params/metrics/tags, verify via REST API."""
    if not is_mlflow_reachable(MLFLOW_URI):
        pytest.skip(f"MLflow not reachable at {MLFLOW_URI}")

    tracker = MLflowTracker(
        "test-experiment",
        tracking_uri=MLFLOW_URI,
        enabled=True,
    )
    assert tracker.enabled, "Tracker should be enabled when MLflow is reachable"

    run_id = None
    with tracker.run("integration-test-run", tags={"source": "pytest"}) as handle:
        assert handle.active
        run_id = handle.run_id
        handle.log_params({"lr": "0.001", "batch_size": "32"})
        handle.log_metrics({"val_loss": 0.456}, step=1)
        handle.set_tags({"phase": "C-prime", "model": "test"})

    assert run_id is not None, "run_id must be set after context exit"

    # Verify the run persisted via MLflow REST API
    resp = requests.get(
        f"{MLFLOW_URI}/api/2.0/mlflow/runs/get",
        params={"run_id": run_id},
        timeout=5,
    )
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}: {resp.text}"

    run_data = resp.json()["run"]
    params = {p["key"]: p["value"] for p in run_data["data"].get("params", [])}
    metrics = {m["key"]: m["value"] for m in run_data["data"].get("metrics", [])}
    tags = {t["key"]: t["value"] for t in run_data["data"].get("tags", [])}

    assert params.get("lr") == "0.001", f"Expected lr=0.001, got {params}"
    assert params.get("batch_size") == "32"
    assert "val_loss" in metrics
    assert pytest.approx(metrics["val_loss"], abs=1e-6) == 0.456
    assert tags.get("source") == "pytest"
    assert tags.get("phase") == "C-prime"

    print(f"\n  run_id : {run_id}")
    print(f"  UI     : {MLFLOW_URI}/#/experiments/1/runs/{run_id}")
