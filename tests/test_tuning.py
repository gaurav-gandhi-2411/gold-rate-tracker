"""Tests for ml/tuning/study.py — Optuna study orchestration."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
from ml.tuning.study import run_study
from omegaconf import OmegaConf

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_OPTUNA_CFG_PATH = (
    __file__[: __file__.rfind("tests")]  # repo root
    + "configs/training/optuna.yaml"
)


def _make_history(n: int = 220, seed: int = 0) -> pd.DataFrame:
    """Synthetic price history with enough rows for walk-forward CV."""
    rng = np.random.default_rng(seed)
    t0 = datetime(2023, 1, 1)
    timestamps = [t0 + timedelta(days=i) for i in range(n)]
    prices = 6500 + rng.integers(-150, 151, size=n).cumsum()
    prices = np.clip(prices, 4000, 12000).tolist()
    return pd.DataFrame(
        {
            "timestamp": [t.isoformat() for t in timestamps],
            "22k": prices,
            "24k": [int(p * 24 / 22) for p in prices],
            "18k": [int(p * 18 / 22) for p in prices],
        }
    )


def _make_cfg():
    """Minimal DictConfig matching what run_study needs."""
    return OmegaConf.create(
        {
            "project": {"seed": 42},
            "tracking": {"mlflow": {"experiment_training": "gold-rate-training"}},
        }
    )


# ---------------------------------------------------------------------------
# 1. run_study returns a Study object with best_params and best_value
# ---------------------------------------------------------------------------


def test_run_study_returns_study_with_best_params(tmp_path):
    """run_study returns a completed Study with best_params and best_value set."""
    history = _make_history(220)
    cfg = _make_cfg()
    storage = f"sqlite:///{tmp_path}/study.db"

    with (
        patch("ml.tuning.study.load_combined_history", return_value=history),
        patch("ml.tuning.study.load_macro_features", return_value=None),
    ):
        study = run_study("lightgbm", cfg, n_trials=3, _storage_override=storage)

    import optuna

    assert isinstance(study, optuna.Study)
    assert study.best_value is not None
    assert isinstance(study.best_value, float)
    assert study.best_params is not None
    assert len(study.best_params) > 0
    assert len(study.trials) == 3


# ---------------------------------------------------------------------------
# 2. Best params are within the configured search space
# ---------------------------------------------------------------------------


def test_best_params_within_search_space(tmp_path):
    """Every best param falls inside its declared search space bounds."""
    from omegaconf import OmegaConf

    optuna_cfg = OmegaConf.load(
        (__file__[: __file__.rfind("tests")] + "configs/training/optuna.yaml").replace("\\", "/")
    )
    space = optuna_cfg.search_spaces.lightgbm

    history = _make_history(220)
    cfg = _make_cfg()
    storage = f"sqlite:///{tmp_path}/study_space.db"

    with (
        patch("ml.tuning.study.load_combined_history", return_value=history),
        patch("ml.tuning.study.load_macro_features", return_value=None),
    ):
        study = run_study("lightgbm", cfg, n_trials=3, _storage_override=storage)

    params = study.best_params
    for param_name in space:
        spec = getattr(space, param_name)
        ptype = str(spec.type)
        val = params[param_name]

        if ptype in ("log_float", "float"):
            assert (
                float(spec.low) <= val <= float(spec.high)
            ), f"{param_name}={val} outside [{spec.low}, {spec.high}]"
        elif ptype == "int":
            assert (
                int(spec.low) <= int(val) <= int(spec.high)
            ), f"{param_name}={val} outside [{spec.low}, {spec.high}]"
        elif ptype == "categorical":
            assert val in list(
                spec.choices
            ), f"{param_name}={val!r} not in choices {list(spec.choices)}"


# ---------------------------------------------------------------------------
# 3. All trials logged to MLflow as nested runs under the parent
# ---------------------------------------------------------------------------


def test_mlflow_nested_runs_logged(tmp_path):
    """Parent sweep run + one nested run per trial are created in MLflow."""
    history = _make_history(220)
    cfg = _make_cfg()
    n_trials = 2

    start_run_calls: list[bool] = []

    class FakeRun:
        def __init__(self, nested: bool = False) -> None:
            self.info = MagicMock()
            self.info.run_id = f"fake-{len(start_run_calls)}"
            start_run_calls.append(nested)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_start_run(**kwargs):
        return FakeRun(nested=kwargs.get("nested", False))

    storage = f"sqlite:///{tmp_path}/mlflow_test.db"

    with (
        patch("ml.tracking.is_mlflow_reachable", return_value=True),
        patch("mlflow.set_tracking_uri"),
        patch("mlflow.set_experiment"),
        patch("mlflow.start_run", side_effect=fake_start_run),
        patch("mlflow.set_tags"),
        patch("mlflow.log_params"),
        patch("mlflow.log_metrics"),
        patch("ml.tuning.study.load_combined_history", return_value=history),
        patch("ml.tuning.study.load_macro_features", return_value=None),
    ):
        run_study("lightgbm", cfg, n_trials=n_trials, _storage_override=storage)

    # 1 parent (nested=False) + n_trials nested runs (nested=True)
    parent_calls = [c for c in start_run_calls if not c]
    nested_calls = [c for c in start_run_calls if c]
    assert len(parent_calls) == 1, f"Expected 1 parent run, got {len(parent_calls)}"
    assert (
        len(nested_calls) == n_trials
    ), f"Expected {n_trials} nested runs, got {len(nested_calls)}"
    assert len(start_run_calls) == n_trials + 1


# ---------------------------------------------------------------------------
# 4. Reproducibility: same seed → identical best_params
# ---------------------------------------------------------------------------


def test_reproducibility(tmp_path):
    """Two studies with the same seed produce identical best_params."""
    history = _make_history(220, seed=7)
    cfg = _make_cfg()

    storage_a = f"sqlite:///{tmp_path}/repro_a.db"
    storage_b = f"sqlite:///{tmp_path}/repro_b.db"

    kwargs = dict(
        model_name="lightgbm",
        cfg=cfg,
        n_trials=3,
    )

    with (
        patch("ml.tuning.study.load_combined_history", return_value=history),
        patch("ml.tuning.study.load_macro_features", return_value=None),
    ):
        study_a = run_study(**kwargs, _storage_override=storage_a)

    with (
        patch("ml.tuning.study.load_combined_history", return_value=history),
        patch("ml.tuning.study.load_macro_features", return_value=None),
    ):
        study_b = run_study(**kwargs, _storage_override=storage_b)

    assert (
        study_a.best_params == study_b.best_params
    ), f"Best params differ:\n  A={study_a.best_params}\n  B={study_b.best_params}"
    assert (
        abs(study_a.best_value - study_b.best_value) < 1e-6
    ), f"Best values differ: {study_a.best_value} vs {study_b.best_value}"
