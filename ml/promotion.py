"""Champion/challenger gate for gold-rate model promotion.

When a training run completes the sequence is:
  1. register_candidate() — creates a new MLflow Registry version from the run
  2. evaluate_promotion() — compares candidate val_MAE against production
  3. If promoted: promote() — transitions candidate to Production, archives prior
  4. If rejected: version stays in None/Staging stage; rejection reason logged

Edge cases handled:
  - No prior production version (first run): always promote
  - Production version missing val_mae tag: reject safely
  - Candidate MAE equal to production: reject (no improvement is not improvement)
  - MLflow Registry unreachable: raise RuntimeError, do NOT auto-promote

Promotion threshold: candidate_mae must be STRICTLY LESS THAN production_mae * 0.98.
  - 2.1% better → candidate < 0.98 × production → PROMOTED
  - exactly 2.0% better → candidate == 0.98 × production → REJECTED (strictly <)
  - any worse or equal → REJECTED
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import mlflow
import structlog
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

log = structlog.get_logger()

_MODEL_NAMES: dict[str, str] = {
    "lgbm": "gold-rate-lgbm",
    "tft": "gold-rate-tft",
    "nbeats": "gold-rate-nbeats",
    "ensemble": "gold-rate-ensemble",
}

_PROMOTION_THRESHOLD: float = 0.98  # candidate must be < production × this


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------


@dataclass
class PromotionResult:
    model_key: str
    model_name: str
    candidate_run_id: str
    candidate_mae: float
    production_mae: float | None
    promoted: bool
    reason: str
    candidate_version: int | None = field(default=None)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_production_version(client: MlflowClient, model_name: str):
    """Return the current Production model version, or None if none exists."""
    try:
        versions = client.get_latest_versions(model_name, stages=["Production"])
        return versions[0] if versions else None
    except MlflowException as exc:
        if "RESOURCE_DOES_NOT_EXIST" in str(exc):
            return None
        raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_promotion(
    model_key: str,
    candidate_run_id: str,
    candidate_mae: float,
) -> PromotionResult:
    """Compare candidate val_MAE against the current production version.

    Raises RuntimeError if MLflow Registry is unreachable (do NOT auto-promote).
    Returns a PromotionResult with promoted=True/False and a human-readable reason.
    """
    if model_key not in _MODEL_NAMES:
        raise ValueError(f"Unknown model key {model_key!r}. Must be one of {list(_MODEL_NAMES)}")

    model_name = _MODEL_NAMES[model_key]

    try:
        client = MlflowClient()
        prod_version = _get_production_version(client, model_name)
    except Exception as exc:
        raise RuntimeError(
            f"MLflow Registry unreachable for {model_name}: {exc}. "
            "Candidate metrics logged but NOT promoted — check server and retry."
        ) from exc

    # First-run: no prior production version
    if prod_version is None:
        return PromotionResult(
            model_key=model_key,
            model_name=model_name,
            candidate_run_id=candidate_run_id,
            candidate_mae=candidate_mae,
            production_mae=None,
            promoted=True,
            reason="No prior production version — first-run auto-promote",
        )

    # Read val_mae from the production version's tag
    prod_mae_tag = prod_version.tags.get("val_mae")
    if prod_mae_tag is None:
        return PromotionResult(
            model_key=model_key,
            model_name=model_name,
            candidate_run_id=candidate_run_id,
            candidate_mae=candidate_mae,
            production_mae=None,
            promoted=False,
            reason=(
                f"REJECTED: production v{prod_version.version} has no val_mae tag "
                "— cannot compare; rejecting to be safe"
            ),
        )

    production_mae = float(prod_mae_tag)
    threshold = production_mae * _PROMOTION_THRESHOLD
    pct_change = (production_mae - candidate_mae) / production_mae * 100

    if candidate_mae < threshold:
        reason = (
            f"PROMOTED: candidate {candidate_mae:.4f} < threshold {threshold:.4f} "
            f"({pct_change:.2f}% improvement over production {production_mae:.4f})"
        )
        return PromotionResult(
            model_key=model_key,
            model_name=model_name,
            candidate_run_id=candidate_run_id,
            candidate_mae=candidate_mae,
            production_mae=production_mae,
            promoted=True,
            reason=reason,
        )

    # Determine rejection flavour for a clear human-readable message
    if candidate_mae == production_mae:
        detail = "equal MAE — no improvement"
    elif candidate_mae > production_mae:
        detail = f"{abs(pct_change):.2f}% worse than production"
    else:
        detail = f"only {pct_change:.2f}% improvement — need strictly >2%"

    reason = (
        f"REJECTED: candidate {candidate_mae:.4f} >= threshold {threshold:.4f} "
        f"({detail}; production={production_mae:.4f})"
    )
    return PromotionResult(
        model_key=model_key,
        model_name=model_name,
        candidate_run_id=candidate_run_id,
        candidate_mae=candidate_mae,
        production_mae=production_mae,
        promoted=False,
        reason=reason,
    )


def register_candidate(
    model_key: str,
    run_id: str,
    val_mae: float,
    artifact_path: str = "native",
) -> int:
    """Register a training run as a new MLflow Registry version (stage=None).

    Tags the version with val_mae so evaluate_promotion can read it.
    Returns the integer version number.
    """
    model_name = _MODEL_NAMES[model_key]
    mv = mlflow.register_model(
        model_uri=f"runs:/{run_id}/{artifact_path}",
        name=model_name,
    )
    client = MlflowClient()
    client.set_model_version_tag(model_name, mv.version, "val_mae", str(val_mae))
    client.set_model_version_tag(model_name, mv.version, "source_run_id", run_id)
    client.set_model_version_tag(
        model_name, mv.version, "registered_at", datetime.now(UTC).isoformat()
    )
    log.info(
        "promotion.registered",
        model=model_name,
        version=mv.version,
        val_mae=val_mae,
    )
    return int(mv.version)


def promote(model_key: str, candidate_version: int) -> None:
    """Transition candidate to Production; archive the prior production version."""
    model_name = _MODEL_NAMES[model_key]
    client = MlflowClient()
    client.transition_model_version_stage(
        name=model_name,
        version=str(candidate_version),
        stage="Production",
        archive_existing_versions=True,
    )
    log.info("promotion.promoted", model=model_name, version=candidate_version)


def rollback(model_key: str, target_version: int | None = None) -> None:
    """Revert to the most-recent archived version, or a specific version."""
    model_name = _MODEL_NAMES[model_key]
    client = MlflowClient()

    if target_version is None:
        archived = client.get_latest_versions(model_name, stages=["Archived"])
        if not archived:
            raise RuntimeError(
                f"No archived versions found for {model_name} — cannot roll back"
            )
        target_version = max(int(v.version) for v in archived)

    client.transition_model_version_stage(
        name=model_name,
        version=str(target_version),
        stage="Production",
        archive_existing_versions=True,
    )
    log.info("promotion.rollback", model=model_name, version=target_version)


# ---------------------------------------------------------------------------
# One-time bootstrap registration
# ---------------------------------------------------------------------------


def bootstrap_register(
    prod_dir: Path,
    tracking_uri: str = "http://localhost:5001",
    experiment_name: str = "gold-rate-training",
) -> None:
    """Register existing production models in MLflow Registry as v1 / Production.

    Idempotent: skips any model that already has a Production version.
    """
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment_name)
    client = MlflowClient()

    models_cfg = {
        "lgbm": {
            "name": _MODEL_NAMES["lgbm"],
            "meta": "lgbm-meta.json",
            "artifacts": ["lgbm.txt", "lgbm-p10.txt", "lgbm-p90.txt"],
        },
        "tft": {
            "name": _MODEL_NAMES["tft"],
            "meta": "tft-meta.json",
            "artifacts": ["tft.onnx"],
        },
        "nbeats": {
            "name": _MODEL_NAMES["nbeats"],
            "meta": "nbeats-meta.json",
            "artifacts": ["nbeats.onnx"],
        },
    }

    for model_key, cfg in models_cfg.items():
        model_name = cfg["name"]

        try:
            existing = client.get_latest_versions(model_name, stages=["Production"])
        except MlflowException:
            existing = []

        if existing:
            print(f"  {model_name}: Production v{existing[0].version} already exists — skipping")
            continue

        meta = json.loads((prod_dir / cfg["meta"]).read_text())
        val_mae = float(meta["val_mae"])
        print(f"  Registering {model_name} (val_mae={val_mae})...")

        with mlflow.start_run(run_name=f"bootstrap-{model_key}") as run:
            mlflow.set_tags({
                "model": model_key,
                "bootstrap": "true",
                "source": "production_meta_json",
            })
            mlflow.log_metrics({"val_mae_rupees": val_mae})
            mlflow.log_params({"source": "bootstrap", "val_mae": val_mae})

            for fname in cfg["artifacts"]:
                p = prod_dir / fname
                if p.exists():
                    mlflow.log_artifact(str(p), artifact_path="native")
            mlflow.log_artifact(str(prod_dir / cfg["meta"]), artifact_path="native")

            run_id = run.info.run_id

        mv = mlflow.register_model(
            model_uri=f"runs:/{run_id}/native",
            name=model_name,
        )
        client.set_model_version_tag(model_name, mv.version, "val_mae", str(val_mae))
        client.set_model_version_tag(model_name, mv.version, "bootstrap", "true")
        client.set_model_version_tag(
            model_name, mv.version, "trained_at", meta.get("trained_at", "")
        )
        client.transition_model_version_stage(
            name=model_name,
            version=mv.version,
            stage="Production",
            archive_existing_versions=True,
        )
        print(f"    ✓ {model_name} v{mv.version} → Production")

    print("Bootstrap complete.")


# ---------------------------------------------------------------------------
# CLI: python -m ml.promotion bootstrap
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Promotion utilities")
    sub = parser.add_subparsers(dest="cmd")

    bp = sub.add_parser("bootstrap", help="Register existing production models in MLflow Registry")
    bp.add_argument("--prod-dir", default="models/production")
    bp.add_argument("--tracking-uri", default="http://localhost:5001")

    rb = sub.add_parser("rollback", help="Roll back a model to the previous version")
    rb.add_argument("model_key", choices=list(_MODEL_NAMES))
    rb.add_argument("--version", type=int, default=None)
    rb.add_argument("--tracking-uri", default="http://localhost:5001")

    args = parser.parse_args()

    if args.cmd == "bootstrap":
        from ml.logging_setup import configure_for_environment
        configure_for_environment()
        print("Bootstrapping MLflow Model Registry...")
        bootstrap_register(
            prod_dir=ROOT / args.prod_dir,
            tracking_uri=args.tracking_uri,
        )
    elif args.cmd == "rollback":
        mlflow.set_tracking_uri(args.tracking_uri)
        rollback(args.model_key, target_version=args.version)
        print(f"Rolled back {args.model_key} to version {args.version or '(latest archived)'}")
    else:
        parser.print_help()
