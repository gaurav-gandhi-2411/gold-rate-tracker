# ADR 031 — M2 Direction Model: Collapse Diagnosis + Reframed-Target Shadow Results

**Status:** Accepted, implemented 2026-09-23. Shadow only — `ml.direction.gate` and
`data/direction_baseline.json` (the live pipeline) are untouched by this ADR.

**Numbered 031, not 030,** because PR #1890 (ADR 030, the M1 proxy) was still open/draft when this
branch forked from master — avoids a numbering collision once both land.

---

## Context: diagnosing the majority-class collapse

`data/direction_baseline.json`'s h2 horizon flags `majority_class_collapse: true`
(`trailing_30_fold_up_fraction: 1.0`). That statistic measures the **model's own predicted
probability**, thresholded at 0.5, over its most recent 30 real folds — not the true label rate.
Reading it as "the market has only gone up for 30 folds straight" would be wrong; measured directly
(rolling 30-row mean of the real `label_binary_h2` labels), the true rate swings between 33% and 80%
over that same window. The signal exists; the model isn't using it. Three pieces of evidence, all
measured against the live `data/feature_store/snapshots.parquet` (183 rows, 2025-01-09 to
2026-09-21):

1. **The model's raw predicted probabilities are anchored near the full-history base rate, not
   varying with the day.** Re-running the h2 walk-forward and inspecting the last 30 real
   `log_prob` values directly: range **0.547–0.669**, mean **0.615** — versus the full-sample base
   rate of **0.597**. The model has never predicted "down" (prob < 0.5) once in that stretch, despite
   real labels alternating throughout, and its probabilities barely leave a ~12pp band centered on
   the unconditional prior.
2. **Feature collinearity.** `FEATURE_COLS` includes five raw price-LEVEL features (`gold_usd`,
   `ibja_pm_916`, `ibja_am_916`, `tanishq_22k`, `usd_inr`) that correlate 0.86–1.00 with each other
   (`ibja_pm_916`/`ibja_am_916` are 1.00 — same underlying quote, sampled twice). These levels are
   non-stationary and dominated by the current price regime, not day-to-day dynamics; no return,
   momentum, or differenced feature exists in `FEATURE_COLS` at all. `tanishq_22k` additionally
   correlates only **-0.064** with `usd_inr` (vs. 0.86–0.96 for every other price-level pair) —
   consistent with Tanishq-source data-quality gaps (self-hosted-runner staleness/CF-blocking,
   documented elsewhere) contributing noise rather than signal to this feature.
3. **Class balance + regularization interaction.** The training set's cumulative up-fraction (59.7%
   full-sample for h2) grows more skewed as the expanding window accumulates more of the recent bull
   run. Combined with moderate, unweighted L2 regularization (`C=1.0`, no `class_weight`) and highly
   collinear features carrying weak independent signal, the fit shrinks toward the intercept — which
   encodes exactly that cumulative base rate — rather than confidently using per-day information.

