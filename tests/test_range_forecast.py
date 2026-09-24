"""Tests for ml.range_forecast (R1 shadow research: range/volatility forecasts).

All tests are deterministic (seed=42 everywhere stochastic), offline, and
use synthetic data only -- no network access, no repo data files, matching
this repo's existing test convention (tests/test_direction_harness.py).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from ml.range_forecast import baselines, conformal, garch, har, metrics
from ml.range_forecast.data import (
    _drop_carried_forward,
    _t_minus_1_lag,
    dense_segments,
    forward_log_return,
    log_returns,
    score_against_ibja,
)
from scipy.stats import binom, chi2

SEED = 42

# ---------------------------------------------------------------------------
# metrics: Kupiec
# ---------------------------------------------------------------------------


class TestKupiecPofTest:
    def test_perfect_calibration_gives_zero_lr(self) -> None:
        """x/n exactly equals the nominal p: LR statistic is exactly 0 and
        p_value is exactly 1.0 (p_hat == p collapses the null and alternative
        log-likelihoods to the same value)."""
        hits = np.array([1] * 10 + [0] * 90)  # 10/100 = nominal 0.10
        result = metrics.kupiec_pof_test(hits, p=0.10)
        assert result["n"] == 100
        assert result["x"] == 10
        assert result["lr_stat"] == pytest.approx(0.0, abs=1e-9)
        assert result["p_value"] == pytest.approx(1.0, abs=1e-9)

    def test_matches_independent_binomial_logpmf_derivation(self) -> None:
        """Cross-check against scipy.stats.binom.logpmf directly (a
        different code path from the log1p-based implementation): the
        combinatorial term C(n,x) is identical in both the null and
        alternative log-likelihoods and cancels in the LR difference, so
        -2*(binom.logpmf(x,n,p) - binom.logpmf(x,n,x/n)) must equal
        kupiec_pof_test's lr_stat exactly."""
        n, x, p = 10, 3, 0.10
        hits = np.array([1] * x + [0] * (n - x))
        result = metrics.kupiec_pof_test(hits, p=p)
        p_hat = x / n
        expected_lr = -2 * (binom.logpmf(x, n, p) - binom.logpmf(x, n, p_hat))
        expected_p = 1 - chi2.cdf(expected_lr, df=1)
        assert result["lr_stat"] == pytest.approx(expected_lr, rel=1e-9)
        assert result["p_value"] == pytest.approx(expected_p, rel=1e-9)

    def test_empty_input(self) -> None:
        result = metrics.kupiec_pof_test(np.array([]), p=0.10)
        assert result["n"] == 0
        assert result["p_value"] is None


# ---------------------------------------------------------------------------
# metrics: Christoffersen
# ---------------------------------------------------------------------------


class TestChristoffersenIndependenceTest:
    def test_matches_independent_binomial_logpmf_derivation(self) -> None:
        """Hand-built hit sequence with a clear clustering pattern (hits
        arrive in a block), cross-checked against an independently derived
        LR using scipy.stats.binom.logpmf for the two-state Markov chain
        likelihood (same cancellation argument as the Kupiec cross-check)."""
        hits = np.array([0, 0, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0])
        result = metrics.christoffersen_independence_test(hits)
        n00, n01, n10, n11 = result["n00"], result["n01"], result["n10"], result["n11"]
        n0_, n1_ = n00 + n01, n10 + n11
        pi01, pi11 = n01 / n0_, n11 / n1_
        pi = (n01 + n11) / (n0_ + n1_)

        expected_ll_null = (
            binom.logpmf(n01, n0_, pi) + binom.logpmf(n11, n1_, pi) if n0_ and n1_ else float("nan")
        )
        expected_ll_alt = binom.logpmf(n01, n0_, pi01) + binom.logpmf(n11, n1_, pi11)
        expected_lr = -2 * (expected_ll_null - expected_ll_alt)
        expected_p = 1 - chi2.cdf(expected_lr, df=1)
        assert result["lr_stat"] == pytest.approx(expected_lr, rel=1e-6)
        assert result["p_value"] == pytest.approx(expected_p, rel=1e-6)

    def test_perfectly_independent_alternating_sequence(self) -> None:
        """A perfectly alternating 0/1 sequence has pi01=1, pi11=0 -- the
        LR should be large (strong rejection of independence, since a
        perfectly alternating sequence is maximally dependent, not
        independent) and finite."""
        hits = np.array([0, 1] * 20)
        result = metrics.christoffersen_independence_test(hits)
        assert result["p_value"] is not None
        assert result["lr_stat"] > 10.0

    def test_degenerate_no_transitions_returns_none(self) -> None:
        """All zeros: no 0->1 or 1->1 transitions ever observed -- n1_ == 0,
        the test is undefined (not 1.0, not 0.0)."""
        hits = np.zeros(20, dtype=int)
        result = metrics.christoffersen_independence_test(hits)
        assert result["p_value"] is None


