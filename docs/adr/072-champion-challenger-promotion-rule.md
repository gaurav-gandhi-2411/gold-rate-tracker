# ADR 072: forward champion/challenger promotion rule, pre-registered

**Status:** Proposed 2026-10-07 (GG brief of 2026-10-07, item 3); **amended 2026-10-08 (Amendment 1:
the promotion test is now the sequential rule v2; the v1 text below is superseded (v1))**.
Pre-registration of v1: this text and
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

## Amendment 1 (2026-10-08): a sequential promotion test replaces the fixed sample (rule v2, in force)

**Decision.** The fixed-sample test below ("Decision: the rule", marked superseded (v1)) is replaced
by a pre-registered sequential test that is valid at every look. **Made by:** Claude Code, under GG's
delegation of 2026-10-07 (the choice of a sequential test in place of the fixed n was relayed to
this session as GG's decision of 2026-10-08). **When:** 2026-10-08, before any challenger has 10 forward days.

**Verified at amendment time (not assumed).** Forward folds (`retro` false, decision day on or after
2026-10-07) in the committed records: `p3` 0, `ensemble` 0, `p3_roll60` 0, `p3_monday` 0 (each file
holds 145 folds, all up to 2026-10-05; the two variants carry `retro: true` on all 145). Command:
a count over `data/nextfix_p3_oos.json`, `data/nextfix_oos.json`, `data/nextfix_p3_variants_oos.json`
(the same count is printed by `scripts/simulate_sequential_promotion.py` in its report). So the
amendment changes the rule before any forward evidence exists and cannot be tuned to an outcome.

**Why.** The fixed rule's required n was sized at ~407-421 forward days per the brief that asked
for this amendment, so nothing could be promoted for over a year even for a challenger 70% better.
Two things are true and both are recorded. (1) I could NOT reproduce 407-421: sizing n from the
retrospective loss differences with the v1 formula gives 81 (ensemble), 172 (`p3_roll60`) and the
40-day floor (`p3_monday`); the simulation below shows the old rule deciding in a median of 40-136
days when it decides at all. The ~400 figure therefore came from a variance I do not have; the
forward variance is unknown until forward days exist. (2) The structural problem stands without
that number: v1 sizes n from the very noisy first days, is re-evaluated every daily run (a
repeated look that its p-value does not account for), and it tests "significantly better than zero"
while requiring only an observed 5% gain. It never certifies the 5% claim itself. The sequential
test fixes all three.

### The rule (frozen, v2)

Data: the days both models issued live (`retro` not true, decision day on or after `common_start`),
loss `|pm1 - pm0 * exp(ret)|`. Per day `e_t = 0.95 * loss_champion_t - loss_challenger_t`.
`mean(e) > 0` is exactly "the challenger's mean error is more than 5% below the champion's", so the
gain threshold is built into the series and **no ratio and no plug-in denominator enters the
decision**. For display only, the gain scale is "fraction of the champion's mean error on the same
days": `gain_estimate = 1 - mean(challenger loss) / mean(champion loss)` and
`lower_bound = 0.05 + L_e / mean(champion loss)`, where `L_e` is the bound below.

| Parameter | Value |
|---|---|
| `version` | 2 |
| test | one-sided normal-mixture confidence sequence (Waudby-Smith et al. 2021, asymptotic form; closed-form half-normal mixture, Ville's inequality) on `mean(e)` |
| `min_gain` | 5% (in the series, see above) |
| `min_forward_days` | **20**: no look before 20 days both models issued live (`first_look_day`), then a look every day |
| `alpha`, `family_size` | family-wise `alpha` 0.05 split by **Bonferroni on the sequence level**: each challenger's sequence is run at `0.05 / 3 = 0.0167` |
| `hac_lags` | 4 (Newey-West long-run variance of `e`, mean-centred, Bartlett weights) |
| `variance_floor_iid`, `variance_inflation` | variance used = max(Newey-West, plain sample variance) x **1.5** (measured, see below) |
| `mix_sd` | 0.3 (sd of the mixing distribution of the tilt, in 1/sigma units; tight near 60-130 days) |
| `horizon_days` | **180** calendar days since the challenger's registration date; at day 180 and after it is **retired** (reported as retired, never promoted); a retired challenger returns only by a new ADR |
| coverage, direction | unchanged from v1: coverage not significantly below 0.80 (exact one-sided binomial, 0.05); direction hit-rate >= the champion's on the same days |
| `common_start` | 2026-10-07 |
| switchable | only models with a live predictor: `p3`, `p3_roll60`, `p3_monday`, `ensemble` |

Lower bound after `n` days: `L_e = mean_n - sqrt(V_n) * s*(n) / n`, with `V_n` the variance above and
`s*(n)` the root of `2/(tau*sqrt(a)) * exp(s^2/(2a)) * Phi(s/sqrt(a)) = 1/level`, `a = n + 1/tau^2`,
`tau = mix_sd`. PROMOTE only when `L_e > 0` (the lower bound of the gain exceeds 5%), every gate
holds, the challenger is live-capable and not retired. At most one promotion per run: the lowest mean
error among those qualifying. Retirement is judged on the data's own clock (the champion's last
forward day), not the wall clock, so it is deterministic.

Registration dates (part of the frozen rule and its hash):

| Id | Registered | Note |
|---|---|---|
| `ensemble`, `p3_roll60`, `p3_monday` | 2026-10-07 | horizon ends 2027-04-04 (day 179); retired from 2027-04-05 |
| `p3` | 2026-10-07 | a challenger only after another model has been promoted |
| `hourly`, `p3_hourly` | the first day they have a forward record | not live-capable: scored, never promotable |

Rule hash of v2 (SHA-256 of the canonical JSON of `ml.promotion.RULE`, tied to the code by
`tests/test_promotion.py`):

`1bbc5dd3eeaefeeed10671a4700378f4d68f63790da8a25acfc1fccf12c91290`

**Why a confidence sequence and why Bonferroni.** An anytime-valid bound lets the rule look every
day without a penalty hidden in the p-value; a group-sequential alpha-spending design would fix the
look dates in advance, which a daily job cannot honour when days are missed. Benjamini-Hochberg on
p-values taken at different looks is not valid, and the rule wants at most one promotion, so the
error that matters is the family-wise one: Bonferroni on the sequence level controls it under any
dependence between the challengers and at every look. The cost is small here because only three
challengers are registered per champion; an e-value BH was rejected as more machinery for a gain
that does not exist at m = 3. Adding a live-capable challenger changes `family_size` and needs a new
ADR.

**The asymptotic sequence is anti-conservative at small n on these series, so it was corrected.**
With the plain Newey-West variance the false-promotion rate at the 5%-gain boundary was 7.1%
(`ensemble`), 10.0% (`p3_roll60`), 3.3% (`p3_monday`) and 16.6% for any of the three (10,000 paths,
seed 42, `reports/sequential_promotion_simulation.json`). The frozen variance (floor at the plain
sample variance, x1.5) gives 0.4%, 1.6%, 0.8% and 2.2% for any of the three (95% Monte-Carlo
interval 2.0-2.5%): every challenger is at or under its own 1.67% level and the family under 5% (the same cells re-drawn in the table below read 1.5% for `p3_roll60`: independent draws).
The first look stays at day 20 by decision; the correction is on the variance, not the first look.

### Operating characteristics (simulation, VERIFIED by running it)

Method: `scripts/simulate_sequential_promotion.py`, seed 42, 10,000 paths per cell. Stationary
bootstrap (mean block 6) of the centred retrospective loss differences between P3 and each
challenger on their 145 shared days, the same days for all three challengers; a true mean gain is
then added (0, 5, 10, 20, 40% of the champion's mean error). Horizon: 128 weekday decision days
inside 180 calendar days (public holidays ignored, so a slight over-count). **The retrospective folds
supply variance and autocorrelation only; they are not evidence of any gain.** Caveats: the 145
folds are not consecutive days, so true daily autocorrelation can differ; the variance is assumed
the same at every gain level; gates only block, so ignoring them overstates promotions.

New rule, share promoted within 180 days (95% Monte-Carlo interval) / retired / median days to
decision (a retired path is decided at the horizon, day 128) / mean days:

| True gain | `ensemble` | `p3_roll60` | `p3_monday` |
|---|---|---|---|
| 0% (wrongful promotion) | 0.0% (0.0-0.0) / 100.0% / 128 / 128.0 | 0.0% (0.0-0.0) / 100.0% / 128 / 128.0 | 0.0% (0.0-0.0) / 100.0% / 128 / 128.0 |
| 5% (boundary = size) | 0.4% (0.3-0.6) / 99.6% / 128 / 127.6 | 1.5% (1.2-1.7) / 98.5% / 128 / 126.7 | 0.7% (0.5-0.8) / 99.3% / 128 / 127.3 |
| 10% | 20.6% (19.8-21.4) / 79.4% / 128 / 115.5 | 37.4% (36.5-38.4) / 62.6% / 128 / 102.4 | 99.5% (99.3-99.6) / 0.5% / 35 / 42.1 |
| 20% | 99.8% (99.7-99.8) / 0.2% / 33 / 39.2 | 98.7% (98.4-98.9) / 1.3% / 24 / 36.2 | 100.0% (100.0-100.0) / 0.0% / 20 / 20.0 |
| 40% | 100.0% / 0.0% / 20 / 20.4 | 100.0% / 0.0% / 20 / 20.9 | 100.0% / 0.0% / 20 / 20.0 |

Wrongful-promotion rate: **0.0%** at 0% true gain for each challenger and for any of the three
(under 0.05% in 10,000 paths); **2.2%** (2.0-2.5%) for any of the three at the 5% boundary. Both are
under the 5% requirement.

Old rule (v1, fixed n, applied every day, same paths), share promoted within the same 128 decision
days / within 600 decision days / median days if promoted (within 600):

| True gain | `ensemble` | `p3_roll60` | `p3_monday` |
|---|---|---|---|
| 0% | 2.7% / 2.8% / 63 | 3.0% / 4.3% / 91 | 0.0% / 0.0% / n/a |
| 5% | 63.4% / 87.3% / 100 | 39.0% / 83.8% / 136 | 77.1% / 90.0% / 40 |
| 10% | 93.2% / 100.0% / 86 | 54.2% / 100.0% / 122 | 100.0% / 100.0% / 40 |
| 20% | 93.3% / 100.0% / 85 | 54.8% / 100.0% / 121 | 100.0% / 100.0% / 40 |
| 40% | 93.3% / 100.0% / 85 | 54.8% / 100.0% / 121 | 100.0% / 100.0% / 40 |

**Honest reading.** The sequential rule decides in 20-35 days for a challenger 20% or better, which
the old rule needed 40-120 days for (and `p3_roll60`, whose daily loss difference is positively
autocorrelated, stalled at 55% within 128 days under v1). It is **stricter, not faster, for true
gains of 5-10%**: v1 promoted on an observed 5% gain that was merely significantly above zero,
while v2 must show the 5% itself. A challenger whose true gain is 5-10% on a noisy series is
therefore usually retired at day 180 rather than promoted; that is the intended trade (a wrongful
promotion costs more than a slow one) and a decision to widen it is GG's, by ADR.

### Demotion monitor: would the same logic help? (measured; `ml/demotion.py` NOT changed)

Same bootstrap on P3's centred retrospective (model error minus hold error) series, 128 days.
Current = the error rule of `ml/demotion.py` (last 40 days, one-sided HAC p < 0.05, 3 checks in a
row). Sequential = lower confidence bound of (model - hold) above 0 at level 0.05 from day 20.

| Scenario | Current: fired / median day | Sequential: fired / median day |
|---|---|---|
| model 20% better than holding (false-alarm check) | 0.0% / n/a | 0.0% / n/a |
| model equal to holding | 67.9% / 60 | 3.1% / 60 |
| worse by 10% from day 1 | 99.1% / 32 | 69.5% / 39 |
| worse by 20% from day 1 | 100.0% / 32 | 98.0% / 23 |
| worse by 40% from day 1 | 100.0% / 32 | 100.0% / 20 |
| good (-20%) for 60 days, then worse by 20% | 97.4% / 93 (33 days after the change) | 0.0% / never |

Reading: the sequential bound is faster for a model that is badly wrong from the start (23 vs 32
days at 20% worse) and fires far less when the model merely equals holding (3.1% vs 67.9%), but it
accumulates the whole history, so a model that was good and then deteriorates is never caught
(0% in the change-point scenario) and moderate decline (10%) is caught less often. **It does not
improve the demotion monitor overall. Recommendation: keep the rolling window rule for regime
change; if anything is added it is the sequential bound as a second, early-life rule for the first
~60 days of a model's life.** The false-alarm rate of the current rule at "equal to holding" (67.9%
within 128 days) is a finding for the owner of `ml/demotion.py`: it is the repeated daily look
that a fixed-window test does not pay for, although a model that merely ties holding is not harmful.

### What changed in code

`ml/promotion.py`: `RULE` v2 with the registry, `RULE_SHA256`, `cs_lower_bounds`, `mixture_boundary`,
`running_long_run_var`, `registration_date`; `compare` and `decide` gain `n`, `first_look_day`,
`looks_started`, `lower_bound`, `gain_estimate`, `retired`, `days_since_registration`,
`horizon_left`, and drop `p_better`, `n_required`, `bh_significant` (`required_n` and `bh_reject`
are removed with the rule that used them). `decide(champion_id, records)` keeps its signature (an
optional `as_of` was added) and `promote` / `challengers` / `promotable` / `mae_challenger` keep their
meaning, so `ml.nextfix._promotion_step` is unchanged. The champion state machinery (promote,
rollback, pin, unpin) is untouched. `scripts/build_model_status.py` shows "first look after 20 days
(n so far)", the current lower bound once looking, retired challengers, in plain words.

## Decision: the rule (v1, superseded by Amendment 1)

**superseded (v1).** Kept verbatim as the record of what was pre-registered on 2026-10-07. Reason
for superseding: a fixed required n that cannot be known before forward data exists, re-checked
daily without accounting for the repeated look, certifying only "better than zero" while demanding
an observed 5% (Amendment 1, "Why"). The v1 parameters below are no longer applied; the v2 rule
and its hash are in Amendment 1. The v1 hash is kept so the supersession is checkable.

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

Rule hash of v1, superseded (v1) (SHA-256 of the canonical JSON of the v1 `RULE`):

`0782301d8890788287be983c63d3d4e9bbd9eb0d8cd5d50d213960207dc44902`

### Required n is computed, not assumed (v1, superseded)

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

**Step 2, verifier fixes (2026-10-08; rule and hash still unchanged).**

- **Pin.** `python -m ml.promotion rollback` now also sets `pinned: true` in the champion file and in
  the rollback event; while pinned, the rule does nothing (logged at info), so the next run cannot
  re-promote the same challenger from the same evidence. `python -m ml.promotion unpin` is a person's
  explicit act (event `unpinned`); `show` prints the pin. A rollback is refused (exit 1, nothing
  written) unless the latest champion change is a promotion still in force, so a second rollback
  fails. A `pinned` value that is not a real boolean reads as pinned. T17 for a rollback says it
  was "rolled back by a person" and names `unpin`; for a promotion it keeps "on its own".
- **Demotion state per model.** `data/model_demotion_state.json` stays P3's file (existing file
  untouched); any other model uses `data/model_demotion_state__<model_version>.json`
  (`ml.nextfix.demotion_state_file`). A champion change no longer overwrites another model's sticky
  state, and a model that returns as champion finds its own. A challenger whose own state says
  demoted, or cannot be read, is skipped by the promotion step (the next promotable challenger, if
  any, is considered). `check-price.yml` has a second guarded `git add` for the per-model files. The
  scorecard's demotion monitor and the status page read the live champion's file.
