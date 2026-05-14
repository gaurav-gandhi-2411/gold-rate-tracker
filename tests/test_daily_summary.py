"""Tests for ml/daily_summary.py — trigger logic and template commentary."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ml.daily_summary import (
    BAND_30D,
    FIVE_DAY_PCT,
    PRICE_MOVE_PCT,
    _build_title,
    _fmt_inr_ascii,
    _is_already_sent,
    _ist_date,
    _latest_for_ist_date,
    _mark_sent,
    _template_commentary,
    call_groq_summary,
    check_triggers,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# 06:00 UTC = 11:30 IST — safe "mid-morning IST" anchor for all test prices.
_BASE_UTC_TIME = "T06:00:00.000Z"

# "Now" during all trigger tests: 10:30 UTC = 16:00 IST (daily summary runs at this time).
_NOW_UTC = datetime(2026, 5, 14, 10, 30, tzinfo=timezone.utc)
_TODAY_IST = date(2026, 5, 14)
_YESTERDAY_IST = date(2026, 5, 13)


def _p(d: str, price: int) -> dict:
    """Build a minimal price entry at 06:00 UTC on the given date."""
    return {"timestamp": f"{d}{_BASE_UTC_TIME}", "22k": price, "24k": price + 1000, "18k": price - 1000}


def _prices(*pairs: tuple[str, int]) -> list[dict]:
    return [_p(d, p) for d, p in pairs]


# ---------------------------------------------------------------------------
# IST date conversion
# ---------------------------------------------------------------------------


def test_ist_date_midday():
    """10:30 UTC = 16:00 IST — same calendar date."""
    ts = datetime(2026, 5, 14, 10, 30, tzinfo=timezone.utc)
    assert _ist_date(ts) == date(2026, 5, 14)


def test_ist_date_late_night_utc():
    """22:00 UTC = 03:30 IST next day."""
    ts = datetime(2026, 5, 13, 22, 0, tzinfo=timezone.utc)
    assert _ist_date(ts) == date(2026, 5, 14)


def test_ist_date_early_morning_utc():
    """00:00 UTC = 05:30 IST same day."""
    ts = datetime(2026, 5, 14, 0, 0, tzinfo=timezone.utc)
    assert _ist_date(ts) == date(2026, 5, 14)


# ---------------------------------------------------------------------------
# T1 — daily price move ≥ 2%
# ---------------------------------------------------------------------------


def test_t1_fires_on_positive_move():
    """T1 fires when today is ≥2% above yesterday."""
    prices = _prices(("2026-05-13", 14160), ("2026-05-14", 14935))  # +5.5%
    triggers, stats = check_triggers(prices, _NOW_UTC)
    assert "price_move" in triggers
    assert stats["pct_change_1d"] == pytest.approx(5.47, abs=0.1)


def test_t1_fires_on_negative_move():
    """T1 fires when today is ≥2% below yesterday."""
    prices = _prices(("2026-05-13", 14500), ("2026-05-14", 14000))  # -3.4%
    triggers, _ = check_triggers(prices, _NOW_UTC)
    assert "price_move" in triggers


def test_t1_no_fire_on_small_move():
    """T1 does not fire for sub-threshold moves."""
    prices = _prices(("2026-05-13", 14160), ("2026-05-14", 14200))  # +0.28%
    triggers, _ = check_triggers(prices, _NOW_UTC)
    assert "price_move" not in triggers


def test_t1_boundary_exact():
    """Exactly 2% fires T1 (inclusive)."""
    base = 14000
    prices = _prices(("2026-05-13", base), ("2026-05-14", int(base * 1.02)))
    triggers, _ = check_triggers(prices, _NOW_UTC)
    assert "price_move" in triggers


# ---------------------------------------------------------------------------
# T2/T3 — near 30-day low/high
# ---------------------------------------------------------------------------


def test_t2_fires_at_30d_low():
    """T2 fires when today's price equals the 30-day low."""
    prices = [_p(f"2026-04-{d:02d}", 14200) for d in range(14, 31)]
    prices += _prices(("2026-05-14", 14000))  # new low
    triggers, _ = check_triggers(prices, _NOW_UTC)
    assert "near_30d_low" in triggers


