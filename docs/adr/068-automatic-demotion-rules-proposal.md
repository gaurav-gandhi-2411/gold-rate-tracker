# ADR 068: automatic demotion of a live model to "hold"

**Status:** Accepted and wired 2026-10-05 (GG decision D3: direction floor 0.55, persistence 3,
windows 40/40/60; "if the live model trips it, the site falls back to 'hold' automatically and an
alert reaches GG through the existing GitHub-side ntfy path"). Built in `ml/demotion.py`, called
from `ml.nextfix.run`; alert T16 in `ml/notifications.py`. Tests: `tests/test_demotion.py`,
`tests/test_demotion_wired.py`.

## Context

The live direction signal is gated by `decide_direction_signal` on the **cumulative** out-of-sample
record (ADR 064). With 143 days banked, a regime change has to outweigh months of earlier wins before
the cumulative p-value moves, so a model could keep showing long after it stopped working. The gate's
only baseline is always-up (48.95% in this sample), which a model with no edge at all can beat by
luck at n = 137. The error side has no demotion rule at all.

## Proposal

Three rolling-window rules over the model's own resolved forecasts (`ml/demotion.py`); a rule demotes
only after `persist` consecutive daily breaches; re-promotion is never automatic.

| Rule | Window | Breach when | Proposed |
|---|---|---|---|
| error | last 40 folds | model's mean absolute error is above holding the last fix AND the one-sided Newey-West (4 lags) test says worse | alpha 0.05 |
| direction | last 40 folds that moved | one-sided exact binomial rejects "true accuracy >= floor" | floor 0.50, alpha 0.05 (see below) |
| range | last 60 folds | one-sided exact binomial rejects "true coverage >= 0.75" (nominal 0.80 minus 0.05 slack) | alpha 0.05 |

`persist` = 3, minimum 30 folds before any rule can fire. What happens on demotion (for GG to
choose): the headline reverts to hold-the-last-fix with the flat-hold range; the direction line is
hidden; the page says nothing new.

## Operating characteristics (VERIFIED: `scripts/simulate_demotion.py`, seed 42, 400 simulated
120-day futures per cell, moving-block bootstrap of the 143-fold record;
`reports/model_audit_2026-10/demotion_simulation.json`, commit of this ADR)

"still good" = futures resampled from the model's own record; "edge lost" = forecasts shuffled
against outcomes (marginal behaviour kept, relationship destroyed).

| Configuration | Wrongly demoted (any rule) | Edge lost: caught within 120 days (any rule) | Median day of demotion |
|---|---|---|---|
| proposed (persist 3, windows 40/40/60, floor 0.50) | 4.3% | 80.3% | 53 |
| persist 1 | 4.8% | 90.0% | 41 |
| persist 5 | 1.0% | 77.3% | 49 |
| windows 60/60 | 1.8% | 76.0% | 46.5 |
| direction floor 0.55 | 3.5% | 85.3% | 48 |
| direction floor 0.60 | 5.8% | 89.8% | 44 |

Per rule: with floor 0.50 the **direction rule almost never fires** on a model with no edge
(20% within 120 days): a model guessing at 50% is not "below 50%". Floor 0.55 catches 59%
(0% false); floor 0.60 catches 85% (3.3% false). The error rule carries nearly all of the detection.

## Decisions (answered by GG, 2026-10-05, D3)

1. Turn it on at all, and the action on demotion.
2. `persist` (false-demotion vs detection delay), window sizes, alpha.
3. Direction floor: 0.50 (nearly inert) vs 0.55 or 0.60 (an actual "no edge" detector).
4. Whether to add the parity rule P3 (ADR 067) as the error baseline instead of hold.

## Limits

- Simulations resample one 143-day record: it cannot show behaviour in regimes it never saw.
- A model that lost its edge gradually is caught later than one that lost it abruptly.
- Daily re-testing inflates false alarms; `persist` is the control and is simulated, not derived.

## Wired behaviour (D3)

