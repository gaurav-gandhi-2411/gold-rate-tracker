# ADR 045 — Pre-registration: do better-calibrated learners lower the detection floor?

**Status:** Proposed 2026-09-24 (PR #2000). Pre-registration only. The text and the code are
frozen at the pushed commit that carries this sentence, **before any run** of
`scripts/analysis_calibrated_floor.py` on COMEX data. Every shard records its git SHA, and the
results are valid only if that SHA is this commit. Research; nothing a user sees changes.

**Extends:** ADR 040 (D3, signal injection) and #1992 (`scripts/analysis_pipeline_sensitivity.py`,
report `reports/pipeline_sensitivity_run_35974753003.json`).

## Context

#1992 split the direction pipeline's detection floor into its two causes. On COMEX (about 3,200
test days, planted "linear" signal of strength q, 20 seeds):

| detector | floor (smallest q detected in ≥ 80% of seeds) |
|---|---|
| plain logistic regression, accuracy test (the pipeline as used) | q = 0.20 (oracle ~60%); q = 0.15 detected in 65% |
| plain logistic regression, Brier test | never, at any q up to 0.30 |
| oracle probabilities, accuracy test | q = 0.10 (~55%) |
| oracle probabilities, Brier test | q = 0.15 (~57.5%) |

The test can see a 55% edge; the learner loses it. One visible symptom: the logistic model's
probabilities score *worse* than climatology, so the probability-based test never fires. #1992
named better calibration as the remaining lever. This ADR tests it.

## Hypothesis

**H1.** At least one calibrated learner detects a planted signal at a smaller q than the pipeline as
used (plain logistic regression, accuracy test), without more false alarms.

**Stated expectation, before any run:** Platt, isotonic and temperature scaling are monotone maps
of one score. They can move the 0.5 threshold but they don't re-rank days, so the accuracy test
should gain little, and it may lose a little because 20% of the training rows go to the
calibrator. The Brier test is where calibration can help. A negative result is a plausible outcome
and will be reported as plainly as a positive one.

## The frozen test

**Data, signal, seeds.** COMEX daily as loaded by `analysis_direction_diagnosis.load("comex")`, the
"linear" planted signal, q ∈ {0, 0.05, 0.10, 0.15, 0.20}, **100 seeds (42–141)**. The first 20
seeds are #1992's. Forward-only walk-forward, refit every 21 days, embargo ≥ h (h = 1).

**Learners** (all fit inside each refit):

| learner | what it is |
|---|---|
| `logit` (control) | #1992's learner, unchanged (`diag.walk_forward`) |
| `logit_platt` | logistic score → 1-D logistic (Platt) calibrator |
| `logit_isotonic` | logistic score → isotonic regression |
| `logit_temperature` | logistic score ÷ T, T fitted by log-loss |
| `ensemble_platt` | mean of `logit_strong_l2` and `gbm_stumps` probabilities → Platt |

**Calibration split, per refit.** The eligible training rows are those whose label is known
before the block's first test day. They are split in time order:
- The last 20% (at least 50 rows) fit the calibrator.
- The base model fits on the earlier rows whose label date is before the first calibration day.
- Imputation uses base-fit means only.
- The climatology baseline is the mean label over all eligible rows, identical to the control.

**Tests.** Each run is scored with two one-sided HAC Diebold-Mariano tests, lag h − 1 = 0:
- accuracy (0/1 loss vs the majority class);
- Brier (squared error vs climatology).

A run "detects" at p < 0.05.

**Floor.** The smallest q > 0 with detection in at least 80 of 100 seeds.

**Decision rule — "the floor comes down".** It comes down if at least one (calibrated learner, test)
cell meets **all** of the following. The family is 4 learners × 2 tests = 8 cells:
1. Its floor is below the control's accuracy floor, **as measured in this same run**.
2. Its false-positive rate at q = 0 is at most 10%.
3. At its floor q, a paired, seed-by-seed exact one-sided McNemar test against the control
   (accuracy test) gives p < 0.05 / 8 = 0.00625 (Bonferroni). The McNemar test counts seeds the
   cell detects and the control misses, against the reverse.

Benjamini-Hochberg at 0.05 over the same 8 p-values is also reported. Otherwise the verdict is "the
floor does not come down". `verdict()` in the script implements this rule, and
`tests/test_analysis_calibrated_floor.py` tests it on synthetic cells.

**Also reported, not tested:** for each cell, the detection rate with Wilson 95% intervals, mean
accuracy, mean Brier skill vs climatology, and expected calibration error (10 equal-width bins).

**Not in scope:** the INR set. At about 147 test days it is data-limited, not calibration-limited
(#1992: even the oracle needs ~70%). The run is COMEX only.

## How it runs

`gh workflow run analysis.yml --ref feat/calibrated-learner-floor-prereg -f analysis=calibrated_floor`
(the branch at the frozen commit; the PR is over the local size gate and waits for GG), GitHub-hosted, 25 shards
(5 learners × 5 q). The report goes into `reports/` with the run id. Any deviation from this text
is logged in the results PR before the numbers are read.

## Consequences

- If the floor comes down: the lowering learner and test become the candidate detector for future
  direction work. Any live use needs its own pre-registration on untouched data.
- If it does not: the direction track has no known cheap lever left on this data. That matches ADR
  040 and #1992. The work moves to new information (the derived Indian premium, G4).

## Alternatives considered

- **Keep 20 seeds.** Rejected. At q = 0.15 the control already detects 13 of 20 seeds. The best
  possible paired result there is 7 of 7 discordant seeds, which gives p = 0.0078 and cannot pass
  0.00625. 100 seeds can.
- **Calibrate with `CalibratedClassifierCV` and k-fold CV.** Rejected. Shuffled folds would put
  later days into the calibrator for earlier days, which is look-ahead.
- **A bigger learner family (random forest, deep GBM).** Rejected. ADR 040 D6 showed high-capacity
  learners overfit this data, and every extra learner widens the correction.
