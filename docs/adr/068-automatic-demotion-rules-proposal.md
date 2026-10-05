# ADR 068: automatic demotion of a live model to "hold" (PROPOSED; thresholds are GG's)

**Status:** Proposed 2026-10-05. Built (`ml/demotion.py`, `tests/test_demotion.py`,
`scripts/simulate_demotion.py`) and NOT wired: nothing on the live path imports it. Turning it on,
and every number below, is GG's decision.

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

## Decisions for GG

1. Turn it on at all, and the action on demotion.
2. `persist` (false-demotion vs detection delay), window sizes, alpha.
3. Direction floor: 0.50 (nearly inert) vs 0.55 or 0.60 (an actual "no edge" detector).
4. Whether to add the parity rule P3 (ADR 067) as the error baseline instead of hold.

## Limits

- Simulations resample one 143-day record: it cannot show behaviour in regimes it never saw.
- A model that lost its edge gradually is caught later than one that lost it abruptly.
- Daily re-testing inflates false alarms; `persist` is the control and is simulated, not derived.

## Rollback

Not wired. To wire: call `demotion_status(folds, hits)` from `ml.nextfix.run` and act on
`demote_any`; remove the call to revert.