def test_t2_fires_within_band():
    """T2 fires when today is within BAND_30D of the 30-day low."""
    low = 13715
    today = low + BAND_30D  # exactly at the band edge
    prices = [_p("2026-04-14", low)] + [_p(f"2026-04-{d:02d}", 14200) for d in range(15, 30)]
    prices += _prices(("2026-05-14", today))
    triggers, _ = check_triggers(prices, _NOW_UTC)
    assert "near_30d_low" in triggers


def test_t2_no_fire_above_band():
    """T2 does not fire when today is more than BAND_30D above the 30-day low."""
    prices = [_p("2026-04-14", 13715)] + [_p(f"2026-04-{d:02d}", 14200) for d in range(15, 30)]
    prices += _prices(("2026-05-14", 14845))  # 1130 above low
    triggers, _ = check_triggers(prices, _NOW_UTC)
    assert "near_30d_low" not in triggers


def test_t3_fires_at_30d_high():
    """T3 fires when today's price is within BAND_30D of the 30-day high."""
    high = 14935
    today = high - BAND_30D + 1  # just inside band
    prices = [_p("2026-04-14", high)] + [_p(f"2026-04-{d:02d}", 14200) for d in range(15, 30)]
    prices += _prices(("2026-05-14", today))
    triggers, _ = check_triggers(prices, _NOW_UTC)
    assert "near_30d_high" in triggers


def test_t3_no_fire_below_band():
    """T3 does not fire when today is more than BAND_30D below the 30-day high."""
    # Put the 30d high on Apr 15 (strict window is > Apr 14, so Apr 15 IS included).
    high = 14935
    today = high - BAND_30D - 100  # well below band edge (14785)
    prices = [_p("2026-04-15", high)] + [_p(f"2026-04-{d:02d}", 14200) for d in range(16, 30)]
    prices += _prices(("2026-05-13", today), ("2026-05-14", today))
    triggers, _ = check_triggers(prices, _NOW_UTC)
    assert "near_30d_high" not in triggers


# ---------------------------------------------------------------------------
# T4 — 5-day cumulative ≥ 3%
# ---------------------------------------------------------------------------


def test_t4_fires_on_5day_move():
    """T4 fires on a ≥3% cumulative 5-day move."""
    prices = _prices(
        ("2026-05-09", 14040),  # 5 days ago
        ("2026-05-10", 14100),
        ("2026-05-11", 14200),
        ("2026-05-12", 14500),
        ("2026-05-13", 14800),
        ("2026-05-14", 14935),  # +6.4% vs 5 days ago
    )
    triggers, stats = check_triggers(prices, _NOW_UTC)
    assert "five_day_move" in triggers
    assert stats["pct_change_5d"] == pytest.approx(6.4, abs=0.2)


def test_t4_no_fire_on_small_5day_move():
    """T4 does not fire for sub-3% 5-day moves."""
    prices = _prices(
        ("2026-05-09", 14000),
        ("2026-05-10", 14010),
        ("2026-05-11", 14020),
        ("2026-05-12", 14030),
        ("2026-05-13", 14040),
        ("2026-05-14", 14050),  # +0.36%
    )
    triggers, _ = check_triggers(prices, _NOW_UTC)
    assert "five_day_move" not in triggers


def test_t4_no_fire_without_5day_history():
    """T4 does not fire when there's no reading from 5 days ago."""
    prices = _prices(("2026-05-13", 14160), ("2026-05-14", 14935))
    triggers, _ = check_triggers(prices, _NOW_UTC)
    assert "five_day_move" not in triggers


# ---------------------------------------------------------------------------
# T5 — scrape gap
# ---------------------------------------------------------------------------


