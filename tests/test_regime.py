"""
Unit tests for ml/regime.py.

All tests use fully synthetic data — no network calls, no real macro data,
no file I/O.  hmmlearn must be installed for these tests to run.

Test groups:
  1. TestFitRegimeModel    — model shape, perm invariants, canonical labeling,
                             error conditions
  2. TestAddRegimeToMacro  — column added, binary values, NaN first row,
                             input immutability
  3. TestRegimeConstants   — REGIME_FEATURE_COLS, N_STATES, MIN_ROWS
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml.regime import (
    MIN_ROWS,
    N_STATES,
    REGIME_FEATURE_COLS,
    add_regime_to_macro,
    fit_regime_model,
)


# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

def _make_two_regime_macro(n_low: int = 200, n_high: int = 100) -> pd.DataFrame:
    """
    Synthetic macro DataFrame with two well-separated volatility regimes.

    Low-vol period : daily std ~0.3% (σ=0.003)
    High-vol period: daily std ~2.0% (σ=0.020)

    The 7x std ratio ensures the HMM reliably separates the two regimes.
    """
    rng = np.random.default_rng(42)
    n = n_low + n_high
    dates = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")

    low_rets  = rng.normal(0.0002, 0.003, n_low)
    high_rets = rng.normal(0.0000, 0.020, n_high)
    all_rets  = np.concatenate([low_rets, high_rets])
    gold = 3000.0 * np.exp(np.cumsum(all_rets))

    return pd.DataFrame(
        {
            "gold_usd":     gold,
            "usd_inr":      rng.uniform(83.0,   85.0,   n),
            "us_10y_yield": rng.uniform(4.0,    4.5,    n),
            "dxy":          rng.uniform(100.0,  106.0,  n),
            "sensex":       rng.uniform(70000., 80000., n),
            "vix":          rng.uniform(12.0,   25.0,   n),
        },
        index=dates,
    )


_MACRO_DF = _make_two_regime_macro()


# ---------------------------------------------------------------------------
# 1. TestFitRegimeModel
# ---------------------------------------------------------------------------

class TestFitRegimeModel:
    def test_returns_model_and_perm(self):
        model, perm = fit_regime_model(_MACRO_DF)
        assert hasattr(model, "predict"), "model must have a predict method"
        assert isinstance(perm, np.ndarray)

    def test_perm_shape_equals_n_states(self):
        _, perm = fit_regime_model(_MACRO_DF)
        assert perm.shape == (N_STATES,)

    def test_perm_is_valid_permutation(self):
        """perm must be a bijection over {0, ..., N_STATES-1}."""
        _, perm = fit_regime_model(_MACRO_DF)
        assert set(perm.tolist()) == set(range(N_STATES))

    def test_low_vol_state_has_lower_emission_std(self):
        """
        HMM state mapped to canonical 0 (low-vol) must have smaller emission
        std than the state mapped to canonical 1 (high-vol).
        """
        model, perm = fit_regime_model(_MACRO_DF)
        # covars_ shape varies by hmmlearn version; flatten robustly
        variances = np.asarray(model.covars_).reshape(N_STATES, -1)[:, 0]
        stds = np.sqrt(variances)
        low_vol_hmm  = int(np.where(perm == 0)[0][0])
        high_vol_hmm = int(np.where(perm == 1)[0][0])
        assert stds[low_vol_hmm] < stds[high_vol_hmm], (
            f"Low-vol std ({stds[low_vol_hmm]:.5f}) must be < "
            f"high-vol std ({stds[high_vol_hmm]:.5f})"
        )

    def test_high_vol_rows_predicted_correctly(self):
        """
        The last n_high rows (injected high-vol period) should be labeled
        mostly as regime 1.  We accept ≥ 60% correct to allow for boundary
        smoothing by the HMM.
        """
        n_low, n_high = 200, 100
        model, perm = fit_regime_model(_MACRO_DF)
        from ml.regime import _log_returns
        X, valid_idx = _log_returns(_MACRO_DF)
        hmm_states = model.predict(X)
        canonical = perm[hmm_states]
        high_vol_labels = canonical[-n_high:]
        frac_correct = (high_vol_labels == 1).mean()
        assert frac_correct >= 0.60, (
            f"Only {frac_correct:.1%} of injected high-vol rows labeled as high-vol"
        )

    def test_too_few_rows_raises_runtime_error(self):
        tiny = _make_two_regime_macro(n_low=5, n_high=5)
        with pytest.raises(RuntimeError, match="at least"):
            fit_regime_model(tiny)

    def test_model_predict_returns_integer_states(self):
        model, _ = fit_regime_model(_MACRO_DF)
        from ml.regime import _log_returns
        X, _ = _log_returns(_MACRO_DF)
        states = model.predict(X)
        assert states.dtype in (np.int32, np.int64, int)
        assert set(states.tolist()) <= set(range(N_STATES))


# ---------------------------------------------------------------------------
# 2. TestAddRegimeToMacro
# ---------------------------------------------------------------------------

class TestAddRegimeToMacro:
    def test_adds_regime_column(self):
        result = add_regime_to_macro(_MACRO_DF)
        assert "regime" in result.columns

    def test_regime_values_are_binary(self):
        result = add_regime_to_macro(_MACRO_DF)
        vals = set(result["regime"].dropna().unique())
        assert vals <= {0.0, 1.0}, f"Unexpected regime values: {vals}"

    def test_first_row_is_nan(self):
        """First calendar row has no log-return, so regime must be NaN."""
        result = add_regime_to_macro(_MACRO_DF)
        assert pd.isna(result["regime"].iloc[0])

    def test_non_first_rows_mostly_labeled(self):
        result = add_regime_to_macro(_MACRO_DF)
        labeled = result["regime"].iloc[1:].notna().sum()
        assert labeled >= len(_MACRO_DF) - 2, (
            f"Expected almost all rows labeled; got {labeled}/{len(_MACRO_DF)-1}"
        )

    def test_preserves_existing_columns(self):
        result = add_regime_to_macro(_MACRO_DF)
        for col in ["gold_usd", "usd_inr", "us_10y_yield", "dxy", "sensex", "vix"]:
            assert col in result.columns, f"Column {col!r} was lost after regime join"

    def test_does_not_modify_input(self):
        original_cols = set(_MACRO_DF.columns)
        _ = add_regime_to_macro(_MACRO_DF)
        assert set(_MACRO_DF.columns) == original_cols, "Input DataFrame was mutated"

    def test_output_length_unchanged(self):
        result = add_regime_to_macro(_MACRO_DF)
        assert len(result) == len(_MACRO_DF)

    def test_output_index_unchanged(self):
        result = add_regime_to_macro(_MACRO_DF)
        pd.testing.assert_index_equal(result.index, _MACRO_DF.index)

    def test_regime_dtype_is_float(self):
        result = add_regime_to_macro(_MACRO_DF)
        assert result["regime"].dtype == float


# ---------------------------------------------------------------------------
# 3. TestRegimeConstants
# ---------------------------------------------------------------------------

class TestRegimeConstants:
    def test_regime_feature_cols_contains_regime(self):
        assert "regime" in REGIME_FEATURE_COLS

    def test_regime_feature_cols_length(self):
        assert len(REGIME_FEATURE_COLS) == 1

    def test_n_states_is_two(self):
        assert N_STATES == 2

    def test_min_rows_is_reasonable(self):
        assert 10 <= MIN_ROWS <= 100, f"MIN_ROWS={MIN_ROWS} looks wrong"
