"""N-BEATS training entrypoint.

Usage:
    python -m ml.training.train_nbeats
    python -m ml.training.train_nbeats model.params.num_stacks=4
    python -m ml.training.train_nbeats training.max_epochs=10 model.trainer.precision=32
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import structlog

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from ml.config import config_to_dict, flatten_for_mlflow, load_config
from ml.forecast import load_combined_history
from ml.logging_setup import configure_for_environment
from ml.models.nbeats import NBeatsForecaster
from ml.tracking import MLflowTracker, get_git_sha
from ml.training.callbacks import GPUStepCallback
from ml.training.utils import gpu_snapshot, verify_pytorch_onnx_parity


def _data_hash(history_len: int) -> str:
    return hashlib.md5(f"{history_len}".encode()).hexdigest()[:8]


def run_training(cfg: object) -> dict:
    """Main training logic. Separated from main() so tests can call it directly."""
    configure_for_environment()
    log = structlog.get_logger()

    # --- Load data ---
    log.info("nbeats.data.loading")
    history = load_combined_history()
    log.info("nbeats.data.loaded", history_rows=len(history))

    # --- Apply training.max_epochs override ---
    max_epochs = getattr(cfg, "training", None)
    if max_epochs is not None:
        max_epochs = getattr(max_epochs, "max_epochs", None)
    if max_epochs is not None:
        try:
            from omegaconf import OmegaConf

            OmegaConf.update(cfg, "model.params.n_epochs", int(max_epochs), merge=True)
            log.info("nbeats.max_epochs.override", max_epochs=int(max_epochs))
        except Exception:
            pass

    # --- MLflow ---
    tracker = MLflowTracker(experiment_name=cfg.tracking.mlflow.experiment_training)
    flat_params = flatten_for_mlflow(config_to_dict(cfg))
    gpu_info = gpu_snapshot()
    git_sha = get_git_sha()
    version = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{git_sha}"

    with tracker.run(
        run_name=f"nbeats-{git_sha}",
        tags={
            "model": "nbeats",
            "git_sha": git_sha,
            "data_hash": _data_hash(len(history)),
            **{f"gpu.{k}": str(v) for k, v in gpu_info.items()},
        },
    ) as run:
        run.log_params(flat_params)

        # --- Fit ---
        log.info("nbeats.fit.start", n_history=len(history))
        logs_dir = ROOT / "logs"
        gpu_cb = GPUStepCallback("nbeats", logs_dir, run_ts=version)
        forecaster = NBeatsForecaster(cfg)
        train_meta = forecaster.fit(history, macro=None, extra_callbacks=[gpu_cb])
        forecaster._version = version

        log.info(
            "nbeats.fit.complete",
            best_epoch=train_meta["best_epoch"],
            val_mae=train_meta["val_mae"],
            naive_mae=train_meta["naive_mae"],
            beats_naive=train_meta["beats_naive"],
            wall_clock_s=train_meta["wall_clock_s"],
        )

        gpu_summary = gpu_cb.summary()
        run.log_metrics(
            {
                "val_mae_rupees": float(train_meta["val_mae"]),
                "naive_baseline_mae": float(train_meta["naive_mae"]),
                "best_epoch": float(train_meta["best_epoch"]),
                "epochs_run": float(train_meta["epochs_run"]),
                "training_wall_clock_seconds": float(train_meta["wall_clock_s"]),
                "n_train": float(train_meta["n_train"]),
                "n_val": float(train_meta["n_val"]),
                "model_beats_naive": float(train_meta["beats_naive"]),
                "feature_count": float(train_meta["feature_count"]),
                **{k: float(v) for k, v in gpu_summary.items()},
            }
        )
        log.info("nbeats.gpu.summary", **gpu_summary)

        # --- Save native checkpoint ---
        local_dir = ROOT / "models" / "local" / "nbeats" / f"v{version}"
        local_dir.mkdir(parents=True, exist_ok=True)
        forecaster.save_native(local_dir)
        log.info("nbeats.checkpoint.saved", path=str(local_dir))
        run.log_artifacts(local_dir, artifact_path="native")

        # --- ONNX export ---
        onnx_path = ROOT / "models" / "production" / "nbeats.onnx"
        onnx_path.parent.mkdir(parents=True, exist_ok=True)
        forecaster.export_onnx(onnx_path)
        onnx_size_kb = onnx_path.stat().st_size / 1024
        log.info("nbeats.onnx.exported", size_kb=round(onnx_size_kb, 1))
        run.log_artifact(onnx_path, artifact_path="onnx")

        # --- Parity check (N-BEATS: past_input only, no future) ---
        rng = np.random.default_rng(0)
        n_past = forecaster._n_past_total
        icl = forecaster._icl
        x0 = rng.standard_normal((4, icl, n_past)).astype(np.float32)

        pt_preds = forecaster.predict_batch_native(x0)
        onnx_preds = forecaster.predict_batch_onnx(onnx_path, x0)
        parity = verify_pytorch_onnx_parity(pt_preds, onnx_preds)
        log.info("nbeats.parity.ok", max_abs_diff=parity["max_abs_diff"])
        run.log_metrics({"onnx_max_abs_diff": parity["max_abs_diff"]})

        # --- Save GPU utilization logs ---
        logs_dir.mkdir(exist_ok=True)
        gpu_log_path = logs_dir / "gpu-utilization-nbeats.txt"
        gpu_log_path.write_text(json.dumps(gpu_info, indent=2))
        run.log_artifact(gpu_log_path)
        if gpu_cb.csv_path.exists():
            run.log_artifact(gpu_cb.csv_path, artifact_path="gpu")
            log.info(
                "nbeats.gpu.csv.logged",
                path=str(gpu_cb.csv_path),
                steps=len(gpu_cb._rows),
            )

        # --- Metadata JSON ---
        meta = {
            **train_meta,
            "model_version": version,
            "onnx_path": str(onnx_path),
            "onnx_size_kb": round(onnx_size_kb, 1),
            "onnx_max_abs_diff": parity["max_abs_diff"],
            "trained_at": datetime.now(UTC).isoformat(),
            "git_sha": git_sha,
        }
        meta_path = ROOT / "models" / "production" / "nbeats-meta.json"
        meta_path.write_text(json.dumps(meta, indent=2))
        run.log_artifact(meta_path)

        log.info("nbeats.training.complete", run_id=run.run_id, version=version)

    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description="Train N-BEATS forecaster")
    parser.add_argument(
        "overrides",
        nargs="*",
        default=[],
        help="Hydra overrides, e.g. model.params.num_stacks=4 training.max_epochs=10",
    )
    args = parser.parse_args()
    cfg = load_config(overrides=["model=nbeats", *args.overrides])
    run_training(cfg)


if __name__ == "__main__":
    main()
