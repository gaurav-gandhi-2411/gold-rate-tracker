"""Tests for ml/metrics.py."""

from pathlib import Path

import numpy as np
from ml.metrics import (
    DROP_THRESHOLD,
    OUTCOME_WINDOW,
    aggregate_metrics,
    compute_decision,
    record_prediction,
    resolve_outcome,
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
    prices = _prices(
        [
            ("2026-05-01", 14845),
            ("2026-05-02", 14700),  # drop of 145 — correct
            ("2026-05-03", 14720),
            ("2026-05-04", 14750),
            ("2026-05-05", 14780),
            ("2026-05-06", 14800),
        ]
    )
    result = resolve_outcome(entry, prices)
    assert result["outcome"] == "correct"
    assert result["min_future_price"] == 14700


def test_outcome_incorrect():
    """5 future prices all > current - 100 → incorrect."""
    entry = _entry(decision_date="2026-05-01", current_22k=14845, decision="wait")
    prices = _prices(
        [
            ("2026-05-01", 14845),
            ("2026-05-02", 14800),
            ("2026-05-03", 14810),
            ("2026-05-04", 14820),
            ("2026-05-05", 14830),
            ("2026-05-06", 14840),
        ]
    )
    result = resolve_outcome(entry, prices)
    assert result["outcome"] == "incorrect"
    assert result["min_future_price"] == 14800


def test_outcome_pending():
    """Fewer than 5 future prices → stays pending."""
    entry = _entry(decision_date="2026-05-01", current_22k=14845, decision="wait")
    prices = _prices(
        [
            ("2026-05-01", 14845),
            ("2026-05-02", 14700),
            ("2026-05-03", 14720),
        ]
    )
    result = resolve_outcome(entry, prices)
    assert result["outcome"] == "pending"


def test_weekend_skipped():
    """Saturday/Sunday carry-forward entries are excluded from the outcome window."""
    entry = _entry(decision_date="2026-05-08", current_22k=14845, decision="wait")
    # 2026-05-08 is a Friday. Weekend prices carry forward.
    prices = _prices(
        [
            ("2026-05-08", 14845),  # Friday — decision date
            ("2026-05-09", 14845),  # Saturday carry-forward — must be EXCLUDED
            ("2026-05-10", 14845),  # Sunday carry-forward — must be EXCLUDED
            ("2026-05-11", 14900),  # Monday — trading day 1
            ("2026-05-12", 14910),  # Tuesday — trading day 2
            ("2026-05-13", 14920),  # Wednesday — trading day 3
            ("2026-05-14", 14930),  # Thursday — trading day 4
            ("2026-05-15", 14940),  # Friday — trading day 5
        ]
    )
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

    prices_data = [
        {"timestamp": "2026-05-14T06:30:00.000Z", "22k": 14845, "24k": 16000, "18k": 12000}
    ]
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
        _resolved_entry("2026-05-02", 14845, 14645, 14545, "wait", "correct"),  # |100|
    ]
    result = aggregate_metrics(entries, window_days=365)
    assert result["n_mae"] == 2
    assert abs(result["mae"] - 72.5) < 0.01


