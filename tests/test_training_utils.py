"""
Unit tests for ml/training/utils.py.

Tests:
  1. TargetNormalizer — fit, normalize/denormalize round-trip, save/load
  2. compute_naive_baseline_mae — correctness on hand-computed examples
  3. compute_metrics — keys present, values correct on hand-computed example
  4. verify_pytorch_onnx_parity — pass within tolerance, raise outside tolerance
  5. gpu_snapshot — always returns dict with cuda_available key
"""

from __future__ import annotations

import numpy as np
import pytest

from ml.training.utils import (
    TargetNormalizer,
    compute_metrics,
    compute_naive_baseline_mae,
    gpu_snapshot,
    verify_pytorch_onnx_parity,
)


# ---------------------------------------------------------------------------
# 1. TargetNormalizer
# ---------------------------------------------------------------------------


class TestTargetNormalizer:
    def _sample_array(self) -> np.ndarray:
        rng = np.random.default_rng(0)
        return rng.normal(loc=100.0, scale=20.0, size=500).astype(np.float64)

    def test_fit_stores_correct_mean_std(self):
        y = self._sample_array()
        n = TargetNormalizer.fit(y)
        assert abs(n.mean - float(y.mean())) < 1e-9
        assert abs(n.std - float(y.std())) < 1e-9
        assert n.fitted_on_n == len(y)

    def test_normalize_denormalize_round_trip(self):
        y = self._sample_array()
        n = TargetNormalizer.fit(y)
        normalized = n.normalize(y)
        recovered = n.denormalize(normalized)
        np.testing.assert_allclose(recovered, y, atol=1e-9)

    def test_normalize_produces_zero_mean(self):
        y = self._sample_array()
        n = TargetNormalizer.fit(y)
        normalized = n.normalize(y)
        assert abs(normalized.mean()) < 1e-9

    def test_save_load_round_trip(self, tmp_path):
        y = self._sample_array()
        original = TargetNormalizer.fit(y)
        path = tmp_path / "normalizer.json"
        original.save(path)
        loaded = TargetNormalizer.load(path)
        assert loaded.mean == original.mean
        assert loaded.std == original.std
        assert loaded.fitted_on_n == original.fitted_on_n
        assert loaded.method == original.method

    def test_save_load_preserves_predictions(self, tmp_path):
        y = self._sample_array()
        original = TargetNormalizer.fit(y)
        path = tmp_path / "normalizer.json"
        original.save(path)
        loaded = TargetNormalizer.load(path)
        np.testing.assert_allclose(loaded.normalize(y), original.normalize(y), atol=1e-12)

    def test_default_method_is_z_score(self):
        y = self._sample_array()
        n = TargetNormalizer.fit(y)
        assert n.method == "z-score"


# ---------------------------------------------------------------------------
# 2. compute_naive_baseline_mae
# ---------------------------------------------------------------------------


class TestComputeNaiveBaselineMAE:
    def test_exact_match_gives_zero(self):
        y = np.array([1.0, 2.0, 3.0])
        assert compute_naive_baseline_mae(y, y) == pytest.approx(0.0)

    def test_hand_computed_example(self):
        # y_true = [10, 20, 30], prev_y = [8, 18, 28]
        # abs diffs = [2, 2, 2], mean = 2.0
        y_true = np.array([10.0, 20.0, 30.0])
        prev_y = np.array([8.0, 18.0, 28.0])
        assert compute_naive_baseline_mae(y_true, prev_y) == pytest.approx(2.0)

    def test_scalar_result(self):
        y = np.arange(10, dtype=float)
        result = compute_naive_baseline_mae(y, y - 1)
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# 3. compute_metrics
# ---------------------------------------------------------------------------


