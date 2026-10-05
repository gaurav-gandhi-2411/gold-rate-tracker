# Model audit, 2026-10-05: the promoted next-fix model (ADR 064/065)

Branch `feat/model-audit-2026-10`. Every number is VERIFIED (run in this session on the scripts
named) unless marked INFERRED. Artifacts are in `reports/model_audit_2026-10/`. Inventory of all
models: `docs/MODEL_INVENTORY_2026-10.md`.

## Verdict (item 2b)

**The model's edge over holding the last fix is real in the sample and survives every check I could
run, but (a) its forward record is empty, (b) its leak status depends on an unpinned USD/INR clock,
and (c) a one-parameter regression gets almost all of it.** Specifically:

| Check | Result | Artifact |
|---|---|---|
| Leak guard replay, 143 folds, 147,041 inputs | Repo's conservative USD/INR clock (bar known 23:59 UTC) flags **143 of 143** folds: the forecast is made at 22:15 UTC, 104 min earlier. With the clock pinned from hourly bars (below), **0 violations**. Every training label and the history behind the 20-day basis pass strictly. Negative control (using the target fix) is flagged. | `leak_audit.json`, `scripts/audit_nextfix_leak.py` |
| USD/INR clock, measured | In 60 weekdays of hourly bars (2026-07-14..10-05), the INR=X daily value matched an early-day bar (57 of 60 days at bar-start hours 0-9 UTC, 1 at 11, 2 at 17-18 (even those end by 19:00 UTC, before the 22:15 decision); median gap 1.8 bp), not the day's end; so the same-day USD/INR is known well before 22:15 UTC. This resolves ADR 058's open question for this use (60 days only; INFERRED to hold earlier in the history). | `inrx_daily_clock.json`, `scripts/audit_inrx_daily_clock.py` |
| Strictly-safe variant (USD/INR from D-1) | MAE -7.0% vs hold, CI [-12.5, -1.0], DM p 0.026; direction 61.5% (vs 64.3%); range coverage 78.9%; direction gate still ships. So the edge does not rest on the same-day INR value; direction accuracy loses 2.8 points (within noise, SE about 4). | `reproduction.json` |
| Reproduction from scratch (fresh macro cache, empty record) | MAE 105.6 vs committed 106.0; -8.8% vs -8.5%; direction 64.3% = 64.3%; coverage 81.3% vs 80.5%; ECE 0.053 vs 0.060. Max |difference| in forecast return 0.0048; 6 of 143 direction calls flip. **Not bit-identical**: cause is INFERRED to be input revisions in the yfinance cache / seed rescale between when each fold was frozen and now (the committed folds were written once, on the day). Not a leak: differences are small and in both directions. | `reproduction.json` |
| Independent ridge | Closed-form ridge written from the formula matches sklearn RidgeCV to 7e-18 on all 143 folds. | `reproduction.json` |
| Published headline (ADR 064/065, `forecast.json`) | n 143: MAE 106.0 vs 115.8 (-8.5%, bootstrap CI [-14.1, -2.5], DM p 0.009); direction 64.3% [57.3, 71.3] vs always-up 48.95%; range coverage 80.5% [72.6, 86.5], n 123. | `forecast.json`, `reproduction.json` |
| **Forward score** (forecasts issued live since promotion) | **n = 0 resolved.** IBJA published nothing on 2026-10-02 (holiday) or the weekend, so the one live model-window forecast (d0 2026-10-01, p_up 0.4948 "unclear", predicted fix 13,571.77) resolves with the 2026-10-05 PM fix (about 11:30 UTC). Scored by the scorecard once resolved. | `data/forecast.json` history |

**Recommendation:** keep the model live (nothing here shows harm), but do not describe it as
validated: it is a 143-day backtest with an empty forward record. Pin the USD/INR clock in
`ml/known_at.py` from the measurement above (or run the strict D-1 variant) so the repo's own guard
passes on this model.

## Item 2c: the right baseline (ADR 067, pre-registered, frozen at `bcaa0bac`)

| Rule | MAE (ensemble 105.95) | Ensemble vs rule, 95% CI | DM p (Bonferroni) |
|---|---|---|---|
| P1 last fix x world move over D | 149.81 | -29.3% [-38.7, -18.6] | 1.5e-5 (4.6e-5) |
| P2 world value x usual basis | 158.33 | -33.1% [-49.1, -8.5] | 0.033 (0.10) |
| P3 one fitted slope on the move (b about 0.19) | 107.27 | **-1.2% [-4.2, +1.6]** | 0.41 (1.0) |

