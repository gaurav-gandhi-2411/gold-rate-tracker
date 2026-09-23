"""ml.direction.reframed_targets — alternative label constructions for M2.

The existing `label_binary_h1`/`label_binary_h2` (ml.direction.dataset) is a
raw "did next-day IBJA go up" label. G3's diagnosis (see
docs/adr/031-m2-direction-model-diagnosis.md) found the live logistic
model's predicted probability has flatlined near the full-history base rate
(0.547-0.669 over the trailing 30 real folds, hugging the 0.597 unconditional
base rate) rather than genuinely discriminating — a symptom, not a broken
input: real label balance in that same window still swings 33%-80% in
rolling-30 terms, so the signal exists, the model just isn't using it.

Three reframings, each a pure function of an already-built dataset (from
`ml.direction.dataset.build_dataset(..., extra_horizons=(5, 10))`):

  1. Dead-zone binary — drop near-flat days entirely; only score genuine
     directional moves (existing DEAD_BAND_PER_GRAM, already a considered
     choice — see ml.direction.dataset.make_label's threshold, roughly
     0.5-0.6% of the current price at 2026 levels).
  2. Detrended (excess-return) binary — subtract the prevailing trend
     (mean of already-MATURED prior deltas for the same horizon) so a
     label reflects deviation from trend, not the trend itself, which is
     what the base-rate-anchoring in the diagnosis suggests the raw label
     can't distinguish. Embargo-aware: only deltas that had already
     matured (label_date < the row's own as_of_date) contribute to the
     trend at that row -- otherwise the trend itself would leak future
     information into what's meant to be a point-in-time label.
  3. Buyer's-decision binary — "will the price be meaningfully lower at
     some point within N days" (the min along the path, not just the
     endpoint) -- the question a buyer deciding whether to wait actually
     asks. Uses window_min_pm916_hN, which build_dataset already computes
     leak-free (it only looks at days strictly after as_of_date).

None of these touch ml.direction.dataset.build_dataset's h1/h2 default
columns, ml.direction.evaluate, or ml.direction.gate — the live gate and its
inputs are unchanged, per GG's spec. These run only in
ml.direction.evaluate_reframed (shadow evaluation, no production wiring).
"""

from __future__ import annotations

import pandas as pd


def add_deadzone_binary(df: pd.DataFrame, horizon: int) -> pd.Series:
    """1 for "up" / 0 for "down" / NaN for "flat" (within the dead band),
    read directly from the horizon's own label_ternary_hN column -- so this
    reuses whatever dead_band_per_gram build_dataset was called with, rather
    than inventing a second threshold.
    """
    ternary = df[f"label_ternary_h{horizon}"]
    return ternary.map({"up": 1.0, "down": 0.0, "flat": None})


def add_detrended_binary(
    df: pd.DataFrame, horizon: int, trend_window: int = 20, min_trend_obs: int = 5
) -> pd.Series:
    """sign(realized delta - trailing trend of already-matured prior deltas).

    For each row t, the trend is the mean of delta_per_gram_h{horizon} over
    up to `trend_window` PRIOR rows whose label_date_h{horizon} is strictly
    before row t's own as_of_date (i.e. already matured/known as of t — the
    same maturity test the embargo-aware walk-forward harness uses for
    training eligibility, applied here to the trend calculation itself so
    the "trend" component can't quietly leak future information into what's
    otherwise a point-in-time label). NaN when fewer than `min_trend_obs`
    matured prior deltas exist (too early in the series to have a trend).
    """
    delta_col = f"delta_per_gram_h{horizon}"
    date_col = f"label_date_h{horizon}"
    as_of = df["as_of_date"].tolist()
    label_dates = df[date_col].tolist()
    deltas = df[delta_col].tolist()

    out: list[float | None] = []
    for i in range(len(df)):
        matured = [
            deltas[j]
            for j in range(i)
            if label_dates[j] is not None
            and not pd.isna(deltas[j])
            and str(label_dates[j]) < str(as_of[i])
        ]
        matured = matured[-trend_window:]
        if len(matured) < min_trend_obs or pd.isna(deltas[i]):
            out.append(None)
            continue
        trend = sum(matured) / len(matured)
        excess = deltas[i] - trend
        out.append(1.0 if excess > 0 else (0.0 if excess < 0 else None))
    return pd.Series(out, index=df.index)


def add_buyer_decision_binary(
    df: pd.DataFrame, horizon: int, dead_band_per_gram: float = 50.0
) -> pd.Series:
    """1 if the price dips at least dead_band_per_gram below the current
    price at ANY point within the next `horizon` days (the buyer's actual
    question: "should I wait?"), 0 otherwise. Uses window_min_pm916_hN
    (build_dataset), not just the endpoint delta.
    """
    window_min = df[f"window_min_pm916_h{horizon}"]
    current = df["current_pm916"]
    dip_per_gram = (current - window_min) / 10.0
    label = (dip_per_gram > dead_band_per_gram).astype(float)
    label[window_min.isna()] = None
    return label
