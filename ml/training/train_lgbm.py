"""LightGBM training entrypoint.

Usage:
    python -m ml.training.train_lgbm
    python -m ml.training.train_lgbm model.params.num_iterations=200
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import structlog

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# UTF-8 stdout so MLflow emoji log lines don't crash on narrow Windows consoles.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from ml.config import config_to_dict, flatten_for_mlflow, load_config
from ml.forecast import load_combined_history
from ml.logging_setup import configure_for_environment
from ml.macro import load_macro_features
from ml.models.lgbm import LightGBMForecaster
from ml.promotion import _PROMOTION_THRESHOLD
from ml.regime import add_regime_to_macro
from ml.tracking import MLflowTracker, get_git_sha


def _data_hash(history_len: int, macro_len: int) -> str:
    return hashlib.md5(f"{history_len}-{macro_len}".encode()).hexdigest()[:8]


def _query_sweep_params(cfg: object) -> tuple[list[str], str]:
    """Query MLflow for best params from the most recent Optuna LightGBM sweep.

    Returns (hydra_overrides, params_source). Falls back to ([], "config_defaults")
    when MLflow is unreachable or no sweep run exists.
    """
    try:
        import mlflow

        from ml.tracking import get_tracking_uri, is_mlflow_reachable

        uri = get_tracking_uri()
        if not is_mlflow_reachable(uri):
            return [], "config_defaults"

        mlflow.set_tracking_uri(uri)
        client = mlflow.tracking.MlflowClient()

        experiment_name = cfg.tracking.mlflow.experiment_training
        exp = client.get_experiment_by_name(experiment_name)
        if exp is None:
            return [], "config_defaults"

        runs = client.search_runs(
            experiment_ids=[exp.experiment_id],
            filter_string="tags.sweep_type = 'optuna' AND tags.model = 'lightgbm'",
            order_by=["start_time DESC"],
            max_results=1,
        )
        if not runs:
            return [], "config_defaults"

        best_run = runs[0]
        best_params = {
            k[len("best.") :]: v for k, v in best_run.data.params.items() if k.startswith("best.")
        }
        if not best_params:
            return [], "config_defaults"

        _FLOAT_PARAMS = {"learning_rate", "feature_fraction", "bagging_fraction"}
        _INT_PARAMS = {"num_leaves", "bagging_freq"}

        overrides = []
        for k, v in best_params.items():
            if k in _FLOAT_PARAMS:
                overrides.append(f"model.params.{k}={float(v)}")
            elif k in _INT_PARAMS:
                overrides.append(f"model.params.{k}={int(float(v))}")

        if not overrides:
            return [], "config_defaults"

        return overrides, f"sweep/{best_run.info.run_id[:8]}"

    except Exception as exc:
        structlog.get_logger().warning("lgbm.sweep_params.query_failed", error=str(exc))
        return [], "config_defaults"


def run_training(cfg: object, params_source: str = "config_defaults") -> dict:
    """Main training logic. Separated from main() so tests can call it directly."""
    configure_for_environment()
    log = structlog.get_logger()

    log.info("lgbm.data.loading")
    history = load_combined_history()
    log.info("lgbm.data.loaded", history_rows=len(history))

    macro = None
    try:
        macro = load_macro_features()
        if macro is not None:
            macro = add_regime_to_macro(macro)
            log.info("lgbm.macro.loaded", macro_rows=len(macro))
        else:
            log.warning("lgbm.macro.missing")
    except Exception as exc:
        log.warning("lgbm.macro.failed", error=str(exc))

    tracker = MLflowTracker(experiment_name=cfg.tracking.mlflow.experiment_training)
    flat_params = flatten_for_mlflow(config_to_dict(cfg))
    git_sha = get_git_sha()
    version = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{git_sha}"

    with tracker.run(
        run_name=f"lgbm-{git_sha}",
        tags={
            "model": "lightgbm",
            "git_sha": git_sha,
            "data_hash": _data_hash(len(history), len(macro) if macro is not None else 0),
            "training.params_source": params_source,
        },
    ) as run:
        run.log_params(flat_params)

        log.info("lgbm.fit.start", n_history=len(history))
        forecaster = LightGBMForecaster(cfg)
        train_meta = forecaster.fit(history, macro=macro)
        forecaster._version = version

        log.info(
            "lgbm.fit.complete",
            best_epoch=train_meta["best_epoch"],
            val_mae=train_meta["val_mae"],
            naive_mae=train_meta["naive_mae"],
            beats_naive=train_meta["beats_naive"],
            wall_clock_s=train_meta["wall_clock_s"],
        )

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
            }
        )

        # --- Save native checkpoint ---
        local_dir = ROOT / "models" / "local" / "lgbm" / f"v{version}"
        local_dir.mkdir(parents=True, exist_ok=True)
        forecaster.save_native(local_dir)
        log.info("lgbm.checkpoint.saved", path=str(local_dir))
        run.log_artifacts(local_dir, artifact_path="native")

        # --- Copy to production ---
        prod_dir = ROOT / "models" / "production"
        prod_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(local_dir / "lgbm-mean.txt", prod_dir / "lgbm.txt")
        shutil.copy(local_dir / "lgbm-p10.txt", prod_dir / "lgbm-p10.txt")
        shutil.copy(local_dir / "lgbm-p90.txt", prod_dir / "lgbm-p90.txt")
        log.info("lgbm.production.copied")

        # --- Metadata JSON ---
        meta = {
            **train_meta,
            "model_version": version,
            "trained_at": datetime.now(UTC).isoformat(),
            "git_sha": git_sha,
        }
        meta_path = prod_dir / "lgbm-meta.json"
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")
        run.log_artifact(meta_path)

        log.info("lgbm.training.complete", run_id=run.run_id, version=version)

        # --- Champion/challenger promotion ---
        if tracker.enabled and run.run_id:
            try:
                from ml.promotion import (
                    evaluate_promotion,
                    promote,
                    register_candidate,
                )

                candidate_version = register_candidate(
                    "lgbm", run.run_id, float(train_meta["val_mae"])
                )
                result = evaluate_promotion("lgbm", run.run_id, float(train_meta["val_mae"]))

                promotion_tags: dict = {
                    "promotion.decision": "PROMOTED" if result.promoted else "REJECTED",
                    "promotion.reason": result.reason,
                    "promotion.candidate_version": str(candidate_version),
                    "promotion.threshold": str(_PROMOTION_THRESHOLD),
                }
                if result.production_mae is not None:
                    promotion_tags["promotion.production_mae"] = str(result.production_mae)
                run.set_tags(promotion_tags)

                if result.promoted:
                    promote("lgbm", candidate_version)
                    log.info(
                        "lgbm.promoted",
                        version=candidate_version,
                        candidate_mae=result.candidate_mae,
                        reason=result.reason,
                    )
                else:
                    log.info(
                        "lgbm.rejected",
                        candidate_mae=result.candidate_mae,
                        production_mae=result.production_mae,
                        reason=result.reason,
                    )

            except Exception as exc:
                log.warning("lgbm.promotion.skipped", error=str(exc))

    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description="Train LightGBM forecaster")
    parser.add_argument(
        "overrides",
        nargs="*",
        default=[],
        help="Hydra overrides, e.g. model.params.num_iterations=200",
    )
    args = parser.parse_args()
    base_cfg = load_config(overrides=["model=lightgbm", *args.overrides])
    sweep_overrides, params_source = _query_sweep_params(base_cfg)
    if sweep_overrides:
        structlog.get_logger().info(
            "lgbm.sweep_params.applied",
            source=params_source,
            overrides=sweep_overrides,
        )
        cfg = load_config(overrides=["model=lightgbm", *args.overrides, *sweep_overrides])
    else:
        cfg = base_cfg
    run_training(cfg, params_source=params_source)


if __name__ == "__main__":
    main()
