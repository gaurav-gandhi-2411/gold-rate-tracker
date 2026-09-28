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

## Addendum: independent leak audit (2026-09-25)

**Verdict in one sentence:** with every input restricted to what was known strictly before the
target reading, the Kalman nowcast still beats IBJA × fixed markup overall and on weekends (not on
weekdays under Bonferroni), but it loses to the strongest carry-forward baseline, Tanishq's own
last reading before the target, on every stratum. So its apparent weekend edge is not an edge
over the best simple rule.

This addendum is an independent audit appended after the Results. The pre-registration text above
`## Results` is not edited, and the frozen hash still reproduces. The re-score **corrects the
measurement** (it applies availability-time filtering). It is not a new hypothesis. It is
retrospective and exploratory relative to the frozen ADR, and it does not change the
pre-registered verdict (NEGATIVE).

Code: `scripts/audit_kalman_leak.py`, with tests in `tests/test_kalman_leak_audit.py`. Output:
- `reports/kalman_leak_audit/inputs_audit.json`: every input for every scored day, with its
  timestamps and its delta to the target;
- `reports/kalman_leak_audit/leak_summary.json`;
- `reports/kalman_leak_audit/rescore.json`.

The reports carry `code_commit` `ae70692d` (two runs, byte-identical apart from the stamp). The registered pipeline re-run inside the audit harness
reproduces the published figures exactly (T1 30.3 vs 62.8, p 1.5e-6), so the comparison below is
like for like. VERIFIED means the audit ran it.

### What is being estimated

- **Target.** For UTC date d, the target T_d is the last Tanishq 22K reading in `data/prices.json`
  whose timestamp falls on d (`analysis_nowcast.load_truth`).
- **Timestamp format.** Every timestamp is ISO UTC ending in `Z`.
- **Why "last" is well defined.** The file is chronological, so the last row in file order is the
  row with the latest timestamp. The audit asserts this.

**When each input counts as known.** An input may be used for T_d only if known_at < T_d. The
comparison is strict: an input known at exactly T_d is excluded.

| source | known at | status |
|---|---|---|
| Tanishq | the reading's own timestamp | from the data |
| IBJA AM | value date 06:30 UTC (12:00 IST) | assumption: the data has value dates only |
| IBJA PM | value date 11:30 UTC (17:00 IST) | assumption: the data has value dates only |
| GRT, Malabar | `capture_utc` | from the data |
| COMEX × USD/INR close dated c | c+1 00:00 UTC at the latest (settlement is 13:30 ET, and the INR=X daily bar closes by about 23:00 UTC) | assumption |

`fetched_at` for IBJA is reported only as a description. Many rows were backfilled long after
publication, so it is not a true availability time.

### Leaks found (VERIFIED; scored days are the union of set A and set B)

| channel | checked | known at/after target | weekday / weekend | magnitude |
|---|---|---|---|---|
| Same-day Tanishq target as an input | every scored day: +5% shift of every Tanishq reading from day d on, filter re-run | **0**. The shift moves the day-d nowcast by exactly 0 (max abs Δlog = 0.0, registered and strict runs) | — | none |
| Earlier same-day Tanishq captures | code read | never an input: `load_truth` keeps only the last reading per date | — | none |
| GRT capture (last capture of the date) | 56 same-day pairs | **24** | 19 / 5 | median 403 min after the target, max 981 min |
| Malabar capture | 48 same-day pairs | **20** | 18 / 2 | median 321 min after, max 981 min |
| IBJA PM before 17:00 IST | 62 | **6** | 6 / 0 | median 231 min after, max 417 min |
| IBJA AM before 12:00 IST | 62 | **1** | 1 / 0 | 117 min after |
| COMEX/USD-INR close | 65 | 0 | — | none |
| Filter state from earlier days | all | 0 inputs known after the target | — | none |
| Noise-parameter MLE window | all | 0 blocks include the target day, 0 training inputs known after the target | — | but see (a) |
| Carry-forward / ffill | code read | none in `build_observations`. The INR=X ffill is backward-looking only | — | none |
| Weekly re-estimation / day-set selection | code read | the sets are selected on input existence, not target values. 3 set-B days have no pre-target retailer capture | — | none on values |
| Baselines | — | IBJA × fixed markup used PM before publication on 6 set-A days. Fusion used post-target captures on 15 set-B days | — | both corrected in the re-score |

