"""Unit tests for ml/experiments/drift_naive.py — mocked data only, no live calls."""

from __future__ import annotations

import pandas as pd
import pytest
from ml.experiments.drift_naive import apply_gate, forecast_drift_naive


def _trending(n: int = 40, start: float = 5000.0, step: float = 10.0) -> pd.Series:
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.Series([start + i * step for i in range(n)], index=idx)


def _flat(n: int = 40, val: float = 5000.0) -> pd.Series:
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.Series([val] * n, index=idx)


# --- forecast_drift_naive ---


def test_drift_naive_uptrend_predicts_above_last():
    fc = forecast_drift_naive(_trending(), horizon=5, span=5)
    assert len(fc) == 5
    assert all(v > 5390.0 for v in fc)  # series ends near 5390, drift pushes higher


def test_drift_naive_flat_series_stays_flat():
    fc = forecast_drift_naive(_flat(), horizon=5, span=10)
    assert all(abs(v - 5000.0) < 1e-6 for v in fc)


def test_drift_naive_monotone_for_constant_delta():
    series = _trending(40, step=5.0)
    fc = forecast_drift_naive(series, horizon=5, span=5)
    for i in range(len(fc) - 1):
        assert fc[i] < fc[i + 1]


@pytest.mark.parametrize("span", [5, 10, 20])
@pytest.mark.parametrize("h", [5, 10, 20])
def test_drift_naive_output_length(span: int, h: int) -> None:
    assert len(forecast_drift_naive(_trending(), horizon=h, span=span)) == h


def test_drift_naive_single_element_context_returns_flat():
    idx = pd.date_range("2024-01-01", periods=1, freq="B")
    series = pd.Series([5000.0], index=idx)
    fc = forecast_drift_naive(series, horizon=5, span=5)
    assert len(fc) == 5
    assert all(v == 5000.0 for v in fc)


# --- apply_gate ---


class TestApplyGate:
    def test_all_criteria_beats_naive(self) -> None:
        beats, pct = apply_gate(244.5, 249.5, wilcoxon_p=0.04, n_folds_ge30ctx=143)
        assert beats is True
        assert abs(pct - (249.5 - 244.5) / 249.5) < 1e-9

    def test_below_2pct_fails(self) -> None:
        # 248.0 vs 249.5 → 0.6% improvement, below 2%
        beats, _ = apply_gate(248.0, 249.5, wilcoxon_p=0.04, n_folds_ge30ctx=143)
        assert beats is False

    def test_wilcoxon_not_significant_fails(self) -> None:
        beats, _ = apply_gate(244.5, 249.5, wilcoxon_p=0.06, n_folds_ge30ctx=143)
        assert beats is False

    def test_wilcoxon_none_fails(self) -> None:
        beats, _ = apply_gate(244.5, 249.5, wilcoxon_p=None, n_folds_ge30ctx=143)
        assert beats is False

    def test_too_few_folds_fails(self) -> None:
        beats, _ = apply_gate(244.5, 249.5, wilcoxon_p=0.04, n_folds_ge30ctx=29)
        assert beats is False

    def test_exactly_2pct_passes(self) -> None:
        mae_naive = 249.53
        mae_variant = mae_naive * (1 - 0.02)
        beats, pct = apply_gate(mae_variant, mae_naive, wilcoxon_p=0.04, n_folds_ge30ctx=143)
        assert beats is True
        assert abs(pct - 0.02) < 1e-9

    def test_exactly_30_folds_passes(self) -> None:
        beats_30, _ = apply_gate(244.5, 249.5, wilcoxon_p=0.04, n_folds_ge30ctx=30)
        beats_29, _ = apply_gate(244.5, 249.5, wilcoxon_p=0.04, n_folds_ge30ctx=29)
        assert beats_30 is True
        assert beats_29 is False

    def test_pct_negative_means_variant_worse(self) -> None:
        _, pct = apply_gate(260.0, 249.5, wilcoxon_p=0.04, n_folds_ge30ctx=143)
        assert pct < 0  # variant is worse
