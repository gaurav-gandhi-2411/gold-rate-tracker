# ADR 005: Honest baseline reporting (model vs. naive)

**Status:** Accepted

## Context

Gold daily prices are close to a random walk over short horizons. The naive baseline — predict
zero delta (no change) — has a hard-to-beat MAE because it's calibrated to the mean of the
delta distribution (which is near zero). A model that learns to predict small non-zero deltas
can have a higher MAE than the naive baseline simply because it tries harder.

There is a temptation to: (a) cherry-pick a validation window where the model wins, (b) report
only the model MAE without showing the baseline, or (c) use a metric where the model always
wins (e.g., direction accuracy vs a zero-delta baseline, which trivially scores 0%).

## Decision

All model metadata files (`models/production/*-meta.json`) and the README must include:
- `val_mae_denormalized_rupees`: model MAE in rupees on the validation split
- `naive_val_mae_rupees`: MAE of the predict-zero baseline on the same split
- `model_beats_naive`: `true` or `false` — no hedging

If the model does not beat naive, the README and RUNBOOK say so. We do not change the
validation split to find one where the model wins. The `warmup` flag in `forecast.json`
signals to the PWA that we are in the low-data regime where this is expected.

## Consequences

**Good:**
- Users and recruiters see an honest picture of model quality.
- Forces us to keep improving — a model that never beats naive is a real problem signal.
- Documents the known limitation: gold prices are hard to predict over short horizons.

**Bad:**
- The README currently shows the model losing to naive on MAE. This might look bad to someone
  who doesn't read context.

**Mitigation:** The README explains *why* naive is hard to beat and what would change it
(more data, longer window, better exogenous features). The model's advantage is directional
accuracy on non-zero moves, which the naive baseline cannot achieve.