- Every `ml.nextfix.run` evaluates the rules on the LIVE model's record (P3, ADR 069), including
  its re-run days, and keeps `data/model_demotion_state.json` (committed by check-price's bot PR).
- **On a trip:** `forecast()` skips the model window, so the headline holds the latest fix with the
  flat-hold range and no direction line is shown (hold windows never show one). Any rule trips the
  whole model; the range rule does not demote the range alone.
- **Sticky:** nothing re-promotes. Resetting the state file while the record is still degraded
  re-demotes at the next run. To bring the model back: wait until the record recovers (or rebuild
  it), then set `demoted` to false in the state file in a human PR.
- **Alert T16:** priority 5, bypasses quiet hours, once per demotion (deduped on `since`), private
  topic only (`NTFY_TOPIC`; T16 is not on the public allowlist).
- **A broken monitor** never switches the model off and never raises; `next_fix.demotion.checked`
  is false and the rules re-run on every cycle. A state file written for another model version is
  ignored.
- **Proof (tests):** a healthy synthetic record: not demoted, no alert. An inverted model: demoted
  on the run, mode `after_afternoon_rate`, `demoted` true, `p_up` null, alert built once, state
  persisted; stays demoted when the record looks healthy again; reset-with-degraded-record re-fires.
- Evaluated on the real P3 record at wiring time: error rule not breached (model MAE 104.2 vs hold
  125.7 over the last 40 folds, p worse 0.985), direction not breached (28 of 40, p below floor
  0.98). Operating characteristics at these settings are in the table above (row "direction floor
  0.55").

## Rollback

Remove the `_demotion` call in `ml.nextfix.run` (the live model then never demotes) or delete
`data/model_demotion_state.json`; the alert needs no removal (it only reads the state).

## 2026-10-07 verifier note

Two corrections to how this ADR reads against the code, found by an independent verifier:

1. **Whole-model demotion.** Any single rule (error, direction or range) demotes the WHOLE model to
   holding the last fix, point and range together. There is no range-only demotion; the earlier
   wording "demote the range (not the point)" was wrong and has been removed from `ml/demotion.py`.
2. **What the windows score today.** The 40/40/60 windows score the model record, which until forward
   n >= 40 (P3 forward from decision day 2026-10-07) is mostly retrospective re-run folds, not
   forecasts users were shown. The rules only score purely published forecasts once forward n reaches
   the window length.

Also changed the same day (rule 98a, fail closed): a monitor that cannot run now holds the model
(reason `monitor_failed`, `checked` false, nothing written to the state file) instead of leaving it
unchanged, and a state file with a missing `model_version` or malformed `history` / `reasons` is
treated as unreadable (demoted, reason `state_unreadable`). This supersedes the "A broken monitor
never switches the model off" bullet above.

## 2026-10-08 amendment: small-sample size of the error test (finding F7)

**Who:** Claude Code, under GG's delegation of 2026-10-08. **Thresholds are unchanged**: demotion
floor 0.55, persistence 3, windows 40/40/60, alphas 0.05, minimum 30 folds.

**Finding (independent review, reproduced here).** The error rule used a normal-tail Newey-West
p-value with 4 lags on a window of 40. With so few effective observations the variance estimate is
biased low, so the test rejects a true "no difference" far more than 5% of the time. A model that
is exactly as good as holding was flagged by a single check 7.4% to 12.8% of the time (tables
below).

**Decision.** Demoting a model that truly has no skill is correct behaviour, not a false alarm. The
problem is that the inflated size also inflates the WRONGFUL demotion of a model that does have
skill. `hac_one_sided_p_worse` (same name and signature) now uses a Student-t tail with `lags`
(4) degrees of freedom instead of the normal tail. Chosen by simulation among: t with n-1 degrees of
freedom (still 8.7% at AR 0.3), the same with a Hansen-Hodrick style n/(n-1) variance rescale (8.5%),
t with 5, 4 and 3 degrees of freedom. A bootstrap was also tried in a scratch run (studentized
circular block bootstrap, 399 resamples: 5.7% / 6.9% / 7.7% at AR 0.0 / 0.3 / 0.5 on 1500 paths,
not committed); it was no better than the t(4) tail and far slower, so it was not adopted.
`error_breach` and `demotion_status` are untouched. Direction and range rules were checked and not
changed (exact binomial, sizes below). Rule definitions in the proposal table above that say
"Newey-West (4 lags) test" now mean the Student-t version.

VERIFIED: `scripts/simulate_demotion_size.py`, seed 42, 5000 paths per sequential scenario, 20000
per single-check cell; outputs `reports/model_audit_2026-10/demotion_test_size.json` and
`.md`. The P3 series (`data/nextfix_p3_oos.json`, 145 folds): model MAE / hold MAE 0.9266, mean loss
difference -8.41, sd 36.33, lag-1 autocorrelation 0.096; futures are moving-block bootstraps (blocks
of 5) of it.

Single-check size at nominal 5% (n = 40, true mean difference zero):

| Null | OLD | NEW |
|---|---|---|
| t(4), AR(1) 0.0 | 7.41% | 3.26% |
| t(4), AR(1) 0.3 | 9.07% | 4.54% |
| t(4), AR(1) 0.5 | 11.77% | 6.68% |
| real P3 loss series, demeaned (model exactly as good as holding) | 12.79% | 7.22% |

Full rule (persistence 3, 121 daily checks; "day" = daily check number, check 1 uses 30 folds):

| Scenario | Test | Demoted within 40 | Mean day (within 40) | Demoted within 121 | Mean day (within 121) |
|---|---|---|---|---|---|
| WRONGFUL: model WITH P3's effect | OLD | 2.68% | 17.6 | 6.28% | 53.3 |
| | NEW | 1.02% | 18.4 | 2.20% | 51.0 |
| No skill: exactly as good as holding | OLD | 33.72% | 15.3 | 65.96% | 45.5 |
| | NEW | 22.20% | 16.6 | 47.78% | 49.7 |
| No skill: 10% worse than holding | OLD | 87.70% | 9.2 | 99.44% | 15.4 |
| | NEW | 78.66% | 10.7 | 98.12% | 21.3 |
| Synthetic null t(4), AR 0.0 | OLD | 21.44% | 16.3 | 48.16% | 51.0 |
| | NEW | 11.28% | 17.4 | 27.62% | 54.8 |
| Synthetic null t(4), AR 0.3 | OLD | 26.44% | 16.0 | 56.28% | 49.0 |
| | NEW | 15.06% | 17.8 | 36.28% | 53.7 |
| Synthetic null t(4), AR 0.5 | OLD | 30.58% | 15.4 | 62.00% | 47.0 |
| | NEW | 19.42% | 16.1 | 43.52% | 51.4 |

Direction and range rules (exact binomial probability of a breach at the edge of the null, a single
check): direction at true accuracy 0.55: 4.05% (n = 40), 3.34% (n = 30); range at true coverage
0.75: 2.98% (n = 60), 2.16% (n = 30). Not inflated; unchanged.

**Consequences and caveats.**
- Wrongful demotion of a model with P3's measured effect falls from 6.3% to 2.2% over 121 checks.
- The catch rate for a model 10% worse than holding stays 98.1% within 121 checks (was 99.4%) but
  slower: mean day 21.3 (was 15.4); within 40 checks 78.7% (was 87.7%). That is the price of the
  larger critical value (one-sided 5%: 2.13 instead of 1.64).
- The size is not exactly 5% everywhere: 4.5% at AR 0.3, 6.7% at AR 0.5, and 7.2% on the real P3
  loss series, which is skewed and heavy-tailed. The residual comes from the skew, not from the
  small-sample bias this fixes. t with 3 degrees of freedom would reach 5.9% there but costs more
  power; not adopted.
- Re-testing every day on overlapping windows compounds any single-check size: even a perfectly
  sized test demotes a no-skill model within 121 checks far more often than 5%. For a no-skill
  model that is the desired behaviour; for a skilled one the 2.2% above is the residual risk.
- On the real committed record the status is unchanged: nothing demoted before or after (error
  p-value over the last 40 folds 0.975 old, 0.939 new; direction not breached).
- Would the sequential logic of ADR 072 / `ml/promotion.py` help the demotion monitor's power? In
  my view, qualitatively yes: a rule that accumulates evidence across checks with a pre-set error
  budget (a sequential test) would spend the 5% once over the whole horizon instead of per daily
  check, and could use a lower critical value per look than the persistence-3 workaround, so it
  would likely detect a worse model sooner at a lower wrongful-demotion rate. This is untested for
  the demotion monitor; D1's executor simulates it. `docs/adr/072` and `ml/promotion.py` were not
  touched here.