**None of this is a reason to retire the model** (GG's explicit instruction). It's a reason to test
whether a different target construction, model regularization, or evaluation protocol can extract a
genuine edge — which is what this ADR's shadow results below do.

## What was built

`ml/direction/dataset.py`: `build_dataset(..., extra_horizons=(5, 10))` — additive, generalizes the
existing h1/h2 idx0-offset pattern to arbitrary trading-day horizons, plus a new
`window_min_pm916_hN` column (the minimum IBJA price across the whole path to day N, not just the
endpoint — needed for the buyer's-decision target below). Default behavior (no `extra_horizons` arg)
is byte-for-byte unchanged; `ml.direction.evaluate`/`ml.direction.gate` don't pass it.

`ml/direction/models.py`: `fit_logistic`/`fit_lightgbm` gained an optional `class_weight` parameter
(default `None`, matching each classifier's own default) — additive, existing callers unaffected.

`ml/direction/reframed_targets.py` (new): three alternative binary-label constructions, each a pure
function of the dataset:
- **Dead-zone** — drop near-flat days (existing `label_ternary_hN`'s dead band), score only genuine
  directional moves.
- **Detrended (excess-return)** — `sign(realized delta - trailing trend)`, where the trend uses only
  prior deltas that had already MATURED (label_date strictly before the row's own as_of_date) by
  that row's own date — an embargo applied to the trend construction itself, not just the
  walk-forward training set, so the "trend" component can't quietly leak future information into a
  label.
- **Buyer's decision** — "will the price dip at least the dead-band amount at ANY point in the next N
  days," using the new path-aware `window_min_pm916_hN`, not just the day-N endpoint (a buyer decides
  whether to wait based on the path, not a single future point).

`ml/direction/evaluate_reframed.py` (new): an **embargo-aware** walk-forward harness — the key fix
this ADR makes to evaluation methodology, not just targets. For horizon N, a training row's label
doesn't mature until N trading days after its own as_of_date; the *existing* h1/h2 harness's
`train_df = dataset.iloc[:i]` silently assumes every prior row's label had already matured by the
test point, true by construction for h=1 but **not** generally true for h=2 and actively wrong for
h=5/h=10 (several of the nearest "prior" rows haven't matured yet as of the test date). This module's
`_embargo_eligible_indices` fixes that by comparing `label_date_hN` to the test row's `as_of_date`
directly. Three models per combination: class-weighted logistic (`class_weight="balanced"`),
calibrated LightGBM (wrapped in `CalibratedClassifierCV`, unlike the live pipeline's uncalibrated
usage — GG's spec asks for calibration "fitted inside the walk-forward" for every model here), and a
simple probability-average ensemble. Metrics: accuracy/Brier/ECE/reliability (reusing
`ml.direction.evaluate.compute_direction_metrics`, which already implements the McNemar-style sign
test vs. always-up), **Brier skill score vs. walk-forward climatology** (a fold-by-fold "predict the
current embargo-eligible training mean" reference, not a single fixed number), and **two
Diebold-Mariano tests on Brier loss** — vs. always-up (spec-required) and vs. climatology (bonus,
consistent with the BSS baseline). DM uses a Newey-West-style long-run variance with truncation lag
`horizon - 1`, the standard correction for h-step-ahead forecast-error autocorrelation; noted as a
normal-approximation p-value, not exact, given small n.

## Results (n_base_rows=183, horizons 5 and 10, ensemble model reported; full breakdown in
`data/direction_reframed_results.json`)

| Target | Horizon | n folds | Accuracy (base) | Brier | BSS vs. climatology | p (McNemar) | ECE |
|---|---|---|---|---|---|---|---|
| raw_binary | 5 | 150 | 0.553 (0.627) | 0.286 | -0.164 | 0.0034 | 0.227 |
| deadzone | 5 | 132 | 0.545 (0.644) | 0.287 | -0.188 | 0.0002 | 0.191 |
| **detrended** | 5 | 140 | 0.514 (0.479) | 0.255 | **+0.052** | 0.672 | **0.116** |
| buyer_decision | 5 | 150 | 0.473 (0.480) | 0.279 | -0.074 | 1.000 | 0.150 |
| raw_binary | 10 | 137 | 0.657 (0.686) | 0.236 | +0.028 | 0.344 | 0.115 |
| deadzone | 10 | 122 | 0.598 (0.672) | 0.264 | -0.047 | 0.023 | 0.194 |
| **detrended** | 10 | 122 | 0.525 (0.484) | 0.245 | **+0.089** | 0.657 | **0.070** |
| buyer_decision | 10 | 137 | 0.358 (0.540) | 0.308 | -0.151 | 0.006 | 0.244 |

**Honest bottom line: none of the 8 (target, horizon) combinations reach `significant_at_05=True`**
(the McNemar test's own requirement that the model both beat always-up AND do so at p<0.05) — this
is a negative result on the "make it work" bar, reported as plainly as a positive one would be. Two
combinations (`deadzone_h5`, `buyer_decision_h10`) are *significantly worse* than always-up
(p=0.0002, p=0.006) — real findings, not noise. `raw_binary_h10`'s p=0.344 with a positive but tiny
BSS (+0.028) is the closest any combination came to daylight, and still isn't there.

**The most promising lead: detrended (excess-return).** Positive BSS at both horizons (+0.052,
+0.089 — the only target beating climatology at either horizon) and by far the best calibration
(ECE 0.116/0.070, roughly half of every other combination's). It doesn't clear significance with the
current 183-row dataset and feature set, but it's the one direction where removing the base-rate
anchoring (this ADR's own diagnosis) measurably helped rather than hurt. Full DM results (both vs.
always-up and vs. climatology, all significance flags) are in the JSON artifact — every number here
has a `p_value`/`n`/`dm_stat` sitting next to it, nothing is asserted without it.

## Not done by this ADR

- **The COMEX-targeted daily variant for #1756** — needs M1's `data/history_seed_inr22k_proxy.parquet`
  (PR #1890), which is currently DRAFT (failed the rule-70a size gate, awaiting GG's manual merge).
  Next step once #1890 lands: rebase this branch, build a fifth target using the proxy's own daily
  changes as ground truth on the same embargo-aware protocol.
- Feature-set changes (adding return/momentum features to directly address the collinearity finding)
  — the diagnosis implicates features as well as targets, but GG's spec scoped this round to target
  reframing with the existing `FEATURE_COLS`; flagged here as the most likely next lever if detrended
  still doesn't clear significance once more data or the COMEX variant is added.
- Promotion of any variant to the live gate — none of these results clear the bar; `ml.direction.gate`
  is untouched, exactly as instructed.

## Alternatives considered

- **Precomputing the detrended target's trend as a fixed rolling-window column** (no embargo check).
  Rejected: for horizon > 1, a naive `.shift(1).rolling()` over `delta_per_gram_hN` would let a
  not-yet-matured prior row's future-dependent delta leak into the current row's trend — the exact
  class of leak GG's spec's "detrended... direction so drift does not dominate" item would have
  silently reintroduced if built carelessly.
- **A single global Diebold-Mariano baseline** (only vs. always-up, dropping the vs.-climatology one).
  Kept both: BSS already compares to climatology, so a DM test against the same baseline lets a
  reader check the BSS sign against a formal significance test on the identical comparison, at
  negligible extra code cost.
