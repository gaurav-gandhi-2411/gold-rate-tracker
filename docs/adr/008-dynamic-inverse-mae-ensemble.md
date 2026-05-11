# ADR 008: Dynamic inverse-MAE ensemble weighting

**Status:** Accepted

## Context

The initial ensemble used a uniform mean across all available models with a
temporary 3× MAE hard-exclusion filter. That design has two problems:

1. **Uniform weighting ignores quality.** A model with MAE=250 and one with
   MAE=1000 contribute equally to the point prediction. The worse model drags
   the ensemble toward its (bad) prediction.

2. **The hard-exclusion threshold was a placeholder.** 3× was chosen to filter
   TFT (which ran at 7.9× the best model's MAE after training instability) but
   was not principled. A model 2.9× worse still gets full weight; a model 5.1×
   worse is silently dropped. There was no floor — a marginal model could be
   one bad retrain away from being excluded entirely.

We need a weighting scheme that degrades gracefully for weak models and hard-
excludes only on catastrophic failure.

## Decision

Replace the uniform + hard-exclusion filter with a two-tier system in
`ml/ensemble.py`:

### Tier 1 — Inverse-MAE with floor (primary mechanism)

Weight each model proportionally to the inverse of its validation MAE, then
apply a **0.1 floor** so no non-excluded model is squeezed below ~10% weight:

```
raw_weight     = 1 / mae
normalized     = raw_weight / Σ raw_weight    (non-excluded only)
pre_renorm     = max(normalized, 0.10)
final_weight   = pre_renorm / Σ pre_renorm    (non-excluded only)
```

### Tier 2 — 5× hard exclusion (safety valve)

If a model's MAE exceeds **5× the best model's MAE**, it is hard-excluded
(weight=0) before the inverse-MAE calculation. This is not the primary
mechanism; it fires only when a model has clearly failed.

Full per-model sequence:

1. `raw_weight = 1 / mae`
2. If `mae > 5 × best_mae` → `weight = 0` (hard exclude)
3. Normalize non-excluded raw weights
4. If normalized < 0.1 → clamp to 0.1 (floor)
5. Renormalize non-excluded weights to sum to 1

### Why the floor is needed

Consider three models: A=100, B=100, C=490 (all within the 5× threshold of
500). Without a floor, C's weight is (1/490)/(1/100+1/100+1/490) ≈ 9.3% —
below 10% but non-zero. The floor raises C to ~10% and splits the remaining
90% between A and B. This prevents C from being so suppressed that a slight
improvement in C has no effect on the ensemble output.

### Why 5× for hard exclusion

- At 5× the best model's MAE, the model's inverse-MAE weight before flooring
  would be (1/5) / (1 + 1/5) = 16.7% for a 2-model ensemble. Combined with
  the floor, it would receive ~10% weight — still meaningful. Hard exclusion at
  5× is therefore conservative: it only fires when the model is catastrophically
  broken, not merely weak.
- Current TFT MAE is 1172 vs best=225.65 → **5.2×**, just over the threshold.
  It is correctly excluded. If TFT improves to within 5× after a retrain it
  will automatically re-enter the ensemble at floor weight.
- The old 3× threshold was too aggressive: a model at 2.9× (which could still
  contribute useful diversity) was penalised as if it had failed.

### CI band

Confidence intervals are also weight-weighted:
- LightGBM exposes q10/q90 quantiles from its three-booster training.
- TFT and N-BEATS (ONNX exports) expose only a point prediction; their q10/q90
  are set equal to their point prediction in the ensemble CI calculation.

## Consequences

**Good:**
- Graceful degradation: a weaker model reduces its influence proportionally
  rather than being binary included/excluded.
- Floor ensures ensemble diversity is never fully lost — no single well-tuned
  model can crowd out all others.
- The 5× safety valve handles catastrophic failure (training collapse, NaN
  weights, wildly wrong scale) without affecting the normal weighting path.
- Weights are persisted to `models/production/ensemble-config.json` after each
  inference run, giving full audit trail of what the ensemble looks like at any
  point in time.

**Bad:**
- The floor (0.1) means a truly terrible model still gets ~10% weight after
  renormalisation. In the current 2-model ensemble (lgbm + nbeats) the floor
  never triggers because the hard exclusion fires first for TFT; but with 3
  included models a weak third model retains floor weight.
- Per-fold MAE from the 90-day holdout is the proxy for weight. This is
  in-sample relative to the last 90 days and may not represent future
  performance during regime shifts.

**Future:**
- When Phase E (SHAP) lands, add an "information-theoretic diversity" bonus
  that slightly up-weights models whose error residuals are uncorrelated with
  the best model's residuals, further rewarding ensemble diversity.
- Consider replacing the 5× threshold with a Mahalanobis-distance test on
  residual distributions once we have ≥180 real readings.
