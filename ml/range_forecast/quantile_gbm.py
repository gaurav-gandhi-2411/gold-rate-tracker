"""ml.range_forecast.quantile_gbm -- LightGBM quantile regression on the M1
driver set (ml.range_forecast.data.build_driver_features): recent realized
vols (5/20/60d), prior-day India VIX, usd_inr change, recent returns,
calendar (wedding season / budget window / duty-event proximity).

One LightGBM model per (quantile level), refit every 21 trading days on an
EMBARGOED expanding window: a training row at origin t' is included only if
its target (the forward h-day log return) has matured strictly before the
current forecast day -- t' + horizon <= t -- otherwise the model would be
fit on rows whose outcome the walk-forward has not actually observed yet
(the same look-ahead trap ADR 038 amendment A1 fixed for the direction
model's embargo_label_date_col).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.range_forecast.data import M1_DRIVER_FEATURE_COLS, build_driver_features
from ml.range_forecast.metrics import implied_normal_scale
from ml.range_forecast.walkforward import log_price_and_returns, price_positions_and_dates

REFIT_EVERY = 21
MIN_TRAIN_SIZE = 250
QUANTILE_LEVELS: tuple[float, ...] = (0.05, 0.10, 0.90, 0.95)
_MIN_TRAIN_ROWS = 60  # below this, skip fitting for this refit point (too few embargoed rows)

_LGB_PARAMS = {
    "n_estimators": 100,
    "max_depth": 4,
    "num_leaves": 7,
    "learning_rate": 0.05,
    "min_child_samples": 20,
    "random_state": 42,
    "verbosity": -1,
    # This walk-forward does ~150-650 separate small fits (rows <= ~3600, 11
    # features) per horizon, not one large fit -- LightGBM's default n_jobs=-1
    # (all cores) pays thread-pool spin-up/teardown cost on EVERY one of those
    # fits, which a local timing run showed dominating wall-clock (one horizon
    # went from 70s to 470s under light concurrent load with other work on the
    # same machine). Pinning to 1 thread trades per-fit parallelism (not worth
    # it at this data size) for far less overhead and much better behavior when
    # this shard runs alongside the other 6 shards on a CI matrix.
    "n_jobs": 1,
}


def _fit_quantile_models(X: np.ndarray, y: np.ndarray, quantile_levels: tuple[float, ...]) -> dict:
    from lightgbm import LGBMRegressor

    models = {}
    for q in quantile_levels:
        model = LGBMRegressor(objective="quantile", alpha=q, **_LGB_PARAMS)
        model.fit(X, y)
        models[q] = model
    return models


def quantile_gbm_forecast_set(
    price: pd.Series,
    dataset: str,
    horizon: int,
    levels: tuple[float, ...] = (0.8, 0.9),
    quantile_levels: tuple[float, ...] = QUANTILE_LEVELS,
    refit_every: int = REFIT_EVERY,
    min_train_size: int = MIN_TRAIN_SIZE,
    macro_start: str | None = None,
    macro_end: str | None = None,
    features: pd.DataFrame | None = None,
) -> dict:
    """`features`: pre-built build_driver_features(...) output, indexed
    exactly on `price.index`. Building it involves one ml.macro network
    fetch (~100s wall clock, observed in a smoke run) -- a caller iterating
    over multiple horizons for the SAME price series (as the analysis
    script's quantile_gbm shard does) should build it once and pass it down
    rather than re-fetching per horizon. When omitted, this function builds
    it itself (used by tests and standalone calls)."""
    log_price, r = log_price_and_returns(price)
    positions, date_strs = price_positions_and_dates(price)
    n_price = len(log_price)

    if features is None:
        returns_series = pd.Series(r, index=price.index[1:])
        features = build_driver_features(
            pd.DatetimeIndex(price.index), returns_series, macro_start, macro_end
        )
    feat_arr = features[M1_DRIVER_FEATURE_COLS].to_numpy(dtype=float)

    fwd_return = np.full(n_price, np.nan)
    fwd_return[: n_price - horizon] = log_price[horizon:] - log_price[: n_price - horizon]

    q_lo80_col, q_hi80_col = 0.10, 0.90
    q_lo90_col, q_hi90_col = 0.05, 0.95

    dates, poss, cur_px, actual, scale = [], [], [], [], []
    level_lo: dict[float, list[float]] = {lv: [] for lv in levels}
    level_hi: dict[float, list[float]] = {lv: [] for lv in levels}

    models: dict | None = None
    last_refit_t = -(10**9)
    fit_count = 0
    skipped_no_model = 0

    row_valid = ~np.isnan(feat_arr).any(axis=1)

    for t in range(min_train_size, n_price - horizon):
        if not row_valid[t]:
            continue
        if models is None or (t - last_refit_t) >= refit_every:
            # Embargoed training rows: origin t' with t' + horizon <= t (matured), features valid.
            train_idx = [
                tp
                for tp in range(0, t)
                if tp + horizon <= t and row_valid[tp] and not np.isnan(fwd_return[tp])
            ]
            last_refit_t = t
            if len(train_idx) >= _MIN_TRAIN_ROWS:
                X_train = feat_arr[train_idx]
                y_train = fwd_return[train_idx]
                models = _fit_quantile_models(X_train, y_train, quantile_levels)
                fit_count += 1
            else:
                models = None

        if models is None:
            skipped_no_model += 1
            continue

        x_t = feat_arr[t : t + 1]
        preds = {q: float(models[q].predict(x_t)[0]) for q in quantile_levels}
        act = float(log_price[t + horizon] - log_price[t])

        lo80, hi80 = preds[q_lo80_col], preds[q_hi80_col]
        lo90, hi90 = preds[q_lo90_col], preds[q_hi90_col]
        # Enforce monotone ordering (a GBM quantile crossing is possible in principle).
        lo80, hi80 = min(lo80, hi80), max(lo80, hi80)
        lo90, hi90 = min(lo90, hi90), max(lo90, hi90)
        lo90, hi90 = min(lo90, lo80), max(hi90, hi80)

        dates.append(date_strs[t])
        poss.append(positions[t])
        cur_px.append(float(price.iloc[t]))
        actual.append(act)
        scale.append(float(implied_normal_scale(np.array([lo90]), np.array([hi90]))[0]))
        level_bounds = {0.8: (lo80, hi80), 0.9: (lo90, hi90)}
        for lv in levels:
            lo_v, hi_v = level_bounds[lv]
            level_lo[lv].append(lo_v)
            level_hi[lv].append(hi_v)

    from ml.range_forecast.walkforward import assemble_forecast_set

    fs = assemble_forecast_set(
        "quantile_gbm",
        dataset,
        horizon,
        dates,
        poss,
        cur_px,
        actual,
        scale,
        {lv: (level_lo[lv], level_hi[lv]) for lv in levels},
    )
    fs["n_refits"] = fit_count
    fs["n_skipped_no_model"] = skipped_no_model
    fs["feature_cols"] = M1_DRIVER_FEATURE_COLS
    return fs
