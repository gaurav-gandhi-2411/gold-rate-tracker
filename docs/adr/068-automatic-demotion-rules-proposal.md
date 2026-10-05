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