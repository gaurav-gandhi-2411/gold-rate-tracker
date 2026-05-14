"""Tests for ml/metrics.py."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from ml.metrics import (
    DROP_THRESHOLD,
    OUTCOME_WINDOW,
    aggregate_metrics,
    compute_decision,
    record_prediction,
    resolve_outcome,
    resolve_pending,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _prices(pairs: list[tuple[str, int]], base_time: str = "T06:30:00.000Z") -> list[dict]:
    """Build minimal prices list from (date_str, 22k_price) pairs."""
    return [
        {"timestamp": f"{d}{base_time}", "22k": p, "24k": p + 1000, "18k": p - 1000}
        for d, p in pairs
    ]


def _entry(
    decision_date: str = "2026-05-14",
    current_22k: int = 14845,
    predicted_22k: int = 14965,
    delta: float = 120.0,
    decision: str = "neutral",
    outcome: str = "pending",
    actual_next_22k: int | None = None,
    drop_threshold: float = DROP_THRESHOLD,
) -> dict:
    return {
        "decision_date": decision_date,
        "predicted_at": "2026-05-14T12:00:00Z",
        "current_22k": current_22k,
        "predicted_22k": predicted_22k,
        "delta": delta,
        "lower": predicted_22k - 200,
        "upper": predicted_22k + 300,
        "decision": decision,
        "outcome_window_days": OUTCOME_WINDOW,
        "outcome_resolved_at": None,
        "outcome": outcome,
        "min_future_price": None,
        "actual_next_22k": actual_next_22k,
        "drop_threshold": drop_threshold,
        "track": "real",
        "model_version": "lgbm-only",
        "real_readings_count": 58,
    }


# ---------------------------------------------------------------------------
# Decision rule
# ---------------------------------------------------------------------------


def test_decision_rule_wait():
    assert compute_decision(-150.0) == "wait"


def test_decision_rule_neutral():
    assert compute_decision(-50.0) == "neutral"


def test_decision_rule_buy_now():
    assert compute_decision(150.0) == "buy_now"


def test_decision_boundary_exact():
    assert compute_decision(-100.0) == "wait"


def test_decision_boundary_just_inside_neutral():
    assert compute_decision(-99.9) == "neutral"


def test_decision_buy_now_boundary():
    assert compute_decision(100.0) == "buy_now"


# ---------------------------------------------------------------------------
# Outcome resolution
# ---------------------------------------------------------------------------


def test_outcome_correct():
    """5 future prices with min ≤ current - 100 → correct."""
    entry = _entry(decision_date="2026-05-01", current_22k=14845, decision="wait")
    prices = _prices([
        ("2026-05-01", 14845),
        ("2026-05-02", 14700),  # drop of 145 — correct
        ("2026-05-03", 14720),
        ("2026-05-04", 14750),
        ("2026-05-05", 14780),
        ("2026-05-06", 14800),
    ])
    result = resolve_outcome(entry, prices)
    assert result["outcome"] == "correct"
    assert result["min_future_price"] == 14700


def test_outcome_incorrect():
    """5 future prices all > current - 100 → incorrect."""
    entry = _entry(decision_date="2026-05-01", current_22k=14845, decision="wait")
    prices = _prices([
        ("2026-05-01", 14845),
        ("2026-05-02", 14800),
        ("2026-05-03", 14810),
        ("2026-05-04", 14820),
        ("2026-05-05", 14830),
        ("2026-05-06", 14840),
    ])
    result = resolve_outcome(entry, prices)
    assert result["outcome"] == "incorrect"
    assert result["min_future_price"] == 14800


def test_outcome_pending():
    """Fewer than 5 future prices → stays pending."""
    entry = _entry(decision_date="2026-05-01", current_22k=14845, decision="wait")
    prices = _prices([
        ("2026-05-01", 14845),
        ("2026-05-02", 14700),
        ("2026-05-03", 14720),
    ])
    result = resolve_outcome(entry, prices)
    assert result["outcome"] == "pending"


def test_weekend_skipped():
    """Saturday/Sunday carry-forward entries are excluded from the outcome window."""
    entry = _entry(decision_date="2026-05-08", current_22k=14845, decision="wait")
    # 2026-05-08 is a Friday. Weekend prices carry forward.
    prices = _prices([
        ("2026-05-08", 14845),   # Friday — decision date
        ("2026-05-09", 14845),   # Saturday carry-forward — must be EXCLUDED
        ("2026-05-10", 14845),   # Sunday carry-forward — must be EXCLUDED
        ("2026-05-11", 14900),   # Monday — trading day 1
        ("2026-05-12", 14910),   # Tuesday — trading day 2
        ("2026-05-13", 14920),   # Wednesday — trading day 3
        ("2026-05-14", 14930),   # Thursday — trading day 4
        ("2026-05-15", 14940),   # Friday — trading day 5
    ])
    result = resolve_outcome(entry, prices)
    # All 5 trading-day prices are > 14845 - 100 = 14745 → incorrect
    assert result["outcome"] == "incorrect"
    assert result["min_future_price"] == 14900


def test_already_resolved_is_unchanged():
    """resolve_outcome is a no-op when outcome is not 'pending'."""
    entry = _entry(decision="wait", outcome="correct")
    entry["outcome_resolved_at"] = "2026-05-19T06:00:00Z"
    prices = _prices([("2026-05-14", 14845)])
    result = resolve_outcome(entry, prices)
    assert result["outcome"] == "correct"
    assert result["outcome_resolved_at"] == "2026-05-19T06:00:00Z"


# ---------------------------------------------------------------------------
# record_prediction idempotency
# ---------------------------------------------------------------------------


def test_idempotency(tmp_path: Path):
    """Calling record twice on the same day does not duplicate the entry."""
    forecast = {
        "predicted_at": "2026-05-14T12:00:00Z",
        "predicted_22k": 14965,
        "lower": 14791,
        "upper": 15236,
        "model_version": "lgbm-only",
        "real_readings_count": 58,
    }
    forecast_path = tmp_path / "forecast.json"
    forecast_path.write_text(__import__("json").dumps(forecast))

    prices_data = [{"timestamp": "2026-05-14T06:30:00.000Z", "22k": 14845, "24k": 16000, "18k": 12000}]
    prices_path = tmp_path / "prices.json"
    prices_path.write_text(__import__("json").dumps(prices_data))

    out_path = tmp_path / "metrics_history.json"

    r1 = record_prediction(forecast_path, prices_path, out_path)
    r2 = record_prediction(forecast_path, prices_path, out_path)

    assert r1 is True
    assert r2 is False

    import json
    entries = json.loads(out_path.read_text())
    assert len(entries) == 1


# ---------------------------------------------------------------------------
# aggregate_metrics
# ---------------------------------------------------------------------------


def _resolved_entry(
    decision_date: str,
    current_22k: int,
    predicted_22k: int,
    actual_next_22k: int,
    decision: str,
    outcome: str,
) -> dict:
    delta = float(predicted_22k - current_22k)
    e = _entry(
        decision_date=decision_date,
        current_22k=current_22k,
        predicted_22k=predicted_22k,
        delta=delta,
        decision=decision,
        outcome=outcome,
        actual_next_22k=actual_next_22k,
    )
    e["outcome_resolved_at"] = f"{decision_date}T12:00:00Z"
    return e


def test_aggregate_decision_accuracy():
    """3 wait signals: 2 correct, 1 incorrect → 67%."""
    entries = [
        _resolved_entry("2026-05-01", 14845, 14600, 14500, "wait", "correct"),
        _resolved_entry("2026-05-02", 14845, 14600, 14600, "wait", "correct"),
        _resolved_entry("2026-05-03", 14845, 14600, 14850, "wait", "incorrect"),
        _resolved_entry("2026-05-04", 14845, 14900, 14870, "neutral", "resolved"),
    ]
    result = aggregate_metrics(entries, window_days=365)
    assert result["n_wait_resolved"] == 3
    assert abs(result["decision_accuracy"] - 2 / 3) < 0.001


def test_aggregate_mae():
    """MAE computed from all resolved entries with actual_next_22k."""
    entries = [
        _resolved_entry("2026-05-01", 14845, 14945, 14900, "neutral", "resolved"),  # |45|
        _resolved_entry("2026-05-02", 14845, 14645, 14545, "wait", "correct"),      # |100|
    ]
    result = aggregate_metrics(entries, window_days=365)
    assert result["n_mae"] == 2
    assert abs(result["mae"] - 72.5) < 0.01


def test_aggregate_directional():
    """Directional accuracy: correct if sign(delta) == sign(actual change)."""
    entries = [
        _resolved_entry("2026-05-01", 14845, 14945, 14950, "buy_now", "resolved"),  # delta+, actual+ → correct
        _resolved_entry("2026-05-02", 14845, 14945, 14800, "buy_now", "resolved"),  # delta+, actual- → wrong
        _resolved_entry("2026-05-03", 14845, 14645, 14700, "wait", "incorrect"),    # delta-, actual- → correct
    ]
    result = aggregate_metrics(entries, window_days=365)
    assert result["n_dir"] == 3
    assert abs(result["directional_accuracy"] - 2 / 3) < 0.001


def test_aggregate_empty():
    """No entries → all None values, no exception."""
    result = aggregate_metrics([], window_days=30)
    assert result["decision_accuracy"] is None
    assert result["mae"] is None
    assert result["directional_accuracy"] is None
    assert result["n_wait_resolved"] == 0
    assert result["n_mae"] == 0


def test_aggregate_no_wait_decisions():
    """All resolved but no wait → decision_accuracy is None, MAE still computes."""
    entries = [
        _resolved_entry("2026-05-01", 14845, 14945, 14900, "neutral", "resolved"),
        _resolved_entry("2026-05-02", 14845, 14945, 14910, "buy_now", "resolved"),
    ]
    result = aggregate_metrics(entries, window_days=365)
    assert result["decision_accuracy"] is None
    assert result["n_wait_resolved"] == 0
    assert result["mae"] is not None
