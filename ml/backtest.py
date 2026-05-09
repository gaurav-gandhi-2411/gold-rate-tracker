"""
backtest.py — Walk-forward 90-day backtest of the LightGBM forecaster.

Usage (from repo root):
    python ml/backtest.py

Reads:  data/history_seed.json + data/prices.json
Writes: data/backtest.json

Walk-forward protocol:
  - For each reading t in the last 90 calendar days (except the very last):
    - Train on ALL readings strictly before t
    - Predict delta for t → t+1
    - Actual = price[t+1]
    - Baseline = price[t]  (naive "predict last value" / delta=0)
  - Report MAE, MAPE, direction-accuracy for model and baseline.

The model does NOT need to beat the naive baseline on this data volume —
reporting both honestly is the point.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", message="X does not have valid feature names")

sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.features import FEATURE_COLS, build_feature_matrix, get_train_Xy
from ml.forecast import _make_lgb, load_combined_history

DATA_DIR = Path(__file__).parent.parent / "data"
BACKTEST_DAYS = 90


def _metrics(actuals: np.ndarray, predictions: np.ndarray) -> dict:
    """Compute MAE, MAPE, direction accuracy vs the previous prices."""
    actuals = np.asarray(actuals, dtype=float)
    predictions = np.asarray(predictions, dtype=float)
    mae = float(np.mean(np.abs(actuals - predictions)))
    # MAPE: avoid div-by-zero; actuals are always positive gold prices
    mape = float(np.mean(np.abs((actuals - predictions) / actuals)) * 100)
    return {"mae": round(mae, 2), "mape": round(mape, 4)}


def _direction_acc(actuals: np.ndarray, predictions: np.ndarray, prevs: np.ndarray) -> float:
    """
    Direction accuracy: fraction of folds where the sign of (predicted - prev)
    matches the sign of (actual - prev).
    """
    actual_dir = np.sign(np.asarray(actuals) - np.asarray(prevs))
    pred_dir = np.sign(np.asarray(predictions) - np.asarray(prevs))
    return round(float(np.mean(actual_dir == pred_dir)), 4)


def run_backtest(df: pd.DataFrame) -> dict:
    """Execute the walk-forward backtest. Returns the full result dict."""
    df = df.copy()
    df["ts_parsed"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("ts_parsed").reset_index(drop=True)

    last_ts = df["ts_parsed"].iloc[-1]
    cutoff_ts = last_ts - pd.Timedelta(days=BACKTEST_DAYS)

    # Indices to predict on: all rows in the backtest window except the last
    # (because we need row i+1 as the actual outcome)
    test_indices = df.index[(df["ts_parsed"] >= cutoff_ts) & (df.index < len(df) - 1)].tolist()

    if len(test_indices) < 5:
        raise RuntimeError(
            f"Only {len(test_indices)} test folds in last {BACKTEST_DAYS} days — need ≥5"
        )

    print(f"Walk-forward backtest: {len(test_indices)} folds over last {BACKTEST_DAYS} days")

    predictions_out = []
    model_actuals, model_preds, baseline_preds, prevs = [], [], [], []

    for fold_idx, test_row_i in enumerate(test_indices):
        # Training data: everything STRICTLY before the test row
        train_df = df.iloc[:test_row_i].copy()
        if len(train_df) < 10:
            continue  # not enough history for this fold

        feat_train = build_feature_matrix(train_df)
        X_train, y_train = get_train_Xy(feat_train)
        if len(X_train) < 10:
            continue

        # Build features for the test row (test_row_i) using data up to test_row_i
        test_df = df.iloc[: test_row_i + 1].copy()
        feat_test = build_feature_matrix(test_df)
        x_row = feat_test.iloc[-1][FEATURE_COLS]
        if x_row.isna().any():
            continue

        model = _make_lgb("regression")
        model.fit(X_train, y_train)

        predicted_delta = float(model.predict(x_row.values.reshape(1, -1))[0])

        current_price = float(df.iloc[test_row_i]["22k"])
        next_price = float(df.iloc[test_row_i + 1]["22k"])
        ts_str = df.iloc[test_row_i]["timestamp"]

        predicted_price = current_price + predicted_delta
        baseline_price = current_price  # naive: no change

        model_actuals.append(next_price)
        model_preds.append(predicted_price)
        baseline_preds.append(baseline_price)
        prevs.append(current_price)

        predictions_out.append(
            {
                "ts": ts_str,
                "actual": next_price,
                "predicted": round(predicted_price, 2),
                "baseline": baseline_price,
            }
        )

        if (fold_idx + 1) % 10 == 0:
            print(f"  fold {fold_idx + 1}/{len(test_indices)}")

    if not model_actuals:
        raise RuntimeError("No valid folds completed")

    model_metrics = _metrics(model_actuals, model_preds)
    model_metrics["direction_acc"] = _direction_acc(model_actuals, model_preds, prevs)

    baseline_metrics = _metrics(model_actuals, baseline_preds)
    baseline_metrics["direction_acc"] = _direction_acc(model_actuals, baseline_preds, prevs)

    return {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "backtest_days": BACKTEST_DAYS,
        "folds": len(predictions_out),
        "model": model_metrics,
        "baseline": baseline_metrics,
        "predictions": predictions_out,
    }


def main():
    df = load_combined_history()
    result = run_backtest(df)

    m = result["model"]
    b = result["baseline"]
    print(
        f"\nModel   -- MAE: Rs.{m['mae']:.1f}  MAPE: {m['mape']:.2f}%  Dir-acc: {m['direction_acc']*100:.1f}%"
    )
    print(
        f"Baseline-- MAE: Rs.{b['mae']:.1f}  MAPE: {b['mape']:.2f}%  Dir-acc: {b['direction_acc']*100:.1f}%"
    )

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "backtest.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"\nBacktest written ({result['folds']} folds).")


if __name__ == "__main__":
    main()
