# ADR 003: 2% MAE improvement gate for model promotion

**Status:** Accepted

## Context

After each local training run, we compare the new model to the currently deployed champion on
a 90-day holdout (the most recent 90 days of data). We need a promotion threshold: how much
better must a new model be before it replaces the current champion?

Too low a threshold → noisy promotions driven by random validation variance, not real
improvement. Too high → legitimate improvements never ship.

## Decision

Promote a new model only if its holdout MAE is at least **2% lower** than the current champion:

```
(champion_mae - challenger_mae) / champion_mae >= 0.02
```

The 2% floor is applied per model family: a new LightGBM challenger must beat the current
LightGBM champion. Ensemble re-weights after any family promotion.

## Consequences

**Good:**
- Prevents promoting models that improved on noise.
- Gold prices have ~₹178 daily delta std; a 2% MAE improvement is ~₹3.5 — small but real.
- Simple threshold is easy to reason about and audit in MLflow.

**Bad:**
- A genuine improvement of 1.5% will not promote. We accept this conservatism.
- The 90-day holdout is short; results can be noisy on < 90 real scraped readings.
  The `warmup` flag in `forecast.json` signals this condition to the PWA.

**Future:** Consider switching to a Wilcoxon signed-rank test on per-fold errors once we have
≥ 180 real readings (enough statistical power for a paired test).