# ---------------------------------------------------------------------------
# metrics: Winkler score
# ---------------------------------------------------------------------------


class TestWinklerScore:
    def test_hand_computed_three_cases(self) -> None:
        """level=0.9 -> alpha=0.1 -> penalty multiplier 2/alpha = 20.
        lo=-1, hi=1 (width=2). actual=0 (inside): score=2.
        actual=1.5 (above hi by 0.5): score=2+20*0.5=12.
        actual=-2 (below lo by 1.0): score=2+20*1.0=22."""
        actual = np.array([0.0, 1.5, -2.0])
        lo = np.array([-1.0, -1.0, -1.0])
        hi = np.array([1.0, 1.0, 1.0])
        scores = metrics.winkler_score(actual, lo, hi, level=0.9)
        assert scores == pytest.approx([2.0, 12.0, 22.0])

    def test_narrower_interval_scores_better_when_both_cover(self) -> None:
        actual = np.array([0.0])
        wide = metrics.winkler_score(actual, np.array([-2.0]), np.array([2.0]), 0.9)
        narrow = metrics.winkler_score(actual, np.array([-0.5]), np.array([0.5]), 0.9)
        assert narrow[0] < wide[0]


# ---------------------------------------------------------------------------
# metrics: Wilson CI, QLIKE, normal bounds
# ---------------------------------------------------------------------------


class TestOtherMetrics:
    def test_wilson_ci_contains_point_estimate(self) -> None:
        lo, hi = metrics.wilson_ci(90, 100)
        assert lo < 0.9 < hi

    def test_qlike_zero_when_forecast_matches_actual(self) -> None:
        # QLIKE = log(h) + actual/h; at actual_var == forecast_var == 1: log(1)+1 = 1, not 0.
        # The MINIMUM of QLIKE over forecast_var, for fixed actual_var, is at forecast_var=actual_var.
        actual_var = np.array([2.0])
        grid = np.linspace(0.5, 5.0, 50)
        losses = [metrics.qlike(actual_var, np.array([g]))[0] for g in grid]
        best = grid[int(np.argmin(losses))]
        assert best == pytest.approx(2.0, abs=0.2)

    def test_normal_quantile_bounds_symmetric_around_mean(self) -> None:
        lo, hi = metrics.normal_quantile_bounds(np.array([1.0, 2.0]), level=0.9)
        assert lo == pytest.approx(-hi)


# ---------------------------------------------------------------------------
# data: carried-forward drop, T-1 lag
# ---------------------------------------------------------------------------


class TestDataHelpers:
    def test_drop_carried_forward_keeps_first_and_changes_only(self) -> None:
        s = pd.Series(
            [100.0, 100.0, 101.0, 101.0, 101.0, 102.0],
            index=pd.date_range("2024-01-01", periods=6, freq="D"),
        )
        out = _drop_carried_forward(s)
        assert list(out.values) == [100.0, 101.0, 102.0]

    def test_t_minus_1_lag_uses_prior_calendar_day(self) -> None:
        idx = pd.date_range("2024-01-01", periods=5, freq="D", tz="UTC")
        s = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0], index=idx)
        as_of = pd.DatetimeIndex(
            [pd.Timestamp("2024-01-03", tz="UTC"), pd.Timestamp("2024-01-05", tz="UTC")]
        )
        lagged = _t_minus_1_lag(s, as_of)
        # value at 2024-01-03 lagged by 1 day -> the value at 2024-01-02 (20.0)
        assert lagged.iloc[0] == pytest.approx(20.0)
        assert lagged.iloc[1] == pytest.approx(40.0)

    def test_forward_log_return_matches_manual_log_diff(self) -> None:
        price = pd.Series(
            [100.0, 105.0, 103.0, 110.0], index=pd.date_range("2024-01-01", periods=4)
        )
        fwd = forward_log_return(price, horizon=2)
        assert fwd.iloc[0] == pytest.approx(np.log(103.0 / 100.0))

    def test_log_returns_length_shrinks_by_one(self) -> None:
        price = pd.Series([100.0, 110.0, 105.0], index=pd.date_range("2024-01-01", periods=3))
        r = log_returns(price)
        assert len(r) == 2


