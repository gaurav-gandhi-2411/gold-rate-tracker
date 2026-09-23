"""ml.direction.config_sweep — parameterized walk-forward for hyperparameter/
feature-set diagnostics (M2, GG spec item 5: "why the INR model flatlines").

ml.direction.evaluate.run_walk_forward (the live pipeline) hardcodes its
model configuration (C=1.0, class_weight=None, uncalibrated LightGBM) — this
module runs the SAME walk-forward protocol with those choices exposed as
parameters, so a candidate fix (lighter regularization, class weighting, a
calibrated gradient-boosting model, additional features) can be measured
against the live baseline on equal footing, without touching the live
pipeline's own code path.

SHADOW ONLY: never writes to data/direction_baseline.json or touches
ml.direction.gate. See docs/adr/034 for the diagnosis and results this
module produced.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import yfinance as yf
from sklearn.calibration import CalibratedClassifierCV

from ml.calendar_events import get_demand_calendar_features
from ml.direction.evaluate import _impute_with_means, compute_direction_metrics
from ml.direction.models import fit_lightgbm, fit_logistic

M1_DRIVER_COLS: list[str] = [
    "india_vix",
    "is_wedding_season",
    "is_budget_window",
    "is_duty_event_recent",
    "days_since_duty_event",
]


def augment_with_m1_drivers(
    dataset: pd.DataFrame, india_vix: pd.Series | None = None
) -> pd.DataFrame:
    """In-memory feature augmentation only — never touches the live feature
    store's schema or committed parquet. `india_vix` may be injected (a
    UTC-DatetimeIndex Series of daily closes) for offline tests; fetched
    live via yfinance when omitted.
    """
    dates = pd.to_datetime(dataset["as_of_date"])
    if india_vix is None:
        start = (dates.min() - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        end = (dates.max() + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
        raw = yf.download(
            "^INDIAVIX", start=start, end=end, auto_adjust=True, progress=False, threads=False
        )
        close_col = ("Close", "^INDIAVIX") if isinstance(raw.columns, pd.MultiIndex) else "Close"
        india_vix = raw[close_col]
        india_vix.index = pd.to_datetime(india_vix.index, utc=True)
        india_vix = india_vix.reindex(
            pd.date_range(india_vix.index.min(), india_vix.index.max(), freq="D", tz="UTC")
        ).ffill()

    out = dataset.copy()
    out["india_vix"] = np.nan
    out["is_wedding_season"] = False
    out["is_budget_window"] = False
    out["is_duty_event_recent"] = False
    out["days_since_duty_event"] = 9999

    for idx, row in out.iterrows():
        as_of_ts = pd.Timestamp(row["as_of_date"], tz="UTC")
        if as_of_ts in india_vix.index:
            val = india_vix.loc[as_of_ts]
            out.at[idx, "india_vix"] = float(val) if not pd.isna(val) else np.nan
        cal = get_demand_calendar_features(date.fromisoformat(row["as_of_date"]))
        out.at[idx, "is_wedding_season"] = bool(cal["is_wedding_season"])
        out.at[idx, "is_budget_window"] = bool(cal["is_budget_window"])
        out.at[idx, "is_duty_event_recent"] = bool(cal["is_duty_event_recent"])
        out.at[idx, "days_since_duty_event"] = int(cal["days_since_duty_event"])  # type: ignore[call-overload]

    return out


def run_config_sweep(
    dataset: pd.DataFrame,
    feature_cols: list[str],
    label_col: str,
    model: str = "logistic",
    class_weight: str | dict | None = None,
    C: float = 1.0,
    calibrate_gbm: bool = False,
    min_train_size: int = 20,
) -> dict:
    """Walk-forward for one (feature_cols, model, hyperparameter) config.

    model: "logistic" or "gbm". calibrate_gbm wraps a fresh LGBMClassifier
    in CalibratedClassifierCV (the live pipeline's own fit_lightgbm returns
    an UNCALIBRATED model — see ml.direction.models — so calibrate_gbm=True
    is the "what if we calibrated it" test, calibrate_gbm=False reproduces
    live behavior exactly for a fair baseline comparison).
    """
    ds = dataset[dataset[label_col].notna()].reset_index(drop=True)
    n = len(ds)
    y_true_all: list[int] = []
    prob_all: list[float] = []

    for i in range(min_train_size, n):
        train_df = ds.iloc[:i]
        test_row = ds.iloc[i]
        y_train = train_df[label_col].astype(int).tolist()
        if len(set(y_train)) < 2:
            continue

        X_train_raw = train_df[feature_cols].values.astype(float)
        X_test_raw = np.array([[test_row[c] for c in feature_cols]], dtype=float)
        col_means = np.nanmean(X_train_raw, axis=0)
        X_train = _impute_with_means(X_train_raw, col_means)
        X_test = _impute_with_means(X_test_raw, col_means)

        if model == "logistic":
            fitted = fit_logistic(
                X_train, y_train, random_state=42, cv=3, C=C, class_weight=class_weight
            )
            prob = float(fitted.predict_proba(X_test)[0, 1])
        elif model == "gbm":
            if calibrate_gbm:
                from lightgbm import LGBMClassifier

                base = LGBMClassifier(
                    n_estimators=100, random_state=42, verbose=-1, class_weight=class_weight
                )
                calibrated = CalibratedClassifierCV(base, method="sigmoid", cv=3)
                calibrated.fit(X_train, y_train)
                prob = float(calibrated.predict_proba(X_test)[0, 1])
            elif (
                gbm := fit_lightgbm(X_train, y_train, random_state=42, class_weight=class_weight)
            ) is not None:
                # gbm is object | None (LGBMClassifier via a guarded import);
                # the None case is handled by the elif condition itself.
                prob = float(gbm.predict_proba(X_test)[0, 1])  # type: ignore[attr-defined]
            else:
                prob = 0.5
        else:
            raise ValueError(f"unknown model: {model!r}")

        y_true_all.append(int(test_row[label_col]))
        prob_all.append(prob)

    metrics = compute_direction_metrics(y_true_all, prob_all, model)
    return {k: v for k, v in metrics.items() if k != "reliability"}
