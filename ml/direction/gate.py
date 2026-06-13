"""ml.direction.gate — product gate for the directional forecast signal.

This is the load-bearing decision function that controls whether a calibrated
direction probability is ever shown to users in the gold-rate-tracker UI.

The signal stays DARK unless ALL four OOS conditions hold simultaneously:
  1. Sufficient OOS folds (n_test_folds >= 30)
  2. Statistical significance (significant_at_05 == True)
  3. Model Brier score < always-up Brier score
  4. Model accuracy > always-up accuracy

See ADR 019 for the rationale.
"""

from __future__ import annotations

import json
from pathlib import Path

from ml.direction.dataset import DATA_DIR

BASELINE_JSON: Path = DATA_DIR / "direction_baseline.json"

# Required number of OOS test folds before the gate considers shipping
MIN_OOS_FOLDS: int = 30

# Keys that must be present in the logistic_metrics block
_REQUIRED_LOGISTIC_KEYS: frozenset[str] = frozenset(
    {
        "n",
        "accuracy",
        "brier",
        "always_up_accuracy",
        "always_up_brier",
        "significant_at_05",
    }
)

_REQUIRED_TOP_KEYS: frozenset[str] = frozenset(
    {
        "n_test_folds",
        "logistic_metrics",
    }
)


def decide_direction_signal(baseline: dict | None) -> dict:
    """Decide whether a calibrated direction probability may be shown to users.

    PURE function — no I/O, no side effects.  The four-gate logic:
      G1: n_test_folds >= 30
      G2: significant_at_05 == True
      G3: model brier < always_up_brier
      G4: model accuracy > always_up_accuracy
    All four must hold to ship.

    Args:
        baseline: Dict produced by run_walk_forward() (loaded from JSON),
                  or None if the file is absent / unparseable.

    Returns:
        Dict with keys:
            ship   (bool)   — True only if all gates pass
            basis  (str)    — "model_calibrated" or "base_rate_fallback"
            reason (str)    — human-readable explanation of the decision
    """
    # --- Validity checks ---------------------------------------------------
    if not baseline:
        return {
            "ship": False,
            "basis": "base_rate_fallback",
            "reason": "no eval results",
        }

    missing_top = _REQUIRED_TOP_KEYS - baseline.keys()
    if missing_top:
        return {
            "ship": False,
            "basis": "base_rate_fallback",
            "reason": f"missing baseline keys: {sorted(missing_top)}",
        }

    logistic = baseline.get("logistic_metrics", {})
    if not logistic:
        return {
            "ship": False,
            "basis": "base_rate_fallback",
            "reason": "logistic_metrics absent",
        }

    missing_log = _REQUIRED_LOGISTIC_KEYS - logistic.keys()
    if missing_log:
        return {
            "ship": False,
            "basis": "base_rate_fallback",
            "reason": f"missing logistic_metrics keys: {sorted(missing_log)}",
        }

    # --- Gate evaluation ---------------------------------------------------
    n_folds: int = int(baseline["n_test_folds"])
    acc: float = float(logistic["accuracy"])
    brier: float = float(logistic["brier"])
    always_up_acc: float = float(logistic["always_up_accuracy"])
    always_up_brier: float = float(logistic["always_up_brier"])
    significant: bool = bool(logistic["significant_at_05"])
    p_value: float = float(logistic.get("p_value", 1.0))

    # G1
    if n_folds < MIN_OOS_FOLDS:
        return {
            "ship": False,
            "basis": "base_rate_fallback",
            "reason": f"insufficient OOS folds: {n_folds} < {MIN_OOS_FOLDS}",
        }

    # G2
    if not significant:
        return {
            "ship": False,
            "basis": "base_rate_fallback",
            "reason": (
                f"not significant vs always-up: p={p_value:.4f}, "
                f"acc={acc:.4f} vs baseline={always_up_acc:.4f}"
            ),
        }

    # G3
    if brier >= always_up_brier:
        return {
            "ship": False,
            "basis": "base_rate_fallback",
            "reason": (f"model Brier {brier:.4f} >= always-up Brier {always_up_brier:.4f}"),
        }

    # G4
    if acc <= always_up_acc:
        return {
            "ship": False,
            "basis": "base_rate_fallback",
            "reason": (f"model accuracy {acc:.4f} <= always-up accuracy {always_up_acc:.4f}"),
        }

    # All gates pass
    return {
        "ship": True,
        "basis": "model_calibrated",
        "reason": (
            f"logistic beats always-up OOS: "
            f"acc {acc:.4f}>{always_up_acc:.4f}, "
            f"brier {brier:.4f}<{always_up_brier:.4f}, "
            f"p={p_value:.4f} over {n_folds} folds"
        ),
    }


def load_baseline(path: Path = BASELINE_JSON) -> dict | None:
    """Load the walk-forward baseline JSON from disk.

    Args:
        path: Path to the direction_baseline.json file.

    Returns:
        Parsed dict, or None if the file is missing or not valid JSON.
    """
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
