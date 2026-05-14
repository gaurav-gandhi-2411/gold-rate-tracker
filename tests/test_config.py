"""Tests for ml/config.py — Hydra config loading helpers."""

from __future__ import annotations

import os
from unittest.mock import patch

from ml.config import config_to_dict, flatten_for_mlflow, load_config
from omegaconf import DictConfig

# ---------------------------------------------------------------------------
# load_config basic
# ---------------------------------------------------------------------------


def test_load_config_returns_dictconfig():
    cfg = load_config()
    assert isinstance(cfg, DictConfig)


def test_load_config_top_level_keys():
    cfg = load_config()
    assert hasattr(cfg, "project")
    assert hasattr(cfg, "paths")
    assert hasattr(cfg, "model")
    assert hasattr(cfg, "training")
    assert hasattr(cfg, "inference")
    assert hasattr(cfg, "tracking")
    assert hasattr(cfg, "data")


def test_load_config_project_name():
    cfg = load_config()
    assert cfg.project.name == "gold-rate-tracker"


def test_load_config_default_model_is_ensemble():
    cfg = load_config()
    assert cfg.model.name == "ensemble"


# ---------------------------------------------------------------------------
# load_config overrides
# ---------------------------------------------------------------------------


def test_load_config_override_model_lightgbm():
    cfg = load_config(overrides=["model=lightgbm"])
    assert cfg.model.name == "lightgbm"
    assert cfg.model.params.num_leaves == 16


def test_load_config_override_model_tft():
    cfg = load_config(overrides=["model=tft"])
    assert cfg.model.name == "tft"
    assert cfg.model.params.hidden_size == 32


def test_load_config_override_model_nbeats():
    cfg = load_config(overrides=["model=nbeats"])
    assert cfg.model.name == "nbeats"
    assert cfg.model.params.num_stacks == 2
    assert cfg.model.params.layer_widths == 128


# ---------------------------------------------------------------------------
# config_to_dict
# ---------------------------------------------------------------------------


def test_config_to_dict_returns_plain_dict():
    cfg = load_config()
    d = config_to_dict(cfg)
    assert isinstance(d, dict)
    assert "project" in d
    assert isinstance(d["project"], dict)
    assert d["project"]["name"] == "gold-rate-tracker"


def test_config_to_dict_resolves_env_var():
    with patch.dict(os.environ, {"MLFLOW_TRACKING_URI": "http://test:9999"}):
        cfg = load_config()
        d = config_to_dict(cfg)
    assert d["tracking"]["mlflow"]["tracking_uri"] == "http://test:9999"


# ---------------------------------------------------------------------------
# flatten_for_mlflow
# ---------------------------------------------------------------------------


def test_flatten_for_mlflow_dot_separated():
    nested = {"model": {"params": {"lr": 0.001, "batch_size": 32}}}
    flat = flatten_for_mlflow(nested)
    assert flat["model.params.lr"] == 0.001
    assert flat["model.params.batch_size"] == 32


def test_flatten_for_mlflow_lists_become_comma_string():
    nested = {"data": {"features": {"lags": [1, 2, 3, 4]}}}
    flat = flatten_for_mlflow(nested)
    assert flat["data.features.lags"] == "1,2,3,4"


def test_flatten_for_mlflow_scalar_passthrough():
    nested = {"seed": 42, "name": "gold-rate-tracker"}
    flat = flatten_for_mlflow(nested)
    assert flat["seed"] == 42
    assert flat["name"] == "gold-rate-tracker"


def test_flatten_for_mlflow_empty():
    assert flatten_for_mlflow({}) == {}
