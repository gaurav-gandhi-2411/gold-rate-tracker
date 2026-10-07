# ADR 069: P3 becomes the live next-fix forecast; the ensemble runs in shadow

**Status:** Accepted 2026-10-05 (GG decision D2: "make P3 the LIVE next-fix forecast. Keep the
ridge/neural-net ensemble running in SHADOW. Score both forward every day, per window, with n and
intervals. Pin the measured USD/INR clock in ml/known_at.py so the leak guard passes on the repo
convention. Before merging, prove P3 reproduces ADR 067's figures and passes the leak guard.")
Builds on ADR 064/065 (the model window) and ADR 067 (the parity comparison).

## Context

ADR 067 (pre-registered, frozen at `bcaa0bac`, 143 folds 2025-07-17..2026-09-30): the ensemble's MAE
105.95 vs P3 107.27 Rs./g; ensemble vs P3 -1.2% [-4.2, +1.6], p 0.41. P3 vs hold-last-fix, post hoc,
-7.3% [-13.2, -1.5], p 0.023. One fitted slope captures nearly all of the ensemble's edge and is
explainable in one line. The comparison favoured the ensemble (its design was chosen on the same days).

## Decision

1. **Live (headline, range, direction): P3.** `ml.nextfix.predict_p3`: forecast log return =
   `b x` the world move over the decision day, `b` least squares through the origin on pairs whose
   target fix is already known (refit each run). P(up) = Phi(forecast / residual spread of recent
   out-of-sample errors), so "up" iff the forecast is positive. Range: split-conformal on P3's own
   out-of-sample errors, scaled by recent volatility, 80% target, exactly as for the ensemble.
2. **Shadow: the ensemble.** It keeps `data/nextfix_oos.json` and its forecast is returned in
   `forecast.json` `next_fix.shadow_ensemble` (point, P(up), and its own scored record). Nothing from
   it is shown. A shadow failure never affects the live forecast.
3. **Two records.** `data/nextfix_p3_oos.json` (live) and `data/nextfix_oos.json` (shadow), one
   out-of-sample fold per day, each trained only on fixes known before it. P3's folds carry
   `retro: true` for decision days before `P3_FORWARD_FROM = 2026-10-07` (the day P3 went live; GG approved it on 2026-10-05, when the ensemble was still the live model) (a re-run on past data, not
   issued live) and `false` after. `next_fix.track_record.n_forward` counts the forward ones. Forward
   and retrospective results are never mixed in any published claim.
4. **Clock.** `ml/known_at.py` pins the USD/INR daily value at 20:00 UTC of its date (was 23:59,
   a stated convention). Measured by matching each daily value to the 1-hour bars of the same day
   over 60 weekdays: 57 matched a bar starting 00:00-09:00 UTC, 1 at 11:00, 2 at 17:00-18:00 (latest
   ends 19:00 UTC), median gap 1.8 bp; 20:00 = latest + 1 h. `USDINR_SNAPSHOT_CONSERVATIVE`
   stays for ADR 058's frozen analysis.
5. **Direction signal** keeps its gate (`decide_direction_signal` on the live model's own record,
   re-checked each run) and the promotion record (extended to P3 by this decision).
   Ensemble and P3 are scored beside each other by the weekly scorecard.

## Evidence (VERIFIED: `scripts/verify_p3_live.py`, `reports/model_audit_2026-10/p3_live_proof.json`)

- Production code path reproduces ADR 067's P3 on the same 143 days: MAE 107.27 (ADR 067: 107.27),
  direction accuracy 62.94% (62.94%).
- Leak guard replay over those 143 forecasts (147,041 inputs, repo clocks): 0 violations; the
  negative control (using the target fix) is flagged. Under the old 23:59 clock the guard flagged all
  143 (`leak_audit.json`, `legacy_conservative`).
- P3's own record: error vs hold -7.3% (CI [-12.7, -1.5], DM p 0.023); direction 62.9% vs always-up
  48.95% (p 0.019, ECE 0.054, gate ships; timing gate does not: ECE > 0.05); range coverage 80.5%
  [72.6, 86.5], mean width Rs. 361.6 on IBJA 22K.

## Consequences

- The visible forecast changes slightly (a different point and width; expected mean width within a
  few Rs. of before). The direction line and the page wording are unchanged by this ADR (D1).
- Forward n for P3 is 0 on 2026-10-07; "validated" means nothing until forward calls accrue. ADR 068's
  demotion rules (wired separately, D3) watch it.
- Rollback: set `MODEL_VERSION` back to the ensemble by making `run()` use `predict` for the live
  record (one call site) or revert this ADR's commit; the ensemble record is intact.
