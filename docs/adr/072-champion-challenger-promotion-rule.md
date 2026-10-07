# ADR 072: forward champion/challenger promotion rule, pre-registered

**Status:** Proposed 2026-10-07 (GG brief of 2026-10-07, item 3). Pre-registration: this text and
`ml/promotion.py` (`RULE`, `RULE_SHA256`) are frozen by the commit that adds this file, **before any
challenger has been scored against the live model under this rule**. Results go below a `## Results`
heading. Changing a number below is a new ADR and GG's decision, never a tuning step
(`tests/test_promotion.py` fails if the code's rule and this hash drift apart).

## Context

Nothing is proven live yet. Every model figure so far is a backtest, and backtests have flattered
this project before (ADR 067: the ensemble's retrospective edge over P3 was -1.2% with an interval
spanning zero; P3's edge over holding, -7.3%, was found after the fact). The only arbiter from here
is **forward** performance against the incumbent, on days that were forecast live, judged by a rule
written before the data exists.

The live model is P3 (ADR 069), forward from decision day 2026-10-07 (`P3_FORWARD_FROM`). The
demotion rules (ADR 068) can only move a model **down to holding**. This ADR is the other direction:
how a challenger replaces the live model.

## Decision: the rule (frozen)

All comparisons use decision days both models issued **live** (`retro` not true, decision day on or
after `common_start`). Nothing older than a model's own registration date counts. Loss is the
absolute error of the forecast next fix, `|pm1 - pm0 * exp(ret)|`, in Rs./g.

| Parameter | Value |
|---|---|
| `common_start` | 2026-10-07 |
| `min_gain` | challenger mean error at least **5%** below the champion's |
| test | one-sided Diebold-Mariano, Newey-West variance, **4 lags**, `alpha = 0.05` |
| multiplicity | **Benjamini-Hochberg** across every challenger that has reached its required n |
| `min_days` | never fewer than **40** forward days (ADR 071's floor) |
| required n | `n = ((z_{0.95} + z_{0.80}) * sigma_LR / delta)^2`, `delta = 5%` of champion MAE, `sigma_LR` the Newey-West long-run sd of the daily loss difference; the window is `max(40, n)` |
| coverage | the challenger's own 80% range must not be significantly below 0.80 (exact one-sided binomial, `alpha = 0.05`) on those days |
| direction | challenger direction hit-rate on days the fix moved is **>=** the champion's on the same days |
| switchable | only models with a live predictor in `ml/nextfix.py`: `p3`, `p3_roll60`, `p3_monday`, `ensemble` |

A challenger is promoted only when **all** hold. If more than one qualifies, the lowest mean error
is promoted; at most one promotion per run.

Rule hash (SHA-256 of the canonical JSON of `ml.promotion.RULE`):

`0782301d8890788287be983c63d3d4e9bbd9eb0d8cd5d50d213960207dc44902`

### Required n is computed, not assumed

Each run reports, per challenger, the observed `sigma_LR`, the required n, the current n, and the
calendar date the window is first reachable at the observed fold rate (5 decision days per 7
calendar days until enough forward days exist to measure the rate). Below 10 forward days `sigma_LR`
cannot be estimated and the row says so. No conclusion is drawn before that date.

### On promotion

1. The switch is automatic, written to `data/champion_state.json` (default `p3`; a missing file means
   P3; an unreadable file or an unknown id **fails closed to P3** and is flagged).
2. The event, with the full evidence row, is appended to the file's history.
3. GG is told over the existing private ntfy path (alert T17, once per promotion).
4. **Reversible in one command:** `python -m ml.promotion rollback` restores the previous champion
   and logs a `rolled_back` event.
5. The demotion rules (ADR 068) apply to the new champion **from its first day as champion**: the
   sticky state is keyed by model version, so a new champion starts with a fresh state and the same
   rules.

### Delivery in two steps (stated, not hidden)

- **Step 1 (this ADR's PR):** the frozen rule, the evaluator (`ml.promotion.decide`), the state file
  format, rollback, tests, and a weekly status that reports every comparison, required n and date.
  **Nothing live changes.** The live forecast does not read the champion file yet.
- **Step 2 (a separate PR, before the earliest date any challenger can qualify):** `ml/nextfix.py`
  reads `data/champion_state.json` to pick the live predictor, the check-price job applies
  `decide`, and the T17 alert. It needs an independent verifier because it changes live behaviour.
  No promotion can happen before step 2 merges; the earliest qualifying date (below) is weeks away.

**Step 2 delivered (2026-10-08, branch `feat/champion-switch-wiring`; the rule and its hash are
unchanged).** `ml.nextfix.run` loads `data/champion_state.json` and uses the champion's own record,
version label and predictor everywhere the P3 constants were used (`CHAMPION_REGISTRY`: `p3`,
`p3_roll60`, `p3_monday`, `ensemble`); with no file the output is identical to before. After the
day's records and the demotion check, `promotion.decide` runs and a winner is written atomically.
**A promotion takes effect on the NEXT run** (one run, one model: the forecast of the run that
promotes was already built by the old champion). The whole step fails closed: any exception is
logged at error level and leaves the champion file and the live forecast untouched; an unreadable
champion file means P3, flagged, and never a promotion; a champion with no forward-day record of its
own is never compared. The demotion state is keyed by model version, so the new champion starts with
a fresh state and the same rules. While a challenger is champion, the shadow is P3 on its own record
(`next_fix.shadow_ensemble.shadow_model_version` names it). `forecast.json` carries
`next_fix.champion`; alert T17 (private topic, once per change) names the old and new model and the
undo command. `check-price.yml` commits the file once it exists.

## Pre-registered challengers (forward from the stated day)

| Id | What it is | Counts forward from | Switchable |
|---|---|---|---|
| `ensemble` | ridge + 5 tanh-MLPs, the pre-P3 live model (ADR 064/065), now shadow | 2026-10-07 (`common_start`) | yes |
| `p3_roll60` | P3 with the slope fitted on the last 60 pairs (ADR 071 V1) | 2026-10-07 | yes |
| `p3_monday` | P3 with a Monday-only slope (ADR 071 V2) | 2026-10-07 | yes |
| `hourly` | hourly world-price variant (ADR 066) | the day its **own** 2026-10-16 check passes; not before | **no**: needs a live predictor in a code PR |
| `p3_hourly` | equal-weight average of P3's and `hourly`'s `ret` | the later of 2026-10-16 and the first day both have a record | **no**: same |

`p3_roll60` and `p3_monday` additionally keep ADR 071's own, stricter rule (forward n >= 40,
`alpha = 0.025`, 2% gain) for their stated verdict; the rule above is the one that can switch the
live model, and it is stricter on gain (5%).

Retrospective numbers (written before the dates above) are shown in a separate column and never
counted.

## Pre-registered fixes for the known weaknesses (shadow only, never live)

Each is specified here; **none is implemented or scored until its own PR merges with its own frozen
hash**, and forward days count from that merge. Nothing here is a result.

1. **Mondays (the only stratum worse than holding).** (a) `p3_monday` is already frozen (ADR 071).
   (b) A weekend-aware feature: split Monday's world move into the part before the Sunday open and
   the part after, each with its own slope fitted on resolved Mondays. It needs timestamped world
   prices at the weekend boundary; if the hourly record cannot supply them, it is not built and that
   is reported.
2. **Under-sized moves (calibration slope 1.64 for P3 and the ensemble).** The conformal range is
   built from realised errors scaled by volatility, so it already widens for under-sized moves; the
   slope mainly means the **point** forecast is too small. Plan: first confirm on forward days
   whether coverage is on target (the demotion monitor already tracks it). Then two shadow
   candidates: `p3_scaled` (point forecast multiplied by a calibration slope fitted on the last 60
   out-of-sample folds, floor 1.0, cap 2.0) and `p3_bandbucket` (the conformal quantile computed
   separately for forecasts above and below the rolling median `|ret|/vol`, so the range widens only
   where the model forecasts large moves, not everywhere).
3. **Nowcast weekends (Rs.96 vs Rs.45/g).** The morning-rate variant and the last-reading rule are
   decided per ADR 063 on **2026-10-22**, with the 6 late-IBJA days excluded as pre-registered
   there. If it qualifies on its own terms it is promoted; this ADR adds nothing to it.

## Consequences

- A challenger cannot be promoted on a good-looking backtest: every input is forward and
  pre-registered; multiplicity is controlled; the window length comes from the data's own variance.
- A genuinely better challenger can be slow to win: a 5% gain on a noisy daily loss needs many days.
  The status page states the date it becomes possible; waiting is the honest answer until then.
- The hourly and P3+hourly challengers cannot go live without a code PR, by design.
- The rule hash is code-checked, but the hash does not stop GG changing the rule; it makes a change
  visible.

## Alternatives considered

- **Promote on a backtest win:** rejected; the reason this ADR exists.
- **A fixed N for every challenger:** rejected; the required n depends on the observed variance of
  the loss difference, which differs by challenger.
- **Bonferroni:** valid but stricter than needed for a handful of positively correlated challengers;
  BH was the brief's choice.

## Results

None yet. Forward n at registration: 0.
