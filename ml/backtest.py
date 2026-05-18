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

from ml.features import MINIMAL_FEATURE_COLS, build_feature_matrix, get_train_Xy
from ml.forecast import _make_lgb, _make_lgb_tuned, load_combined_history

DATA_DIR = Path(__file__).parent.parent / "data"
BACKTEST_DAYS = 90

_EPS = 1.0  # same eps used in inference.py blending


def _metrics(actuals: np.ndarray, predictions: np.ndarray) -> dict:
    actuals = np.asarray(actuals, dtype=float)
    predictions = np.asarray(predictions, dtype=float)
    mae = float(np.mean(np.abs(actuals - predictions)))
    mape = float(np.mean(np.abs((actuals - predictions) / actuals)) * 100)
    return {"mae": round(mae, 2), "mape": round(mape, 4)}


def _direction_acc(actuals: np.ndarray, predictions: np.ndarray, prevs: np.ndarray) -> float:
    actual_dir = np.sign(np.asarray(actuals) - np.asarray(prevs))
    pred_dir = np.sign(np.asarray(predictions) - np.asarray(prevs))
    return round(float(np.mean(actual_dir == pred_dir)), 4)


def run_backtest(
    df: pd.DataFrame,
    macro_df=None,
    feature_cols_override: list[str] | None = None,
    use_tuned: bool = False,
    label: str = "minimal_v2",
) -> dict:
    """Execute the walk-forward backtest. Returns the full result dict.

    Parameters
    ----------
    feature_cols_override : list[str], optional
        Feature columns to use. Defaults to MINIMAL_FEATURE_COLS (minimal_v2).
    use_tuned : bool
        Use bd602a6 regularized hyperparams (_make_lgb_tuned) when True.
    label : str
        Feature set name surfaced in print output.
    """
    df = df.copy()
    df["ts_parsed"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("ts_parsed").reset_index(drop=True)

    last_ts = df["ts_parsed"].iloc[-1]
    cutoff_ts = last_ts - pd.Timedelta(days=BACKTEST_DAYS)

    test_indices = df.index[(df["ts_parsed"] >= cutoff_ts) & (df.index < len(df) - 1)].tolist()

    if len(test_indices) < 5:
        raise RuntimeError(
            f"Only {len(test_indices)} test folds in last {BACKTEST_DAYS} days — need >=5"
        )

    make_model = _make_lgb_tuned if use_tuned else _make_lgb
    print(
        f"Walk-forward backtest: {len(test_indices)} folds  "
        f"feature_set={label}  params={'tuned' if use_tuned else 'base'}  "
        f"macro={'yes' if macro_df is not None else 'no'}"
    )

    predictions_out = []
    model_actuals, model_preds, baseline_preds, prevs = [], [], [], []
    fold_lgbm_errors: list[float] = []
    fold_naive_errors: list[float] = []

    for fold_idx, test_row_i in enumerate(test_indices):
        train_df = df.iloc[:test_row_i].copy()
        if len(train_df) < 10:
            continue

        feat_train = build_feature_matrix(train_df, macro_df=macro_df)
        candidate_cols = (
            feature_cols_override if feature_cols_override is not None else MINIMAL_FEATURE_COLS
        )
        feature_cols = [c for c in candidate_cols if c in feat_train.columns]
        X_train, y_train = get_train_Xy(feat_train, feature_cols=feature_cols)
        if len(X_train) < 10:
            continue

        test_df = df.iloc[: test_row_i + 1].copy()
        feat_test = build_feature_matrix(test_df, macro_df=macro_df)
        x_row = feat_test.iloc[-1][feature_cols]
        if x_row.isna().any():
            continue

        model = make_model("regression")
        model.fit(X_train.values, y_train.values)

        predicted_delta = float(model.predict(x_row.values.reshape(1, -1))[0])

        current_price = float(df.iloc[test_row_i]["22k"])
        next_price = float(df.iloc[test_row_i + 1]["22k"])
        ts_str = df.iloc[test_row_i]["timestamp"]

        predicted_price = current_price + predicted_delta
        baseline_price = current_price

        lgbm_err = abs(predicted_price - next_price)
        naive_err = abs(baseline_price - next_price)
        fold_lgbm_errors.append(lgbm_err)
        fold_naive_errors.append(naive_err)

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

    actuals_arr = np.array(model_actuals)
    preds_arr = np.array(model_preds)
    prevs_arr = np.array(prevs)
    lgbm_errs = np.array(fold_lgbm_errors)
    naive_errs = np.array(fold_naive_errors)

    # --- Stratified direction accuracy by |actual delta| ---
    actual_deltas = actuals_arr - prevs_arr
    big_move_mask = np.abs(actual_deltas) > 50
    small_move_mask = ~big_move_mask

    def _dir_acc_subset(mask: np.ndarray) -> float | None:
        if mask.sum() == 0:
            return None
        return round(float(_direction_acc(actuals_arr[mask], preds_arr[mask], prevs_arr[mask])), 4)

    dir_acc_big = _dir_acc_subset(big_move_mask)
    dir_acc_small = _dir_acc_subset(small_move_mask)

    # --- Rolling blend weight: simulate live inverse-MAE blend per fold ---
    # For fold i, use errors from folds max(0, i-4)..i (rolling 5-fold window).
    blend_weights: list[float] = []
    for i in range(len(lgbm_errs)):
        window_start = max(0, i - 4)
        w_lgbm_mae = float(np.mean(lgbm_errs[window_start : i + 1]))
        w_naive_mae = float(np.mean(naive_errs[window_start : i + 1]))
        w_raw = 1.0 / (w_lgbm_mae + _EPS)
        n_raw = 1.0 / (w_naive_mae + _EPS)
        w = max(0.1, min(0.9, w_raw / (w_raw + n_raw)))
        blend_weights.append(w)

    blend_arr = np.array(blend_weights)

    # --- Per-fold MAE std for uncertainty reporting ---
    model_metrics = _metrics(actuals_arr, preds_arr)
    model_metrics["mae_std"] = round(float(np.std(lgbm_errs)), 2)
    model_metrics["direction_acc"] = _direction_acc(actuals_arr, preds_arr, prevs_arr)
    model_metrics["direction_acc_big_move"] = dir_acc_big
    model_metrics["direction_acc_small_move"] = dir_acc_small
    model_metrics["n_big_move_folds"] = int(big_move_mask.sum())
    model_metrics["n_small_move_folds"] = int(small_move_mask.sum())
    model_metrics["blend_weight_lgbm_mean"] = round(float(blend_arr.mean()), 4)
    model_metrics["blend_weight_lgbm_std"] = round(float(blend_arr.std()), 4)

    baseline_metrics = _metrics(actuals_arr, np.array(baseline_preds))
    baseline_metrics["direction_acc"] = _direction_acc(
        actuals_arr, np.array(baseline_preds), prevs_arr
    )

    # Paired differences (model - baseline) for IQR comparison
    paired_diff = lgbm_errs - naive_errs
    pair_stats = {
        "median": round(float(np.median(paired_diff)), 2),
        "iqr_25": round(float(np.percentile(paired_diff, 25)), 2),
        "iqr_75": round(float(np.percentile(paired_diff, 75)), 2),
    }

    return {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "backtest_days": BACKTEST_DAYS,
        "folds": len(predictions_out),
        "feature_set": label,
        "model": model_metrics,
        "baseline": baseline_metrics,
        "paired_diff_model_minus_baseline": pair_stats,
        "predictions": predictions_out,
    }


def main():
    df = load_combined_history()

    macro_df = None
    try:
        from ml.macro import load_macro_features
        from ml.regime import add_regime_to_macro

        macro_df = load_macro_features()
        if macro_df is not None:
            today_utc = pd.Timestamp.now(tz="UTC").normalize()
            if macro_df.index[-1] < today_utc:
                extended_idx = pd.date_range(macro_df.index[0], today_utc, freq="D", tz="UTC")
                macro_df = macro_df.reindex(extended_idx, method="ffill")
            macro_df = add_regime_to_macro(macro_df)
    except Exception as exc:
        print(f"Macro unavailable -- backtest uses base features only ({exc})")

    result = run_backtest(df, macro_df=macro_df)

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
