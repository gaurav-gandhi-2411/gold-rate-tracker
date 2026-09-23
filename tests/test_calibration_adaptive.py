"""Tests for ml.calibration_adaptive (M3: adaptive conformal inference
vs. the static band, offline/held-out comparison)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from ml.calibration_adaptive import _wilson_ci, evaluate_adaptive_conformal_vs_static

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_overlap_data(n: int = 120, seed: int = 42) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A same-day IBJA/Tanishq overlap with a stable linear relationship and
    small noise -- enough pairs to clear _MIN_FIT_OBSERVATIONS (30) with
    room for scored days after warmup."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    ibja_per_g = 7000 + np.cumsum(rng.normal(0, 20, n))
    noise = rng.normal(0, 30, n)
    tanishq_22k = 1.02 * ibja_per_g + 50 + noise

    ibja_df = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "pm_916": ibja_per_g * 10})
    tanishq_df = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "22k": tanishq_22k})
    return ibja_df, tanishq_df


# ---------------------------------------------------------------------------
# _wilson_ci
# ---------------------------------------------------------------------------


class TestWilsonCI:
    def test_zero_n_returns_nan(self) -> None:
        lo, hi = _wilson_ci(0, 0)
        assert lo != lo  # nan
        assert hi != hi

    def test_full_success_ci_near_one(self) -> None:
        lo, hi = _wilson_ci(100, 100)
        assert hi > 0.95
        assert lo < 1.0

    def test_half_success_ci_brackets_half(self) -> None:
        lo, hi = _wilson_ci(50, 100)
        assert lo < 0.5 < hi


# ---------------------------------------------------------------------------
# evaluate_adaptive_conformal_vs_static
# ---------------------------------------------------------------------------


class TestEvaluateAdaptiveConformalVsStatic:
    def test_returns_expected_top_level_keys(self) -> None:
        ibja_df, tanishq_df = _make_overlap_data()
        result = evaluate_adaptive_conformal_vs_static(ibja_df, tanishq_df, level=80)
        for key in ("n", "level", "static", "adaptive", "final_alpha", "alpha_target"):
            assert key in result

    def test_both_methods_score_the_same_n(self) -> None:
        ibja_df, tanishq_df = _make_overlap_data()
        result = evaluate_adaptive_conformal_vs_static(ibja_df, tanishq_df, level=80)
        assert result["static"]["n_in_band"] <= result["n"]
        assert result["adaptive"]["n_in_band"] <= result["n"]

    def test_higher_level_gives_higher_static_coverage(self) -> None:
        ibja_df, tanishq_df = _make_overlap_data()
        low = evaluate_adaptive_conformal_vs_static(ibja_df, tanishq_df, level=50)
        high = evaluate_adaptive_conformal_vs_static(ibja_df, tanishq_df, level=95)
        assert high["static"]["coverage"] >= low["static"]["coverage"]

    def test_alpha_stays_within_bounds(self) -> None:
        ibja_df, tanishq_df = _make_overlap_data()
        result = evaluate_adaptive_conformal_vs_static(ibja_df, tanishq_df, level=80, gamma=0.2)
        assert 0.01 <= result["final_alpha"] <= 0.99

    def test_alpha_target_matches_level(self) -> None:
        ibja_df, tanishq_df = _make_overlap_data()
        result = evaluate_adaptive_conformal_vs_static(ibja_df, tanishq_df, level=80)
        assert abs(result["alpha_target"] - 0.20) < 1e-9

    def test_zero_gamma_freezes_adaptive_at_target_level(self) -> None:
        """gamma=0 means alpha never updates -- the adaptive band should
        then be identical to the static band's own coverage (both use the
        same fixed quantile level throughout)."""
        ibja_df, tanishq_df = _make_overlap_data()
        result = evaluate_adaptive_conformal_vs_static(ibja_df, tanishq_df, level=80, gamma=0.0)
        assert result["static"]["n_in_band"] == result["adaptive"]["n_in_band"]

    def test_too_few_overlap_pairs_returns_zero_scored(self) -> None:
        ibja_df, tanishq_df = _make_overlap_data(n=10)  # below _MIN_FIT_OBSERVATIONS
        result = evaluate_adaptive_conformal_vs_static(ibja_df, tanishq_df, level=80)
        assert result["n"] == 0
        assert result["static"]["coverage"] is None
