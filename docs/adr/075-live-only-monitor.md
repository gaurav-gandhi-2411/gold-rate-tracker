# ADR 075: a live-only safety monitor beside ADR 068's demotion rules

**Status:** Accepted and wired 2026-10-12 (GG brief of 2026-10-12, item 1: "decided for GG"). The
rule is frozen by the hash below. Built in `ml/live_monitor.py` (merged with its simulation in the
previous PR), called from `ml.nextfix._demotion` and `ml.demotion.demotion_status`; alert T18 in
`ml/notifications.py`. Tests: `tests/test_live_monitor.py` (the rule) and
`tests/test_live_monitor_wired.py` (the wiring and T18). Simulation:
`scripts/simulate_live_monitor.py`, run on the CI runner (`.github/workflows/live-monitor-simulation.yml`)
because the model's record is encrypted (ADR 060). Output: `reports/live_monitor_simulation.json`
(aggregates only), commit `0a30992c`, workflow run 38295374836.

## Context

ADR 068's windows (error 40 folds, direction 40, range 60) are filled mostly by retrospective re-run
days until forward days reach the window length, about December 2026 (verifier note 2026-10-07 in ADR
068). P3's first live days (2026-10-07 to 2026-10-09, n = 3) are too few to conclude anything: its
average miss was Rs.114 against Rs.100 for repeating the last rate, direction right 2 of 3, range held
1 of 3. But a model that is worse live than in its backtest would go unnoticed for about two months.

## Decision

Two checks, scoring ONLY days the model issued live (`retro` explicitly false; a fold with no flag is
not counted). Both are valid at every look, so they look every day.

| Check | Statistic | First look | Action |
|---|---|---|---|
| error vs hold | per live day d = model abs error - hold abs error. One-sided normal-mixture confidence sequence (the machinery of rule v7, `ml.promotion.cs_lower_bounds`) for mean(d): the model is clearly worse when its LOWER bound is above 0 | 10 live days | **demote to hold** (same sticky state and alert T16 as ADR 068; reason `live_vs_hold`) |
| live range coverage | e-process: a mixture of likelihood ratios against the 80% target, uniform over true coverages 0.50 to 0.78 (step 0.02) | 10 live days | **alert only** (T18, private topic, at most weekly); never demotes |

Frozen numbers (`LIVE_RULE`, canonical-JSON SHA-256
`7326ea844d7e98c597db9a725c27010e9097e9f5fe1accf5dc96bf0f985f42b1`; `tests/test_live_monitor.py`
fails if code and hash drift apart): alpha 0.05 one-sided, family of one test, 4 Newey-West lags with
the iid variance floor, mixture sd 0.3, **variance inflation 2.25** and **coverage alert threshold 150**
(both set by the simulation below, not by hand and not on the live days). The error check is not
subject to ADR 068's `persist` (it is anytime-valid; a breach demotes at once). A live-monitor failure
fails closed to hold like the rest of `_demotion` (reason `monitor_failed`, not sticky).

## How the numbers were set (pre-registered; no live day was used)

`scripts/simulate_live_monitor.py`, seed 42, 10,000 paths of 180 live days per cell, resampling the
model's own 148-fold retrospective record: moving-block and stationary bootstraps and synthetic AR(1)
series with the record's spread. The record's mean model error over mean hold error is 0.9304; the
spread of d is 0.318 of mean hold error; its lag-1 autocorrelation is 0.085.

1. **Calibration.** Variance inflation: the smallest value on a 0.25 grid for which the boundary
   demotion rate (the model exactly as good as holding; the mean of d set to 0) is at most 5% on every
   calibration resampler.
2. **Confirmation on fresh resamplers** (new seeds, block lengths and AR parameters), pass when the
   upper 95% Wilson bound is at most 6%.

**Amendments made before freezing (disclosed; they changed the procedure, not the live data).**
The first run (placeholder constants) chose k = 2.0 on five resamplers; it then failed its own
held-out AR(1) 0.5 proof (5.48%). So those held-out sets were moved INTO the calibration set (k =
2.25), and confirmation was re-done on fresh ones. The coverage threshold was first calibrated at the
record's own hit autocorrelation (0.0); its stress proof failed (threshold 20: 8.8% at clustering
0.2), so the calibration null now clusters at least 0.20 (the estimate's standard error is about 0.09)
and also at 0.30, and the threshold is 150. Two confirmation cases beyond the calibrated range (error
check: AR(1) 0.55, 5.66% against 5%; coverage: clustering 0.35, 6.53% against 5%) failed in the run
that followed; they are reported as stress, not gating, and the thresholds were not adjusted further.

## Results (VERIFIED: reports/live_monitor_simulation.json, 180 live days, 10,000 paths per cell)

**Wrongful demotion of a model with P3's backtest effect** (about 7% better than hold, kept as
measured): **0.00%** (95% interval 0.00 to 0.04%) on block bootstraps of 10, 20 and 40 days.