def test_aggregate_directional():
    """Directional accuracy: correct if sign(delta) == sign(actual change)."""
    entries = [
        _resolved_entry(
            "2026-05-01", 14845, 14945, 14950, "buy_now", "resolved"
        ),  # delta+, actual+ → correct
        _resolved_entry(
            "2026-05-02", 14845, 14945, 14800, "buy_now", "resolved"
        ),  # delta+, actual- → wrong
        _resolved_entry(
            "2026-05-03", 14845, 14645, 14700, "wait", "incorrect"
        ),  # delta-, actual- → correct
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


# ============================================================
# h=5 metric functions (PR F)
# ============================================================


def test_mae_per_horizon_basic():
    """MAE computed correctly per horizon step."""
    from ml.metrics import compute_mae_per_horizon

    actuals = np.array([[10.0, 20.0, 30.0], [10.0, 20.0, 30.0]])
    preds = np.array([[12.0, 18.0, 33.0], [8.0, 22.0, 27.0]])
    mae = compute_mae_per_horizon(actuals, preds)
    assert abs(mae[0] - 2.0) < 1e-6  # mean(|12-10|, |8-10|)
    assert abs(mae[1] - 2.0) < 1e-6  # mean(|18-20|, |22-20|)
    assert abs(mae[2] - 3.0) < 1e-6  # mean(|33-30|, |27-30|)


def test_mae_per_horizon_perfect():
    """Perfect forecast → MAE = 0 at every step."""
    from ml.metrics import compute_mae_per_horizon

    m = np.ones((5, 5)) * 14000.0
    assert all(v == 0.0 for v in compute_mae_per_horizon(m, m))


def test_dir_acc_h5_all_correct():
    """All folds have correct directional prediction → 1.0."""
    from ml.metrics import compute_dir_acc_h5

    context_lasts = np.array([100.0, 100.0, 100.0])
    p50_h5 = np.array([110.0, 90.0, 105.0])  # +, -, +
    actuals_h5 = np.array([115.0, 85.0, 108.0])  # +, -, +
    assert compute_dir_acc_h5(context_lasts, p50_h5, actuals_h5) == 1.0


def test_dir_acc_h5_all_wrong():
    """All folds have wrong directional prediction → 0.0."""
    from ml.metrics import compute_dir_acc_h5

    context_lasts = np.array([100.0, 100.0])
    p50_h5 = np.array([110.0, 90.0])  # predicted up, predicted down
    actuals_h5 = np.array([90.0, 110.0])  # actual down, actual up
    assert compute_dir_acc_h5(context_lasts, p50_h5, actuals_h5) == 0.0


def test_dir_acc_h5_zero_actual_move_counted_wrong():
    """Folds where actual_h5 == context_last (zero move) are counted as wrong."""
    from ml.metrics import compute_dir_acc_h5

    context_lasts = np.array([100.0])
    p50_h5 = np.array([110.0])  # predicted up
    actuals_h5 = np.array([100.0])  # actual no move → zero → wrong
    assert compute_dir_acc_h5(context_lasts, p50_h5, actuals_h5) == 0.0


def test_pi_coverage_all_inside():
    """All actuals inside [p10, p90] → coverage 1.0 at every step."""
    from ml.metrics import compute_pi_coverage

    n, h = 5, 3
    actuals = np.full((n, h), 100.0)
    p10 = np.full((n, h), 90.0)
    p90 = np.full((n, h), 110.0)
    cov = compute_pi_coverage(actuals, p10, p90)
    assert all(v == 1.0 for v in cov)


def test_pi_coverage_all_outside():
    """All actuals outside [p10, p90] → coverage 0.0 at every step."""
    from ml.metrics import compute_pi_coverage

    n, h = 4, 2
    actuals = np.full((n, h), 200.0)
    p10 = np.full((n, h), 90.0)
    p90 = np.full((n, h), 110.0)
    cov = compute_pi_coverage(actuals, p10, p90)
    assert all(v == 0.0 for v in cov)


def test_decision_accuracy_h5_zero_predicted():
    """No predicted drops → n_predicted=0, precision=None, recall=0.0 (missed all actual drops)."""
    from ml.metrics import compute_decision_accuracy_h5

    context_lasts = np.array([14000.0, 14000.0])
    # p50 never drops 100 from context_last (14000 - 100 = 13900)
    p50 = np.array([[14050.0] * 5, [14020.0] * 5])
    actuals = np.array([[13800.0] * 5, [13700.0] * 5])  # actual big drops
    result = compute_decision_accuracy_h5(context_lasts, p50, actuals)
    assert result["n_chronos_predicted_100_drop"] == 0
    assert result["precision"] is None
    assert result["recall"] == 0.0  # 0 TP / 2 actual drops = 0, not undefined
    assert result["n_actual_100_drops_total"] == 2  # both had actual drops


def test_decision_accuracy_h5_zero_actual():
    """Predicted drops but no actual drops → recall=None (no actual drops total)."""
    from ml.metrics import compute_decision_accuracy_h5

    context_lasts = np.array([14000.0, 14000.0])
    p50 = np.array([[13800.0] * 5, [13850.0] * 5])  # both predict big drops
    actuals = np.array([[14100.0] * 5, [14050.0] * 5])  # no actual drops
    result = compute_decision_accuracy_h5(context_lasts, p50, actuals)
    assert result["n_chronos_predicted_100_drop"] == 2
    assert result["n_actual_100_drop_when_predicted"] == 0
    assert result["precision"] == 0.0
    assert result["n_actual_100_drops_total"] == 0
    assert result["recall"] is None


def test_decision_accuracy_h5_perfect():
    """Every predicted drop is an actual drop → precision=1.0, recall=1.0."""
    from ml.metrics import compute_decision_accuracy_h5

    context_lasts = np.array([14000.0, 14000.0])
    p50 = np.array([[13800.0] * 5, [13850.0] * 5])  # both predict drops >=100
    actuals = np.array([[13700.0] * 5, [13750.0] * 5])  # both have actual drops >=100
    result = compute_decision_accuracy_h5(context_lasts, p50, actuals)
    assert result["precision"] == 1.0
    assert result["recall"] == 1.0


def test_peak_timing_error_zero():
    """When argmin(p50) == argmin(actual) for every fold → median error = 0."""
    from ml.metrics import compute_peak_timing_error

    # Both p50 and actuals have their minimum at index 0 in every fold
    p50 = np.array([[1.0, 5.0, 5.0, 5.0, 5.0], [2.0, 5.0, 5.0, 5.0, 5.0]])
    actuals = np.array([[1.5, 5.0, 5.0, 5.0, 5.0], [2.5, 5.0, 5.0, 5.0, 5.0]])
    assert compute_peak_timing_error(p50, actuals) == 0.0


def test_peak_timing_error_nonzero():
    """Predictable off-by-one in trough timing → median error = 1."""
    from ml.metrics import compute_peak_timing_error

    # p50 minimum at index 0; actual minimum at index 1
    p50 = np.array([[1.0, 5.0, 5.0, 5.0, 5.0], [1.0, 5.0, 5.0, 5.0, 5.0]])
    actuals = np.array([[5.0, 1.5, 5.0, 5.0, 5.0], [5.0, 1.5, 5.0, 5.0, 5.0]])
    assert compute_peak_timing_error(p50, actuals) == 1.0


def test_peak_timing_error_empty():
    """Empty arrays → None."""
    from ml.metrics import compute_peak_timing_error

    result = compute_peak_timing_error(np.zeros((0, 5)), np.zeros((0, 5)))
    assert result is None


def test_wilcoxon_p_null_when_n_lt_6():
    """Returns None when fewer than 6 paired diffs."""
    from ml.metrics import compute_wilcoxon_p

    assert compute_wilcoxon_p([1.0, 2.0, -1.0]) is None
    assert compute_wilcoxon_p([]) is None


def test_wilcoxon_p_all_zeros():
    """All-zero diffs → p-value 1.0 (no systematic difference)."""
    from ml.metrics import compute_wilcoxon_p

    result = compute_wilcoxon_p([0.0] * 10)
    assert result == 1.0


# ============================================================
# Displayed-band coverage (naive_flat_hold headline PI) — bug fix:
# app.js previously attributed bt.pi_coverage_80_5d_avg (Chronos's own quantile PI)
# to this band; these guard the metric that's actually sourced correctly now.
# ============================================================


def test_band_coverage_all_inside():
    """All resolved entries fall inside [lower, upper] → coverage 1.0."""
    from ml.metrics import compute_band_coverage

    entries = [
        _resolved_entry("2026-05-01", 14845, 14845, 14800, "neutral", "resolved"),
        _resolved_entry("2026-05-02", 14845, 14845, 14900, "neutral", "resolved"),
    ]
    result = compute_band_coverage(entries)
    assert result["n"] == 2
    assert result["n_in_band"] == 2
    assert result["coverage"] == 1.0


def test_band_coverage_partial():
    """One of three resolved entries falls outside its band → coverage 2/3."""
    from ml.metrics import compute_band_coverage

    entries = [
        _resolved_entry(
            "2026-05-01", 14845, 14845, 14800, "neutral", "resolved"
        ),  # in [14645,15145]
        _resolved_entry(
            "2026-05-02", 14845, 14845, 14900, "neutral", "resolved"
        ),  # in [14645,15145]
        _resolved_entry("2026-05-03", 14845, 14845, 20000, "neutral", "resolved"),  # outside
    ]
    result = compute_band_coverage(entries)
    assert result["n"] == 3
    assert result["n_in_band"] == 2
    assert abs(result["coverage"] - 2 / 3) < 0.001


def test_band_coverage_excludes_pending():
    """Pending entries (no actual_next_22k yet) never count toward n."""
    from ml.metrics import compute_band_coverage

    entries = [
        _resolved_entry("2026-05-01", 14845, 14845, 14800, "neutral", "resolved"),
        _entry(decision_date="2026-05-02", outcome="pending", actual_next_22k=None),
    ]
    result = compute_band_coverage(entries)
    assert result["n"] == 1


def test_band_coverage_empty():
    """No resolved entries → coverage None, n 0 — caller must handle, not fabricate."""
    from ml.metrics import compute_band_coverage

    result = compute_band_coverage([])
    assert result["coverage"] is None
    assert result["n"] == 0
    assert result["n_in_band"] == 0


def test_save_coverage_metrics_persists_and_grows(tmp_path: Path):
    """save_coverage_metrics writes a schema'd JSON file whose n grows as more
    entries resolve — not a static one-time snapshot."""
    import json

    from ml.metrics import save_coverage_metrics

    metrics_path = tmp_path / "metrics_history.json"
    out_path = tmp_path / "coverage_metrics.json"

    entries = [_resolved_entry("2026-05-01", 14845, 14845, 14800, "neutral", "resolved")]
    metrics_path.write_text(json.dumps(entries))

    payload1 = save_coverage_metrics(metrics_path=metrics_path, out_path=out_path)
    assert payload1["n"] == 1
    assert payload1["coverage"] == 1.0
    assert payload1["schema_version"] == 1
    assert json.loads(out_path.read_text())["n"] == 1

    entries.append(_resolved_entry("2026-05-02", 14845, 14845, 20000, "neutral", "resolved"))
    metrics_path.write_text(json.dumps(entries))

    payload2 = save_coverage_metrics(metrics_path=metrics_path, out_path=out_path)
    assert payload2["n"] == 2
    assert payload2["n_in_band"] == 1
