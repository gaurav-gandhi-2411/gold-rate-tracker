# ADR 053 — Pre-registration: CFTC positioning and US real yields at longer horizons

**Status:** Registered 2026-09-24. **Result recorded 2026-09-25: no cell in the pre-registered
family is Bonferroni-significant; none replicates in both halves either (see Result).** Research
only; nothing a user sees changes.

**Extends:** ADR 040 (why daily direction is noise), ADR 044 (volatility-regime withdrawal), ADR
045 (calibration doesn't lower the floor). All three found no lever left on the SAME data
(COMEX price history, +/- INR macro) at the SAME horizon (next trading day). This ADR changes
both axes at once: two genuinely new pieces of information (CFTC positioning, US real yields --
neither has been a feature anywhere in this project before), at three longer horizons (1 week, 1
month, 3 months) instead of 1 day.

## Why new information, not new modelling

ADR 045's own consequence: "the direction track has no known cheap lever left on this data...
The remaining lever is new information, not better modelling of the same features." This is that
lever. `docs/adr/044-volatility-regime-preregistration.md`'s sibling scratchpad research
(`sources_cot_events.md`, 2026-09-24) surveyed legally reusable, run-time-fetchable sources; CFTC
Commitments of Traders and Treasury real yields are the two with (a) a public-domain legal basis
needing no license, (b) a stable, documented API (not HTML scraping), and (c) a long enough
history to be useful at monthly/quarterly horizons.

## Legal / data rules (frozen, not just followed)

- **Fetch at run time, every run.** `ml/macro_drivers.py`'s loaders hit the CFTC Socrata API and
  Treasury's own XML feed live, inside the analysis job. Neither series is cached to disk or
  committed to the repo -- only this ADR's *derived* results (`reports/macro_horizons_run_*.json`)
  are committed, per the item-8 legal/data rule.
- **Fail loudly, never silently stale.** Every fetch raises `ml.macro_drivers.MacroFetchError` on
  a network failure (after 3 retries, exponential backoff) or a response that parses but is empty
  or malformed (zero rows, non-positive open interest, a yield feed with no usable `<entry>`).
  There is no fallback path that would let an analysis run silently score against truncated or
  fabricated data.
- **Sources and their reuse terms** (VERIFIED primary-source citations in
  `sources_cot_events.md`, 2026-09-24):

| Source | Legal basis | Access |
|---|---|---|
| CFTC COT, Disaggregated, gold 088691 | US federal work, 17 U.S.C. Sec 105, public domain | Socrata API, dataset `72hh-3qpy` (`publicreporting.cftc.gov`) |
| Treasury Daily Par Real Yield Curve | US federal work (INFERRED by the standard .gov pattern; Treasury's own copyright-statement page 403'd during research) | Treasury's own XML feed, no API key |

- **Release lags, encoded so a feature can never be used before it was actually public.** This
  pipeline's decision timestamp is GC=F's own daily close as fetched from Yahoo Finance -- and a
  sibling analysis (this session, not independently re-derived here) found that close is struck
  well before 2pm ET (FOMC-decision-day reactions land in the NEXT day's GC=F close, not the same
  day's). Both release-lag rules below are checked against that finding, added before any result
  from this ADR was read:
  - COT: an as-of-Tuesday report is nominally public from **Friday (as-of + 3 days), 15:30 ET**
    (CFTC Release Schedule page, VERIFIED) -- AFTER a close struck well before 2pm ET, so a report
    released on day R is not safely seen by a decision using day R's own close.
    `cot_release_available_date` therefore adds **one more business day** on top of the nominal
    release date before treating a report as available: Friday+3 (holiday-shifted) -> next
    business day; the two known shutdown catch-up dates get the same +1-business-day buffer.
    Shutdown windows: 2013-10-01..2013-10-15 -> forced 2013-10-25 (CFTC press release 6745-13) ->
    available 2013-10-28; 2018-12-18..2019-01-29 -> forced 2019-03-08 (external reporting, not
    independently opened as a primary source this session -- flagged INFERRED in
    `sources_cot_events.md`, used here as the conservative bound regardless) -> available
    2019-03-11.
  - Real yields: Treasury posts by ~18:00 ET the **same day** (Yield Curve Methodology page,
    VERIFIED) -- comfortably after ANY same-day close, including an early one, so this rule was
    already conservative relative to the finding above and needed no change. Conservative rule
    used throughout this pipeline: a decision made at COMEX's close on day *d* may use day
    *d-1*'s real yield, never day *d*'s own --
    `real_yield_available_date(quote_date) = quote_date + 1 calendar day`.
  - Both are enforced structurally, not just documented: `ml.macro_drivers.
    align_feature_to_decision_dates` is a backward `merge_asof` on `available_date` -- it cannot
    return a value whose available date is after the decision date, by construction (tested in
    `tests/test_macro_drivers.py`, no network).
  - COMEX price: `ml.direction.comex_daily`, unchanged (same roll-adjustment as the rest of this
    project). Its module docstring already flags Yahoo Finance's terms risk; repeated here for
    completeness, not re-litigated.

## The frozen test

**Data window.** CFTC **Disaggregated** report (not Legacy): "managed money" is its own reported
category there, the standard "spec net long" proxy, where Legacy only has "non-commercial" (a
coarser bucket including some non-fund positions). Disaggregated's empirically confirmed start
for gold (088691) is **2006-06-13** (first row returned by the live API, 2026-09-24 -- the
CFTC's own "About" and "Historical Compressed" pages disagree by about 3 months on this; the API
response is ground truth, not either page's prose). This is the single binding start date for
the whole analysis. Real yields are fetched from Treasury's own history floor, 2003-01-02 --
well before 2006, so no real-yield feature is ever NaN at the window's own start for lack of
history. COMEX price is fetched from 2006-01-01 (a short buffer before the COT floor, purely so
the 63-trading-day momentum control is non-NaN at the window's true start). End: frozen at
**2026-09-24** (`MACRO_HORIZONS_END` in the script), the registration date.

**Horizons.** 1 week (5 trading days), 1 month (21), 3 months (63) -- `HORIZONS = (5, 21, 63)`.
Overlap correction: none of the three uses a non-overlapping design; instead every significance
test is HAC (Newey-West, lag = horizon - 1), the same overlap correction already standard in this
project (`ml.direction.evaluate_reframed.diebold_mariano_test`), reported with `effective_n`
alongside raw `n`.

**Targets, per horizon, always both:**
- **Direction** -- `label_binary_h{H}` (already built by `ml.direction.comex_daily`: 1 if the
  price H trading days ahead is higher than today's, 0 otherwise).
- **Log return** -- `ln(price[t+H] / price[t])`, built once alongside the direction label from
  the same (already roll-adjusted) price series.

**Features -- always the same seven, for every horizon and every model:**

| feature | what it is | source |
|---|---|---|
| `cot_net_pct_oi` | (managed-money long - short) / total open interest x 100 | CFTC Disaggregated, weekly |
| `cot_net_pct_oi_chg_4w` | its change over 4 report periods | CFTC Disaggregated, weekly |
| `cot_net_pct_oi_z` | z-score vs the trailing 156-report (3-year) window, full-window only (NaN before ~2009) | CFTC Disaggregated, weekly |
| `real_yield_10y` | Treasury 10-year real (TIPS) par yield, level | Treasury, daily |
| `real_yield_10y_chg_4w` | its change over 20 trading days (~4 weeks) | Treasury, daily |
| `mom_21` | price momentum, 1 month: `current/current.shift(21) - 1` | COMEX (already T-1) |
| `mom_63` | price momentum, 3 months: `current/current.shift(63) - 1` | COMEX (already T-1) |

Every feature is release-lag aligned to its decision date before it reaches any model; NaN before
a feature's own history warms up is filled by fold-local training-mean imputation, identical to
`analysis_direction_diagnosis._impute` (never a global mean, never test-set information).

**Models -- the same complexity pair for both targets, resolved as follows (rule 73: ambiguity
stated and resolved before the run, not during it):**
- **Direction:** `logit` (standard-scaled `LogisticRegression(C=1.0)`) and `gbm_stumps`
  (`LGBMClassifier`, depth 1, 2 leaves, 50 estimators, lr 0.05) -- both reused verbatim from
  `analysis_direction_diagnosis.make_model`, unchanged.
- **Log return:** the literal regression analogs at the same hyperparameters -- `ridge`
  (standard-scaled `Ridge(alpha=1.0)`, Ridge's alpha is L2's C=1.0 counterpart) and
  `gbm_stumps_reg` (`LGBMRegressor`, identical depth/leaves/estimators/lr to `gbm_stumps`).

**Baselines:**
- Direction: **always-up** (predict "up" every day -- gold's long-run drift makes this the
  correct baseline, per ADR 038/040/044's established convention, not the per-fold majority/
  climatology baseline those diagnosis scripts otherwise use for exploratory work).
- Return: **both** reported, per the brief. **Zero-return** (the standard random-walk null for a
  price series) is PRIMARY -- it is the one that enters the confirmatory family below.
  **Historical-mean-drift** (the training fold's own mean realised return) is reported alongside,
  not part of the family: a model beating it is stronger evidence (it already "knows" the
  window's realised drift), but the brief's plain "no-change" most naturally names zero-return.

**Walk-forward.** Expanding window, refit every 21 trading days (`BLOCK`). A refit starting at
test row `s` trains only on rows whose `label_date_h{H}` matured before `as_of[s]` -- embargo >=
horizon by construction (reusing `ml.direction.comex_daily`'s existing label-date columns and
`analysis_direction_diagnosis.walk_forward`'s embargo logic exactly, for direction; a new,
structurally identical `walk_forward_return` for the continuous target).
`MIN_TRAIN = 500` trading days (~2 years) before any row is scored.

**Test.** One-sided HAC Diebold-Mariano (Newey-West, lag = horizon - 1): 0/1 loss vs always-up
(direction), squared error vs zero-return (return, PRIMARY). `effective_n` reported alongside raw
`n` for every cell (`ml.direction.evaluate_reframed.diebold_mariano_test`'s existing HAC
correction).

**Multiplicity.** The confirmatory family is **3 horizons x 2 targets x 2 models = 12 cells**
(`FAMILY_KEYS` in the script). Bonferroni (`alpha = 0.05 / 12`) is the primary correction; BH at
q = 0.05 is also reported over the same 12 p-values (`ml.direction.stats_corrections`, reused
unchanged).

**Success rule, decided now, applied mechanically at aggregation time (`build_verdict`):** a cell
succeeds only if **both**:
1. its primary p-value survives Bonferroni across the 12-cell family;
2. the edge (model loss < baseline loss) has the **same sign** in both halves --
   2006-01-01..2015-12-31 and 2016-01-01..2026-09-24, split on `as_of_date`, at least 30 scored
   rows per half (otherwise `"better": null`, not a fabricated sign). Independent significance in
   each half is NOT required, only non-reversal -- matching the brief's "replicates... with the
   same sign".

A cell that fails either condition is a negative result and is reported exactly as plainly as a
positive one, per horizon and target, not folded into a single headline number.

**Detection floor (secondary, not part of the confirmatory family; item 8's own explicit
requirement, following the ADR 040 D3 / ADR 045 precedent this extends to 5/21/63-day horizons).**
Per horizon: a signal of known strength `q` (deterministic function of the `mom_21` control
feature: `mom_21 > median`) replaces the true direction label with probability `q`; grid
`q in {0, 0.05, 0.10, 0.15, 0.20}` (oracle accuracy `0.5 + q/2`, i.e. up to ~60%), **20 seeds**
per cell, both `logit` and `gbm_stumps`. Detection = one-sided p < 0.05 vs the walk-forward
climatology/majority baseline (`analysis_direction_diagnosis.walk_forward` + `.score`, unchanged
-- this is a pipeline-sensitivity calibration, not the "does gold have drift" question the
confirmatory family asks, so it intentionally reuses the ADR 040/045 baseline convention rather
than always-up). Floor = the smallest `q > 0` detected in >= 80% of seeds; false positives
reported at `q = 0`. Reported plainly per horizon, whatever it shows.

## How it runs

`gh workflow run analysis.yml --ref feat/macro-horizons-prereg -f analysis=macro_horizons`
(GitHub-hosted runners only, per the analysis.yml header's standing constraint), 15 shards (12
confirmatory + 3 detection-floor). Any deviation from this text is logged in the results PR
before the numbers are read.

## Consequences

- If any cell succeeds: that (horizon, target, model) becomes a candidate for its own
  pre-registered live-use test, gated the same way every other direction/return finding in this
  project has been (ADR 036's promotion gate). It does not reach users from this ADR alone.
- If none does: CFTC positioning and US real yields, at these three horizons, with this feature
  set, join the list of things tried and found not to help -- reported with the same honesty as
  ADR 040/044/045, including exactly what the detection floor says this pipeline could and could
  not have seen.

## Alternatives considered

- **Legacy report instead of Disaggregated.** Rejected: Legacy's "non-commercial" bucket is
  coarser than Disaggregated's "managed money" (it also includes non-fund large speculators), and
  Legacy's own gold history (1986) is not meaningfully longer than Disaggregated's once the
  extra ~20 years of pre-2006 history would sit almost entirely in COT's early, thinly-reported
  era. Disaggregated is the standard "spec positioning" series in gold commentary for this reason.
- **FRED `DFII10` instead of Treasury's own feed.** Rejected: FRED's website ToS bars scraping
  (API-only access is fine but needs a key and its own rate limits); Treasury's own feed is the
  primary source FRED itself republishes from, is unambiguously public domain, and needs no key.
- **Non-overlapping horizon sampling** (score only every 5th/21st/63rd day) instead of HAC
  correction. Rejected: it throws away the large majority of the scored sample for no power gain
  the HAC correction doesn't already give, and this project's existing DM machinery already
  handles the overlap correctly (ADR 040/044/045 precedent).

## Result

**Provenance:** `reports/macro_horizons_run_36039973496.json`. All 15 shards ran at the frozen
commit `e618b704b8351d7e98cd2e1789181cee4235b2fc`, none were missing.

**Two earlier runs, disclosed rather than discarded (rule: any deviation from this text is logged
before the numbers are read):**
1. Run `36018964021` (commit `6b2b9192`) crashed on every shard before any COT/real-yield/price
   data was ever scored: `pandas.errors.MergeError` from a datetime64-unit mismatch in
   `align_feature_to_decision_dates` (a column of Python `date` objects vs a column of ISO date
   strings). Purely mechanical; fixed at `fda9f9e9`, no methodology touched.
2. Run `36020769555` (commit `fda9f9e9`) completed and scored real data, but under a COT
   release-lag rule later found to be insufficiently conservative: this pipeline's decision
   timestamp is GC=F's own daily close, and a sibling analysis this session found that close is
   struck well before 2pm ET (FOMC-day reactions land in the next day's close). CFTC's 15:30 ET
   release is after that, so a report released on day R was not safely seen by a decision using
   day R's own close under the old rule. `cot_release_available_date` was tightened to add one
   more business day (commit `e618b704`) before any number from run 2 was written here, and the
   analysis was re-run a third time. Run 2's `any_success` was also `false`, with the same
   qualitative pattern (all 12 cells non-significant, none replicating); the tightened lag moved
   individual p-values by roughly 0.01-0.08 and left every conclusion below unchanged. Real
   yields' existing +1-calendar-day rule was already conservative relative to this finding and did
   not change.

**Pre-registered verdict: no cell succeeds.** `any_success: false`. Not one of the 12 confirmatory
cells is Bonferroni-significant (`alpha = 0.05/12 = 0.004167`); the smallest p-value in the whole
family is 0.346 (h63, direction, `gbm_stumps`) -- nowhere close. BH at q = 0.05 flags nothing
either, so this is not a "Bonferroni was too strict" story. Every cell also fails the
both-halves-replicate check independently of significance (see below).

### Direction (0/1 loss vs always-up, one-sided HAC DM, lag = horizon-1)

| horizon | model | n | effective n | acc (model) | acc (always-up) | p | Bonferroni | BH | half 06-15 better? | half 16-26 better? | replicates | success |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | logit | 4710 | 2146.3 | 52.87% | 54.50% | 0.907 | no | no | no (+0.0035) | no (+0.0260) | no | no |
| 5 | gbm_stumps | 4710 | 2340.8 | 53.48% | 54.50% | 0.984 | no | no | yes (-0.0005) | no (+0.0182) | no | no |
| 21 | logit | 4700 | 629.8 | 52.79% | 55.45% | 0.846 | no | no | no (+0.0268) | no (+0.0265) | no | no |
| 21 | gbm_stumps | 4700 | 697.0 | 54.77% | 55.45% | 0.642 | no | no | no (+0.0030) | no (+0.0097) | no | no |
| 63 | logit | 4670 | 240.9 | 56.04% | 56.23% | 0.516 | no | no | yes (-0.0927) | no (+0.0739) | no | no |
| 63 | gbm_stumps | 4670 | 227.5 | 57.71% | 56.23% | 0.346 | no | no | yes (-0.0560) | no (+0.0166) | no | no |

"better?" is the sign of (model loss - baseline loss) in that half -- negative/"yes" means the
model beat always-up in that half alone, independent of significance. Every direction row fails
in the 2016-2026 half regardless of what the 2006-2015 half shows: three rows (`h5/gbm_stumps`,
`h63/logit`, `h63/gbm_stumps`) show a same-signed edge in the OLDER half only, which is exactly
the non-replication pattern ADR 044 warned about (an effect visible in one slice, gone in the
other) and precisely why the both-halves rule exists.

### Log return (squared error vs zero-return [PRIMARY] and historical-mean-drift, one-sided HAC DM)

| horizon | model | n | eff. n | MSE (model) | MSE (zero) | MSE (drift) | p vs zero | p vs drift | Bonferroni | BH | half 06-15 better? | half 16-26 better? | replicates | success |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 5 | ridge | 4710 | 1350.8 | 4.58e-4 | 4.43e-4 | 4.43e-4 | 0.979 | 0.985 | no | no | no (+4.0e-5) | yes (-2.7e-6) | no | no |
| 5 | gbm_stumps_reg | 4710 | 1974.3 | 4.51e-4 | 4.43e-4 | 4.43e-4 | 0.931 | 0.940 | no | no | no (+2.1e-5) | yes (-2.1e-6) | no | no |
| 21 | ridge | 4700 | 406.1 | 1.910e-3 | 1.721e-3 | 1.718e-3 | 0.980 | 0.987 | no | no | no (+4.6e-4) | yes (-1.2e-5) | no | no |
| 21 | gbm_stumps_reg | 4700 | 504.2 | 1.724e-3 | 1.721e-3 | 1.718e-3 | 0.528 | 0.579 | no | no | no (+1.1e-4) | yes (-7.9e-5) | no | no |
| 63 | ridge | 4670 | 156.0 | 5.504e-3 | 4.781e-3 | 4.771e-3 | 0.918 | 0.929 | no | no | no (+2.0e-3) | yes (-2.1e-4) | no | no |
| 63 | gbm_stumps_reg | 4670 | 147.1 | 4.913e-3 | 4.781e-3 | 4.771e-3 | 0.656 | 0.752 | no | no | no (+8.3e-4) | yes (-4.0e-4) | no | no |

Every return model has HIGHER squared error than both baselines at every horizon -- the model is
never a net improvement over "predict zero" or "predict the training window's own mean drift", let
alone significantly so. The half pattern flips sign the other way from direction (worse in
2006-2015, marginally better in 2016-2026) but the "better" half's edge is tiny (order 1e-5 to
1e-4) against baselines of order 1e-3 to 1e-4 -- not evidence of anything, and still fails
replication since the two halves disagree.

### Detection floor (secondary; signal of strength q planted on `mom_21 > median`, 20 seeds, floor
= smallest q detected in >= 80% of seeds, `logit`/`gbm_stumps`, vs the walk-forward climatology
baseline -- NOT the always-up baseline used above, see the design-choice note earlier in this ADR)

| horizon | learner | floor q | floor oracle accuracy | false positives at q=0 | detection at q=0.20 |
|---|---|---|---|---|---|
| 5 | logit | none (<=0.20) | -- | 0/20 | 10% |
| 5 | gbm_stumps | **0.20** | **~60%** | 0/20 | 90% |
| 21 | logit | none (<=0.20) | -- | 0/20 | 0% |
| 21 | gbm_stumps | none (<=0.20) | -- | 0/20 | 15% |
| 63 | logit | none (<=0.20) | -- | 0/20 | 0% |
| 63 | gbm_stumps | none (<=0.20) | -- | 0/20 | 0% |

**Plainly: the pipeline's own sensitivity collapses as the horizon grows.** At 1 week, `gbm_stumps`
can still reliably see a signal worth about a 10-point oracle-accuracy edge (q = 0.20, matching
ADR 040 D3's daily-horizon floor). At 1 month it can barely see that same-sized signal 15% of the
time. At 3 months it cannot see it at all, at any strength tried. This is not a surprise given the
HAC-shrunk effective sample sizes above (effective n falls from ~2,000-2,500 at 1 week to ~150-240
at 3 months, as more of each fold's outcome overlaps with its neighbours) -- it means a genuinely
present return-level effect at 21/63-day horizons would need to be considerably larger than what
COT and real yields showed here before this pipeline could confirm it. No false positives at
q = 0 anywhere (0/20 in all six cells), so the floor collapse is a real power problem, not a
miscalibrated test.

**In plain words:**
- **CFTC positioning and US real yields, at 1 week / 1 month / 3 months, do not clear the bar.**
  Every direction model is at or below the always-up baseline; every return model has higher
  squared error than predicting zero or the training window's own drift. None is even close to
  Bonferroni-significant, and where an edge shows up in one half of the 20-year window (2006-2015),
  it either reverses or shrinks to nothing in the other half (2016-2026) every single time.
- **This is not "the pipeline couldn't see it."** At 1 week the detection floor is the same as
  ADR 040's daily-horizon floor (~60% oracle accuracy) and the family's actual p-values are nowhere
  near that boundary (smallest 0.346) -- a real edge of ADR-040-sized strength would have shown up.
  The genuine blind spot is 1 month and 3 months, where the floor is much higher than 60% oracle
  accuracy (unmeasured beyond q = 0.20) -- a smaller real effect at those horizons could exist and
  still be invisible here.
- **What would change this verdict:** a stronger control feature set at 1m/3m specifically (the
  detection floor there is the real gap, not the direction/return result itself); a longer trailing
  window for `cot_net_pct_oi_z` once more COT history accumulates (it is NaN before ~2009,
  discarding real information within the pre-2016 half); or testing swap-dealer or other-reportable
  positioning instead of managed money, which this ADR did not try.

## Consequences (realized)

Per the "If none does" branch above: CFTC positioning and US real yields, at 1 week / 1 month / 3
month horizons, with this feature set, join ADR 040/044/045 on the list of things tried and found
not to help at the direction/return level this project scores against. The 1-month/3-month
detection floor (not measured past q = 0.20 oracle ~60%) is the concrete, reusable finding for any
future attempt at these horizons -- a future feature needs to clear a materially higher bar than a
daily-horizon feature would, or it will be invisible to this same walk-forward regardless of
whether it is real.
