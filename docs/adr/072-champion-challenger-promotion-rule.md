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

**Decision.** The fixed-sample test below (marked superseded (v1)) is replaced by a pre-registered
sequential test that is valid at every look. **Made by:** Claude Code, under GG's delegation of
2026-10-07 (the choice of a sequential test was relayed as GG's decision of 2026-10-08). **When:**
2026-10-08, before any challenger has 10 forward days.

**Verified at amendment time.** Forward folds (`retro` false, decision day on or after 2026-10-07)
in `data/nextfix_p3_oos.json`, `data/nextfix_oos.json`, `data/nextfix_p3_variants_oos.json`: `p3` 0,
`ensemble` 0, `p3_roll60` 0, `p3_monday` 0 (zero forward days). The amendment changes the rule
before any forward evidence exists, so it cannot be tuned to an outcome.

**Why, and honest notes.**
- The brief that asked for this amendment sized v1 at ~407-421 forward days. That did not reproduce
  on real data: the v1 formula on the retrospective loss differences gives 80.9 (`ensemble`), 171.6
  (`p3_roll60`) and 10.4 (`p3_monday`, so the 40-day floor), against the synthetic figure. The
  forward variance is unknown until forward days exist.
- The structural problem stands without that number: v1 sizes n from the noisiest first days, is
  re-checked daily with no allowance for the repeated look, and certifies "better than zero" while
  requiring only an observed 5% gain.
- The asymptotic confidence sequence is anti-conservative at small n on these series (false
  promotion at the 5% boundary with plain Newey-West: 7.1% `ensemble`, 10.0% `p3_roll60`, 3.3%
  `p3_monday`, 16.6% any of three). It is fixed by the variance `max(Newey-West, iid) x 1.5`.
- The horizon is 180 CALENDAR days (about 128 decision days). That is a GG judgement call, not a
  derived number.

### The rule (frozen, v2)

Per day `e_t = 0.95 * loss_champion_t - loss_challenger_t` on days both models issued live (`retro`
not true, on or after `common_start`); loss `|pm1 - pm0 * exp(ret)|`. `mean(e) > 0` means the
challenger's mean error is more than 5% below the champion's, so no ratio enters the decision.

| Parameter | Value |
|---|---|
| `version`, test | 2; one-sided normal-mixture confidence sequence on `mean(e)` (Waudby-Smith et al. 2021, asymptotic) |
| `min_gain`, `min_forward_days` | 5% (built into `e`); **20** (no look before, then a look every day) |
| `alpha`, `family_size` | 0.05 Bonferroni on the sequence level: 0.05 / 3 = 0.0167 per challenger |
| `hac_lags`, variance | 4; max(Newey-West, sample variance) x 1.5 |
| `mix_sd` | 0.3 |
| `horizon_days` | **180** calendar days from registration (2026-10-07); then **retired**, never promoted |
| coverage, direction | unchanged from v1 |
| switchable | `p3`, `p3_roll60`, `p3_monday`, `ensemble` (live-capable only) |

PROMOTE only when the lower bound `L_e > 0`, every gate holds and the challenger is live-capable and
not retired; at most one promotion per run (lowest mean error). Retirement uses the data's own
clock. Adding a live-capable challenger changes `family_size` and needs a new ADR.

Rule hash of v2 (SHA-256 of canonical JSON of `ml.promotion.RULE`, checked by
`tests/test_promotion.py`):

`1bbc5dd3eeaefeeed10671a4700378f4d68f63790da8a25acfc1fccf12c91290`

### Simulation result (full tables: `reports/sequential_promotion_simulation.md`)

Seed 42, 10,000 paths per cell, stationary-bootstrap of the retrospective loss differences, 128
decision days. The script `scripts/simulate_sequential_promotion.py` that produces the report
arrives in the next part of this split (part B); the report files are committed here unchanged.
- Wrongful promotion at 0% true gain: 0.0% per challenger. At the 5% boundary: 0.4%, 1.5%, 0.7%;
  2.1% for any of the three (under the 5% requirement).
- A challenger 20% better is promoted in a median of 20-33 days (v1: 40-120 days, or stalled).
- It is stricter, not faster, for true gains of 5-10%: those are mostly retired at day 180. An
  independent paired bootstrap on the real losses found power at a true 10% gain of about 10%
  (ensemble) and 21% (roll60), roughly half the report's 21% and 37%; treat the report's figures as
  an upper end. At 20% gain roll60 is promoted in 98.7% of paths within the horizon (the others
  99.8% and 100%).
- **Assumption behind "at most 5% for any of three":** it holds when the daily difference series
  has modest autocorrelation (an independent check found 0.1% to 3.1% per challenger at AR 0 to
  0.5, and 0.5% for any of three on block bootstraps of the real losses). It does NOT hold for
  strongly autocorrelated series: at AR 0.6 a challenger at exactly the 5% boundary is promoted in
  4.6% of paths (about 13% for any of three if independent), at AR 0.7 in 7.3%. The retrospective
  lag 1-4 autocorrelations are -0.17 to +0.19, but those folds are not consecutive days, so real
  daily autocorrelation is unknown until forward days exist. The weekly status page therefore
  reports the lag 1-4 autocorrelation of the forward series once it has 20 days; if it exceeds 0.4
  the first-look rule is to be re-examined by a new ADR before any promotion is trusted.
- The 1.5x variance inflation and the mixture scale were chosen after reading the same
  retrospective simulation that reports the 2.1%, so that figure is in-sample for the tuning; the
  independent AR and bootstrap checks above are the out-of-sample evidence.

## Decision: the rule (v1, superseded by Amendment 1)

**superseded (v1).** Kept verbatim as the record of what was pre-registered on 2026-10-07; the v1
parameters below are no longer applied. The v1 hash is kept so the supersession is checkable.

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
