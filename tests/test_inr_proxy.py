"""
Unit tests for ml/inr_proxy.py (M1 long-history INR 22K proxy).

All tests are fully offline — yfinance is never called; ml.macro._download_with_retry
is mocked wherever a raw-driver fetch is needed.

Test groups:
  1. TestDutySchedule       — duty_cbic.json -> daily rate reconstruction (step function)
  2. TestRollAdjustment     — GC=F/GLD divergence detection + ratio back-adjustment
  3. TestLeakageAlignment   — T-1 lag is actually applied (no same-day leakage)
  4. TestWalkForwardPremium — no future information leaks into past fitted params
  5. TestValidation         — direction-agreement / MAE computation on synthetic data
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from ml.inr_proxy import (
    _detect_and_adjust_rolls,
    _walk_forward_premium,
    build_proxy_history,
    load_duty_schedule,
    validate_against_ibja,
)

# ---------------------------------------------------------------------------
# TestDutySchedule
# ---------------------------------------------------------------------------


def _write_duty_table(path: Path, rows: list[dict], unverified: list[dict] | None = None) -> None:
    path.write_text(json.dumps({"rows": rows, "unverified_pre_2019": {"rows": unverified or []}}))


class TestDutySchedule:
    def test_before_first_row_uses_base_rate(self, tmp_path: Path) -> None:
        path = tmp_path / "duty_cbic.json"
        _write_duty_table(path, [{"effective_date": "2015-01-01", "total_duty_pct": 6.0}])

        idx = pd.date_range("2013-01-01", "2013-12-31", freq="D", tz="UTC")
        schedule = load_duty_schedule(path=path, index=idx)
        assert (schedule == 4.0).all()  # _BASE_DUTY_PCT

    def test_step_after_two_rows(self, tmp_path: Path) -> None:
        path = tmp_path / "duty_cbic.json"
        _write_duty_table(
            path,
            [
                {"effective_date": "2013-01-01", "total_duty_pct": 6.0},
                {"effective_date": "2013-06-01", "total_duty_pct": 8.0},
            ],
        )

        idx = pd.date_range("2013-01-01", "2013-12-31", freq="D", tz="UTC")
        schedule = load_duty_schedule(path=path, index=idx)
        assert schedule.loc["2013-01-01"] == pytest.approx(6.0)
        assert schedule.loc["2013-05-31"] == pytest.approx(6.0)
        assert schedule.loc["2013-06-01"] == pytest.approx(8.0)
        assert schedule.loc["2013-12-31"] == pytest.approx(8.0)

    def test_a_cut_is_a_lower_absolute_level(self, tmp_path: Path) -> None:
        path = tmp_path / "duty_cbic.json"
        _write_duty_table(
            path,
            [
                {"effective_date": "2013-01-01", "total_duty_pct": 10.0},
                {"effective_date": "2021-02-02", "total_duty_pct": 7.81},
            ],
        )

        idx = pd.date_range("2021-01-01", "2021-03-01", freq="D", tz="UTC")
        schedule = load_duty_schedule(path=path, index=idx)
        assert schedule.loc["2021-02-01"] == pytest.approx(10.0)
        assert schedule.loc["2021-02-02"] == pytest.approx(7.81)

    def test_unverified_pre_2019_rows_are_included(self, tmp_path: Path) -> None:
        path = tmp_path / "duty_cbic.json"
        _write_duty_table(
            path,
            [{"effective_date": "2019-07-06", "total_duty_pct": 13.75}],
            unverified=[{"effective_date": "2013-01-01", "total_duty_pct": 6.0}],
        )

        idx = pd.date_range("2013-01-01", "2019-12-31", freq="D", tz="UTC")
        schedule = load_duty_schedule(path=path, index=idx)
        assert schedule.loc["2013-01-01"] == pytest.approx(6.0)
        assert schedule.loc["2019-07-06"] == pytest.approx(13.75)


# ---------------------------------------------------------------------------
# TestRollAdjustment
# ---------------------------------------------------------------------------


class TestRollAdjustment:
    def _make_series(self, n: int = 120) -> tuple[pd.Series, pd.Series]:
        idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
        rng = np.random.default_rng(7)
        # Both series co-move on a shared small daily log-return.
        shared_ret = rng.normal(0, 0.003, n)
        gld = 180 * np.exp(np.cumsum(shared_ret))
        gc = 2000 * np.exp(np.cumsum(shared_ret))
        return pd.Series(gc, index=idx), pd.Series(gld, index=idx)

    def test_no_jump_no_adjustment(self) -> None:
        gc, gld = self._make_series()
        adjusted, flagged = _detect_and_adjust_rolls(gc, gld)
        assert not flagged.any()
        pd.testing.assert_series_equal(adjusted, gc)

    def test_artificial_roll_jump_is_detected_and_removed(self) -> None:
        gc, gld = self._make_series()
        jump_day = gc.index[80]
        # Inject a 4% artificial jump into GC=F only (GLD unaffected) —
        # simulates a front-month roll discontinuity.
        gc_with_jump = gc.copy()
        gc_with_jump.loc[jump_day:] *= 1.04

        adjusted, flagged = _detect_and_adjust_rolls(gc_with_jump, gld)
        assert flagged.loc[jump_day]
        # After adjustment, the series should closely match the pre-jump
        # (true, un-jumped) series — the artificial 4% should be gone.
        ratio = (adjusted / gc).dropna()
        assert ratio.iloc[-10:].apply(lambda r: abs(r - 1.0) < 0.005).all()

    def test_genuine_comovement_is_not_flagged(self) -> None:
        # A large but SHARED move (both GC=F and GLD jump together) is a
        # real market move, not a roll artifact — must not be flagged.
        gc, gld = self._make_series()
        big_day = gc.index[80]
        gc.loc[big_day:] *= 1.05
        gld.loc[big_day:] *= 1.05
        _, flagged = _detect_and_adjust_rolls(gc, gld)
        assert not flagged.loc[big_day]


# ---------------------------------------------------------------------------
# TestLeakageAlignment
# ---------------------------------------------------------------------------


class TestLeakageAlignment:
    def test_build_proxy_uses_prior_day_close_only(self, tmp_path: Path) -> None:
        """A price spike recorded on day T must not appear in day T's proxy —
        only from day T+1 onward (T-1 lag)."""
        idx = pd.date_range("2013-01-01", "2013-04-01", freq="D", tz="UTC")
        gc = pd.Series(2000.0, index=idx)
        gld = pd.Series(180.0, index=idx)
        usd_inr = pd.Series(83.0, index=idx)

        spike_day = idx[50]
        gc_spiked = gc.copy()
        gc_spiked.loc[spike_day] = 4000.0  # a same-day spike that must not leak

        raw = pd.DataFrame({"gold_usd": gc_spiked, "usd_inr": usd_inr, "gld": gld})

        duty_events = tmp_path / "duty_cbic.json"
        _write_duty_table(duty_events, [{"effective_date": "2013-01-01", "total_duty_pct": 6.0}])

        empty_ibja = tmp_path / "ibja_rates.parquet"
        pd.DataFrame({"date": pd.Series(dtype=str), "pm_916": pd.Series(dtype=float)}).to_parquet(
            empty_ibja
        )

        # Isolate the lag/leakage behaviour from roll-adjustment (covered
        # separately by TestRollAdjustment) by making roll-detection a no-op
        # here — this test is only about the shift(1) alignment.
        with (
            patch("ml.inr_proxy._fetch_raw_drivers", return_value=raw),
            patch(
                "ml.inr_proxy._detect_and_adjust_rolls",
                return_value=(gc_spiked, pd.Series(False, index=idx)),
            ),
        ):
            df = build_proxy_history(
                start="2013-01-01",
                end="2013-04-01",
                duty_events_path=duty_events,
                ibja_path=empty_ibja,
            )

        # Same-day value must reflect the SPIKE (it's in the raw pre-lag input on
        # spike_day - 1's shift target)... concretely: proxy on spike_day uses
        # gc at spike_day - 1 (normal, 2000), and proxy on spike_day + 1 uses
        # the spiked value.
        assert (
            df.loc[spike_day, "raw_pre_duty"]
            < df.loc[spike_day + pd.Timedelta(days=1), "raw_pre_duty"]
        )
        # The spike itself must not appear on spike_day.
        normal_level = df.loc[spike_day - pd.Timedelta(days=2), "raw_pre_duty"]
        assert df.loc[spike_day, "raw_pre_duty"] == pytest.approx(normal_level, rel=0.01)


# ---------------------------------------------------------------------------
# TestWalkForwardPremium
# ---------------------------------------------------------------------------


class TestWalkForwardPremium:
    def test_early_fold_params_unaffected_by_later_data(self) -> None:
        idx = pd.date_range("2022-01-01", periods=80, freq="D", tz="UTC")
        rng = np.random.default_rng(3)
        x = pd.Series(100 + np.cumsum(rng.normal(0, 1, 80)), index=idx)
        y_full = 2 * x + 10 + rng.normal(0, 0.5, 80)
        y_full = pd.Series(y_full.values, index=idx)

        full_result = _walk_forward_premium(x, y_full, min_train=30)

        # Truncate the series to only the first 50 days and refit — the
        # walk-forward params for day 40 (well within both ranges) must be
        # IDENTICAL whether or not days 50-79 ever existed, proving no
        # future leakage.
        truncated_result = _walk_forward_premium(x.iloc[:50], y_full.iloc[:50], min_train=30)

        assert full_result["slope"].iloc[40] == pytest.approx(truncated_result["slope"].iloc[40])
        assert full_result["predicted"].iloc[40] == pytest.approx(
            truncated_result["predicted"].iloc[40]
        )

    def test_raises_below_min_train(self) -> None:
        idx = pd.date_range("2022-01-01", periods=10, freq="D", tz="UTC")
        x = pd.Series(range(10), index=idx, dtype=float)
        y = pd.Series(range(10), index=idx, dtype=float)
        with pytest.raises(ValueError):
            _walk_forward_premium(x, y, min_train=30)


# ---------------------------------------------------------------------------
# TestValidation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_direction_agreement_perfect_match(self, tmp_path: Path) -> None:
        idx = pd.date_range("2022-01-01", periods=20, freq="D", tz="UTC")
        ibja_values = pd.Series(np.linspace(50000, 51000, 20), index=idx)
        proxy_df = pd.DataFrame(
            {
                "proxy_22k_per_10g": ibja_values + 5,  # small constant offset, same direction
                "is_walk_forward_oos": True,
            },
            index=idx,
        )
        ibja_df = pd.DataFrame(
            {"date": idx.strftime("%Y-%m-%d"), "pm_916": ibja_values.values},
        )
        parquet_path = tmp_path / "ibja_rates.parquet"
        ibja_df.to_parquet(parquet_path)

        result = validate_against_ibja(proxy_df, ibja_path=parquet_path)

        assert result["direction_agreement"] == pytest.approx(1.0)
        assert result["n_direction_days"] == 19

    def test_no_overlap_reports_error_not_crash(self, tmp_path: Path) -> None:
        idx = pd.date_range("2010-01-01", periods=5, freq="D", tz="UTC")
        proxy_df = pd.DataFrame(
            {"proxy_22k_per_10g": [1, 2, 3, 4, 5], "is_walk_forward_oos": False}, index=idx
        )

        other_idx = pd.date_range("2022-01-01", periods=5, freq="D", tz="UTC")
        ibja_df = pd.DataFrame({"date": other_idx.strftime("%Y-%m-%d"), "pm_916": [500000.0] * 5})
        parquet_path = tmp_path / "ibja_rates.parquet"
        ibja_df.to_parquet(parquet_path)

        result = validate_against_ibja(proxy_df, ibja_path=parquet_path)
        assert "error" in result
