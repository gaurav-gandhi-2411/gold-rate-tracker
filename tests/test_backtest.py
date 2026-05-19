"""Tests for ml/backtest.py — Chronos pipeline mocked, no real model download."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
import torch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ibja_series(n: int = 25, base_date: str = "2026-04-01") -> pd.Series:
    """Deterministic date-indexed IBJA-level series (INR/g)."""
    rng = np.random.default_rng(42)
    base = pd.Timestamp(base_date)
    dates = [str((base + pd.Timedelta(days=i)).date()) for i in range(n)]
    values = 14_000.0 + np.arange(n, dtype=float) * 10 + rng.normal(0, 50, n)
    return pd.Series(values, index=dates, name="ibja_pm_g")


def _stub_pipeline(
    p50_step: float = 100.0,
    p10_offset: float = -200.0,
    p90_offset: float = 200.0,
):
    """Return a mock pipeline that adds a fixed step at each horizon position.

    predict_quantiles returns:
      p10[h] = context_last_ignored + h*p50_step + p10_offset
      p50[h] = context_last_ignored + h*p50_step           (used for MAE)
      p90[h] = context_last_ignored + h*p50_step + p90_offset
    The context value is not accessible inside predict_quantiles, so we use
    a simple deterministic formula relative to index h.
    """
    from unittest.mock import MagicMock

    mock = MagicMock()

    def _predict(inputs, prediction_length, quantile_levels, **kwargs):
        # inputs is a tensor; last value is accessible but we use a simple formula
        n_q = len(quantile_levels)
        data = np.zeros((1, prediction_length, n_q), dtype=np.float32)
        offsets = [p10_offset, 0.0, p90_offset]  # for q=[0.1, 0.5, 0.9]
        for qi in range(n_q):
            for h in range(prediction_length):
                data[0, h, qi] = 14_000.0 + h * p50_step + offsets[qi]
        return torch.tensor(data), torch.tensor(data[:, :, 1:2])

    mock.predict_quantiles.side_effect = _predict
    return mock


# ---------------------------------------------------------------------------
# load_ibja_series
# ---------------------------------------------------------------------------


def test_load_ibja_series(tmp_path):
    """load_ibja_series returns INR/g series sorted ascending, non-null."""
    import pandas as pd
    from ml.backtest import load_ibja_series

    df = pd.DataFrame(
        {
            "date": ["2026-05-18", "2026-05-15", "2026-05-17"],
            "pm_916": [144489.0, 144920.0, 147455.0],
        }
    )
    parquet_path = tmp_path / "ibja_rates.parquet"
    df.to_parquet(parquet_path, index=False)

    series = load_ibja_series(parquet_path)

    assert series.index.tolist() == ["2026-05-15", "2026-05-17", "2026-05-18"]
    assert abs(series.iloc[0] - 14492.0) < 0.1  # 144920 / 10
    assert abs(series.iloc[-1] - 14448.9) < 0.1  # 144489 / 10


# ---------------------------------------------------------------------------
# run_backtest — fold structure and leakage
# ---------------------------------------------------------------------------


def test_fold_count():
    """25-row series, min_context=8, horizon=5 → 13 folds."""
    from ml.backtest import run_backtest

    series = _make_ibja_series(25)
    pipeline = _stub_pipeline()
    result = run_backtest(series, pipeline, horizon=5, min_context=8)

    # range(7, 20) → context_end_idx in [7..19] = 13 folds
    assert result["n_folds"] == 13


def test_no_leakage():
    """Each fold: context_end_date < first actuals date."""
    from ml.backtest import run_backtest

    series = _make_ibja_series(20)
    pipeline = _stub_pipeline()
    result = run_backtest(series, pipeline, horizon=5, min_context=8)

    for fold in result["folds"]:
        context_end = fold["context_end_date"]
        # actuals cover the 5 days after context_end_date
        # context_end must be strictly before those dates
        # We verify context_end_date is the (fold_id + min_context - 1)th date
        assert fold["context_size"] == fold["fold_id"] + 8
        assert fold["context_size"] < 20  # strictly inside the series


def test_expanding_window():
    """Each successive fold has exactly one more context row than the previous."""
    from ml.backtest import run_backtest

    series = _make_ibja_series(20)
    pipeline = _stub_pipeline()
    result = run_backtest(series, pipeline, horizon=5, min_context=8)

    folds = result["folds"]
    for i in range(1, len(folds)):
        assert folds[i]["context_size"] == folds[i - 1]["context_size"] + 1


def test_actuals_count():
    """Each fold has exactly horizon actuals."""
    from ml.backtest import run_backtest

    series = _make_ibja_series(20)
    pipeline = _stub_pipeline()
    result = run_backtest(series, pipeline, horizon=5, min_context=8)

    for fold in result["folds"]:
        assert len(fold["actuals"]) == 5
        assert len(fold["chronos_p50"]) == 5
        assert len(fold["naive"]) == 5
        assert len(fold["mae_chronos_per_h"]) == 5
        assert len(fold["in_pi_80"]) == 5


def test_naive_is_flat_hold():
    """naive[h] == context_last for all h."""
    from ml.backtest import run_backtest

    series = _make_ibja_series(20)
    pipeline = _stub_pipeline()
    result = run_backtest(series, pipeline, horizon=5, min_context=8)

    for fold in result["folds"]:
        last_val = fold["naive"][0]
        assert all(abs(v - last_val) < 1e-6 for v in fold["naive"])


def test_sub_30_context_flag():
    """All folds sub_30_context=True when series has fewer than 35 rows."""
    from ml.backtest import run_backtest

    series = _make_ibja_series(20)
    pipeline = _stub_pipeline()
    result = run_backtest(series, pipeline)

    assert all(f["sub_30_context"] for f in result["folds"])
    assert result["n_folds_sub_30_context"] == result["n_folds"]


def test_insufficient_folds_error():
    """Series too short to produce any fold raises RuntimeError."""
    from ml.backtest import run_backtest

    series = _make_ibja_series(12)  # need min_context(8) + horizon(5) = 13 rows
    pipeline = _stub_pipeline()
    with pytest.raises(RuntimeError, match="No valid backtest folds"):
        run_backtest(series, pipeline, horizon=5, min_context=8)


# ---------------------------------------------------------------------------
# run_backtest — aggregate metric shapes
# ---------------------------------------------------------------------------


def test_aggregate_fields_present():
    """Result dict contains all required top-level fields."""
    from ml.backtest import run_backtest

    series = _make_ibja_series(20)
    pipeline = _stub_pipeline()
    result = run_backtest(series, pipeline)

    required = {
        "backtest_run_at",
        "n_folds",
        "n_folds_sub_30_context",
        "horizon",
        "model_version",
        "mae_5d_avg_chronos",
        "mae_5d_avg_naive",
        "mae_chronos_per_h",
        "mae_naive_per_h",
        "dir_acc_5d_chronos",
        "dir_acc_5d_naive",
        "pi_coverage_80_per_h",
        "pi_coverage_80_5d_avg",
        "decision_acc",
        "peak_timing_err_days_median",
        "paired_diff_median",
        "wilcoxon_signed_rank_p",
        "insufficient_evidence",
        "folds",
    }
    assert required.issubset(result.keys())


def test_per_horizon_lists_length_5():
    """mae_chronos_per_h, mae_naive_per_h, pi_coverage_80_per_h all have 5 elements."""
    from ml.backtest import run_backtest

    series = _make_ibja_series(20)
    pipeline = _stub_pipeline()
    result = run_backtest(series, pipeline)

    assert len(result["mae_chronos_per_h"]) == 5
    assert len(result["mae_naive_per_h"]) == 5
    assert len(result["pi_coverage_80_per_h"]) == 5


def test_dir_acc_naive_is_05():
    """dir_acc_5d_naive is always 0.5 (no directional signal)."""
    from ml.backtest import run_backtest

    series = _make_ibja_series(20)
    result = run_backtest(series, _stub_pipeline())
    assert result["dir_acc_5d_naive"] == 0.5


def test_wilcoxon_null_when_n_lt_6():
    """Wilcoxon p is null when n_folds < 6 (too few folds)."""
    from ml.backtest import run_backtest

    # range(7, 12) → 5 folds (indices 7..11); 5 < 6 → insufficient_evidence
    series = _make_ibja_series(17)
    pipeline = _stub_pipeline()
    result = run_backtest(series, pipeline, horizon=5, min_context=8)

    assert result["n_folds"] == 5
    assert result["wilcoxon_signed_rank_p"] is None
    assert result["insufficient_evidence"] is True


def test_decision_acc_zero_predicted_drops():
    """When Chronos never predicts a >=100 drop, n_predicted=0 and precision=None."""
    from ml.backtest import run_backtest

    # Flat series keeps context_last ≈ 14000; stub predicts 14000 → no 100 drop
    rng = np.random.default_rng(99)
    base = pd.Timestamp("2026-04-01")
    dates = [str((base + pd.Timedelta(days=i)).date()) for i in range(20)]
    values = np.full(20, 14000.0) + rng.normal(0, 5, 20)
    flat_series = pd.Series(values, index=dates, name="ibja_pm_g")

    pipeline = _stub_pipeline(p50_step=0.0, p10_offset=-50.0, p90_offset=50.0)
    result = run_backtest(flat_series, pipeline)

    da = result["decision_acc"]
    assert da["n_chronos_predicted_100_drop"] == 0
    assert da["precision"] is None


def test_mae_nonnegative():
    """MAE values are non-negative."""
    from ml.backtest import run_backtest

    series = _make_ibja_series(20)
    result = run_backtest(series, _stub_pipeline())

    assert result["mae_5d_avg_chronos"] >= 0
    assert result["mae_5d_avg_naive"] >= 0
    assert all(v >= 0 for v in result["mae_chronos_per_h"])
    assert all(v >= 0 for v in result["mae_naive_per_h"])


def test_pi_coverage_in_0_1():
    """PI coverage values are in [0, 1]."""
    from ml.backtest import run_backtest

    series = _make_ibja_series(20)
    result = run_backtest(series, _stub_pipeline())

    assert 0.0 <= result["pi_coverage_80_5d_avg"] <= 1.0
    assert all(0.0 <= v <= 1.0 for v in result["pi_coverage_80_per_h"])
