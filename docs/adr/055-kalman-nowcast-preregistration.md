# ADR 055 — Pre-registration: a state-space (Kalman) nowcast of today's retail 22K price

**Status:** Proposed 2026-09-25. Pre-registration. This text and the code it names
(`ml/kalman_nowcast.py`, `scripts/analysis_kalman_nowcast.py`, `scripts/run_kalman_shadow.py`,
`tests/test_kalman_nowcast.py`) are frozen by the commit that adds this file, **before the model
has produced a single nowcast on real data**. Results are appended below a `## Results` heading
after the run. The pre-registration hash is the sha256 of this file's text above that heading,
which is the whole file at freeze time. Research and forward shadow only. Nothing a user sees
changes; promotion is GG's call in a separate PR.

**Builds on:** the #2015 scorecard (`scripts/analysis_scorecard.py`, `reports/model_scorecard.json`),
whose target, day sets and baselines are reused exactly; ADR 048 (stale-IBJA days); F1 (#2022,
store markups); G4 (#2004, `data/duty_cbic.json` and the derived premium).

## Context

The site's nowcast estimates today's Tanishq 22K price from the latest IBJA PM rate. The #2015
scorecard found (n 90, 2026-06-12 to 2026-09-24):

- nowcast MAE ₹61.2/g; it does **not** beat IBJA × a fixed markup (₹62.8, p 0.32);
- weekdays ₹45.5, weekends ₹93.9; on weekends yesterday's Tanishq price wins (₹46.0);
- fusion (shadow, n 56) ₹45.6;
- the 80% accuracy band covers 72.2% overall, 79.0% on weekdays and 57.1% on weekends.

**These baseline figures were seen before this registration.** The Kalman model's own outcomes
have never been computed. Before the freeze, the only runs on real data were
`scripts/analysis_kalman_nowcast.py --shape-only`, which prints counts (readings per source, day
sets per day type) and no error, coverage or likelihood. The unit tests run on synthetic data only.

The nowcast ignores most of what is known: yesterday's Tanishq reading, the IBJA morning rate,
other retailers' boards and the global gold price. It also has no principled uncertainty on
weekends and holidays, when IBJA does not publish. A local-level state-space model treats the true
retail price as a hidden state, each source as a noisy and sometimes missing view of it, and lets
the state variance grow when nothing is observed. The predictive variance then gives the band
directly.

## Decision — the model (frozen in `ml/kalman_nowcast.py`)

**Time step.** One step per UTC calendar day, from the first Tanishq reading (2026-04-14) onward.

**State.** `x_t = [s_t, m_ibja, m_grt, m_malabar, m_comex]`.
- `s_t` is the log of the true retail 22K price. It is anchored to Tanishq, so Tanishq has no
  markup state and its readings observe `s_t` directly.
- Each `m_g` is a source group's log markup relative to Tanishq.

**Transition.** A random walk: `x_t = x_{t-1} + w_t` with
`Var(w_t) = diag(q_level(t), q_markup, q_markup, q_markup, q_markup)`.
- `q_level(t)` is `q_level_nontrading` on Saturdays and Sundays (UTC) and `q_level_trading`
  otherwise. Reason: the markets that move the price are shut at weekends. This split is fixed
  here, not chosen from data.
- The markups are slow random walks with one shared `q_markup`.

**Observations.** `log z = s_t + m_g(source) + e`, with `e ~ N(0, r_source + q_age × age_days)`.

| source | series | known on | age | markup group |
|---|---|---|---|---|
| Tanishq (anchor) | `data/prices.json`, last reading per UTC date | its own date | 0 | none |
| IBJA AM | `ibja_rates.parquet` `am_916`/10 (₹/g), value date | value date (~06:30 UTC) | 0 | ibja |
| IBJA PM | `pm_916`/10, value date | value date (~11:30 UTC) | 0 | ibja (shared with AM) |
| GRT, Malabar | `fusion_snapshots.parquet` national rows, last capture per `as_of_date` (UTC capture date) | capture date | capture date − `observed_at` date | own |
| COMEX × USD/INR | Yahoo GC=F and INR=X daily closes, converted to landed 22K ₹/g (below) | close date + 1 | 1 | comex |

Assimilation rules:
- Each distinct reading is assimilated once, on the day it is first known.
- An IBJA value identical to the previous published row's same field is a stale re-publication
  and is skipped.
- A retailer row whose `observed_at` date equals the one already used for that source is skipped.
- A GC=F close identical to the previous close is dropped.

**COMEX conversion.** Reused from `scripts/analysis_derived_premium.py` and `ml/inr_proxy.py`:
`GC=F close / 31.1034768 × INR=X close × (1 + duty in force on the assimilation day) × 22/24`.
- The duty comes from `data/duty_cbic.json` via `analysis_derived_premium.duty_rate_series`.
- The loader is the same (`ml.macro._download_with_retry`).
- The COMEX markup state absorbs the premium, the tariff-value gap and futures contango.

**Kalyan is excluded:**
- its snapshots are city-level only;
- they carry corrupt timestamps since 2026-09-08 (#2022);
- it is not in `ml.fusion.DEFAULT_WEIGHTS`.

**Order within a day.**
1. A day's non-Tanishq readings are assimilated.
2. The posterior of `s_t` is recorded. **That is the nowcast of day t.**
3. Only then is day t's own Tanishq reading assimilated, for later days.

So a nowcast never sees its own target.

**Information set.** Same as the scorecard's baselines. IBJA × fixed markup uses the same-date IBJA
PM, and fusion uses the last capture of the date, whatever the target reading's timestamp. On
36/148 dates the last Tanishq reading is stamped before 11:30 UTC; most of these are backfilled
rows with a synthetic 06:30Z stamp. The Kalman model gets exactly that information and never the
target day's Tanishq.

**Nowcast and band.**
- Point: `exp(E[s_d])`, the median in ₹/g.
- 80% band: `exp(E[s_d] ± 1.2816 × sqrt(Var[s_d] + r_tanishq))`.

**Parameters (10).** `q_level_trading`, `q_level_nontrading`, `q_markup`, `q_age`, `r_tanishq`,
`r_ibja_am`, `r_ibja_pm`, `r_grt`, `r_malabar`, `r_comex`.

**Estimation.** Maximum likelihood by prediction-error decomposition:
- L-BFGS-B on the log-variances, bounded to [1e-11, 1e-1], at most 200 iterations; deterministic.
- Likelihood terms from the first 10 days (diffuse start) are not scored.
- Initial state: the first Tanishq log price for the level, 0 for every markup, variance 1e-2 on
  every state.
- Starting point: the method-of-moments values in `default_start_params`, then warm-started from
  the previous block's estimate.

**Deviations from the brief, fixed here:**
- One shared `q_age` rather than one per source. Staleness only varies for Malabar (0–2 days), so
  per-source slopes would not be identified.
- IBJA AM and PM share one markup state (same product, same fix desk).
- The markups are random walks rather than mean-reverting. That is one parameter instead of two
  per source, and the random walk is the conservative choice for level shifts such as a duty change.

## Decision — evaluation protocol (frozen in `scripts/analysis_kalman_nowcast.py`)

**Walk-forward.**
- Scored days are split into blocks of 7 calendar days, starting at the first scored day.
- For each block starting on day b, the parameters are fitted by MLE on days strictly before b.
- The filter then runs from the start with those parameters, and each scored day in the block gets
  its pre-anchor nowcast.
- The first block has 59 days of training history.

No parameter, bound, prior, burn-in, block length or model structure is changed after the freeze.
If the run crashes, only the crash is fixed, and the fix is disclosed in Results.

**Target.** The scorecard's truth: the last Tanishq 22K reading of the UTC date
(`scripts/analysis_nowcast.load_truth`).

**Day sets (the scorecard's, reproduced in `--shape-only` before the freeze):**
- **Set A**, the production-M0 nowcast days: n 90, 2026-06-12 to 2026-09-24. Of these, 62 are IBJA
  days, 26 are weekends and 2 are weekdays without an IBJA fix.
- **Set B**, the fusion days: n 56, 2026-07-19 to 2026-09-24. Of these, 40 are IBJA days, 15 are
  weekends and 1 is a weekday without an IBJA fix.

"Weekday" means Monday to Friday (it includes the weekdays without an IBJA fix). "Weekend" means
Saturday or Sunday.

**Baselines (the scorecard's constructions, verbatim):**
- **IBJA × fixed markup:** the IBJA PM as-of the date × the median Tanishq/IBJA-PM ratio over the
  first 30 same-day pairs (1.013868). It is scored only after those pairs. Set A.
- **Yesterday's Tanishq (carry-forward):** the previous available date's last reading.
- **Fusion:** `DEFAULT_WEIGHTS`-weighted mean of the national snapshots of the date. Set B.

**Primary family (one-sided paired Diebold–Mariano on absolute error, H1: Kalman lower).** HAC with
lag 1 (`ml.direction.evaluate_reframed.diebold_mariano_test`, horizon 2, as the scorecard). Effective
n is reported for each test.

| id | comparison | day set |
|---|---|---|
| T1 | Kalman vs IBJA × fixed markup, all days | A |
| T2 | Kalman vs IBJA × fixed markup, weekdays | A |
| T3 | Kalman vs IBJA × fixed markup, weekends | A |
| T4 | Kalman vs fusion, weekdays | B |
| T5 | Kalman vs fusion, weekends | B |
| T6 | Kalman vs yesterday's Tanishq, weekends | A |

- Multiplicity: Bonferroni at α = 0.05/6 = 0.00833 and Benjamini–Hochberg at q = 0.05, both over
  T1–T6 (`ml.direction.stats_corrections`).
- MAE is reported with a Newey–West (lag 1) 95% CI for the model and every baseline, per stratum.

**Band coverage (the filter's 80% interval, set A).**
- **C1** weekdays and **C2** weekends: coverage, Wilson 95% CI (`ml.calibration_adaptive._wilson_ci`),
  exact two-sided binomial p against 0.80, and mean and median width in ₹/g.
- These are calibration checks, not superiority tests, so they sit outside the T-family.
  Multiplicity-widening their CIs would make "contains 80%" easier to pass.

**Success gate. PASS only if all three hold:**
1. T1 is significant under Bonferroni (one-sided p ≤ 0.00833).
2. The C1 weekday Wilson 95% CI contains 0.80.
3. The C2 weekend Wilson 95% CI contains 0.80.

**Negative.** Anything else is a negative result and is reported plainly as one. Some cases still
count as negative:
- the Kalman model beats a baseline in some strata only;
- it wins under BH but not Bonferroni;
- its band has the right coverage but T1 fails.

T2–T6 and the BH verdicts are reported. They inform the promotion discussion but do not change
the gate.

**Secondary (descriptive only, no gate):**
- Kalman vs the production nowcast M0 on set A;
- the whole table re-run with DM lag 5;
- band coverage on set B;
- the fitted parameters per block.

**Power (INFERRED, not measured).** With 26 weekend days in set A and 15 in set B, the weekend
tests have little power. A weekend improvement of ₹20–30/g could fail Bonferroni on noise alone.
An inconclusive weekend test is inconclusive, not evidence of no effect. The Wilson CI half-width
at n 26 and p 0.8 is about ±0.15, so C2 can only detect gross miscalibration.

## Forward shadow

`scripts/run_kalman_shadow.py` appends one entry per run to `reports/kalman_nowcast/shadow.json`.
- Each run fits on all days before the target UTC date and filters through everything known on it
  except its own Tanishq.
- The script is not wired into any workflow; the orchestrator decides that.
- **Forward scoring:** the last entry per `target_date`, against that date's last Tanishq reading,
  with the same family and gate.
- The forward read comes at n ≥ 60 scored days with at least 20 weekend days, about
  December 2026 at a 3-hourly cadence.
- The retrospective above is a real walk-forward backtest, but the baseline numbers were known
  beforehand. The forward shadow is the blind test.

## Consequences

- If the gate passes, a promotion PR can propose the Kalman nowcast and band for GG, subject to
  the forward shadow agreeing.
- If it fails, the model stays in shadow or is retired. The record states which part failed.
- Data caveats:
  - GRT and Malabar exist only from 2026-07-19, so on set A's earlier days the filter has only
    Tanishq, IBJA and COMEX;
  - Yahoo and FBIL terms restrict redistribution, so the outputs carry derived numbers only.

## Alternatives considered

- **Per-source staleness slopes, mean-reverting markups, t-distributed errors:** more parameters
  than about 150 days can identify. Deferred.
- **A regression of Tanishq on all sources:** it cannot absorb missing sources or produce a
  variance that grows over gaps without ad-hoc rules. The state-space form does both by construction.

## Results

Everything above this heading is the pre-registration. It is frozen at commit `70d5474c`
(pushed before the run) with sha256
`5358ba5849d9d44f00ebcb176495c865a6a066042c690469441f2c475b35d12e`. The run recomputes that hash
(`prereg_sha256_of_adr_above_results` in the report) and records `code_commit` `70d5474c`.

**Run.** `scripts/analysis_kalman_nowcast.py`, run locally on 2026-09-24 UTC in 56 s; output
`reports/kalman_nowcast/results.json`.
- 15 walk-forward refits. All but one report convergence; the 2026-08-28 block stopped with
  `converged: False` after 11 L-BFGS-B iterations and its estimate was used as-is, per the protocol.
- No change to the model or the scoring after the freeze, and no crash fix. One post-run change:
  `prereg_sha256()` hashed one blank line too many once this section was appended. It now drops
  that separator, and it reproduces the frozen hash on this file. The hash in `results.json` was
  computed before the append and is correct.
- Data: 2026-04-14 to 2026-09-24. Readings used: Tanishq 148, IBJA AM 103, IBJA PM 103,
  COMEX 114, GRT 68, Malabar 56.

**Verdict: NEGATIVE (the gate fails on C2).** The point nowcast beats every baseline in every
pre-registered test, but the weekend 80% band over-covers: its Wilson CI excludes 80% from above.

### Primary family

One-sided HAC DM, lag 1; m = 6; Bonferroni threshold 0.00833.

| id | comparison | n | eff. n | Kalman MAE ₹/g [95% HAC] | baseline MAE ₹/g | p (one-sided) | Bonferroni | BH |
|---|---|---|---|---|---|---|---|---|
| T1 | vs IBJA × fixed markup, all | 90 | 68.8 | 30.3 [20.2, 40.3] | 62.8 [48.2, 77.3] | 1.5e-6 | yes | yes |
| T2 | vs IBJA × fixed markup, weekdays | 64 | 51.6 | 35.9 [22.9, 49.0] | 50.6 [39.9, 61.3] | 0.0016 | yes | yes |
| T3 | vs IBJA × fixed markup, weekends | 26 | 17.0 | 16.3 [6.7, 25.9] | 92.7 [55.0, 130.4] | 1.5e-5 | yes | yes |
| T4 | vs fusion, weekdays | 41 | 37.6 | 19.6 [7.8, 31.4] | 41.1 [30.4, 51.8] | 9.5e-9 | yes | yes |
| T5 | vs fusion, weekends | 15 | 10.0 | 5.3 [3.7, 6.9] | 59.7 [46.3, 73.0] | < 1e-15 | yes | yes |
| T6 | vs yesterday's Tanishq, weekends | 26 | 35.2 | 16.3 [6.7, 25.9] | 46.0 [28.8, 63.2] | 0.0025 | yes | yes |

T5's p is a normal approximation with an effective n of 10 and 15 days. Read it as "very small",
not as a precise value.

### Band coverage (80% nominal, set A)

| stratum | n | covered | coverage [Wilson 95%] | exact p vs 0.80 | mean width ₹/g |
|---|---|---|---|---|---|
| C1 weekdays | 64 | 53 | 82.8% [71.8, 90.1] | 0.64 | 161.7 |
| C2 weekends | 26 | 25 | 96.2% [81.1, 99.3] | 0.046 | 178.0 |
| all | 90 | 78 | 86.7% [78.1, 92.2] | 0.15 | 166.4 |

**Gate:**
- T1 passes Bonferroni: **yes**.
- The C1 CI contains 80%: **yes**.
- The C2 CI contains 80%: **no**, it over-covers.
- **FAIL.**

The weekend band is about ₹178 wide around a nowcast whose weekend MAE is ₹16. The Gaussian band
carries Tanishq's fitted reading noise (`r_tanishq`, sd about 0.43%) on every day, and on weekends
that noise dominates. The failure is a band that is too wide, not one that is too narrow.

### Secondary (descriptive, no gate)

- **Against the production nowcast M0 (set A):**

  | stratum | Kalman ₹/g | M0 ₹/g | p |
  |---|---|---|---|
  | all | 30.3 | 61.2 | < 1e-4 |
  | weekdays | 35.9 | 47.9 | 0.029 |
  | weekends | 16.3 | 93.9 | < 1e-4 |

- **DM lag 5:** T1–T6 p = 0.0000, 0.011, 0.0001, 0.0000, 0.0000 and 0.025. Only the weekday and
  weekend-carry-forward cells weaken.
- **Band on set B:** 92.9% [83.0, 97.2] overall; weekdays 90.2% [77.5, 96.1]; weekends 15/15
  [79.6, 100]. It is wide on set B too.
- **Fitted parameters (last block):**
  - `r_grt`, `r_malabar`, `q_markup` and `q_age` sit at the lower bound (1e-11): GRT and Malabar
    boards track Tanishq with a near-constant log markup. Same-day correlation of daily log changes,
    GRT vs Tanishq: 0.92, sd of the log ratio 0.26%.
  - `r_comex` has sd about 1.25%, so COMEX carries little weight.
  - `q_level_trading` has sd about 1.18%/day and `q_level_nontrading` sd about 0.61%/day.

### Post-hoc exploratory timing check (NOT pre-registered)

`scripts/analysis_kalman_nowcast_strict_timing.py` writes
`reports/kalman_nowcast/exploratory_strict_retail_timing.json`.

**Why it was run.** The pre-registered information set gives the filter, like the fusion baseline,
the last retailer capture of the UTC date. On 47/112 set-B day/source pairs that capture is stamped
after the target Tanishq reading. A pre-target capture exists on 106/112.

**What changed.** The walk-forward was re-run with captures restricted to before the target
reading's timestamp; nothing else changed.

**Result:**
- T1 still holds: 32.3 vs 62.8, p 4.8e-5.
- T3, T5 and T6 still pass Bonferroni: weekend MAE 15.8; set-B weekend 4.3 vs fusion 59.7.
- **T2 (p 0.036) and T4 (p 0.017) no longer pass Bonferroni:**
  - weekday MAE 38.9 vs fixed markup 50.6;
  - set-B weekday MAE 24.3 vs fusion 41.1.
- Coverage is unchanged in kind: weekdays 84.4% [73.6, 91.3], weekends 96.2% [81.1, 99.3].

**Reading.** Part of the weekday edge in the primary run comes from end-of-day retailer captures.
The weekend and overall edges do not depend on them. This does not change the pre-registered
verdict. It does mean the forward shadow, which only sees what exists at run time, is the test
that matters for weekdays.

### What this means (INFERRED, for GG)

- As a point estimate, the Kalman nowcast roughly halves the live nowcast's error (₹30 vs ₹61).
- On weekends it beats yesterday's Tanishq price, the baseline that beat the live model there.
- It fails its own pre-registered band criterion by being too conservative on weekends.
- **No retuning here.** The band fix (e.g. a separate weekend reading-noise term, or conformal
  scaling of the filter's sd on training folds) would need a new pre-registration.
- The forward shadow (`scripts/run_kalman_shadow.py`, not yet wired) is the blind test for both.