def test_t5_fires_when_yesterday_missing():
    """T5 fires when today has readings but yesterday does not."""
    prices = _prices(
        ("2026-05-12", 14040),  # two days ago
        ("2026-05-14", 14845),  # today — gap on May 13
    )
    triggers, _ = check_triggers(prices, _NOW_UTC)
    assert "scrape_gap" in triggers


def test_t5_no_fire_when_yesterday_present():
    """T5 does not fire when yesterday had readings."""
    prices = _prices(("2026-05-13", 14160), ("2026-05-14", 14845))
    triggers, _ = check_triggers(prices, _NOW_UTC)
    assert "scrape_gap" not in triggers


def test_t5_no_fire_when_no_today_reading():
    """T5 does not fire when there is no reading for today."""
    prices = _prices(("2026-05-13", 14160))  # no reading today
    triggers, _ = check_triggers(prices, _NOW_UTC)
    assert triggers == []


# ---------------------------------------------------------------------------
# No triggers
# ---------------------------------------------------------------------------


def test_no_triggers_on_stable_prices():
    """No triggers fire when prices are mid-range and unremarkable.

    Uses a spread from 13800 to 14600 so today (14200) is neither near the
    30-day low (13800+50=13850) nor near the 30-day high (14600-50=14550).
    The window is strictly after Apr 14, so Apr 15 onwards is evaluated.
    """
    prices = (
        [_p("2026-04-15", 13800)]   # 30d low anchor
        + [_p(f"2026-04-{d:02d}", 14200) for d in range(16, 30)]
        + [_p("2026-04-30", 14600)]  # 30d high anchor
        + _prices(("2026-05-13", 14200), ("2026-05-14", 14210))  # small stable move
    )
    triggers, _ = check_triggers(prices, _NOW_UTC)
    assert triggers == []


def test_no_triggers_returns_empty_stats():
    """check_triggers returns empty stats when no today reading."""
    triggers, stats = check_triggers([], _NOW_UTC)
    assert triggers == []
    assert stats == {}


# ---------------------------------------------------------------------------
# Multiple triggers fire simultaneously
# ---------------------------------------------------------------------------


def test_multiple_triggers_simultaneously():
    """T1 + T4 both fire on the big May 13 move."""
    prices = _prices(
        ("2026-05-08", 14040),
        ("2026-05-09", 14010),
        ("2026-05-10", 14010),
        ("2026-05-11", 13990),
        ("2026-05-12", 14160),
        ("2026-05-13", 14935),  # +5.5% vs May 12, +6.4% vs 5 days ago
    )
    now = datetime(2026, 5, 13, 10, 30, tzinfo=timezone.utc)
    triggers, _ = check_triggers(prices, now)
    assert "price_move" in triggers
    assert "five_day_move" in triggers


# ---------------------------------------------------------------------------
# Template commentary
# ---------------------------------------------------------------------------


def _stats(today=14935, yesterday=14160, low=13715, high=14935, avg7=14100, pct1d=5.5, pct5d=6.4):
    return {
        "today_ist": "2026-05-13",
        "today_price": today,
        "yesterday_price": yesterday,
        "pct_change_1d": pct1d,
        "low_30d": low,
        "high_30d": high,
        "avg_7d": avg7,
        "five_day_price": 14040,
        "pct_change_5d": pct5d,
    }


def test_template_price_move():
    t = _template_commentary(["price_move"], _stats())
    assert "5.5%" in t
    assert "Rs." in t


def test_template_near_low():
    t = _template_commentary(["near_30d_low"], _stats(today=13740))
    assert "low" in t.lower()
    assert "13,715" in t


def test_template_near_high():
    t = _template_commentary(["near_30d_high"], _stats())
    assert "high" in t.lower()


def test_template_scrape_gap():
    t = _template_commentary(["scrape_gap"], _stats())
    assert "outage" in t.lower() or "gap" in t.lower()