Hold-last-fix: 115.77. The literal rule GG described (P1) is **worse than holding the fix**,
because the midday fix already contains most of the day's world move; the usable signal is the
**remaining** part, about 19% of the move (P3). EXPLORATORY, post hoc: P3 beats hold by -7.3% (CI
[-13.2, -1.5], p 0.023); P3 direction 62.9%. The ensemble is not shown to beat P3 (1.2%, interval
spans zero; sample biased in the ensemble's favour). The registered decision rule mechanically
outputs "P2" because of low power; reported as is and explained in ADR 067. **Decision for GG:** keep
the ensemble or ship P3 (`pm0 x exp(b x world move)`, one slope refit daily) as the simpler,
explainable alternative; the evidence does not separate them.

## Item 2e: where it fails (descriptive; `error_analysis.json`)

Realised return divided by forecast return (calibration slope) is **1.64**: the model's moves are
too timid, most in calm periods (2.5) and on the largest 10% of moves (3.4). By stratum (n,
MAE model vs hold, change):

| Stratum | n | Model vs hold |
|---|---|---|
| Consecutive IBJA days | 114 | -5.5% [-11.7, +0.8] |
| Gap over 1 day (weekend/holiday) | 29 | -19.1% [-32.0, -4.4] |
| Decided on Monday | 29 | **+6.7% [-4.3, +17.3]**; slope 0.54 (over-reacts): the only stratum worse than hold |
| Decided on Friday | 27 | -20.1% [-32.7, -6.2]; direction 74.1% |
| Decided on Wednesday | 29 | direction **44.8%** (13 of 29; not significant, 5 weekday strata tested) |
| Calm / middle / volatile (terciles of recorded vol) | 48 / 47 / 48 | -10.7% / -8.6% / -6.8%; direction 68.8% / 53.2% / 70.8% |
| Event between D and next fix (FOMC, CPI, jobs, budget) | 19 | -10.7% [-22.7, +5.0] |
| Largest 10% moves (conditions on outcome) | 15 | -12.1%; MAE 303 vs 345 |

Reading: the model helps most when the world moved over a gap it can see (Friday decisions); its gains
are the weakest in the middle-volatility tercile and on ordinary consecutive days (the interval
reaches +0.8%). It under-reacts rather than over-reacts, which is what a ridge/weight-decayed net
does on 143 points. Not tested: whether re-scaling the forecast by about 1.6 helps (it would be a
new model change requiring its own pre-registration). The other two windows hold the fix: mean
error 29.8 Rs/g after the AM fix (n 264) and 91.7 after the PM fix (n 210).

## Item 2d: automatic demotion

Built, not wired: ADR 068, `ml/demotion.py`, `scripts/simulate_demotion.py`. Proposed settings
wrongly demote a still-good model 4.3% of the time over 120 days and catch a model that lost its
edge 80% of the time (median day 53). The direction rule at floor 0.50 is nearly inert (20%); floors
0.55-0.60 are the real detectors. Thresholds are GG's (list in the ADR).

## Item 2h: dates

- **2026-10-16, ADR 066 hourly-shadow check:** the shadow already scores every run
  (`data/nextfix_intraday_shadow.json`, `_backtest.json`); the promotion rule is in ADR 066 (14 days,
  at least 8 resolved base fixes per window, backtest CI entirely below 0 with DM p < 0.05, shadow
  agrees, range refit keeps 80%). Open caveat found here: the shadow runs **before** the IBJA append
  in check-price (PR #2390 reorders it), so its entries lag a cycle; the check must read resolved
  fixes by publish time, not by run order. Not yet run: the date has not arrived.
- **2026-10-22, morning-rate decision:** ADR 063's M3 (AM + PM) vs M0 (PM only) nowcast of **today's
  Tanishq price** is a different target from ADR 065's AM-fix hold for the **next IBJA fix**. ADR 065
  does not cover it; the 10-22 decision stands as written (certified same-day days only). 4 same-day
  days logged so far (INFERRED from the inventory: M0 39.4 vs M3 36.0, p 0.42, n 4).

## Defects found on the way

1. check-price runs inference **before** the IBJA append: forecasts lag one cycle after a fix.
   Proof: `forecast.json` predicted_at 07:13:34Z (still `after_us_close`, base 2026-10-01) vs the
   2026-10-05 AM row fetched 07:13:46Z. Draft PR #2390.
2. The repo's own leak guard is not used by `ml.nextfix`; this audit's replay is the first run.
3. `ci.yml` never runs (triggers on `main`; default is `master`). See the cleanup map (PR #2389).