# ---------------------------------------------------------------------------
# IBJA protocol: dense segments + rescoring (2026-09-24 fix -- IBJA is not
# daily before 2025-Q2; a per-row horizon there silently spanned weeks)
# ---------------------------------------------------------------------------


class TestDenseSegments:
    def _gappy_ibja(self) -> pd.Series:
        idx = pd.to_datetime(
            [
                *["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
                *["2024-02-01", "2024-02-02", "2024-02-05", "2024-02-06"],
            ]
        )
        return pd.Series([100.0, 90.0, 80.0, 85.0, 200.0, 210.0, 220.0, 230.0], index=idx)

    def test_splits_on_gap_exceeding_max_gap_days(self) -> None:
        segs = dense_segments(self._gappy_ibja())
        assert [len(s) for s in segs] == [4, 4]
        assert segs[0].index[-1] == pd.Timestamp("2024-01-04")
        assert segs[1].index[0] == pd.Timestamp("2024-02-01")

    def test_does_not_split_within_max_gap_days(self) -> None:
        # 2024-02-05 to 2024-02-06 is a 1-day gap; 2024-02-02 to 2024-02-05 is a
        # 3-day gap (<= MAX_GAP_DAYS=4) -- both stay inside the second segment.
        segs = dense_segments(self._gappy_ibja())
        assert list(segs[1].index) == list(
            pd.to_datetime(["2024-02-01", "2024-02-02", "2024-02-05", "2024-02-06"])
        )

    def test_empty_series_returns_no_segments(self) -> None:
        assert dense_segments(pd.Series(dtype=float)) == []

    def test_rows_never_dropped_only_partitioned(self) -> None:
        p = self._gappy_ibja()
        segs = dense_segments(p)
        total = sum(len(s) for s in segs)
        assert total == len(p)


class TestScoreAgainstIbja:
    def _gappy_ibja(self) -> pd.Series:
        idx = pd.to_datetime(
            [
                *["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
                *["2024-02-01", "2024-02-02", "2024-02-05", "2024-02-06", "2024-02-07"],
            ]
        )
        vals = [100.0, 101.0, 99.0, 102.0, 103.0, 200.0, 205.0, 210.0, 208.0, 215.0]
        return pd.Series(vals, index=idx)

    def _proxy_forecast_set(self) -> dict:
        proxy_idx = pd.date_range("2023-12-01", "2024-02-10", freq="D")
        rng = np.random.default_rng(SEED)
        vals = 100 * np.exp(np.cumsum(rng.normal(0, 0.005, len(proxy_idx))))
        proxy = pd.Series(vals, index=proxy_idx)
        return baselines.historical_vol_forecast_set(
            proxy, "proxy", horizon=2, vol_window=10, min_train_size=10
        )

    def test_no_scored_window_crosses_a_gap(self) -> None:
        """h=2: within each 5-row segment, only local positions 0,1,2 have a
        local_i+2 that still lies inside the segment (local 3,4 would cross
        into the next segment's first rows -- excluded)."""
        ibja = self._gappy_ibja()
        raw = self._proxy_forecast_set()
        out = score_against_ibja(raw, ibja, horizon=2)
        allowed = {
            "2024-01-01",
            "2024-01-02",
            "2024-01-03",
            "2024-02-01",
            "2024-02-02",
            "2024-02-05",
        }
        assert set(out["as_of_date"]) <= allowed
        assert len(out["as_of_date"]) == len(allowed)

    def test_h_counts_rows_within_segment_not_calendar_days(self) -> None:
        """The 2024-02-02 -> 2024-02-05 step is a 3-CALENDAR-day gap but only
        ONE ROW within the segment -- h=1 from 2024-02-02 must land on
        2024-02-05's price, not be excluded for spanning >1 calendar day."""
        ibja = self._gappy_ibja()
        raw = self._proxy_forecast_set()
        out = score_against_ibja(raw, ibja, horizon=1)
        i = out["as_of_date"].index("2024-02-02")
        expected = float(np.log(210.0) - np.log(205.0))  # ibja[2024-02-05] vs ibja[2024-02-02]
        assert out["actual_return"][i] == pytest.approx(expected)

    def test_excludes_dates_that_are_not_ibja_rows(self) -> None:
        ibja = self._gappy_ibja()
        raw = self._proxy_forecast_set()
        out = score_against_ibja(raw, ibja, horizon=1)
        ibja_date_strs = {ts.strftime("%Y-%m-%d") for ts in ibja.index}
        assert set(out["as_of_date"]) <= ibja_date_strs

    def test_bounds_copied_unchanged_from_proxy_forecast(self) -> None:
        """The model forecasts FROM the proxy series regardless of which
        series scores it -- only the row subset and the actual/current_price
        change; raw interval bounds must be byte-identical to the source row."""
        ibja = self._gappy_ibja()
        raw = self._proxy_forecast_set()
        out = score_against_ibja(raw, ibja, horizon=1)
        for k, src_i in enumerate(out["source_proxy_index"]):
            assert out["levels"]["0.9"]["lo"][k] == raw["levels"]["0.9"]["lo"][src_i]
            assert out["levels"]["0.9"]["hi"][k] == raw["levels"]["0.9"]["hi"][src_i]
            assert out["scale"][k] == raw["scale"][src_i]

    def test_empty_when_no_dates_overlap(self) -> None:
        ibja = pd.Series([1.0, 2.0], index=pd.to_datetime(["2099-01-01", "2099-01-02"]))
        raw = self._proxy_forecast_set()
        out = score_against_ibja(raw, ibja, horizon=1)
        assert out["as_of_date"] == []
        assert out["dataset"] == "ibja"


class TestIbjaConformalFallback:
    def test_falls_back_to_proxy_below_min_n(self) -> None:
        rng = np.random.default_rng(SEED)
        n = 200
        positions = np.arange(n)
        actual = rng.normal(0, 1.0, n)
        lo, hi = np.full(n, -0.5), np.full(n, 0.5)

        proxy_cal = conformal.walk_forward_conformal(
            positions, 1, lo, hi, actual, 0.9, window=250, min_calibration_n=20
        )
        # Only 25 points -- below IBJA_FALLBACK_MIN_N=30, so the IBJA-native
        # calibration never accumulates enough matured scores to fire.
        ibja_cal = conformal.walk_forward_conformal(
            positions[:25], 1, lo[:25], hi[:25], actual[:25], 0.9, window=250, min_calibration_n=30
        )
        assert np.all(np.isnan(ibja_cal["calibrated_lo"]))

        merged = conformal.apply_ibja_fallback(ibja_cal, proxy_cal, list(range(25)))
        assert set(merged["source"]) <= {"proxy_fallback", "insufficient_history"}
        # proxy's own calibration matures at 20 matured scores -- well before
        # row 25 -- so at least SOME rows must have successfully fallen back.
        assert "proxy_fallback" in merged["source"]

    def test_uses_ibja_native_once_enough_matured(self) -> None:
        rng = np.random.default_rng(SEED)
        n = 200
        positions = np.arange(n)
        actual = rng.normal(0, 1.0, n)
        lo, hi = np.full(n, -0.5), np.full(n, 0.5)

        proxy_cal = conformal.walk_forward_conformal(positions, 1, lo, hi, actual, 0.9, window=250)
        ibja_cal = conformal.walk_forward_conformal(
            positions, 1, lo, hi, actual, 0.9, window=250, min_calibration_n=30
        )
        merged = conformal.apply_ibja_fallback(ibja_cal, proxy_cal, list(range(n)))
        # With all 200 points available to both, the ibja-native calibration
        # matures (>=30) well before the end -- later rows must use "ibja".
        assert merged["source"][-1] == "ibja"

    def test_never_uses_unmatured_ibja_errors(self) -> None:
        """The IBJA-native leg of the fallback is just walk_forward_conformal
        with a higher min_calibration_n -- it inherits that function's own
        embargo guarantee (already covered by TestConformalCalibration), so
        this only checks the fallback wiring doesn't bypass it: n_matured is
        passed through unchanged from the IBJA-native calibration."""
        rng = np.random.default_rng(SEED)
        n = 50
        positions = np.arange(n)
        actual = rng.normal(0, 1.0, n)
        lo, hi = np.full(n, -0.5), np.full(n, 0.5)
        proxy_cal = conformal.walk_forward_conformal(positions, 1, lo, hi, actual, 0.9, window=250)
        ibja_cal = conformal.walk_forward_conformal(
            positions, 1, lo, hi, actual, 0.9, window=250, min_calibration_n=30
        )
        merged = conformal.apply_ibja_fallback(ibja_cal, proxy_cal, list(range(n)))
        assert merged["n_matured"] is ibja_cal["n_matured"]


# ---------------------------------------------------------------------------
# Leakage: a forecast at day t must not depend on data after day t
# ---------------------------------------------------------------------------


class TestNoLookaheadLeakage:
    def _make_price(self, n: int, diverge_from: int, seed: int = SEED) -> pd.Series:
        rng = np.random.default_rng(seed)
        rets = rng.normal(0, 0.01, n)
        rets[diverge_from:] += 0.5  # large artificial shock after the divergence point
        return pd.Series(
            100 * np.exp(np.cumsum(rets)), index=pd.date_range("2020-01-01", periods=n, freq="B")
        )

    def test_historical_vol_forecast_unaffected_by_future_shock(self) -> None:
        n, diverge_at, horizon = 600, 500, 5
        price_a = self._make_price(n, diverge_from=diverge_at)
        price_b = price_a.copy()
        price_b.iloc[diverge_at:] = price_b.iloc[diverge_at:] * 3.0  # only the future changes

        fs_a = baselines.historical_vol_forecast_set(price_a, "d", horizon, min_train_size=250)
        fs_b = baselines.historical_vol_forecast_set(price_b, "d", horizon, min_train_size=250)

        # A forecast at as_of position t depends on: returns up to t (vol window)
        # and the outcome at t+horizon. Only positions with t + horizon <= diverge_at
        # are guaranteed untouched by the shock in EITHER series.
        safe = [i for i, pos in enumerate(fs_a["as_of_position"]) if pos + horizon < diverge_at]
        assert len(safe) > 50
        for i in safe:
            assert fs_a["scale"][i] == pytest.approx(fs_b["scale"][i], rel=1e-9)
            assert fs_a["actual_return"][i] == pytest.approx(fs_b["actual_return"][i], rel=1e-9)

    def test_ewma_forecast_unaffected_by_future_shock(self) -> None:
        n, diverge_at, horizon = 600, 500, 3
        price_a = self._make_price(n, diverge_from=diverge_at)
        price_b = price_a.copy()
        price_b.iloc[diverge_at:] = price_b.iloc[diverge_at:] * 2.0

        fs_a = baselines.ewma_forecast_set(price_a, "d", horizon, min_train_size=100)
        fs_b = baselines.ewma_forecast_set(price_b, "d", horizon, min_train_size=100)
        safe = [i for i, pos in enumerate(fs_a["as_of_position"]) if pos + horizon < diverge_at]
        assert len(safe) > 50
        for i in safe:
            assert fs_a["scale"][i] == pytest.approx(fs_b["scale"][i], rel=1e-9)


# ---------------------------------------------------------------------------
# Conformal calibration: embargo, coverage on synthetic iid data
# ---------------------------------------------------------------------------


class TestConformalCalibration:
    def test_matured_window_indices_respects_embargo(self) -> None:
        positions = np.arange(20)
        horizon = 5
        for cur_idx in range(20):
            idx = conformal.matured_window_indices(positions, horizon, cur_idx, window=250)
            conformal.assert_no_unmatured_leakage(positions, horizon, cur_idx, idx)
            # every matured index must genuinely be < cur_idx
            assert all(i < cur_idx for i in idx)

    def test_never_includes_the_forecast_at_its_own_position(self) -> None:
        positions = np.array([0, 1, 2, 3, 4])
        idx = conformal.matured_window_indices(positions, horizon=1, cur_idx=1, window=250)
        assert 1 not in idx

    def test_reaches_nominal_coverage_on_synthetic_iid_data(self) -> None:
        """A raw interval deliberately built with the WRONG (too-small)
        scale under-covers badly; conformal calibration, using only past
        matured scores, should bring empirical coverage close to the
        nominal level on iid data."""
        rng = np.random.default_rng(SEED)
        n = 3000
        horizon = 1
        true_scale = 1.0
        actual = rng.normal(0, true_scale, n)
        wrong_scale = 0.4  # deliberately miscalibrated (too narrow)
        level = 0.90
        z = 1.6448536269514722  # norm.ppf(0.95)
        lo = np.full(n, -z * wrong_scale)
        hi = np.full(n, z * wrong_scale)
        positions = np.arange(n)

        raw_hits = (actual < lo) | (actual > hi)
        raw_coverage = 1 - raw_hits.mean()
        assert raw_coverage < 0.80  # confirm the raw model is indeed badly miscalibrated

        result = conformal.walk_forward_conformal(
            positions, horizon, lo, hi, actual, level, window=250
        )
        valid = ~np.isnan(result["calibrated_lo"])
        assert valid.sum() > n - 300  # only the warm-up (< min_calibration_n) is skipped
        cal_hits = (actual < result["calibrated_lo"]) | (actual > result["calibrated_hi"])
        cal_coverage = 1 - cal_hits[valid].mean()
        assert abs(cal_coverage - level) < 0.03

    def test_cqr_score_matches_definition(self) -> None:
        lo, hi, actual = (
            np.array([-1.0, -1.0, -1.0]),
            np.array([1.0, 1.0, 1.0]),
            np.array([0.0, 1.5, -2.0]),
        )
        scores = conformal.cqr_score(lo, hi, actual)
        assert scores == pytest.approx([-1.0, 0.5, 1.0])


# ---------------------------------------------------------------------------
# GARCH: recovers parameters roughly on simulated GARCH(1,1) data
# ---------------------------------------------------------------------------


class TestGarch:
    def _simulate_garch(
        self, n: int, omega: float, alpha: float, beta: float, seed: int = SEED
    ) -> np.ndarray:
        rng = np.random.default_rng(seed)
        sigma2 = np.empty(n)
        sigma2[0] = omega / (1 - alpha - beta)
        r = np.empty(n)
        r[0] = rng.normal(0, np.sqrt(sigma2[0]))
        for i in range(1, n):
            sigma2[i] = omega + alpha * r[i - 1] ** 2 + beta * sigma2[i - 1]
            r[i] = rng.normal(0, np.sqrt(sigma2[i]))
        return r

    def test_normal_fit_recovers_alpha_beta_roughly(self) -> None:
        true_alpha, true_beta = 0.08, 0.88
        r = self._simulate_garch(3000, omega=1e-5, alpha=true_alpha, beta=true_beta)
        fit = garch.fit_garch(r, dist="normal")
        assert fit["converged"]
        assert fit["alpha"] == pytest.approx(true_alpha, abs=0.05)
        assert fit["beta"] == pytest.approx(true_beta, abs=0.08)

    def test_student_t_fit_recovers_alpha_beta_roughly(self) -> None:
        true_alpha, true_beta = 0.08, 0.88
        r = self._simulate_garch(3000, omega=1e-5, alpha=true_alpha, beta=true_beta)
        fit = garch.fit_garch(r, dist="student_t")
        assert fit["converged"]
        assert fit["alpha"] == pytest.approx(true_alpha, abs=0.05)
        assert fit["beta"] == pytest.approx(true_beta, abs=0.08)

    def test_sigma2_path_matches_manual_recursion(self) -> None:
        r = np.array([0.01, -0.02, 0.015, -0.01])
        omega, alpha, beta = 1e-4, 0.1, 0.8
        path = garch.garch_sigma2_path(r, omega, alpha, beta)
        manual = np.empty(4)
        manual[0] = omega / (1 - alpha - beta)
        for i in range(1, 4):
            manual[i] = omega + alpha * r[i - 1] ** 2 + beta * manual[i - 1]
        assert path == pytest.approx(manual)

    def test_h_day_variance_increases_with_horizon(self) -> None:
        v1 = garch.garch_h_day_variance(sigma2_next=1e-4, long_run_var=8e-5, persistence=0.97, h=1)
        v5 = garch.garch_h_day_variance(sigma2_next=1e-4, long_run_var=8e-5, persistence=0.97, h=5)
        assert v5 > v1


# ---------------------------------------------------------------------------
# HAR: synthetic recovery
# ---------------------------------------------------------------------------


class TestHar:
    def test_recovers_coefficients_roughly_on_synthetic_har_process(self) -> None:
        rng = np.random.default_rng(SEED)
        n = 2500
        rv = np.empty(n)
        rv[:22] = 1e-4
        c, bd, bw, bm = 1e-6, 0.30, 0.30, 0.30
        for t in range(22, n):
            rv_d, rv_w, rv_m = har.har_components(rv, t)
            mean_rv = c + bd * rv_d + bw * rv_w + bm * rv_m
            rv[t] = max(1e-8, mean_rv * rng.gamma(4, 0.25))

        coefs = har.fit_har(rv, t=2000)
        # multicollinearity between the d/w/m components (by construction) means
        # individual coefficients are noisy, but persistence (bd+bw+bm) is identified well.
        assert coefs[1] + coefs[2] + coefs[3] == pytest.approx(bd + bw + bm, abs=0.15)

    def test_har_h_day_variance_matches_recursive_sum(self) -> None:
        rv = np.array([1e-4] * 30)
        coefs = np.array([1e-6, 0.3, 0.3, 0.3])
        h_var = har.har_h_day_variance(rv, coefs, t=25, horizon=3)

        history = list(rv[:25])
        total = 0.0
        for _ in range(3):
            rv_d = history[-1]
            rv_w = float(np.mean(history[-5:]))
            rv_m = float(np.mean(history[-22:]))
            pred = max(
                coefs[0] + coefs[1] * rv_d + coefs[2] * rv_w + coefs[3] * rv_m, har._RV_FLOOR
            )
            total += pred
            history.append(pred)
        assert h_var == pytest.approx(total)

    def test_realized_variance_is_squared_returns(self) -> None:
        r = np.array([0.01, -0.02, 0.0])
        assert har.realized_variance(r) == pytest.approx([0.0001, 0.0004, 0.0])


# ---------------------------------------------------------------------------
# Baselines: sanity on the forecast-set shape and level ordering
# ---------------------------------------------------------------------------


class TestBaselineForecastSets:
    def _flat_vol_price(self, n: int = 400, seed: int = SEED) -> pd.Series:
        rng = np.random.default_rng(seed)
        rets = rng.normal(0, 0.01, n)
        return pd.Series(
            100 * np.exp(np.cumsum(rets)), index=pd.date_range("2020-01-01", periods=n, freq="B")
        )

    def test_90_interval_wider_than_80_interval(self) -> None:
        price = self._flat_vol_price()
        fs = baselines.historical_vol_forecast_set(price, "d", horizon=5, min_train_size=250)
        w80 = np.array(fs["levels"]["0.8"]["hi"]) - np.array(fs["levels"]["0.8"]["lo"])
        w90 = np.array(fs["levels"]["0.9"]["hi"]) - np.array(fs["levels"]["0.9"]["lo"])
        assert np.all(w90 >= w80)

    def test_historical_simulation_intervals_widen_with_horizon(self) -> None:
        price = self._flat_vol_price(n=900)
        fs1 = baselines.historical_simulation_forecast_set(
            price, "d", horizon=1, min_train_size=250
        )
        fs20 = baselines.historical_simulation_forecast_set(
            price, "d", horizon=20, min_train_size=250
        )
        w1 = np.mean(np.array(fs1["levels"]["0.9"]["hi"]) - np.array(fs1["levels"]["0.9"]["lo"]))
        w20 = np.mean(np.array(fs20["levels"]["0.9"]["hi"]) - np.array(fs20["levels"]["0.9"]["lo"]))
        assert w20 > w1

    def test_ewma_variance_path_matches_manual_recursion(self) -> None:
        r = np.array([0.01, -0.02, 0.03, -0.01, 0.005])
        path = baselines.ewma_variance_path(r, lam=0.94, seed_window=3)
        seed = float(np.var(r[:3], ddof=1))
        manual = np.empty(5)
        prev = seed
        for i in range(5):
            prev = 0.94 * prev + 0.06 * r[i] ** 2
            manual[i] = prev
        assert path == pytest.approx(manual)
