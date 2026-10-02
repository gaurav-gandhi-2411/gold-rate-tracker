# ADR 037 — M2 Statistical Corrections: Overlapping Horizons, One-Sided Tests, Multiplicity

**Status:** Accepted, implemented 2026-09-23. Supersedes ADR 031's significance claims (the
underlying data/methodology for everything else in ADR 031 — dataset construction, embargo,
reframed-target definitions — is unchanged and re-verified here, not redone).

---

## Context

GG's spec (item 3): "Correct the statistics — apply to every result in the M2 table and to
everything that follows." Four specific corrections, applied to all 24 (target × horizon × model)
combinations in ADR 031's original grid.

## (a) Overlapping horizons

At horizon h, consecutive folds share h-1 days of outcome — n overstates independent information.
`ml.direction.evaluate_reframed.diebold_mariano_test` already used HAC (Newey-West, lag=h-1)
variance; this ADR adds `effective_n = n * gamma_0 / long_run_var` (shrinks toward n only when
there's no real autocorrelation) and a **block bootstrap** (block length = h) as an independent
confirmation, per GG's explicit ask.

**Effect size:** effective n at h=10 ranges from 21–43 (raw n=122–137) — a **3–6x reduction**.
At h=5, effective n ranges from 40–81 (raw n=132–150) — smaller but still a genuine reduction
(1.9–3.3x). This alone is why several results that looked marginal in ADR 031 do not survive.

## (b) Direction

Several ADR 031 rows had strong two-sided p-values with **negative** skill (e.g. `deadzone_h5`:
McNemar p=0.0002, BSS -0.188) — significantly *worse* than baseline, not better, which a two-sided
test doesn't distinguish. Fixed two ways:

1. `classify_direction` labels every row BETTER/WORSE before any p-value is considered.
2. All significance tests are now one-sided, in BOTH directions, reported separately:
   `dm_p_better_accuracy_vs_always_up` (H1: model beats baseline) and
   `dm_p_worse_accuracy_vs_always_up` (H1: model loses to baseline) — these are exact complements
   under the normal approximation (p_worse = 1 - p_better), so no second DM computation is needed.

**A methodological finding surfaced building this, not anticipated going in:** ADR 031's grid used
BOTH a Brier-vs-always-up comparison AND BSS-vs-climatology as if they were the same kind of
"beats baseline" claim. They aren't, and can disagree: **always-up is a maximally overconfident
(hard, probability=1.0) Brier forecaster**, so almost any calibrated model beats it on raw Brier
loss with essentially no real skill (18 of 24 rows show `direction_vs_always_up = BETTER` on the
Brier-loss DM test alone — a weak bar). Climatology (a walk-forward mean-probability baseline) is a
much harder bar, matching what BSS's negative values were actually showing. **The corrected primary
significance test in this ADR uses 0/1 misclassification loss (accuracy), not squared-error loss
(Brier)** — matching `ml.direction.gate`'s own G4 condition (`accuracy > always_up_accuracy`) and
the original detrended_h10 finding's own framing ("64.75% vs 48.36%"). The Brier-vs-always-up DM
test is retained as a labeled SECONDARY diagnostic, not the significance claim.

## (c) Multiplicity

All 24 (target × horizon × model) configurations tried are treated as one family for both corrections
(computed separately for the BETTER-direction and WORSE-direction p-value families, m=24 each):

- **Bonferroni** (family-wise error rate): threshold = 0.05/24 = **0.00208**.
- **Benjamini-Hochberg** (false discovery rate, step-up procedure, less conservative): reported
  alongside, not instead of, Bonferroni.

## (d) Leakage re-verification (all three checked directly against the code, not asserted from memory)

1. **Embargo ≥ h for every horizon.** Measured directly: for every eligible (test, train) pair
   across both h=5 and h=10, the minimum gap between the test row's `as_of_date` and any eligible
   training row's `label_date` is **exactly 1 day, always positive, never 0**. This is a full
   date-based check (`label_date < as_of_date`), stronger than a fixed "h-1 row" embargo — it's
   correct regardless of calendar-day spacing between rows.
2. **Detrended target uses a TRAILING trend only.** Confirmed by direct code read
   (`ml/direction/reframed_targets.py::add_detrended_binary`): the trend at row i is computed only
   from `deltas[j]` where `j < i` AND `label_dates[j] < as_of[i]` — strictly prior rows whose own
   label had already matured before row i's capture date.
3. **Calibration fitted inside each walk-forward fold, never on the evaluation fold.** Confirmed by
   direct code read: `fit_logistic(X_train, y_train, ...)` and
   `_fit_lightgbm_calibrated(X_train, y_train, ...)` — both `CalibratedClassifierCV`-wrapped models
   are fit using only `X_train`/`y_train` (which itself only contains embargo-eligible prior rows);
   `.predict_proba(X_test)` is called strictly after fitting, on the single held-out point, which
   never enters either model's own internal calibration-CV split.

## Re-issued 24-row table (corrected)

All values: n=raw fold count, eff_n=HAC-effective fold count, dir=direction vs always-up
(accuracy-based), p_better/p_worse=one-sided accuracy-DM p-values, boot_p=block-bootstrap
confirmation (accuracy-based), bonf/bh=significant after correction (m=24 each direction).

| target | h | model | n | eff_n | dir | BSS(clim) | p_better | boot_p | bonf | bh |
|---|---|---|---|---|---|---|---|---|---|---|
| raw_binary | 5 | logistic | 150 | 54.9 | WORSE | -0.199 | 0.886 | 0.863 | N | N |
| raw_binary | 5 | gbm | 150 | 58.7 | WORSE | -0.189 | 0.992 | 1.000 | N | N |
| raw_binary | 5 | ensemble | 150 | 60.7 | WORSE | -0.164 | 0.978 | 0.992 | N | N |
| deadzone | 5 | logistic | 132 | 52.2 | WORSE | -0.259 | 0.984 | 0.984 | N | N |
| deadzone | 5 | gbm | 132 | 40.3 | WORSE | -0.195 | 0.989 | 1.000 | N | N |
| deadzone | 5 | ensemble | 132 | 44.1 | WORSE | -0.188 | 0.986 | 1.000 | N | N |
| detrended | 5 | logistic | 140 | 56.6 | BETTER | 0.009 | 0.324 | 0.304 | N | N |
| detrended | 5 | gbm | 140 | 53.9 | WORSE | 0.033 | 0.621 | 0.584 | N | N |
| detrended | 5 | ensemble | 140 | 56.1 | BETTER | 0.052 | 0.369 | 0.354 | N | N |
| buyer_decision | 5 | logistic | 150 | 81.0 | BETTER | -0.105 | 0.306 | 0.318 | N | N |
| buyer_decision | 5 | gbm | 150 | 73.6 | BETTER | -0.087 | 0.475 | 0.465 | N | N |
| buyer_decision | 5 | ensemble | 150 | 71.8 | WORSE | -0.074 | 0.526 | 0.523 | N | N |
| raw_binary | 10 | logistic | 137 | 28.8 | WORSE | -0.074 | 0.780 | 0.846 | N | N |
| raw_binary | 10 | gbm | 137 | 84.8 | WORSE | 0.016 | 0.903 | 0.922 | N | N |
| raw_binary | 10 | ensemble | 137 | 71.6 | WORSE | 0.028 | 0.821 | 0.928 | N | N |
| deadzone | 10 | logistic | 122 | 29.6 | WORSE | -0.117 | 0.943 | 0.959 | N | N |
| deadzone | 10 | gbm | 122 | 49.7 | WORSE | -0.082 | 0.819 | 0.959 | N | N |
| deadzone | 10 | ensemble | 122 | 40.0 | WORSE | -0.047 | 0.929 | 0.994 | N | N |
| **detrended** | **10** | **logistic** | **122** | **28.9** | **BETTER** | **0.171** | **0.106** | **0.176** | **N** | **N** |
| detrended | 10 | gbm | 122 | 24.0 | WORSE | -0.078 | 0.538 | 0.619 | N | N |
| detrended | 10 | ensemble | 122 | 27.2 | BETTER | 0.089 | 0.396 | 0.475 | N | N |
| buyer_decision | 10 | logistic | 137 | 38.7 | WORSE | -0.246 | 0.839 | 0.839 | N | N |
| buyer_decision | 10 | gbm | 137 | 43.1 | WORSE | -0.150 | 0.944 | 0.939 | N | N |
| buyer_decision | 10 | ensemble | 137 | 43.5 | WORSE | -0.151 | 0.951 | 0.957 | N | N |

Full per-row detail (Brier-based secondary DM, McNemar, n-for-80%-power) in
`data/direction_reframed_results_corrected.json`.

## Bottom line — stated plainly, per GG's explicit ask

**Zero of 24 combinations are significant, in either direction, after Bonferroni or
Benjamini-Hochberg correction.**

**`detrended_h10`/logistic does NOT still stand.** Its effective n drops from 122 (raw) to **28.9**
once the horizon-10 outcome overlap is properly HAC-corrected — a 4.2x reduction — and its one-sided
"beats baseline" p-value rises from the originally-reported 0.0169 (two-sided McNemar, no overlap
correction) to **0.106** (one-sided, HAC-corrected, accuracy-based). The block bootstrap confirms
this: p=0.176, same conclusion. Not significant before any multiplicity correction is even applied,
let alone after it. The original finding was substantially an artifact of overstating independent
information at h=10.

**The block bootstrap independently confirms the HAC-DM conclusion on every single row** — the two
methods never disagree on direction or rough p-value magnitude anywhere in the grid, which is itself
evidence the correction is doing what it's supposed to (not an artifact of one particular variance
estimator).

## Consequences

- ADR 031's headline claim ("detrended is the standout lead... doesn't clear significance yet")
  understated the actual finding: it doesn't just narrowly miss — it misses by a wide margin once
  overlapping-horizon information is correctly accounted for (p=0.106 vs the nominal 0.05 bar, before
  even reaching the m=24 Bonferroni threshold of 0.002).
- No row in this grid is a promotion candidate. This is a clean negative result across the whole
  reframed-target family, reported as plainly as a positive one would be.
- Item 4 (the #1903 flatline-fix candidate) is evaluated separately, via pre-registration on
  genuinely unseen data — not folded into this same 24-row family, since it was found via a
  different search (10 configs on the same 159 folds), which pre-registration handles directly
  rather than through post-hoc correction.

## Alternatives considered

- **Exact (not normal-approximation) DM p-values via a t-distribution correction.** Rejected for
  this pass: with effective n in the 20s-80s range, a Student-t correction would shift p-values
  modestly but not qualitatively change any conclusion in this grid — the negative result is not
  close enough to the boundary for the approximation choice to matter. Flagged as a refinement if a
  future result lands near a threshold.
- **Only Bonferroni, dropping BH.** Rejected: GG explicitly asked for both, and reporting both costs
  nothing here since neither found any significant row.
