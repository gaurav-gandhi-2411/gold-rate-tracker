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
    dataset: pd.DataFrame,
    india_vix: pd.Series | None = None,
    india_vix_prior_day: bool = False,
) -> pd.DataFrame:
    """In-memory feature augmentation only — never touches the live feature
    store's schema or committed parquet. `india_vix` may be injected (a
    UTC-DatetimeIndex Series of daily closes, forward-filled onto every
    calendar day) for offline tests; fetched live via yfinance when omitted.

    india_vix_prior_day: use the India VIX close of the last trading day
    strictly BEFORE as_of_date instead of as_of_date's own close. Required
    when as_of_date is the day whose move is being predicted (the proxy arm,
    ADR 038 amendment A2): that day's own VIX close is not known until the
    move it would predict has already happened.
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
        # On the forward-filled daily series, the value at t-1 is the last
        # close on or before t-1, i.e. the last close strictly before t.
        vix_ts = as_of_ts - pd.Timedelta(days=1) if india_vix_prior_day else as_of_ts
        if vix_ts in india_vix.index:
            val = india_vix.loc[vix_ts]
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
    return_raw: bool = False,
    embargo_label_date_col: str | None = None,
    score_after_as_of: str | None = None,
) -> dict:
    """Walk-forward for one (feature_cols, model, hyperparameter) config.

    model: "logistic" or "gbm". calibrate_gbm wraps a fresh LGBMClassifier
    in CalibratedClassifierCV (the live pipeline's own fit_lightgbm returns
    an UNCALIBRATED model — see ml.direction.models — so calibrate_gbm=True
    is the "what if we calibrated it" test, calibrate_gbm=False reproduces
    live behavior exactly for a fair baseline comparison).

    return_raw: when True, adds result["raw"] = {"y_true": [...], "y_prob":
    [...]} — the per-fold ground truth and predicted probability, needed by
    callers (e.g. ml.direction.preregistration) that run their own
    significance test (DM-HAC) on top of this walk-forward's output instead
    of relying on compute_direction_metrics' own McNemar-based p_value.

    embargo_label_date_col: when set (e.g. "label_date_h2"), a training row is
    used only if its label had matured strictly BEFORE the test row's
    as_of_date. Without it, train = every earlier row, and at h2 each fold
    trains on ~2 rows whose outcomes were not yet known on the test date — a
    look-ahead leak (ADR 038 amendment A1). None keeps the historical
    (leaky) protocol so ADR 034's published numbers stay reproducible.

    score_after_as_of: when set ("YYYY-MM-DD"), only test rows with
    as_of_date strictly after it are fitted and scored; earlier rows still
    serve as training data. Used by the pre-registration to score only days
    that did not exist when the config was selected.
    """
    ds = dataset[dataset[label_col].notna()].reset_index(drop=True)
    n = len(ds)
    as_of = pd.to_datetime(ds["as_of_date"]).dt.strftime("%Y-%m-%d").tolist()
    label_dates: list[str | None] | None = None
    if embargo_label_date_col is not None:
        label_dates = [
            None if pd.isna(v) else pd.Timestamp(v).strftime("%Y-%m-%d")
            for v in ds[embargo_label_date_col]
        ]
    y_true_all: list[int] = []
    prob_all: list[float] = []
    as_of_scored: list[str] = []
    train_max_label_date: list[str | None] = []

    for i in range(min_train_size, n):
        if score_after_as_of is not None and as_of[i] <= score_after_as_of:
            continue
        if label_dates is None:
            train_df = ds.iloc[:i]
        else:
            keep = [j for j in range(i) if (d := label_dates[j]) is not None and d < as_of[i]]
            if len(keep) < min_train_size:
                continue
            train_df = ds.iloc[np.array(keep, dtype=int)]
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
        as_of_scored.append(as_of[i])
        if label_dates is not None:
            train_max_label_date.append(max(str(label_dates[j]) for j in keep))
        else:
            train_max_label_date.append(None)

    metrics = compute_direction_metrics(y_true_all, prob_all, model)
    result = {k: v for k, v in metrics.items() if k != "reliability"}
    if return_raw:
        result["raw"] = {
            "y_true": y_true_all,
            "y_prob": prob_all,
            "as_of_date": as_of_scored,
            "train_max_label_date": train_max_label_date,
        }
    return result
