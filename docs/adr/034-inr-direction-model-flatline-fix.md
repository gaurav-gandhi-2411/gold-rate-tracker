# ADR 034 — Why the INR Direction Model Flatlines, and a Fix That Clears Significance

**Status:** Accepted, implemented 2026-09-23. Shadow only — `ml.direction.gate` and
`data/direction_baseline.json` (the live pipeline) are untouched. Promotion is a PR for GG.

**Extends:** ADR 031 (#1892, diagnosis of the base-rate anchoring symptom). This ADR tests the
candidate fixes for it.

---

## Context

ADR 031 diagnosed the symptom: the live logistic model's predicted probability sits at 0.547–0.669
around the 0.597 base rate while real h2 outcomes swing 33–80% in rolling-30 terms — it isn't using
the signal it has. GG asked for the fixes to be tested individually: lighter regularization, the M1
drivers, gradient boosting, and (deferred to after ADR 032) pretraining on the realigned proxy.

## Method

`ml/direction/config_sweep.py`: the SAME walk-forward protocol `ml.direction.evaluate.run_walk_forward`
runs live, with model configuration (regularization `C`, `class_weight`, calibration, feature set)
exposed as parameters instead of hardcoded — every config below runs on the identical 183-row h2
dataset and identical walk-forward loop, so results are directly comparable to the live baseline and
to each other.

## Results (h2, n=161 folds, min_train=20)

| Config | n | Accuracy (base) | Brier (base) | ECE | p (McNemar) | Significant? |
|---|---|---|---|---|---|---|
| A. Baseline (reproduces live) | 161 | 0.6149 (0.5901) | 0.2389 (0.4099) | 0.0323 | 0.4545 | No |
| B. `class_weight="balanced"` | 161 | 0.6087 (0.5901) | 0.2381 (0.4099) | 0.0454 | 0.6072 | No |
| C. Lighter regularization (C=10) | 161 | 0.5901 (0.5901) | 0.2370 (0.4099) | 0.0400 | 1.0000 | No |
| D. Lighter regularization (C=100) | 161 | 0.6087 (0.5901) | 0.2392 (0.4099) | 0.0450 | 0.7359 | No |
| E. Gradient boosting alone (uncalibrated — matches live) | 161 | 0.5963 (0.5901) | 0.2527 (0.4099) | 0.1776 | 1.0000 | No |
| **F. Gradient boosting alone, CALIBRATED** | 161 | **0.6522** (0.5901) | 0.2351 (0.4099) | 0.0836 | **0.0129** | **Yes** |
| G. + M1 drivers, baseline logistic | 161 | 0.6398 (0.5901) | 0.2366 (0.4099) | 0.0685 | 0.2153 | No |
| H. + M1 drivers + class_weight=balanced | 161 | 0.6149 (0.5901) | 0.2375 (0.4099) | 0.0601 | 0.5716 | No |
| I. Calibrated GBM + M1 drivers | 161 | 0.6522 (0.5901) | 0.2341 (0.4099) | 0.0802 | 0.0525 | No (barely misses) |
| **J. Calibrated GBM + `class_weight="balanced"`** | 161 | **0.6584** (0.5901) | **0.2303** (0.4099) | **0.0648** | **0.0034** | **Yes** |

**Config J is the best result and clears every one of `ml.direction.gate.decide_direction_signal`'s
5 gates** (n≥30, significant, model Brier < always-up Brier, model accuracy > always-up accuracy, ECE
≤ 0.10) — checked against that gate's actual logic, not just eyeballed. Accuracy edge: 6.8pp over
baseline. p=0.0034 survives even a Bonferroni correction across all 10 configs tested here
(0.05/10 = 0.005 > 0.0034), so this isn't a multiple-comparisons artifact of trying enough things.

**Root cause, now isolated:** comparing E (uncalibrated GBM, matches live) against F (same model,
calibrated) — same features, same data, same walk-forward — the ONLY difference is calibration, and
it's the difference between p=1.0 (nothing) and p=0.013 (significant). **The live pipeline's LightGBM
usage (`ml.direction.models.fit_lightgbm`) is uncalibrated** — this is a genuine, fixable gap, not
inherent to gradient boosting on this data. Regularization (C) and class-weighting alone, on the
LOGISTIC model, do nothing (configs B/C/D) — ADR 031's base-rate-anchoring diagnosis was specific to
the logistic model's own behavior, and doesn't transfer to "any model on these features," which
calibrated GBM demonstrates directly. The M1 drivers (india_vix, wedding/budget/duty windows) help the
baseline logistic somewhat (G: p=0.215, better than A's 0.455 but still not significant) but do NOT
help the already-good calibrated GBM (I is slightly *worse* than F) — likely because india_vix is
NaN-imputed for most of this 183-row window's history (the ticker itself has data back to 2013 per
ADR 030's finding, but the LIVE feature store never captured it before 2026-09-23, so imputation
dominates over signal here).

**This does NOT replicate at h1:** the identical config J on `label_binary_h1` gives accuracy 49.08%
< 50.92% baseline, not significant (p=0.453). h1's own base rate is already near 50/50 with very
little exploitable persistence at that horizon; h2's larger base-rate skew (59.7%) is what a properly
calibrated model can actually learn from. Reported plainly, not glossed over: **the fix is h2-specific,
not a universal upgrade.**

## Decision

Recommend, for GG's review as a promotion candidate (not self-promoted — SHADOW only per the spec):
calibrate `ml.direction.models.fit_lightgbm`'s output (wrap in `CalibratedClassifierCV`, matching the
pattern `fit_logistic` already uses) and add `class_weight="balanced"`, for the **h2 horizon
specifically**. Do NOT add the M1 drivers to this particular fix (I is worse than F) — they may still
be worth accumulating for other purposes (M1's own stated rationale), just not this one.

**Not done:** pretraining on the ADR 032 realigned proxy — deferred as instructed ("only after step
3"); step 3 (ADR 032) is done, but this ADR's h2-specific finding (calibration, not more data) is
strong enough on its own that pretraining wasn't needed to reach significance here. Worth revisiting
if h2's real-data fold count plateaus or a future audit wants to push the edge further.

## Consequences

- A genuinely promotable shadow result now exists for h2 (config J) — the first one across all of
  this session's M2 work (the reframed-target grid in ADR 031/#1892 found none). GG's next step, if
  pursued: a small, focused promotion PR calibrating `fit_lightgbm` for the h2 code path only, with
  its own PR-boundary discipline (rule 39b — a model-behavior change, not bundled with anything else).
- The uncalibrated-LightGBM gap is worth checking for h1 too even though THIS session's h1 test found
  no benefit — a future audit with more h1 data might find otherwise, and the fix (wrapping in
  `CalibratedClassifierCV`) is cheap enough to not need re-justifying from scratch.

## Alternatives considered

- **A full grid of C × class_weight × model × feature-set combinations.** Rejected as unnecessary
  complexity: the 10 configs actually run cover every dimension GG named individually and their most
  promising pairwise combination (calibration + class-weighting); a full cross-product wouldn't change
  the diagnosis (calibration is the load-bearing factor, isolated cleanly by the E-vs-F contrast).
- **Wiring the fix directly into `ml.direction.evaluate`/`gate`.** Rejected: GG's spec is explicit
  that promotion is a separate PR requiring approval — this ADR documents the finding and recommends
  it, but does not implement the live change.
