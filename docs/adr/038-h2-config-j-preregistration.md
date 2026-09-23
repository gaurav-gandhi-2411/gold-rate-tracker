# ADR 038 — Pre-Registration: ADR 034's h2 "Config J" Candidate on Genuinely New Data

**Status:** Accepted, implemented 2026-09-23. **Amended 2026-09-23 (A1: embargo + post-registration
days only), before any post-registration day was scored — see "Amendment A1".** Pre-registration only — no promotion, no gate/user-facing
change. Shadow arms run additively from `weekly-backtest.yml`; write only to
`data/preregistered_h2_shadow_results.json`.

**Extends:** ADR 034 (config J: calibrated LightGBM + `class_weight="balanced"` at h2, accuracy
65.84% vs 59.01% baseline, p=0.0034).

---

## Context

GG's spec item 4: ADR 034's config J was selected by trying 10 candidate fixes on the SAME 159-161
folds used to diagnose the flatline in the first place. Bonferroni across those 10 configs (0.05/10 =
0.005 > 0.0034, ADR 034) covers the number of configs *tried* — it does not cover the fact that these
same folds *chose* the winning config. A config selected and validated on identical data is not
independent evidence; the fold history that produced p=0.0034 could not have produced any other
"winner." This is a real, separate validity gap from multiplicity, and the only fix is scoring the
frozen config on data that did not exist yet when the config was chosen.

## What is frozen (before any new data is scored)

`ml.direction.preregistration.PREREGISTERED_CONFIG`:

