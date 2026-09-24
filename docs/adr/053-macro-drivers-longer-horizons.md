# ADR 053 — Pre-registration: CFTC positioning and US real yields at longer horizons

**Status:** Registered 2026-09-24, before any COT/real-yield/price data for THIS analysis is
downloaded (ml/macro_drivers.py and scripts/analysis_macro_horizons.py are committed with this
ADR, at the freeze commit named below). Research only; nothing a user sees changes.

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

*(To be filled in after the freeze commit's analysis run; see the PR.)*