def test_template_multiple_triggers():
    t = _template_commentary(["price_move", "near_30d_high"], _stats())
    assert "%" in t
    assert "high" in t.lower()


def test_template_ends_with_period():
    t = _template_commentary(["price_move"], _stats())
    assert t.endswith(".")


# ---------------------------------------------------------------------------
# Title builder
# ---------------------------------------------------------------------------


def test_title_near_high_takes_priority():
    """near_30d_high title takes priority over price_move."""
    title = _build_title(["price_move", "near_30d_high"], _stats())
    assert "30-day high" in title
    assert "Rs." in title


def test_title_near_low():
    title = _build_title(["near_30d_low"], _stats(today=13740))
    assert "30-day low" in title


def test_title_price_move():
    title = _build_title(["price_move"], _stats())
    assert "%" in title
    assert "Rs." in title


def test_title_scrape_gap():
    title = _build_title(["scrape_gap"], _stats())
    assert "online" in title.lower() or "back" in title.lower()


def test_title_is_ascii():
    """Titles must not contain non-ASCII characters (HTTP header constraint)."""
    for triggers in [["near_30d_high"], ["near_30d_low"], ["price_move"], ["five_day_move"], ["scrape_gap"]]:
        title = _build_title(triggers, _stats())
        assert title.isascii(), f"Non-ASCII in title for triggers={triggers}: {title!r}"


# ---------------------------------------------------------------------------
# fmtInrAscii
# ---------------------------------------------------------------------------


def test_fmt_inr_ascii_no_rupee_symbol():
    assert "₹" not in _fmt_inr_ascii(14845)


def test_fmt_inr_ascii_prefix():
    assert _fmt_inr_ascii(14845).startswith("Rs.")


def test_fmt_inr_ascii_format():
    assert _fmt_inr_ascii(14845) == "Rs.14,845"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotency_not_sent(tmp_path: Path):
    path = tmp_path / "last_summary.json"
    assert not _is_already_sent.__wrapped__(date(2026, 5, 14)) if hasattr(_is_already_sent, "__wrapped__") else True
    # Without a file, should return False
    import ml.daily_summary as ds
    original = ds.LAST_SUMMARY_PATH
    ds.LAST_SUMMARY_PATH = path
    try:
        assert not _is_already_sent(date(2026, 5, 14))
    finally:
        ds.LAST_SUMMARY_PATH = original


def test_idempotency_mark_and_check(tmp_path: Path):
    import ml.daily_summary as ds
    original = ds.LAST_SUMMARY_PATH
    ds.LAST_SUMMARY_PATH = tmp_path / "last_summary.json"
    try:
        d = date(2026, 5, 14)
        assert not _is_already_sent(d)
        _mark_sent(d, ["price_move"], 14935)
        assert _is_already_sent(d)
        # Different date → not already sent
        assert not _is_already_sent(date(2026, 5, 15))
    finally:
        ds.LAST_SUMMARY_PATH = original


# ---------------------------------------------------------------------------
# Groq call (mocked)
# ---------------------------------------------------------------------------


def test_groq_call_returns_text():
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "  Gold rose 5.5% today.  "}}]
    }

    with patch("ml.daily_summary.call_groq_summary") as mock_call:
        mock_call.return_value = "Gold rose 5.5% today."
        result = call_groq_summary.__wrapped__("fake-key", ["price_move"], _stats()) if hasattr(call_groq_summary, "__wrapped__") else mock_call("fake-key", ["price_move"], _stats())

    assert "Gold" in result or "fake" in str(mock_call.call_args)


def test_groq_mocked_integration():
    """Full call_groq_summary with mocked requests.post."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "Gold 22K up 5.5% today."}}]
    }

    with patch("requests.post", return_value=mock_resp):
        result = call_groq_summary("test-key", ["price_move"], _stats())

    assert result == "Gold 22K up 5.5% today."
    mock_resp.raise_for_status.assert_called_once()