| Parameter | Value |
|---|---|
| Model | LightGBM (`ml.direction.models.fit_lightgbm`'s underlying `LGBMClassifier`) wrapped in `CalibratedClassifierCV` (sigmoid, cv=3) |
| `class_weight` | `"balanced"` |
| Horizon | h2 (`label_binary_h2`) |
| Feature set | Live default (`ml.direction.dataset.FEATURE_COLS`) — no M1 drivers (ADR 034: adding them made the already-calibrated GBM slightly *worse*, config I < F) |
| `min_train_size` | 20 (matches ADR 034/live walk-forward) |

**Test** (`ml.direction.preregistration.score_config`): one-sided Diebold-Mariano, HAC-corrected
(Newey-West, `max_lag = horizon - 1 = 1`), on 0/1 misclassification loss (matches
`ml.direction.gate`'s own accuracy-based G4 condition) vs the "always predict up" baseline.
`alternative="less"` (candidate loss < baseline loss). **A single pre-registered hypothesis — no
multiplicity correction applied**, because Bonferroni/BH exist to control error across a *family* of
tests and this freezes exactly one test in advance, before the data that will be scored by it exists.

**Significance threshold:** α = 0.05, one-sided.

**n required for 80% power**, computed from the OBSERVED ADR-034 effect size (re-derived here under the
new DM-HAC methodology, not the original McNemar test) — used strictly as a forward-looking design
target, never as evidence the original result is valid (post-hoc power from an observed effect is a
well-known statistical trap; see `stats_corrections.power_for_observed_effect`'s own docstring):

Re-running config J on the same 161-fold live h2 dataset under the new test: **n=161, effective_n=
119.38** (autocorrelation shrinks the usable information by ~26% at lag=1), mean_diff=-0.06832,
long_run_var=0.10260, dm_stat=-2.7065, **p=0.00340** (accuracy 65.84% vs baseline 59.01% — reproduces
ADR 034's numbers exactly; the new DM-HAC p-value is numerically close to, but methodologically
distinct from, ADR 034's original McNemar p=0.0034 — same conclusion, different, more conservative
test). **n_for_80pct_power at this effect size (effective_n scale) = 135.9.**

Stated plainly: the ORIGINAL 161-fold sample (effective_n=119.38) was itself nominally *underpowered*
for 80% detection at its own observed effect size, despite reaching p=0.0034 — power calculations
describe average-case detectability, not a guarantee that an underpowered sample cannot reach
significance. This is reported exactly as computed, not smoothed over: it means a replication attempt
on ~120 effective folds has less than 80% odds of reaching significance even if the true effect is
exactly this size — **135.9 effective folds (roughly ~183 raw folds, assuming similar h2 autocorrelation
holds forward) is the pre-registered target before this candidate's live-arm result is treated as
confirmatory, not merely "still watching."**

**PREREGISTERED_N_FOR_POWER = 135.9 is frozen in code** (`ml.direction.preregistration`) and must not
be recomputed from accumulating shadow data — recomputing the power target from the same data being
accumulated toward it would silently move the goalposts.

## Amendment A1 — 2026-09-23: embargo, and post-registration days only

**Made 2026-09-23, approved by GG, before any post-registration day was scored.** The first scheduled
scoring run is `weekly-backtest.yml` on Sunday 2026-09-27 02:00 UTC; no run of the pre-registration
step has happened before this amendment (the live h2 dataset's newest labelled `as_of_date` at the
time of writing is 2026-09-18, so zero days after the registration date exist yet —
`reports/preregistration_embargo_a1.json`, `amended_live_arm.n = 0`).

**Why.** The protocol above inherited two flaws from the walk-forward it copied:

1. **No embargo.** Training for test day *i* was every earlier row. At h2, the labels of the ~2 rows
   immediately before *i* mature after *i*'s `as_of_date` — the model trained on outcomes that were
   not yet known on the day it predicted (measured: 1.95 such rows per fold on average).
2. **The confirmation re-scored the selection data.** `run_live_arm` scored every fold, including
   the 161 folds that selected config J from 10 candidates — exactly the dependence this
   pre-registration exists to remove. Only days that did not exist at registration are independent.

**What changes** (`ml.direction.preregistration`, `PROTOCOL_VERSION = "adr038-A1"`):

- `EMBARGO_LABEL_DATE_COL = "label_date_h2"` — a training row is used only if its h2 label had
  matured strictly before the test day's `as_of_date` (an embargo of ≥ 2 trading days = ≥ h).
- `CONFIRMATORY_AFTER_AS_OF = "2026-09-23"` — only test days with `as_of_date` after it are scored.
  Every earlier day, including the 161 selection folds, is training data only.
- Every logged live-arm run records `protocol_version`, the scored `as_of_date`s and, per scored day,
  the latest label date in its training set (`train_max_label_dates`), so the embargo is auditable
  from the log alone.

**Also fixed:** the workflow step `python scripts/run_preregistered_h2_shadow.py` could not import
`ml` (run as a file, `sys.path[0]` is `scripts/`; reproduced locally: `ModuleNotFoundError: No
module named 'ml'`). Under `continue-on-error: true` the first scheduled run would have failed with
nothing written. The script now adds the repo root to `sys.path`, the idiom other `scripts/run_*.py`
files already use.

**What does not change (frozen):** the model configuration, the one-sided HAC-DM test, α = 0.05 and
`PREREGISTERED_N_FOR_POWER = 135.9`. The proxy arm is unchanged and remains exploratory (never
confirmatory); its label is same-day, so its h1 embargo holds by construction.

**What the flaws did to the evidence** (same config, same data, re-measured 2026-09-23 at master
`a26c5ab2`, `scripts/measure_preregistration_embargo.py` → `reports/preregistration_embargo_a1.json`):

| protocol | n | effective n | accuracy | always-up | one-sided HAC-DM p |
|---|---|---|---|---|---|
| original (no embargo) | 161 | 112.5 | 65.84% | 59.01% | 0.0043 |
| embargo on `label_date_h2` | 159 | 159.2 | 60.38% | 59.75% | 0.327 |

With the embargo, config J shows no significant edge over always-up on the data that selected it.
The embargoed run has 2 fewer folds because a fold now needs ≥ 20 *matured* training rows.

*Reproduction note, stated plainly:* this re-measurement gives the no-embargo p as 0.0043
(effective n 112.5), not the 0.00340 (effective n 119.38) frozen above; accuracy and mean loss
difference reproduce exactly, the long-run variance does not (0.10885 vs 0.10260). The cause was not
isolated. An earlier diagnostic note recorded config J's embargoed accuracy as 57.76% (p = 0.776); a
re-run of that same diagnostic on today's data gives 59.63% (p = 0.327, n = 161) — the 57.76% figure
matches the *live logistic* model's embargoed accuracy and appears to have been transcribed onto the
wrong row. Neither discrepancy changes the conclusion.

**Consequence for power.** 135.9 was derived from the leaky effect size. The embargoed effect on the
selection data is ~0.6 pp, so if config J has any real edge it is much smaller than the power target
assumes — the confirmatory test is optimistic about its own power, and a non-significant result at
135.9 effective folds should be read as "no detectable edge", not "no edge". The target stays frozen
anyway: re-deriving it now, after seeing the embargoed result, would be the goalpost-moving this
document forbids. Counting only new days, 135.9 effective folds is ~136 new labelled days if the
embargoed effective/raw ratio (1.00 above) holds, or ~183 at the leaky protocol's ratio (0.70). New
labelled days currently accrue at 0.66-0.70 per calendar day (last 30/60/90 days: 20/42/59 rows), so
the target is reached roughly between April and July 2027.

## Two shadow arms

1. **Live arm** (`run_live_arm`): re-scores config J on the live h2 dataset every
   `weekly-backtest.yml` run. Folds accumulate slowly (one new IBJA label matures roughly once a
   week) — this arm will take a long time to reach 135.9 effective folds on its own.

2. **Proxy dead-zone arm** (`run_proxy_arm`): scores the SAME model/calibration/class-weighting on
   `ml.inr_proxy_labels`' realigned proxy history (2013-2026, n=5,014 days), restricted to ADR 032's
   dead-zone threshold (|implied move| ≥ 100 Rs/gram — 85-100% real-IBJA agreement there, vs 44-54%
   below it). Feature set differs from the live arm: only the M1 calendar drivers (india_vix,
   wedding/budget/duty-event windows) are computable across the full 2013-2026 range — the live
   snapshot-derived features do not exist that far back.

   **Explicit, load-bearing caveat:** the proxy label is a SAME-DAY (h1-equivalent) direction, not h2.
   ADR 034 found config J does NOT replicate at h1 on live data (49.08% vs 50.92%, not significant).
   This proxy-arm result therefore answers a related but DIFFERENT question — does calibrated-GBM+
   balanced help on much more history at h1-equivalent framing with a calendar-only feature set — not
   a replication of the h2 finding. **Never pooled with the live arm's n or effective_n.**

   **First-run result (2026-09-23, n=349 dead-zone days, 329 test folds after min_train=20 warmup):
   accuracy 48.02% vs always-up baseline 55.62% — WORSE than baseline, not better** (mean_diff=+0.076,
   one-sided p=0.980, not significant in the "better" direction). Reported plainly: this arm does not
   currently support config J's edge — consistent with ADR 034's own finding that the M1 drivers alone
   do not help (config G), now extended to a feature-starved, calendar-only proxy setting. Not
   evidence against the live h2 finding (different label horizon, different feature set) — evidence
   that the *effect does not trivially generalize* to a much larger but feature-poor dataset.

## Decision

Pre-register now, before the next weekly eval (Sunday 2026-09-27 02:00 UTC; an earlier draft of this
line said Monday 2026-09-28, which was wrong) scores any new h2 fold. Wire both arms into
`weekly-backtest.yml` as additive, `continue-on-error: true` steps that append to
`data/preregistered_h2_shadow_results.json` (an append-only audit log, never overwritten). Neither arm
touches `ml.direction.gate`, `data/direction_baseline.json`, or any user-facing path — see ADR 036's
`is_signal_promoted` for the separate, required, human-approved promotion step this pre-registration
feeds into if and when the live arm's effective_n reaches 135.9 with a still-significant result.

**Will not report the live arm's result as confirmatory before `effective_n >= 135.9` is reached** —
per GG's explicit instruction ("report when the pre-registered n is reached — not before"). Each
weekly run's current n/effective_n/p-value is logged regardless, so progress is auditable, but
`append_shadow_result` computes and stores `reached_preregistered_n` explicitly rather than leaving it
implicit.

## Consequences

- The live arm will take a long time (~weeks to months, at roughly one new matured h2 label per real
  trading day) to reach 135.9 effective folds from its current 119.38 — this pre-registration commits
  to *not* treating any interim significant result on the live arm as confirmatory before then.
- The proxy arm's honest negative first-run result is now on record before any future audit could be
  tempted to re-run it with a different feature set "until something works" — the frozen config and
  test in this ADR is what any future proxy-arm re-run must be compared against, not cherry-picked.
- `ml.direction.config_sweep.run_config_sweep` gained an additive `return_raw` parameter (default
  `False`, zero behavior change to existing callers) so this module and any future one can run its own
  significance test on the walk-forward's raw per-fold output instead of relying on
  `compute_direction_metrics`'s built-in McNemar test.

## Alternatives considered

- **Re-running the original McNemar test on new data instead of switching to DM-HAC.** Rejected:
  item 3's corrections (this session, ADR 037) established DM-HAC as the more rigorous standard for
  exactly this kind of overlapping-horizon walk-forward comparison; using a weaker test for the
  pre-registered confirmation while using the stronger one everywhere else in this session's M2 work
  would be an inconsistency with no principled justification.
- **Treating the proxy arm's h1-equivalent framing as if it were a genuine h2 replication.** Rejected
  as dishonest — the label horizons are different by construction (proxy label is same-day; ADR 034's
  h2 label matures 2 days out) and ADR 034 already showed this candidate's edge is h2-specific. Kept
  as two separately-reported arms rather than one conflated number.
- **Pooling live-arm and proxy-arm folds into a single effective_n for the 135.9 target.** Rejected for
  the same reason — different label construction, different feature set, different horizon; pooling
  would silently launder a heterogeneous mixture into what reads as one homogeneous confirmation.
