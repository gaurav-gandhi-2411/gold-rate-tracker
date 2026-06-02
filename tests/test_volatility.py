"""
Tests for ml/volatility.py — dynamic 5-day half-width computation.

All tests use mocked data (norm #11). No live I/O.
"""

from __future__ import annotations

import math

from ml.volatility import (
    FLOOR_FRACTION,
    MIN_CONTIGUOUS_DAYS,
    VOL_WINDOW,
    _dedup_daily,
    _log_returns,
    _recent_contiguous_run,
    _regime,
    _std,
    compute_vol_context,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_prices(dates: list[str], values: list[float]) -> list[dict]:
    """Build a minimal prices.json-shaped list."""
    return [
        {"timestamp": f"{d}T12:00:00.000Z", "22k": str(v)}
        for d, v in zip(dates, values, strict=True)
    ]


def _daily_dates(n: int, start: str = "2026-01-01") -> list[str]:
    """Generate n consecutive YYYY-MM-DD strings from start."""
    from datetime import date, timedelta

    base = date.fromisoformat(start)
    return [(base + timedelta(days=i)).isoformat() for i in range(n)]


STATIC_PI = 900.0


# ---------------------------------------------------------------------------
# Unit: _dedup_daily
# ---------------------------------------------------------------------------


class TestDedupDaily:
    def test_keeps_latest_same_day(self) -> None:
        prices = [
            {"timestamp": "2026-03-01T06:00:00.000Z", "22k": "100"},
            {"timestamp": "2026-03-01T18:00:00.000Z", "22k": "110"},
        ]
        result = _dedup_daily(prices)
        assert len(result) == 1
        assert result[0]["22k"] == "110"

    def test_multiple_days(self) -> None:
        prices = [
            {"timestamp": "2026-03-01T12:00:00.000Z", "22k": "100"},
            {"timestamp": "2026-03-02T12:00:00.000Z", "22k": "105"},
            {"timestamp": "2026-03-02T18:00:00.000Z", "22k": "107"},
        ]
        result = _dedup_daily(prices)
        assert len(result) == 2
        assert result[-1]["22k"] == "107"

    def test_empty_input(self) -> None:
        assert _dedup_daily([]) == []


# ---------------------------------------------------------------------------
# Unit: _recent_contiguous_run
# ---------------------------------------------------------------------------


class TestRecentContiguousRun:
    def test_all_contiguous(self) -> None:
        dates = _daily_dates(30)
        prices = _make_prices(dates, [14000.0] * 30)
        daily = _dedup_daily(prices)
        run = _recent_contiguous_run(daily)
        assert len(run) == 30

    def test_gap_breaks_streak(self) -> None:
        """A 15-day gap should cut the contiguous run at the gap."""
        dates = _daily_dates(20, "2026-01-01") + _daily_dates(10, "2026-02-10")
        prices = _make_prices(dates, [14000.0] * 30)
        daily = _dedup_daily(prices)
        run = _recent_contiguous_run(daily)
        # After the 40-day gap (Jan 20 → Feb 10) only the Feb block survives
        assert len(run) == 10

    def test_weekend_gap_survives(self) -> None:
        """Friday→Monday (3-day gap) is treated as contiguous."""
        dates = ["2026-03-06", "2026-03-07", "2026-03-09", "2026-03-10"]  # Fri/Sat skip Sun→Mon
        prices = _make_prices(dates, [14000.0] * 4)
        daily = _dedup_daily(prices)
        run = _recent_contiguous_run(daily)
        assert len(run) == 4

    def test_empty_input(self) -> None:
        assert _recent_contiguous_run([]) == []

    def test_single_row(self) -> None:
        prices = _make_prices(["2026-03-01"], [14000.0])
        daily = _dedup_daily(prices)
        run = _recent_contiguous_run(daily)
        assert len(run) == 1


# ---------------------------------------------------------------------------
# Unit: _log_returns / _std
# ---------------------------------------------------------------------------


class TestLogReturnsStd:
    def test_log_returns_count(self) -> None:
        assert len(_log_returns([100.0, 101.0, 102.0])) == 2

    def test_log_returns_flat(self) -> None:
        rets = _log_returns([14000.0, 14000.0, 14000.0])
        assert all(r == 0.0 for r in rets)

    def test_log_returns_empty(self) -> None:
        assert _log_returns([100.0]) == []
        assert _log_returns([]) == []

    def test_std_single_value(self) -> None:
        assert _std([1.0]) == 0.0

    def test_std_known(self) -> None:
        # std([0, 0, 2, 2]) = 1.154...
        s = _std([0.0, 0.0, 2.0, 2.0])
        assert abs(s - math.sqrt(4 / 3)) < 1e-9

    def test_std_empty(self) -> None:
        assert _std([]) == 0.0


# ---------------------------------------------------------------------------
# Unit: _regime
# ---------------------------------------------------------------------------


class TestRegime:
    def test_normal(self) -> None:
        assert _regime(1.0, 1.0) == "normal"

    def test_calm(self) -> None:
        # ratio 0.60 < CALM_THRESHOLD
        assert _regime(0.60, 1.0) == "calm"

    def test_elevated(self) -> None:
        # ratio 1.50 > ELEVATED_THRESHOLD
        assert _regime(1.50, 1.0) == "elevated"

    def test_zero_baseline(self) -> None:
        assert _regime(0.5, 0.0) == "normal"


# ---------------------------------------------------------------------------
# compute_vol_context: degraded path
# ---------------------------------------------------------------------------


class TestComputeVolContextDegraded:
    def test_too_few_contiguous_days_degrades(self) -> None:
        """< MIN_CONTIGUOUS_DAYS contiguous rows → is_degraded=True, fallback to static PI."""
        dates = _daily_dates(MIN_CONTIGUOUS_DAYS - 1)
        prices = _make_prices(dates, [14000.0] * (MIN_CONTIGUOUS_DAYS - 1))
        ctx = compute_vol_context(prices, STATIC_PI)
        assert ctx["is_degraded"] is True
        assert ctx["half_width"] == round(STATIC_PI)
        assert ctx["method"] == "degraded_static"

    def test_gap_reduces_contiguous_below_min(self) -> None:
        """A large gap early on should leave fewer than MIN days and trigger degradation."""
        # 5 recent days + 100-day gap + 30 old days
        old_dates = _daily_dates(30, "2025-10-01")
        new_dates = _daily_dates(5, "2026-04-10")
        all_dates = old_dates + new_dates
        prices = _make_prices(all_dates, [14000.0] * len(all_dates))
        ctx = compute_vol_context(prices, STATIC_PI)
        assert ctx["is_degraded"] is True
        assert ctx["contiguous_days"] == 5

    def test_empty_prices_degrades(self) -> None:
        ctx = compute_vol_context([], STATIC_PI)
        assert ctx["is_degraded"] is True

    def test_degraded_flag_visible(self) -> None:
        """is_degraded must be True (not silently hidden) per norm #8."""
        ctx = compute_vol_context([], STATIC_PI)
        assert "is_degraded" in ctx
        assert ctx["is_degraded"] is True


# ---------------------------------------------------------------------------
# compute_vol_context: happy path
# ---------------------------------------------------------------------------


class TestComputeVolContextHappyPath:
    def _make_sufficient_prices(self, n: int = 35) -> list[dict]:
        """35 daily prices with 1% daily moves (alternating) for deterministic vol."""
        prices: list[float] = [14000.0]
        for i in range(1, n):
            prices.append(prices[-1] * (1.01 if i % 2 == 0 else 0.99))
        return _make_prices(_daily_dates(n), prices)

    def test_method_and_not_degraded(self) -> None:
        ctx = compute_vol_context(self._make_sufficient_prices(), STATIC_PI)
        assert ctx["is_degraded"] is False
        assert ctx["method"] == "realized_20d"

    def test_half_width_positive(self) -> None:
        ctx = compute_vol_context(self._make_sufficient_prices(), STATIC_PI)
        assert ctx["half_width"] > 0

    def test_floor_applied_when_vol_too_low(self) -> None:
        """A perfectly flat price series yields zero raw vol → floor must bind."""
        dates = _daily_dates(35)
        prices = _make_prices(dates, [14000.0] * 35)
        ctx = compute_vol_context(prices, STATIC_PI)
        assert ctx["is_floored"] is True
        assert ctx["half_width"] == round(STATIC_PI * FLOOR_FRACTION)
        assert ctx["half_width_raw"] == 0.0

    def test_floor_not_applied_when_vol_sufficient(self) -> None:
        """High-vol series (5% daily moves) should exceed the floor."""
        prices: list[float] = [14000.0]
        for i in range(1, 35):
            prices.append(prices[-1] * (1.05 if i % 2 == 0 else 0.95))
        price_list = _make_prices(_daily_dates(35), prices)
        ctx = compute_vol_context(price_list, STATIC_PI)
        # 5% daily × sqrt(5) × 14000 ≈ 1565 >> floor(450)
        assert ctx["is_floored"] is False
        # half_width is rounded; half_width_raw is float — they agree within rounding
        assert ctx["half_width"] == round(ctx["half_width_raw"])

    def test_static_pi_half_preserved(self) -> None:
        ctx = compute_vol_context(self._make_sufficient_prices(), STATIC_PI)
        assert ctx["static_pi_half"] == STATIC_PI

    def test_floor_fraction_preserved(self) -> None:
        ctx = compute_vol_context(self._make_sufficient_prices(), STATIC_PI)
        assert ctx["floor_fraction"] == FLOOR_FRACTION

    def test_window_days_matches_constant(self) -> None:
        ctx = compute_vol_context(self._make_sufficient_prices(), STATIC_PI)
        assert ctx["window_days"] == VOL_WINDOW

    def test_regime_calm_when_recent_very_flat(self) -> None:
        """Last 20 days flat but earlier days volatile → calm."""
        volatile_part = [14000.0]
        for i in range(1, 20):
            volatile_part.append(volatile_part[-1] * (1.03 if i % 2 == 0 else 0.97))
        flat_part = [volatile_part[-1]] * 21  # 21 so last 20 returns are all zero
        all_prices = volatile_part + flat_part
        price_list = _make_prices(_daily_dates(len(all_prices)), all_prices)
        ctx = compute_vol_context(price_list, STATIC_PI)
        assert ctx["regime"] == "calm"

    def test_regime_elevated_when_recent_very_volatile(self) -> None:
        """Last 20 days very volatile vs a long flat earlier run → elevated.

        Need the flat portion to dominate the baseline std (≥30 flat days so zero
        returns pull the baseline std well below the recent volatile std).
        """
        flat_part = [14000.0] * 35  # 35 flat days → 34 zero log-returns dominate baseline
        volatile_part: list[float] = [flat_part[-1]]
        # 6% alternating moves so each return ≈ 0.058 — well above baseline noise
        for i in range(1, 22):
            volatile_part.append(volatile_part[-1] * (1.06 if i % 2 == 0 else 0.94))
        all_prices = flat_part + volatile_part
        price_list = _make_prices(_daily_dates(len(all_prices)), all_prices)
        ctx = compute_vol_context(price_list, STATIC_PI)
        assert ctx["regime"] == "elevated"

    def test_contiguous_days_reported(self) -> None:
        prices = self._make_sufficient_prices(35)
        ctx = compute_vol_context(prices, STATIC_PI)
        assert ctx["contiguous_days"] == 35

    def test_baseline_half_width_positive(self) -> None:
        ctx = compute_vol_context(self._make_sufficient_prices(), STATIC_PI)
        assert ctx["baseline_half_width"] > 0


# ---------------------------------------------------------------------------
# Gap-handling integration
# ---------------------------------------------------------------------------


class TestGapHandling:
    def test_101_day_gap_followed_by_sufficient_recent(self) -> None:
        """Mirrors the Aug→Nov 2025 IBJA gap. Recent 30 days should be used."""
        old_dates = _daily_dates(60, "2025-05-01")
        new_dates = _daily_dates(35, "2025-11-17")
        all_dates = old_dates + new_dates
        prices: list[float] = [14000.0]
        for i in range(1, len(all_dates)):
            prices.append(prices[-1] * (1.005 if i % 2 == 0 else 0.995))
        price_list = _make_prices(all_dates, prices)
        ctx = compute_vol_context(price_list, STATIC_PI)
        assert ctx["is_degraded"] is False
        # Only the post-gap 35 days should be in the run
        assert ctx["contiguous_days"] == 35

    def test_gap_right_before_recent_leaves_insufficient(self) -> None:
        """Gap < MIN_CONTIGUOUS_DAYS days before end triggers degradation."""
        old_dates = _daily_dates(60, "2025-05-01")
        new_dates = _daily_dates(MIN_CONTIGUOUS_DAYS - 5, "2026-04-15")
        all_dates = old_dates + new_dates
        prices = _make_prices(all_dates, [14000.0] * len(all_dates))
        ctx = compute_vol_context(prices, STATIC_PI)
        assert ctx["is_degraded"] is True
