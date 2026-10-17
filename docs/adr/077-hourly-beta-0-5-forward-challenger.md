# ADR 077: Hourly world-price model with beta 0.5, a forward-only shadow challenger (pre-registration)

Status: pre-registered by CC on 2026-10-17 under GG's brief of the same day. ADR 076 is final and is not
touched by this ADR. Registration only: no effect is computed, printed or read before the look below.

## Context

ADR 066's backtest (cached 1-hour bars, about 60 days) compared three betas of the pass-through forecast
`B x (G(now) / G(t_B)) ** beta`. Beta 0.5 had the lower mean absolute error in the morning window (23.3
against 29.8 for beta 1.0) and was level in the afternoon (79.0 against 78.2). ADR 076 fixed beta at 1.0 in
advance (the live predictor's value) and does not use those numbers.

**Plain statement: beta 0.5 was selected after looking at the ADR 066 backtest.** That backtest, and every
shadow entry logged up to the registration instant below, were seen while choosing it. They are spent for
this challenger and can never count toward it, however they would score.

## Decision

1. **Challenger.** `hourly_b05`: the hourly model with beta 0.5. It needs no new data: every shadow entry in
   `data/nextfix_intraday_shadow.json` already carries `pred["beta_0.5"]` (`BETAS = (0.0, 0.5, 1.0)` in
   `ml/nextfix_intraday.py`). Shadow only; it does not reach the page, `forecast.json`, the champion file or
   any existing record.
2. **Counts from.** Only entries logged strictly after **2026-10-18T00:00:00Z** (`REGISTERED_FROM` in
   `ml/hourly_beta05.py`), chosen to be later than the merge time of this ADR's PR; if the merge turns out
   to be later than that instant the ADR is void and re-registered. Windows `after_morning_rate` and
   `after_afternoon_rate`, resolved as `ml.nextfix_intraday.score` resolves them.
3. **One look, late and after ADR 076.** On the first Monday on or after **2026-12-14** on which a window
   has at least 20 resolved fixes and at least 28 calendar days of counted entries, counted on entries logged
   before 00:00Z of that Monday, and only once ADR 076's look has been taken. Beta 0.5 and beta 1.0 forecasts
   are strongly correlated and these entries are the same ones ADR 076 reserves, so an earlier read of beta
   0.5 would leak into ADR 076's look. Until the look day no effect of any kind is computed for this
   challenger.
4. **Conditions per window, both required:** (a) the 95% cluster-bootstrap CI (seed 42, 10,000) of the
   change in MAE against holding the fix lies below 0; (b) one-sided Diebold-Mariano p below 0.025. The
   0.025 is Bonferroni over the two windows; it is **not** corrected for ADR 076's test of beta 1.0 on the
   same period. If both pass, this ADR does not choose between them (GG's decision), and neither is a claim
   that one beta beats the other. No interval-coverage condition is added here: there is no frozen
   calibration for beta 0.5, and coverage is a demotion readout (point 6) once the model is live.
5. **Promotion only through rule v7.** A pass makes the challenger eligible for a code PR that gives it a
   live predictor and a daily record, and for a new ADR adding it to the v7 family (family size 4, so
   alpha 0.05 / 4 per challenger, stricter than today). From then rule v7 (ADR 072, hash `e4efabec`) judges it
   against the live champion exactly as written, on forward days counted from that code PR's merge. The look
   above is a gate to that step, never a substitute for it. Nothing in `ml/promotion.py` changes now.
6. **Protections unchanged.** The ADR 073 runtime leak guard and the ADR 068 demotion rules apply to any
   live predictor built later, with their current numbers.
7. **Implementation.** `ml/hourly_beta05.py` freezes the rule (`RULE`, `RULE_SHA256`) and selects eligible
   entries (`eligible`); it computes no error of any kind. The look is a separate code PR merged before
   2026-12-14 that must reproduce the hash below and register itself in the ADR 076 effect guard.

Frozen rule, version 1: SHA-256 of the canonical JSON of `RULE` in `ml/hourly_beta05.py` (checked by
`tests/test_adr077_beta05.py`):

`f41f5fa34a6d8666a53564cb29eeb127b59d1df4561c72e537202ee451015b72`

## What this PR guarantees, and how it is checked

- **Nothing scores it early.** `tests/test_adr076_effect_guard.py` enumerates every function that names
  `ml.hourly_beta05`, its challenger id, or the `beta_0.5` forecast key; each must be classified, and only
  the module itself is. A new scorer fails until someone reads it and registers it.
- **The live forecast and all records are unchanged.** No file the live pipeline imports is edited
  (`ml/nextfix_intraday.py`, `ml/promotion.py`, the scripts and workflows are untouched), and a test lists
  every importer of `ml.hourly_beta05` by AST and finds none outside the tests.

## Consequences

- Cost: beta 0.5 cannot be judged before 2026-12-14, and at the earliest about two months after the
  backtest that suggested it.
- It is a second hypothesis on the same period as ADR 076; both passing would not show which beta is right.

## Alternatives

- **Count the ADR 066 backtest days.** Rejected: beta was chosen on them.
- **Add it to the v7 family now.** Rejected: that changes `family_size` and the frozen v7 hash, and the
  challenger has no live predictor or daily record yet.
- **Score it with ADR 076's look.** Rejected: ADR 076 is final, and a second beta read on its look would be
  a second hypothesis inside it.
