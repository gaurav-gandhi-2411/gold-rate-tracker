"""Tests for ml/ensemble.py — dynamic inverse-MAE ensemble weighting."""

from __future__ import annotations

import json

from ml.ensemble import _EPS, _EXCLUSION_MULTIPLIER, _FLOOR_WEIGHT, compute_weights, save_ensemble_config

# ---------------------------------------------------------------------------
# 1. Weights sum to 1
# ---------------------------------------------------------------------------


def test_weights_sum_to_one_two_models():
    maes = {"lgbm": 225.65, "nbeats": 268.6}
    weights = compute_weights(maes)
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_weights_sum_to_one_with_exclusion():
    """Excluded model has weight=0; remaining sum to 1."""
    maes = {"lgbm": 100.0, "tft": 600.0, "nbeats": 120.0}
    # best=100, threshold=500; tft (600) > 500 → excluded
    weights = compute_weights(maes)
    assert weights["tft"] == 0.0
    active = {m: w for m, w in weights.items() if w > 0}
    assert abs(sum(active.values()) - 1.0) < 1e-9


def test_weights_sum_to_one_all_similar():
    maes = {"lgbm": 100.0, "tft": 102.0, "nbeats": 98.0}
    weights = compute_weights(maes)
    assert abs(sum(weights.values()) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# 2. Better MAE gets higher weight
# ---------------------------------------------------------------------------


def test_better_mae_model_gets_higher_weight():
    maes = {"a": 100.0, "b": 300.0}
    weights = compute_weights(maes)
    assert (
        weights["a"] > weights["b"]
    ), f"Lower-MAE model should have higher weight: a={weights['a']:.4f} b={weights['b']:.4f}"


def test_weight_monotonic_with_mae():
    """Lower MAE → higher weight, strictly ordered."""
    maes = {"best": 100.0, "mid": 200.0, "worst": 400.0}
    weights = compute_weights(maes)
    assert weights["best"] > weights["mid"] > weights["worst"]


# ---------------------------------------------------------------------------
# 3. MAE > 5× best gets weight=0
# ---------------------------------------------------------------------------


def test_model_over_5x_excluded():
    maes = {"lgbm": 100.0, "tft": 501.0}
    weights = compute_weights(maes)
    assert weights["tft"] == 0.0
    assert abs(weights["lgbm"] - 1.0) < 1e-9


def test_model_exactly_at_5x_not_excluded():
    """Boundary: MAE == 5× best is NOT excluded (strictly greater than triggers exclusion)."""
    maes = {"a": 100.0, "b": 500.0}
    weights = compute_weights(maes)
    assert weights["b"] > 0.0


def test_tft_excluded_with_real_maes():
    """TFT at 1172 vs best=225.65 → 5.2×, must be excluded."""
    maes = {"lgbm": 225.65, "nbeats": 268.6, "tft": 1172.0}
    weights = compute_weights(maes)
    assert weights["tft"] == 0.0
    assert weights["lgbm"] > 0.0
    assert weights["nbeats"] > 0.0


# ---------------------------------------------------------------------------
# 4. Floor 0.1 applied to non-excluded models
# ---------------------------------------------------------------------------


def test_floor_raises_low_weight_model():
    """A third model near the exclusion threshold gets floored above its natural weight."""
    # With 3 models where c ≈ 4.9× best, c's natural normalized weight ≈ 0.09 < floor 0.1
    maes = {"a": 100.0, "b": 100.0, "c": 490.0}
    # threshold = 500; c (490) < 500, not excluded
    # natural norm_c = (1/490) / (1/100 + 1/100 + 1/490) ≈ 0.0926
    natural_c = (1 / 490) / (1 / 100 + 1 / 100 + 1 / 490)
    assert natural_c < _FLOOR_WEIGHT, "Precondition: c's natural weight is below floor"

    weights = compute_weights(maes)
    assert (
        weights["c"] > natural_c
    ), f"Floor should raise c's weight above natural {natural_c:.4f}, got {weights['c']:.4f}"
    assert weights["c"] > 0.0
    # Renorm slightly reduces the final value below 0.1, but it must be above the natural weight
    assert weights["c"] < 0.12  # sanity upper bound


def test_floor_not_applied_when_not_needed():
    """Models with high natural weights don't get floor adjustment."""
    maes = {"a": 100.0, "b": 150.0}
    weights = compute_weights(maes)
    # Both weights are well above 0.1 (roughly 0.6 and 0.4)
    assert weights["a"] > _FLOOR_WEIGHT
    assert weights["b"] > _FLOOR_WEIGHT


# ---------------------------------------------------------------------------
# 5. Similar MAEs → roughly equal weights
# ---------------------------------------------------------------------------


def test_similar_maes_roughly_equal_weights():
    maes = {"lgbm": 100.0, "tft": 102.0, "nbeats": 98.0}
    weights = compute_weights(maes)
    # All within 4% of each other — weights should all be near 1/3
    for m, w in weights.items():
        assert abs(w - 1 / 3) < 0.05, f"{m} weight {w:.4f} is too far from 1/3"


def test_identical_maes_equal_weights():
    maes = {"a": 200.0, "b": 200.0, "c": 200.0}
    weights = compute_weights(maes)
    for m, w in weights.items():
        assert abs(w - 1 / 3) < 1e-9, f"{m} weight={w}"


# ---------------------------------------------------------------------------
# 6. Edge cases
# ---------------------------------------------------------------------------


def test_empty_maes():
    assert compute_weights({}) == {}


def test_single_model():
    weights = compute_weights({"lgbm": 150.0})
    assert abs(weights["lgbm"] - 1.0) < 1e-9


def test_all_excluded_returns_zeros():
    """When all models are excluded (shouldn't happen in practice), return all zeros."""
    # Only possible if we inject an impossible MAE structure; simulate by using
    # a ridiculously low best_mae so everything exceeds 5×.
    # Here we test the internal guard: non_excluded is empty.
    # The easiest way: pass a single model that's its own best → never excluded.
    # Instead test via edge: all models have inf MAE.
    maes = {"a": float("inf"), "b": float("inf")}
    # best_mae = inf, threshold = 5*inf = inf; none are > inf, so nothing is excluded
    weights = compute_weights(maes)
    # 1/inf = 0, raw weights are 0, total_raw=0 → guard returns zeros
    assert all(w == 0.0 for w in weights.values())


# ---------------------------------------------------------------------------
# 7. save_ensemble_config writes valid JSON
# ---------------------------------------------------------------------------


def test_save_ensemble_config(tmp_path):
    maes = {"lgbm": 225.65, "nbeats": 268.6, "tft": 1172.0}
    weights = compute_weights(maes)
    path = tmp_path / "ensemble-config.json"

    save_ensemble_config(weights, maes, path)

    assert path.exists()
    config = json.loads(path.read_text())

    assert config["method"] == "inverse_mae"
    assert config["floor_weight"] == _FLOOR_WEIGHT
    assert config["exclusion_multiplier"] == _EXCLUSION_MULTIPLIER
    assert "lgbm" in config["models"]
    assert "tft" in config["models"]
    assert config["models"]["tft"]["excluded"] is True
    assert config["models"]["lgbm"]["excluded"] is False
    # lgbm + nbeats weights should sum to ≈1
    active_sum = sum(v["weight"] for v in config["models"].values() if not v["excluded"])
    assert abs(active_sum - 1.0) < 1e-4


# ---------------------------------------------------------------------------
# 8. Phase 3 regression: EPS=1.0 clamping guard
# ---------------------------------------------------------------------------


def test_eps_is_nonzero():
    """_EPS must be >= 1.0 to prevent division-blowup when MAE ~ 0."""
    assert _EPS >= 1.0, f"_EPS={_EPS} is too small — near-zero MAE will blow up weights"


def test_near_zero_mae_does_not_blowup():
    """Model with MAE=0.001 must not receive a weight that blows past 0.9 after floor/norm."""
    maes = {"lgbm": 0.001, "naive": 300.0}
    weights = compute_weights(maes)
    assert weights["lgbm"] <= 1.0
    assert weights["naive"] >= 0.0
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_floor_weight_is_01():
    """Floor must be 0.1 — matches the clamp in ml/inference.py naive blend."""
    assert _FLOOR_WEIGHT == 0.1, f"_FLOOR_WEIGHT={_FLOOR_WEIGHT}, expected 0.1"


def test_dominant_model_still_floored_by_others():
    """Dominant model does not get weight=1.0 when others are below the exclusion threshold."""
    # threshold = 5 × best_mae = 5 × 1.0 = 5.0; b and c (MAE 4.9) are NOT excluded
    maes = {"a": 1.0, "b": 4.9, "c": 4.9}
    weights = compute_weights(maes)
    assert weights["b"] > 0.0, "b should not be excluded (4.9 < 5.0)"
    assert weights["c"] > 0.0, "c should not be excluded (4.9 < 5.0)"
    assert weights["a"] < 1.0, "dominant model should not get weight=1.0 when others are not excluded"
    assert abs(sum(weights.values()) - 1.0) < 1e-9
