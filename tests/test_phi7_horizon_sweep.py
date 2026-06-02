"""Unit tests for ml/experiments/horizon_sweep.py -- mocked data, no Chronos calls."""

from __future__ import annotations

from ml.experiments.horizon_sweep import extract_ge30ctx_gate_metrics


def _mock_bt_result(n_folds: int, sub30: int, chronos_mae: float, naive_mae: float) -> dict:
    """Build a minimal mock run_backtest() result dict for testing."""
    folds = []
    for i in range(n_folds):
        is_sub30 = i < sub30
        folds.append(
            {
                "sub_30_context": is_sub30,
                "mae_chronos_per_h": [chronos_mae] * 5,
                "mae_naive_per_h": [naive_mae] * 5,
            }
        )
    return {"folds": folds, "n_folds": n_folds, "n_folds_sub_30_context": sub30}


def test_extract_ge30ctx_counts_correctly() -> None:
    result = _mock_bt_result(n_folds=165, sub30=22, chronos_mae=55.1, naive_mae=50.0)
    metrics = extract_ge30ctx_gate_metrics(result)
    assert metrics["n_folds_ge30ctx"] == 143


def test_extract_ge30ctx_chronos_worse_than_naive() -> None:
    result = _mock_bt_result(n_folds=165, sub30=22, chronos_mae=55.1, naive_mae=50.0)
    metrics = extract_ge30ctx_gate_metrics(result)
    assert metrics["mae_variant"] > metrics["mae_naive"]  # type: ignore[operator]
    assert metrics["beats_naive"] is False


def test_extract_ge30ctx_chronos_beats_naive_gate() -> None:
    """When Chronos wins by >=2% with consistent per-fold improvement and >=30 folds, beats_naive=True.

    All 143 ge30ctx folds have chronos_mae=48.5 < naive_mae=50.0 (3% improvement).
    Paired diffs are all -1.5 (all-same-sign, non-zero) -> Wilcoxon p=0.0 < 0.05.
    pct_improvement = (50.0 - 48.5) / 50.0 = 0.03 >= 0.02 gate.
    n_folds_ge30ctx = 143 >= 30 gate.
    All three gate conditions satisfied -> beats_naive=True.
    """
    naive_mae = 50.0
    chronos_mae = naive_mae * 0.97  # 3% improvement, consistent across all folds
    result = _mock_bt_result(n_folds=165, sub30=22, chronos_mae=chronos_mae, naive_mae=naive_mae)
    metrics = extract_ge30ctx_gate_metrics(result)
    assert metrics["beats_naive"] is True
    assert metrics["n_folds_ge30ctx"] == 143


def test_extract_ge30ctx_no_ge30_folds() -> None:
    result = _mock_bt_result(n_folds=10, sub30=10, chronos_mae=55.0, naive_mae=50.0)
    metrics = extract_ge30ctx_gate_metrics(result)
    assert metrics["n_folds_ge30ctx"] == 0
    assert metrics["beats_naive"] is False
