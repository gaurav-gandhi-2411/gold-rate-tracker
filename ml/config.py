"""Hydra config loading helpers.

Usage from a script:
    from ml.config import load_config
    cfg = load_config(overrides=["model=lightgbm"])
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hydra import compose, initialize_config_dir
from omegaconf import DictConfig, OmegaConf

CONFIG_DIR = (Path(__file__).resolve().parent.parent / "configs").resolve()


def load_config(
    overrides: list[str] | None = None,
    config_name: str = "config",
) -> DictConfig:
    """Load Hydra config with optional overrides. Returns a DictConfig."""
    overrides = overrides or []
    with initialize_config_dir(version_base=None, config_dir=str(CONFIG_DIR)):
        cfg = compose(config_name=config_name, overrides=overrides)
    return cfg


def config_to_dict(cfg: DictConfig) -> dict[str, Any]:
    """Convert a DictConfig to a plain dict (e.g., for MLflow logging)."""
    return OmegaConf.to_container(cfg, resolve=True)  # type: ignore[return-value]


def flatten_for_mlflow(cfg_dict: dict, prefix: str = "") -> dict[str, Any]:
    """Flatten nested config to dot-separated keys for MLflow params."""
    out: dict[str, Any] = {}
    for k, v in cfg_dict.items():
        key = f"{k}" if not prefix else f"{prefix}.{k}"
        if isinstance(v, dict):
            out.update(flatten_for_mlflow(v, key))
        elif isinstance(v, list):
            out[key] = ",".join(str(x) for x in v)
        else:
            out[key] = v
    return out
