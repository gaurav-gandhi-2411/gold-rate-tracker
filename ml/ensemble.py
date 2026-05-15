"""Dynamic ensemble weighting for gold-rate-tracker.

Two-tier design (see ADR 008):
- Primary: inverse-MAE weights with floor 0.1. No model is fully sidelined
  when it retains any predictive signal (graceful degradation).
- Safety valve: hard exclusion when a model's MAE > 5× the best model's MAE.
  This fires only on catastrophic failure (e.g., TFT after training instability).

In normal operation every model participates; the floor prevents the weakest
model from being squeezed below 10% before renormalisation.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

_FLOOR_WEIGHT: float = 0.1
_EXCLUSION_MULTIPLIER: float = 5.0
_EPS: float = 1.0  # added to MAE before inversion to prevent blowup when MAE ≈ 0


def compute_weights(maes: dict[str, float]) -> dict[str, float]:
    """Inverse-MAE ensemble weights with floor 0.1 and 5× hard exclusion.

    Sequence per model:
      1. raw_weight = 1 / mae
      2. If mae > 5 * best_mae → weight = 0 (hard exclude)
      3. Normalize non-excluded raw weights
      4. If normalized < 0.1 → clamp to 0.1 (floor)
      5. Renormalize non-excluded weights so they sum to 1

    Returns a dict containing every input model; excluded models have weight=0.0.
    """
    if not maes:
        return {}

    best_mae = min(maes.values())
    exclusion_threshold = best_mae * _EXCLUSION_MULTIPLIER

    excluded = frozenset(m for m, mae in maes.items() if mae > exclusion_threshold)
    non_excluded = {m: maes[m] for m in maes if m not in excluded}

    if not non_excluded:
        return {m: 0.0 for m in maes}

    raw = {m: 1.0 / (mae + _EPS) for m, mae in non_excluded.items()}
    total_raw = sum(raw.values())
    if total_raw == 0.0:
        return {m: 0.0 for m in maes}

    normalized = {m: w / total_raw for m, w in raw.items()}
    floored = {m: max(w, _FLOOR_WEIGHT) for m, w in normalized.items()}
    total_floored = sum(floored.values())
    final = {m: w / total_floored for m, w in floored.items()}

    result: dict[str, float] = {m: 0.0 for m in excluded}
    result.update(final)
    return result


def save_ensemble_config(
    weights: dict[str, float],
    maes: dict[str, float],
    path: Path,
) -> None:
    """Persist ensemble weights, MAEs, and metadata to JSON."""
    best_mae = min(maes.values()) if maes else float("inf")
    config = {
        "method": "inverse_mae",
        "floor_weight": _FLOOR_WEIGHT,
        "exclusion_multiplier": _EXCLUSION_MULTIPLIER,
        "best_mae": round(best_mae, 4),
        "exclusion_threshold": round(best_mae * _EXCLUSION_MULTIPLIER, 4),
        "models": {
            m: {
                "mae": round(maes[m], 4) if m in maes else None,
                "weight": round(weights.get(m, 0.0), 6),
                "excluded": weights.get(m, 0.0) == 0.0 and m in maes,
            }
            for m in sorted(set(weights) | set(maes))
        },
        "updated_at": datetime.now(UTC).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n")