These GRT/Malabar counts differ from the author's 47/112 because they count only the pairs the
model actually assimilated. The model skips a Malabar row whose `observed_at` date is unchanged.

**(a) The IBJA timing also affects training.** 36 of 148 dates have their last Tanishq reading
before 11:30 UTC, and 25 of those are the April–May history backfill with a synthetic 06:30Z stamp.
The registered filter assimilates the same-day IBJA PM before those targets, which shapes the
fitted noise parameters. The strict-IBJA correction therefore moves the nowcast on all 90 set-A
days: mean abs change ₹10.9/g, max ₹90.

Corrected nowcast, all leaks removed:
- it changes on all 90 set-A days;
- mean abs change is ₹19.4/g, and the largest single change is ₹282/g;
- correcting the retailer timing alone changes 55 days, mean ₹19.8/g.

**Same-run sensitivity.** Retailer captures within 15 min before the target come from the target's
own scrape run. Excluding them switches 10 day-source pairs to an earlier capture, and all 10 carry
an identical value, so the results do not change.

### Weekends

Same-day inputs on the 26 set-A weekend days:

| source | days with a reading |
|---|---|
| GRT | 14 |
| Malabar | 9 |
| COMEX (Friday's close, assimilated on Saturday) | 12 |
| IBJA PM (1 deferred row) | 1 |

8 of the 26 days have no same-day input. On those days the nowcast is the propagated state, which is
effectively yesterday's Tanishq price.

How often the target moved:
- the target equals yesterday's last reading on **13/26** days;
- it equals the last reading before the target on **25/26** days;
- 24/26 weekend days have an earlier Tanishq reading on the same day.

Tanishq's weekend board changes once, early, and then holds, so the last reading before the target
is nearly always the answer. The Kalman model does not use earlier same-day Tanishq readings. Its
weekend skill against yesterday's price comes from same-day GRT/Malabar captures, which move with
Tanishq's once-a-day change.

Weekend MAE after the strict correction (set A):

| predictor | MAE ₹/g |
|---|---|
| Kalman | 22.9 |
| yesterday's Tanishq | 46.0 |
| last Tanishq reading before the target | **2.7** |
| IBJA × fixed markup | 92.7 |

### Strict re-score (all leaks removed; same day sets, metrics and tests)

One-sided HAC DM on absolute error; Bonferroni and BH over T1–T6 (m 6, threshold 0.00833); CIs are
Newey–West lag 1. Baselines are strict too: the IBJA PM must be published before the target, and
fusion uses the last pre-target capture per source. E1–E5 are exploratory, with their own family
(m 5). They compare against the last Tanishq reading strictly before the target. Every cell below
has Kalman MAE higher than that carry-forward, so E1–E5 are not significant under Bonferroni or BH.

| id | stratum (set) | n | eff. n (lag 1) | Kalman MAE ₹/g [95%] | baseline MAE ₹/g [95%] | p lag 1 | p lag 5 | Bonferroni (lag 1 / lag 5) | BH (lag 1 / lag 5) |
|---|---|---|---|---|---|---|---|---|---|
| T1 | all (A) vs IBJA × markup | 90 | 67.0 | 35.1 [23.6, 46.6] | 62.4 [48.4, 76.5] | 1.3e-4 | 3.6e-4 | yes / yes | yes / yes |
| T2 | weekday (A) vs IBJA × markup | 64 | 52.9 | 40.0 [25.8, 54.2] | 50.1 [39.7, 60.5] | 0.041 | 0.067 | no / no | yes / no |
| T3 | weekend (A) vs IBJA × markup | 26 | 16.4 | 22.9 [7.8, 38.1] | 92.7 [55.0, 130.4] | 1.4e-4 | 1.2e-3 | yes / yes | yes / yes |
| T4 | weekday (B) vs fusion | 41 | 49.2 | 24.4 [10.0, 38.8] | 49.6 [34.8, 64.3] | 5.7e-7 | 1.5e-9 | yes / yes | yes / yes |
| T5 | weekend (B) vs fusion | 15 | 9.5 | 5.0 [1.1, 8.9] | 59.5 [46.2, 72.9] | 4.0e-13 | 1.6e-11 | yes / yes | yes / yes |
| T6 | weekend (A) vs yesterday's Tanishq | 26 | 28.2 | 22.9 [7.8, 38.1] | 46.0 [28.8, 63.2] | 0.039 | 0.11 | no / no | yes / no |
| E1 | all (A) vs last reading before target | 90 | 69.6 | 35.1 | 13.9 [1.9, 26.0] | 0.9995 | 0.993 | no | no |
| E2 | weekday (A) vs last reading before target | 64 | 53.1 | 40.0 | 18.5 [1.9, 35.1] | 0.996 | 0.974 | no | no |
| E3 | weekend (A) vs last reading before target | 26 | 19.6 | 22.9 | 2.7 [−2.4, 7.8] | 0.996 | 0.979 | no | no |
| E4 | weekday (B) vs last reading before target | 41 | 41.7 | 24.4 | 22.3 [−0.1, 44.7] | 0.62 | 0.62 | no | no |
| E5 | weekend (B) vs last reading before target | 15 | 13.0 | 5.0 | 0.0 (15/15 exact) | 0.994 | 0.999 | no | no |

What changed from the registered run:

| test | registered | strict | change |
|---|---|---|---|
| T1 | 30.3 | 35.1 | still Bonferroni-significant |
| T2 | p 0.0016 | p 0.041 | loses Bonferroni |
| T3 | 16.3 | 22.9 | still Bonferroni-significant |
| T6 | p 0.0025 | p 0.039 (lag 5: 0.11) | loses Bonferroni |

T4 and T5 hold.

Against the last reading before the target, the registered, uncorrected run also loses on E1–E3,
E5 and is level on E4. **This baseline was never beaten, leak or not.**

**Band coverage, strict (80% nominal; Wilson 95%; exact binomial p vs 0.80):**

| stratum | covered / n | coverage [Wilson 95%] | p vs 0.80 | mean width ₹/g |
|---|---|---|---|---|
| C1 weekdays (A) | 59/64 | 92.2% [83.0, 96.6] | 0.012 | 216 |
| C2 weekends (A) | 25/26 | 96.2% [81.1, 99.3] | 0.046 | 220 |
| all (A) | 84/90 | 93.3% [86.2, 96.9] | 0.0008 | 217 |
| set B all | 53/56 | 94.6% [85.4, 98.2] | 0.0039 | 198 |

After the correction, **both** C1 and C2 over-cover (their CIs exclude 80% from above). The gate
now fails on C1 as well as C2.

### Reading (INFERRED)

The leaks were real, and they came from three places:
- end-of-day retailer captures, on 44 day-source pairs;
- IBJA values used before publication, on 7 scored pairs, plus the training days;
- two baselines that shared the same timing problems.

Removing them costs the Kalman model about ₹5/g of MAE overall and ₹7/g on weekends. It still
clearly beats IBJA × markup and fusion.

The weekend "₹16 vs ₹46" result compared the model against yesterday's price. That is the wrong
yardstick for a nowcast when an earlier reading from the same day exists. On weekends Tanishq's
board is already final by the earlier reading on 25/26 days, so carrying it forward scores ₹2.7.

The right forward question is narrower: does the model beat the last available Tanishq reading at
the moment a nowcast is actually needed? That moment is when the Tanishq scrape has failed. The
forward shadow should log the age of that last reading, and it should be scored against it.
