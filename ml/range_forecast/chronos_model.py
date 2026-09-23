"""ml.range_forecast.chronos_model -- Chronos-Bolt-Tiny quantile forecasts
on the log-price series (same checkpoint/revision as ml.chronos_forecast,
reused rather than re-pinned here).

No refitting (Chronos is a pretrained, zero-shot foundation model) -- the
walk-forward here re-runs INFERENCE at each as-of day, batched for
throughput. torch/chronos-forecasting are NOT importable in this repo's
local dev venv (ml/requirements-inference.lock only installs on the CI
runner) -- every import of them is deferred into functions so this module
itself is always importable, and `chronos_available()` lets callers detect
the gap and report it explicitly rather than crash.

STRIDE (methodological choice to review): running one Chronos inference per
trading day across ~4700 proxy-history days would take, per the existing
single-call timing already recorded for this same checkpoint in
ml.chronos_forecast's probe (wall_clock_ms.forecast, data/chronos_probe.json
history), on the order of hours per (horizon, dataset) combination even
batched. `chronos_stride` (default 5) forecasts only every Nth trading day,
keeping the full run tractable on a GitHub-hosted CPU runner within the
300-minute shard timeout. This makes Chronos's walk-forward sparser than
every other model's (daily) -- flagged explicitly in the report, not hidden.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.chronos_forecast import CHRONOS_BOLT_TINY_MODEL_ID, CHRONOS_BOLT_TINY_REVISION
from ml.range_forecast.metrics import implied_normal_scale
from ml.range_forecast.walkforward import (
    assemble_forecast_set,
    log_price_and_returns,
    price_positions_and_dates,
)

CONTEXT_LENGTH = 512
CHRONOS_STRIDE = 5
QUANTILE_LEVELS: tuple[float, ...] = (0.05, 0.10, 0.90, 0.95)
BATCH_SIZE = 16
MIN_TRAIN_SIZE = 250


def chronos_available() -> bool:
    try:
        import chronos  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


def load_pipeline():
    """Load ChronosBoltPipeline -- same model id/revision as
    ml.chronos_forecast.load_chronos_pipeline, imported here (not called
    directly) so this module has no import-time dependency on that one's
    IBJA-probe-specific code path."""
    from chronos import ChronosBoltPipeline

    return ChronosBoltPipeline.from_pretrained(
        CHRONOS_BOLT_TINY_MODEL_ID,
        revision=CHRONOS_BOLT_TINY_REVISION,
        device_map="cpu",
    )


def chronos_forecast_set(
    price: pd.Series,
    dataset: str,
    horizon: int,
    levels: tuple[float, ...] = (0.8, 0.9),
    quantile_levels: tuple[float, ...] = QUANTILE_LEVELS,
    context_length: int = CONTEXT_LENGTH,
    stride: int = CHRONOS_STRIDE,
    min_train_size: int = MIN_TRAIN_SIZE,
    batch_size: int = BATCH_SIZE,
    pipeline=None,
) -> dict:
    """Chronos quantile forecast on the log-price series, at every `stride`-th
    trading day from min_train_size to N-horizon-1. `pipeline`: an
    already-loaded ChronosBoltPipeline (avoids a reload per horizon in the
    analysis script); loaded internally when omitted."""
    import torch

    if pipeline is None:
        pipeline = load_pipeline()

    log_price, _ = log_price_and_returns(price)
    positions, date_strs = price_positions_and_dates(price)
    n_price = len(log_price)

    t_values = list(range(min_train_size, n_price - horizon, max(1, stride)))

    dates, poss, cur_px, actual, scale = [], [], [], [], []
    level_lo: dict[float, list[float]] = {lv: [] for lv in levels}
    level_hi: dict[float, list[float]] = {lv: [] for lv in levels}

    q_lo80_i, q_hi80_i = quantile_levels.index(0.10), quantile_levels.index(0.90)
    q_lo90_i, q_hi90_i = quantile_levels.index(0.05), quantile_levels.index(0.95)

    for batch_start in range(0, len(t_values), batch_size):
        batch_t = t_values[batch_start : batch_start + batch_size]
        contexts = [
            torch.tensor(log_price[max(0, t - context_length + 1) : t + 1], dtype=torch.float32)
            for t in batch_t
        ]
        quantiles_t, _ = pipeline.predict_quantiles(
            inputs=contexts,
            prediction_length=horizon,
            quantile_levels=list(quantile_levels),
        )
        q = quantiles_t.numpy()  # (batch, horizon, n_quantiles)

        for i, t in enumerate(batch_t):
            step_q = q[i, horizon - 1, :]  # the h-th step: log-price quantiles at t+horizon
            log_price_t = log_price[t]
            ret_q = step_q - log_price_t  # log-return quantiles from t to t+horizon
            act = float(log_price[t + horizon] - log_price_t)

            lo80, hi80 = float(ret_q[q_lo80_i]), float(ret_q[q_hi80_i])
            lo90, hi90 = float(ret_q[q_lo90_i]), float(ret_q[q_hi90_i])
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

    fs = assemble_forecast_set(
        "chronos",
        dataset,
        horizon,
        dates,
        poss,
        cur_px,
        actual,
        scale,
        {lv: (level_lo[lv], level_hi[lv]) for lv in levels},
    )
    fs["stride"] = stride
    fs["context_length"] = context_length
    fs["model_version"] = f"{CHRONOS_BOLT_TINY_MODEL_ID}@{CHRONOS_BOLT_TINY_REVISION[:8]}"
    return fs
