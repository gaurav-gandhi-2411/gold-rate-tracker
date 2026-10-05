# Model inventory, 2026-10-05 (master `de27ee08`)

Read-only inventory (item 2a). Status codes: LIVE-shown, LIVE-computed (not shown), SHADOW (logged),
DARK (flag off), DEAD. Gate and track-record figures are copied from committed JSON on disk and were
NOT recomputed here, except rows 1 and 1b, which section "Re-verification" of
`docs/MODEL_AUDIT_2026-10.md` re-measured. check-price runs on cron `37 1-22/3 * * *` (8 a day).

| # | Model | Predicts / target | Inputs and when known | Baseline | Gate and current status | Status | Runs where, output |
|---|---|---|---|---|---|---|---|
| 1 | `ml.nextfix` ridge + 5 tanh nets, logistic P(up) (ADR 064/065) | Next IBJA PM fix (22K, Rs/g); shop price = current + slope x (pred - ref) | IBJA PM/AM at 11:30Z/06:30Z (assumed publish clocks); GC=F x USD/INR close of D and D-1; window opens 22:15Z | Hold last fix | Active when the OOS record is ready; range = split-conformal, 80%. Direction shown only if `decide_direction_signal` ships on its own record AND the promotion record exists. Now: direction gate ship, timing gate no, side "unclear" | LIVE-shown (headline, range, direction line) | check-price "Run inference" -> `forecast.json`, `nextfix_oos.json` |
| 1b | nextfix hold modes (after AM fix, after PM fix) | Next fix = latest fix | Latest fix | (is the baseline) | Window record ready; range from window's own conformal | LIVE-shown | same |
| 2 | `ml.nextfix_intraday` hourly pass-through, beta 0/.5/1 (ADR 066) | Next fix from hourly world price | 1-hour GC=F, USD/INR bars ended by t | beta 0 (hold) | ADR 066 promotion rule; shadow has 0 resolved fixes | SHADOW | check-price -> `nextfix_intraday_shadow.json`, `_backtest.json` |
| 3 | `ml.calibration` Huber IBJA -> Tanishq | Tanishq 22K from IBJA PM | IBJA PM, Tanishq pairs | n/a | `valid`, n 95, slope 1.0131; band coverage 0.707 (n 99) vs 0.80 nominal | LIVE-shown (slope used by row 1; band only on IBJA tier) | check-price -> `calibration.json` |
| 4 | IBJA-derived trend series | 22K = slope x IBJA | IBJA PM, calibration | n/a | n/a | LIVE-shown (chart) | `ibja_derived_prices.json` |
| 5 | `ml.volatility` | context ("typical move") | Tanishq returns | static PI | contiguity gates | LIVE-shown | forecast.json |
| 6 | `ml.drivers` | past-tense attribution | hourly bars closed by the fix | n/a | validity flags | LIVE-shown | forecast.json |
| 7 | `ml.chronos_forecast` (chronos-bolt-tiny) | 5-day p10/p50/p90, calibrated | IBJA history | flat-hold | dir_acc_30f 0.333 < 0.55: base-rate fallback | LIVE-computed, only `status` shown | "Run Chronos probe" every cycle |
| 8 | `ml.backtest` walk-forward Chronos vs naive | h1-5 error | IBJA, leak guard | naive | n 252, Chronos worse (MAE5d 291.9 vs 247.9) | LIVE (how-we-know copy; feeds headline interval fields) | weekly Sunday |
| 9 | `ml.drift`, `ml.metrics` | monitors | forecast vs later price | naive MAE | coverage 0.766 (n 77) | LIVE-shown | check-price |
| 10 | `ml.fusion` fallback | retail consensus | GRT, Malabar, Kalyan | n/a | tier 3 | LIVE fallback (not current tier) | inference |
| 11 | `ml.shadow_fusion` | city/national benchmark | board fetches | Tanishq | Phase D pending; Kalyan `observed_at` 1969/1970 placeholder seen | SHADOW | `shadow-fusion.yml` every 6 h |
| 12 | `ml.direction` logistic + LightGBM (ADR 019/031/040) | h1/h2 direction | feature store; leak guard | always-up | h1 0.481 vs 0.506 (p 0.67), h2 0.56 vs 0.58 (p 0.61): no ship | DARK/SHADOW | `eval-direction.yml` Monday |
| 13 | `ml.feature_store` snapshots | data for row 12 | macro per cycle | n/a | n/a | capture only | every cycle |
| 14 | h2 pre-registration shadow (ADR 038/042) | h2 direction | proxy / store | always-up | n 329, 0.526 vs 0.556, p 0.79 | SHADOW | weekly |
| 15 | `ml.weekly_range` (ADR 043) | 1d/7d range | proxy history | displayed range | ~8-10 weeks needed; n_scored 4 / 0 | SHADOW (read only under `page_v2`, OFF) | weekly |
| 16 | `ml.next_day_range` v2 (ADR 047) | next-day range | same | displayed range | retro failed width bar (+30.6%) | SHADOW | weekly |
| 17 | `ml.wait_or_buy` (ADR 049) | P(lower after N days) | IBJA, proxy | 80% coverage | flag off; 8 weeks forward needed | DARK + SHADOW | weekly |
| 18 | nowcast shadow M3 vs M0 (ADR 061/063) | same-day Tanishq | IBJA AM/PM via `ml.known_at` | M0 | window ends 2026-10-22; certified days only; n 4 | SHADOW | weekly |
| 19 | `ml.stale_day_estimate` (ADR 048) | weekend/holiday estimate | Tanishq, IBJA, fusion | S1 | forward n 0 | SHADOW, NO scheduler | manual |
| 20 | `ml.kalman_nowcast` (ADR 055/062) | nowcast when Tanishq stale | multi-source | baselines | needs n >= 30, Bonferroni | SHADOW, NO scheduler | manual |
| 21 | `ml.fhs_ranges` (ADR 056) | adaptive ranges | proxy returns | ADR 047 live | width gate | SHADOW, NO scheduler | manual |
| 22 | `ml.markup_reversion` (ADR 057) | markup reversion | Tanishq, IBJA | market | stop 2027-09-30 | SHADOW, NO scheduler | manual |
| 23 | `ml.markup` meter (F1) | markup % | prices | n/a | flag off; `markup_today.json` dated 2026-09-24 | DARK | no producer |
| 24 | `ml.event_watch` (F4, ADR 050) | event-day move size | events calendar | normal days | no type passes; flag off | DARK | manual |
| 25 | Research (`ml/range_forecast`, `ml/experiments`, `scripts/analysis_*`) | various | various | various | `analysis.yml` is dispatch-only | research | artifacts |
| 26 | Legacy (`ml/features.py`, `models/local/*`, `ml/llm_cache_helpers.py`) | n/a | n/a | n/a | nothing imports them | DEAD | n/a |