class TestComputeMetrics:
    def _perfect_metrics(self) -> dict:
        y = np.array([1.0, 2.0, 3.0, 4.0])
        return compute_metrics(y, y)

    def test_returns_all_expected_keys(self):
        m = self._perfect_metrics()
        for key in ("mae", "mape", "rmse", "direction_accuracy", "n_samples"):
            assert key in m, f"Missing key: {key}"

    def test_perfect_prediction_gives_zero_errors(self):
        m = self._perfect_metrics()
        assert m["mae"] == pytest.approx(0.0)
        assert m["rmse"] == pytest.approx(0.0)

    def test_n_samples_correct(self):
        y = np.ones(7)
        m = compute_metrics(y, y)
        assert m["n_samples"] == 7

    def test_hand_computed_mae(self):
        y_true = np.array([10.0, 20.0, 30.0])
        y_pred = np.array([12.0, 18.0, 33.0])
        # abs_err = [2, 2, 3], mean = 7/3
        m = compute_metrics(y_true, y_pred)
        assert m["mae"] == pytest.approx(7.0 / 3.0, rel=1e-6)

    def test_direction_accuracy_all_correct(self):
        y_true = np.array([1.0, -1.0, 2.0])
        y_pred = np.array([0.5, -0.5, 1.0])
        m = compute_metrics(y_true, y_pred)
        assert m["direction_accuracy"] == pytest.approx(1.0)

    def test_direction_accuracy_all_wrong(self):
        y_true = np.array([1.0, -1.0, 2.0])
        y_pred = np.array([-0.5, 0.5, -1.0])
        m = compute_metrics(y_true, y_pred)
        assert m["direction_accuracy"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 4. verify_pytorch_onnx_parity
# ---------------------------------------------------------------------------


class TestVerifyPytorchOnnxParity:
    def test_within_tolerance_returns_dict(self):
        pt = np.array([1.0, 2.0, 3.0])
        onnx = np.array([1.0005, 2.0002, 2.9998])
        result = verify_pytorch_onnx_parity(pt, onnx, tolerance=1e-3)
        assert "max_abs_diff" in result
        assert "mean_abs_diff" in result
        assert "tolerance" in result
        assert result["max_abs_diff"] <= 1e-3

    def test_identical_arrays_give_zero_diff(self):
        pt = np.array([1.0, 2.0, 3.0])
        result = verify_pytorch_onnx_parity(pt, pt.copy())
        assert result["max_abs_diff"] == pytest.approx(0.0)

    def test_outside_tolerance_raises_value_error(self):
        pt = np.array([1.0, 2.0, 3.0])
        onnx = np.array([1.0, 2.0, 4.0])  # diff = 1.0 >> 1e-3
        with pytest.raises(ValueError, match="parity failure"):
            verify_pytorch_onnx_parity(pt, onnx, tolerance=1e-3)

    def test_error_message_contains_max_diff(self):
        pt = np.array([0.0])
        onnx = np.array([5.0])
        with pytest.raises(ValueError, match="5.0"):
            verify_pytorch_onnx_parity(pt, onnx, tolerance=1e-3)

    def test_custom_tolerance_respected(self):
        pt = np.array([1.0])
        onnx = np.array([1.05])
        # Should pass with tolerance=0.1
        result = verify_pytorch_onnx_parity(pt, onnx, tolerance=0.1)
        assert result["max_abs_diff"] == pytest.approx(0.05)
        # Should fail with tolerance=0.01
        with pytest.raises(ValueError):
            verify_pytorch_onnx_parity(pt, onnx, tolerance=0.01)


# ---------------------------------------------------------------------------
# 5. gpu_snapshot
# ---------------------------------------------------------------------------


class TestGpuSnapshot:
    def test_returns_dict(self):
        result = gpu_snapshot()
        assert isinstance(result, dict)

    def test_has_cuda_available_key(self):
        result = gpu_snapshot()
        assert "cuda_available" in result

    def test_cuda_available_is_bool(self):
        result = gpu_snapshot()
        assert isinstance(result["cuda_available"], bool)

    def test_gpu_fields_present_when_cuda_available(self):
        result = gpu_snapshot()
        if result["cuda_available"]:
            assert "device_name" in result
            assert "memory_allocated_mb" in result
            assert "memory_reserved_mb" in result
            assert "torch_version" in result
