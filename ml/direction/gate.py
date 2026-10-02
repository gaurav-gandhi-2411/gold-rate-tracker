"""ml.direction.gate — product gates for the directional forecast signal.

Two independent, load-bearing decisions, BOTH DARK until earned:

decide_direction_signal — may a calibrated direction probability ("58% up
  tomorrow") be shown? Ships only when ALL hold:
    1. Sufficient OOS folds (n_test_folds >= 30)
    2. Statistical significance (significant_at_05 == True)
    3. Model Brier < always-up Brier
    4. Model accuracy > always-up accuracy
    5. Well-calibrated (ECE <= ECE_MAX_PROB) — calibration is the primary quality bar

decide_timing_signal — may a buy/wait/sell timing signal be shown? It implies an
  ACTION, so it is gated STRICTER: the probability gate must pass AND a higher
  fold count, a meaningful accuracy edge, tighter calibration, and stronger
  significance must all hold.

See ADR 019 for the rationale (no signal that fails its baseline ships).
"""

from __future__ import annotations

import json
from pathlib import Path

from ml.direction.dataset import DATA_DIR

BASELINE_JSON: Path = DATA_DIR / "direction_baseline.json"

# --- Probability gate thresholds ------------------------------------------------
MIN_OOS_FOLDS: int = 30
# Max Expected Calibration Error to call a probability "well-calibrated". With ~100
# folds ECE is noisy; 0.10 is a permissive bar (a probability off by >10pp on
# average is not honest to show).
ECE_MAX_PROB: float = 0.10

# --- Timing gate thresholds (stricter — a buy/wait/sell signal implies action) --
TIMING_MIN_OOS_FOLDS: int = 60
TIMING_MIN_ACC_EDGE: float = 0.05  # accuracy must beat base rate by >= 5pp
TIMING_MAX_ECE: float = 0.05
TIMING_MAX_P: float = 0.01

# Keys that must be present in the logistic_metrics block
_REQUIRED_LOGISTIC_KEYS: frozenset[str] = frozenset(
    {
        "n",
        "accuracy",
        "brier",
        "always_up_accuracy",
        "always_up_brier",
        "significant_at_05",
        "ece",
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

    # G5 — calibration (primary quality bar)
    ece = float(logistic["ece"])
    if ece > ECE_MAX_PROB:
        return {
            "ship": False,
            "basis": "base_rate_fallback",
            "reason": f"poorly calibrated: ECE {ece:.4f} > {ECE_MAX_PROB}",
        }

    # All gates pass
    return {
        "ship": True,
        "basis": "model_calibrated",
        "reason": (
            f"logistic beats always-up OOS: "
            f"acc {acc:.4f}>{always_up_acc:.4f}, "
            f"brier {brier:.4f}<{always_up_brier:.4f}, "
            f"ECE {ece:.4f}<={ECE_MAX_PROB}, "
            f"p={p_value:.4f} over {n_folds} folds"
        ),
    }


def decide_timing_signal(baseline: dict | None) -> dict:
    """Decide whether a buy/wait/sell TIMING signal may be shown to users.

    A timing signal implies an action, so it is gated STRICTER than the
    probability gate. PURE function. Ships only when the probability gate passes
    AND all of:
      T1: n_test_folds >= TIMING_MIN_OOS_FOLDS (60)
      T2: accuracy - always_up_accuracy >= TIMING_MIN_ACC_EDGE (5pp)
      T3: ece <= TIMING_MAX_ECE (0.05)
      T4: p_value < TIMING_MAX_P (0.01)

    Returns {ship, basis ("timing_model" | "hold_dark"), reason}.
    """
    prob = decide_direction_signal(baseline)
    if not prob["ship"]:
        return {
            "ship": False,
            "basis": "hold_dark",
            "reason": f"probability gate not passed ({prob['reason']})",
        }

    # baseline/logistic are valid here (probability gate already passed).
    assert baseline is not None
    logistic = baseline["logistic_metrics"]
    n_folds = int(baseline["n_test_folds"])
    acc = float(logistic["accuracy"])
    always_up_acc = float(logistic["always_up_accuracy"])
    ece = float(logistic["ece"])
    p_value = float(logistic.get("p_value", 1.0))

    if n_folds < TIMING_MIN_OOS_FOLDS:
        return {
            "ship": False,
            "basis": "hold_dark",
            "reason": f"insufficient folds for an actionable signal: {n_folds} < {TIMING_MIN_OOS_FOLDS}",
        }
    edge = acc - always_up_acc
    if edge < TIMING_MIN_ACC_EDGE:
        return {
            "ship": False,
            "basis": "hold_dark",
            "reason": f"accuracy edge {edge:.4f} < required {TIMING_MIN_ACC_EDGE}",
        }
    if ece > TIMING_MAX_ECE:
        return {
            "ship": False,
            "basis": "hold_dark",
            "reason": f"calibration too loose for action: ECE {ece:.4f} > {TIMING_MAX_ECE}",
        }
    if p_value >= TIMING_MAX_P:
        return {
            "ship": False,
            "basis": "hold_dark",
            "reason": f"significance too weak for action: p={p_value:.4f} >= {TIMING_MAX_P}",
        }

    return {
        "ship": True,
        "basis": "timing_model",
        "reason": (
            f"timing signal earned: edge {edge:.4f}>={TIMING_MIN_ACC_EDGE}, "
            f"ECE {ece:.4f}<={TIMING_MAX_ECE}, p={p_value:.4f}<{TIMING_MAX_P}, "
            f"{n_folds} folds"
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


# ---------------------------------------------------------------------------
# Promotion gate — a passing decide_direction_signal/decide_timing_signal
# gate is a MEASUREMENT, never an authorization by itself (GG spec,
# 2026-09-23, item 1). Traced end to end: as of this addition, nothing in
# app.js/notifications reads probability_gate/timing_gate at all (the
# "Direction signal" UI section is a hardcoded DARK state gated on an
# unrelated field, ADR 019/020) — this function exists so that if a FUTURE
# change ever wires a user-facing surface to this gate's output, that wiring
# is required to also check is_signal_promoted(), which is False unless GG
# has committed an explicit promotion record. scripts/check_direction_
# signal_not_wired_without_promotion.py enforces the "no wiring without a
# record" half of this mechanically in CI; this function is the "and the
# record must actually be present" half any future wiring would call.
# ---------------------------------------------------------------------------

PROMOTION_RECORD_PATH: Path = DATA_DIR / "direction_promotion_record.json"


def is_signal_promoted(path: Path = PROMOTION_RECORD_PATH) -> bool:
    """True only if an explicit, committed promotion record exists.

    A passing gate (decide_direction_signal/decide_timing_signal returning
    ship=True) is NOT sufficient on its own — this is a separate, human
    (GG) authorization step. Any future code that wants to show the
    direction/timing signal to a user MUST check this in addition to the
    gate itself; a passing gate with no promotion record must still show
    nothing.
    """
    return path.exists()