## Issues the inventory surfaced

1. **Step order:** inference and the hourly shadow run before the IBJA append in check-price, so
   forecasts lag one cycle after a new fix (proof: `forecast.json` predicted 07:13:34Z; the 2026-10-05
   AM row was fetched 07:13:46Z). Fix drafted as PR #2390.
2. **Leak guard is not used by the live path** (`ml.nextfix`, `ml.drivers`, calibration) or by
   most shadows; only backtest, the direction harness and the nowcast shadow use it. The replay in
   `scripts/audit_nextfix_leak.py` now covers nextfix.
3. **Four forward shadows have no scheduler** (rows 19-22): their forward windows cannot accrue.
4. **Stale public JSON** behind OFF flags: `markup_today.json`, `event_watch_today.json`,
   `stale_day_shadow.json` (all dated 2026-09-24).
5. **Displayed IBJA-calibrated band under-covers** (0.707, Wilson [0.611, 0.788]) vs 0.80.
6. **Labelling mismatches:** `coverage_metrics.json` `band_source` still says naive flat-hold; the
   drift record pairs nextfix residuals with the 5-day naive baseline (247.87). UNVERIFIED whether
   either changes a user-visible number.
7. **Direction gate compares only with always-up** (base rate 0.49 in this sample); a rule-based
   baseline (ADR 067) is the meaningful one.
8. Chronos probe (8 runs a day) and its weights cache feed only a status string (cost measured in
   `docs/CLEANUP_MAP_2026-10.md`, PR #2389: about 9 s per run, 2.4% of the job).
9. `ci.yml` has never run (triggers on `main`, default branch is `master`); it is the only runner of
   `scripts/check_manifest_provenance.py` (from the cleanup map, PR #2389).