**Detection when P3 is truly worse than hold** (the mean model error is 1.15 times the mean hold
error; "proportional" = the shift scales with each day's hold error, the more conservative, and the
one to quote; "additive" = a constant shift):

| True effect | Detected within 180 live days (proportional / additive) | by day 60 | by day 90 | median day when detected |
|---|---|---|---|---|
| 5% worse | 21.3% / 23.8% | 6.4% / 10.8% | 11.2% / 15.1% | 86 / 68 |
| 10% worse | 70.3% / 67.2% | 33.3% / 40.3% | 47.9% / 50.8% | 64 / 45 |
| **15% worse** | **96.0% / 90.0%** | 65.7% / 64.0% | 77.8% / 73.9% | 41 / 32 |
| 25% worse | 100% / 99.8% | 95.5% / 87.6% | 99.6% / 95.3% | 22 / 16 |

**Boundary** (the model exactly as good as holding): at most 4.4% over 180 days on every calibration
resampler (block 10/20/30/40/60, stationary 20/40, AR(1) 0.2 to 0.5), at most 2.0% on the in-range
fresh ones (block 15 and 50, stationary 30, AR(1) 0.35). Stress: AR(1) 0.55, 5.66%.

**Coverage alert, false alerts at a true coverage of exactly 80%** over 180 days: 0.28% independent,
2.4% clustered at 0.2, 4.7% clustered at 0.3; 0.05% on a block bootstrap of the record's own hits
(the record's coverage is 0.797, 128 hits). Stress: clustering 0.35, 6.5%; 0.5, 12.6%.
**Alerts when the true coverage is lower** (clustered 0.2, 180 days): 0.70 -> 53.6% (day 90: 29%);
0.65 -> 88.0% (day 90: 58%); 0.60 -> 98.9% (day 90: 84%); 0.50 -> 100% (day 60: 95%).

## Consequences and limits

- The error check cannot speak before 10 live days (around 2026-10-21 at five decision days a week) and
  is slow for a small harm: a model 5% worse than hold is caught 21% of the time in 180 days. It is a
  safety net for a clearly worse model, not a performance test; the promotion rule (ADR 072) is the
  performance test.
- A model 15% worse is caught by day 60 in about two cases of three, by day 90 in about three of four;
  while it waits, ADR 068's windows (re-run days mostly) can also trip.
- The simulation resamples one 148-day record. It cannot show behaviour in a regime that record never
  saw, and the live days may be more persistent than the record (the stress row shows the cost: a boundary
  rate of 5.66% against the 5% target at AR(1) 0.55, about six times the observed persistence).
- The coverage alert is deliberately conservative (threshold 150) because hit clustering breaks the
  independence its textbook bound needs. It is slow: a coverage of 0.70 (10 points short) is reported
  only about half the time within 180 days.
- Live days include forward days a challenger accrued in shadow; after a champion switch the new
  champion is judged on its own `retro` false days.
- Hold, not P3, is the baseline: a model that is worse than hold is worse than what users would see
  with no model. ADR 068 decision 4 (parity rule P3 as baseline) is untouched.

## Rollback

Remove the `live=` argument and the `lmon.live_status` call in `ml.nextfix._demotion` (ADR 068's rules
then run alone); delete `_check_t18_live_coverage` and its call to stop the coverage alert. A demotion
this check caused is cleared exactly like ADR 068's: a person sets `demoted` to false in
`data/model_demotion_state.json` once the record has recovered.

## 2026-10-15 note: a decision day whose forecast was never shown is not a live day

From about 13:00 UTC on 2026-10-14 to 07:33 UTC on 2026-10-15 no data PR could merge (the sync token had
expired, HTTP 401), so nothing was published. P3's forecast for decision day 2026-10-14 (mode
`after_us_close`, target the 2026-10-15 PM fix) is published each morning (about 06:57 IST); that morning it
was not, and users saw the hold forecast from 13:03 IST. A fold is built from the data when its pair
resolves, so without a rule it would have been flagged live. `data/unpublished_decision_days.json` now lists
such days (here 2026-10-14, with the evidence: the commit history of `data/forecast.json`); `update_oos`
records them but flags them `retro`, so the promotion rule, the demotion windows and this monitor do not
count them. An unreadable file fails closed (no new fold counts as live). Decision day 2026-10-13, whose
forecast was published at 06:57 IST on 2026-10-14, resolves late (its PM fix of 2026-10-14 was stored only
on 2026-10-15) and counts as live. The rule's numbers and hash are unchanged.
