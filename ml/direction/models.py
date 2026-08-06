"""ml.direction.models — model factory for directional classification.

Provides logistic regression (with calibration) and LightGBM classifiers
for the walk-forward OOS evaluation harness.
"""

from __future__ import annotations

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

# ---------------------------------------------------------------------------
# Optional LightGBM
# ---------------------------------------------------------------------------
try:
    from lightgbm import LGBMClassifier

    _LGBM_AVAILABLE: bool = True
except ImportError:
    _LGBM_AVAILABLE = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_cv(y: list[int] | np.ndarray, requested_cv: int) -> int:
    """Return a CV fold count no larger than the smallest class count.

    Args:
        y: Training labels (binary integers).
        requested_cv: Desired number of folds.

    Returns:
        Adjusted fold count in [2, requested_cv], or 0 when the smallest
        class has fewer than 2 samples (calibration is impossible).
    """
    arr = np.asarray(y)
    unique, counts = np.unique(arr, return_counts=True)
    if len(unique) < 2:
        # Single class — can't calibrate
        return 0
    min_count = int(counts.min())
    if min_count < 2:
        return 0
    return max(2, min(requested_cv, min_count))


# ---------------------------------------------------------------------------
# Logistic regression
# ---------------------------------------------------------------------------


def fit_logistic(
    X_train: np.ndarray,
    y_train: list[int] | np.ndarray,
    calibration_method: str = "sigmoid",
    C: float = 1.0,
    random_state: int = 42,
    cv: int = 3,
) -> Pipeline | CalibratedClassifierCV:
    """Fit a calibrated logistic regression classifier.

    Wraps a StandardScaler + LogisticRegression pipeline in
    CalibratedClassifierCV.  If calibration is not possible (fewer than 2
    samples per class) the bare pipeline is returned instead.

    Args:
        X_train: Feature matrix of shape (n_samples, n_features).
        y_train: Binary target labels.
        calibration_method: "sigmoid" or "isotonic".
        C: Inverse regularisation strength.
        random_state: Seed for reproducibility.
        cv: Requested number of CV folds for calibration.

    Returns:
        Fitted estimator (CalibratedClassifierCV or Pipeline).
    """
    pipeline = Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "clf",
                LogisticRegression(C=C, max_iter=1000, random_state=random_state),
            ),
        ]
    )

    safe = _safe_cv(y_train, cv)
    if safe < 2:
        pipeline.fit(X_train, y_train)
        return pipeline

    calibrated = CalibratedClassifierCV(pipeline, method=calibration_method, cv=safe)
    calibrated.fit(X_train, y_train)
    return calibrated


# ---------------------------------------------------------------------------
# LightGBM
# ---------------------------------------------------------------------------


def fit_lightgbm(
    X_train: np.ndarray,
    y_train: list[int] | np.ndarray,
    random_state: int = 42,
) -> object | None:
    """Fit a LightGBM classifier.

    Returns None when LightGBM is not installed, avoiding hard import errors
    in CI environments without the package.

    Args:
        X_train: Feature matrix.
        y_train: Binary target labels.
        random_state: Seed for reproducibility.

    Returns:
        Fitted LGBMClassifier, or None if LightGBM is unavailable.
    """
    if not _LGBM_AVAILABLE:
        return None
    model = LGBMClassifier(n_estimators=100, random_state=random_state, verbose=-1)
    model.fit(X_train, y_train)
    return model


# ---------------------------------------------------------------------------
# Feature importances
# ---------------------------------------------------------------------------


def logistic_feature_importances(
    calibrated_model: CalibratedClassifierCV | Pipeline,
    feature_names: list[str],
) -> dict[str, float]:
    """Extract mean absolute coefficients from a calibrated logistic model.

    Args:
        calibrated_model: Fitted CalibratedClassifierCV wrapping a
            (scaler, clf) Pipeline, or a bare Pipeline.
        feature_names: Ordered list of feature names.

    Returns:
        Mapping of feature name → mean absolute coefficient.  Returns an
        empty dict when coefficients cannot be extracted.
    """
    try:
        if isinstance(calibrated_model, CalibratedClassifierCV):
            coef_list = []
            for est in calibrated_model.calibrated_classifiers_:
                lr = est.estimator.named_steps["clf"]
                coef_list.append(np.abs(lr.coef_).flatten())
            mean_coef = np.mean(coef_list, axis=0)
        else:
            # Bare pipeline
            lr = calibrated_model.named_steps["clf"]
            mean_coef = np.abs(lr.coef_).flatten()

        return {name: float(val) for name, val in zip(feature_names, mean_coef, strict=True)}
    except Exception:
        return {}


def lightgbm_feature_importances(
    model: object | None,
    feature_names: list[str],
) -> dict[str, float]:
    """Extract gain-based feature importances from a LightGBM model.

    Args:
        model: Fitted LGBMClassifier, or None.
        feature_names: Ordered list of feature names.

    Returns:
        Mapping of feature name → importance value.  Returns {} when model
        is None or extraction fails.
    """
    if model is None:
        return {}
    try:
        # fit_lightgbm returns object | None (LGBMClassifier is a guarded import,
        # so mypy cannot see feature_importances_); guarded by try/except above.
        importances = model.feature_importances_  # type: ignore[attr-defined]
        return {name: float(val) for name, val in zip(feature_names, importances, strict=True)}
    except Exception:
        return {}


import os
def _deliberate_lint_break_for_strict_removal_verification():
    unused_variable_that_ruff_will_flag = 12345
    return None
