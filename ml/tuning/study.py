"""Optuna study orchestration for gold-rate-tracker models.

Each model has its own study, all logged to MLflow as nested runs under a
parent sweep run. Search spaces come from configs/training/optuna.yaml.
Persistence: SQLite at models/local/optuna/{model_name}.db.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import optuna
from omegaconf import DictConfig, OmegaConf

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from ml.forecast import load_combined_history
from ml.macro import load_macro_features
from ml.regime import add_regime_to_macro
from ml.tracking import MLflowTracker

_OPTUNA_CFG_PATH = ROOT / "configs" / "training" / "optuna.yaml"


def _sample_params(trial: optuna.Trial, model_name: str, search_space_cfg) -> dict[str, Any]:
    """Sample hyperparameters from the configured search space for model_name."""
    params: dict[str, Any] = {}
    space = getattr(search_space_cfg, model_name, None)
    if space is None:
        raise ValueError(f"No search space configured for model: {model_name}")

    for param_name in space:
        spec = getattr(space, param_name)
        ptype = str(spec.type)
        if ptype == "log_float":
            params[param_name] = trial.suggest_float(
                param_name, float(spec.low), float(spec.high), log=True
            )
        elif ptype == "float":
            params[param_name] = trial.suggest_float(param_name, float(spec.low), float(spec.high))
        elif ptype == "int":
            params[param_name] = trial.suggest_int(param_name, int(spec.low), int(spec.high))
        elif ptype == "categorical":
            params[param_name] = trial.suggest_categorical(param_name, list(spec.choices))
        else:
            raise ValueError(f"Unknown search space param type: {ptype!r}")

    return params


def _lgbm_walk_forward_cv(
    params: dict[str, Any],
    history: Any,
    macro: Any,
    wf_cfg: Any,
    trial: optuna.Trial,
) -> float:
    """Walk-forward cross-validation for LightGBM. Returns mean MAE across folds.

    Reports intermediate fold MAEs to the trial for pruning.
    """
    import lightgbm as lgb

    from ml.features import ALL_FEATURE_COLS, FEATURE_COLS, build_feature_matrix, get_train_Xy
    from ml.regime import REGIME_FEATURE_COLS

    feat_df = build_feature_matrix(history, macro_df=macro)

    if macro is not None:
        feature_cols: list[str] = list(ALL_FEATURE_COLS)
        if all(c in feat_df.columns for c in REGIME_FEATURE_COLS):
            feature_cols = feature_cols + list(REGIME_FEATURE_COLS)
    else:
        feature_cols = list(FEATURE_COLS)

    X, y = get_train_Xy(feat_df, feature_cols=feature_cols)
    n = len(X)

    holdout_days = int(wf_cfg.holdout_days)
    step_days = int(wf_cfg.step_days)
    min_folds = int(wf_cfg.min_folds)

    holdout_start = n - holdout_days
    if holdout_start < 20:
        raise RuntimeError(
            f"Not enough data for walk-forward CV: {n} rows, "
            f"holdout_start={holdout_start} (need ≥20 training rows)"
        )

    n_folds = holdout_days // step_days
    if n_folds < min_folds:
        raise RuntimeError(f"Too few walk-forward folds: {n_folds} < min_folds={min_folds}")

    fold_maes: list[float] = []
    for fold_idx in range(n_folds):
        val_start = holdout_start + fold_idx * step_days
        val_end = min(val_start + step_days, n)
        if val_end <= val_start:
            break

        X_train, y_train = X.iloc[:val_start], y.iloc[:val_start]
        X_val, y_val = X.iloc[val_start:val_end], y.iloc[val_start:val_end]

        if len(X_train) < 20 or len(X_val) == 0:
            continue

        model = lgb.LGBMRegressor(
            objective="regression_l1",
            learning_rate=float(params["learning_rate"]),
            num_leaves=int(params["num_leaves"]),
            min_child_samples=int(params["min_data_in_leaf"]),
            feature_fraction=float(params["feature_fraction"]),
            subsample=float(params["bagging_fraction"]),
            subsample_freq=int(params["bagging_freq"]),
            n_estimators=200,
            verbose=-1,
            random_state=42,
        )
        model.fit(X_train, y_train)

        preds = model.predict(X_val)
        fold_mae = float(np.mean(np.abs(preds - y_val.values)))
        fold_maes.append(fold_mae)

        # Report running mean to allow MedianPruner to act
        trial.report(float(np.mean(fold_maes)), step=fold_idx)
        if trial.should_prune():
            raise optuna.TrialPruned()

    if not fold_maes:
        raise RuntimeError("No valid walk-forward folds produced")

    return float(np.mean(fold_maes))


def run_study(
    model_name: str,
    cfg: DictConfig,
    n_trials: int,
    *,
    _storage_override: str | None = None,
) -> optuna.Study:
    """Run Optuna hyperparameter search for model_name.

    Each trial is logged as a nested MLflow run under a parent sweep run.
    Returns the completed study with best_params and best_value populated.

    Args:
        model_name: One of "lightgbm", "tft", "nbeats".
        cfg: Hydra DictConfig (needs project.seed and tracking.mlflow.experiment_training).
        n_trials: Number of Optuna trials to run.
        _storage_override: Internal — override SQLite path (used in tests).
    """
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    optuna_cfg = OmegaConf.load(_OPTUNA_CFG_PATH)

    seed = int(cfg.project.seed)
    sampler = optuna.samplers.TPESampler(seed=seed)
    pruner = optuna.pruners.MedianPruner()

    storage_dir = ROOT / "models" / "local" / "optuna"
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage = _storage_override or f"sqlite:///{storage_dir / model_name}.db"

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    study_name = f"sweep-{model_name}-{date_str}"

    study = optuna.create_study(
        study_name=study_name,
        storage=storage,
        sampler=sampler,
        pruner=pruner,
        direction="minimize",
        load_if_exists=True,
    )

    history = load_combined_history()
    macro = None
    try:
        macro = load_macro_features()
        if macro is not None:
            macro = add_regime_to_macro(macro)
    except Exception:
        pass

    search_space_cfg = optuna_cfg.search_spaces
    wf_cfg = optuna_cfg.walk_forward
    tracker = MLflowTracker(experiment_name=cfg.tracking.mlflow.experiment_training)

    with tracker.run(
        run_name=study_name,
        tags={"sweep_type": "optuna", "model": model_name, "n_trials": str(n_trials)},
    ) as _parent_run:

        def objective(trial: optuna.Trial) -> float:
            params = _sample_params(trial, model_name, search_space_cfg)

            with tracker.run(
                run_name=f"{study_name}-trial-{trial.number}",
                nested=True,
            ) as trial_run:
                trial_run.log_params(params)
                trial_run.log_params({"trial_number": trial.number})

                if model_name == "lightgbm":
                    mae = _lgbm_walk_forward_cv(params, history, macro, wf_cfg, trial)
                else:
                    raise NotImplementedError(
                        f"Walk-forward CV not yet implemented for: {model_name}"
                    )

                trial_run.log_metrics({"val_mae": mae})

            return mae

        study.optimize(objective, n_trials=n_trials)

        # Log best params to parent run so train_lgbm.py can query them
        try:
            _parent_run.log_params({f"best.{k}": v for k, v in study.best_params.items()})
            _parent_run.log_metrics({"best_val_mae": study.best_value})
        except Exception:
            pass

    return study