- **Bad champion file.** A valid champion id with a `history` that is not a list of objects reads
  as unreadable (P3, flagged); the promotion step never raises from a champion file problem.
- **Missing champion record.** If the champion (not P3) has no record of its own, that run uses P3,
  logs at error level and publishes `next_fix.champion.fallback = "record_missing"` with
  `effective_id = "p3"`; the champion file is not written.
- **Scorecard.** When `forecast.json` says another model is live, the P3 row's gate and demotion
  readouts read "unavailable: the live model is <id>, not P3" instead of the champion's numbers.

Known and left as is (documented, not fixed): an unreadable champion file is only a log line and the
published `unreadable` flag (no alert); an ensemble champion's `n_forward` counts only folds with
`retro` False (ensemble folds carry no flag); the BH family excludes non-switchable challengers
until they have a live predictor; `scripts/check_bot_pr_sync_allowlist.py` does not parse guarded
`if [ -f ... ]` lines (the runtime guard in the action still enforces `data/` only); a champion
change has no cooldown beyond the pin.

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

Registration dates for the clock of Amendment 1 are in its registry table.

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
  BH was the brief's choice. (Amendment 1 uses Bonferroni on the sequence level after all: BH on
  p-values from different looks is not valid, and only three challengers are registered.)
- **Group-sequential alpha spending, e-value BH (considered for Amendment 1):** rejected for a
  daily job whose look dates cannot be fixed in advance, and for needless machinery at m = 3.

## Results

None yet. Forward n at registration: 0.
