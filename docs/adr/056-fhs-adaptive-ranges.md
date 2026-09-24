# ADR 056 — Pre-registration: adaptive ranges via filtered historical simulation (FHS)

**Status:** Accepted for pre-registration, 2026-09-25. `ml/fhs_ranges.py`,
`scripts/analysis_fhs_ranges.py`, `scripts/run_fhs_shadow.py` and their tests are frozen in
this PR **before the retrospective is run on any real data**. Research and forward shadow
only. Nothing a user sees changes until GG approves a separate promotion PR.

**Builds on:** ADR 043 (`ml.weekly_range` — historical simulation + IBJA split-conformal
scale, "week" horizon definition reused exactly) and ADR 047 (`ml.next_day_range` — the same
method applied at the displayed decision day; "v2" below). Reuses, without duplication:
`ml.range_forecast.garch` (GARCH(1,1) MLE), `ml.range_forecast.baselines.ewma_variance_path`
(EWMA recursion), `ml.range_forecast.metrics` (`wilson_ci`, `kupiec_pof_test`,
`christoffersen_independence_test`, `winkler_score`, `qlike`), `ml.direction.evaluate_
reframed.diebold_mariano_test` (HAC Diebold-Mariano), `ml.direction.stats_corrections`
(`bonferroni`, `benjamini_hochberg`).

## Context

ADR 047 rebuilt the displayed "tomorrow's range" on ADR 043's method (v2) and measured
84.1% coverage (n = 63) against the displayed range's 73.0% — but v2's mean width was 31%
wider than the displayed range's, failing v2's own pre-registered 25% width ceiling. v2's
shape (`ml.weekly_range.base_range`) is an UNCONDITIONAL historical-simulation quantile: the
same width every day, calm or volatile, calibrated only by a single fixed split-conformal
scale. It clears the coverage bar by being wide everywhere, all the time — a real cost to a
viewer even when the wider range happens to be right.

Brief item 5b asks whether an ADAPTIVE shape can match v2's coverage without paying as much
width: narrow the range on calm days, widen it on volatile ones, using a conditional-
volatility estimate that is re-derived from data known at each as-of day (no look-ahead),
rather than a single fixed multiplier. This is filtered historical simulation (FHS,
Barone-Adesi/Giannopoulos/Vosper-style): standardize historical returns by a conditional
volatility estimate BEFORE taking empirical quantiles of the standardized residuals, then
rescale those quantiles by TODAY's own volatility forecast (not the historical average
baked into the quantiles).

## Decision

`ml/fhs_ranges.py` implements this as a drop-in extension of `ml.weekly_range`: `fhs_base_
range` returns the exact same `(lo, hi)` log-return contract as `ml.weekly_range.base_
range`, so `ml.weekly_range.complete_windows` / `score` / `conformal_scale` — ADR 043's own
IBJA split-conformal calibration layer, reused verbatim, no new conformal statistics — apply
to FHS unchanged. Only the proxy-side SHAPE computation changes.

**Two conditional-volatility estimators** (`ml.fhs_ranges.VOL_METHODS = ("ewma", "garch")`),
each refit only every `REFIT_EVERY` (21, reused from `ml.range_forecast.garch`) trading days,
using ONLY proxy returns strictly before the current position (`ml.fhs_ranges.VolCache`):

- **"ewma"**: `ml.range_forecast.baselines.ewma_variance_path`, reused, not duplicated.
  Lambda is selected from `EWMA_LAMBDA_GRID = (0.90, 0.92, 0.94, 0.96, 0.98)` by minimizing
  mean 1-step-ahead QLIKE loss (`ml.range_forecast.metrics.qlike`, reused) on an internal
  held-back TAIL (30%, minimum 20 observations) of the training returns at each refit point
  — "chosen on training folds only": the selection never touches a return at or after the
  refit position, and never touches IBJA truth or a coverage outcome.
- **"garch"**: `ml.range_forecast.garch.fit_garch` / `garch_sigma2_path` /
  `garch_h_day_variance`, reused, not duplicated — GARCH(1,1), Student-t innovations, MLE via
  `scipy.optimize.minimize` on a numpy log-likelihood and numpy variance recursion (the
  existing MLE-in-numpy implementation already in this codebase).

**FHS shape**, per as-of day `t` and path length `k` (1, or ADR 043's weekly `issue_days = 5`,
reused exactly via `ml.weekly_range.issue_days` / `complete_windows` / `WEEK_CALENDAR_DAYS`):

1. `sigma2_forecast[i] = Var(r_i | F_{i-1})` for every return `r_i` known before `t`
   (GARCH's own pre-return convention; EWMA's own post-return path is shifted by one index
   to match it — `ml.fhs_ranges.forecast_variance_path`).
2. Standardized residuals `z_i = r_i / sqrt(sigma2_forecast[i])`.
3. Over the trailing `HS_WINDOW` (500, reused from `ml.weekly_range`) standardized returns,
   build every rolling `k`-day cumulative-standardized-return path — the identical rolling-
   path construction `ml.weekly_range.base_range` uses on `log(price)`, applied here to
   `cumsum(z)` instead. `lo_q` = 10th percentile of path minima, `hi_q` = 90th percentile of
   path maxima (dimensionless "z-units", `LEVEL = 0.80` reused from `ml.weekly_range`).
4. Rescale by TODAY's `h`-day-ahead volatility FORECAST (not the historical realized vol
   baked into steps 1–3): `sigma_h = sqrt(GARCH h-day variance)` (mean-reverting,
   `garch_h_day_variance`) or `sqrt(EWMA sigma2_next * h)` (flat sqrt-time, matching `ml.
   range_forecast.baselines.ewma_forecast_set`'s own convention). Returned bounds =
   `(sigma_h * lo_q, sigma_h * hi_q)`, clipped the same way `ml.weekly_range.base_range`
   clips (`min(lo, -1e-6)`, `max(hi, 1e-6)`) to avoid a zero-division inside `score`.

**Truth:** IBJA PM 916 (View A, both horizons) and `actual_next_22k` from `data/metrics_
history.json` (View B, horizon "1d" only — the same truth the displayed range and v2 are
scored against).

## Frozen evaluation protocol

Both methods (`ewma`, `garch`) are evaluated separately throughout — this is not a single
combined FHS result, it is two competing pre-registered variants.

### Two views

- **View A (IBJA-native, "1d" and "week"):** `ml.fhs_ranges.fhs_walk_forward` vs `ml.
  weekly_range.walk_forward`'s two outputs on the identical `complete_windows(ibja, horizon)`
  rows — "plain historical simulation" (its `raw_hit`/`raw_width_pct`: unconditional
  quantiles, scale fixed at 1) and "weekly-range method" (its calibrated `hit`/`width_pct`:
  ADR 043's method as-is).
- **View B (decision-day, "1d" only):** `ml.fhs_ranges.fhs_next_day_range_pct_series` vs
  the **live** displayed range (`data/metrics_history.json` `lower`/`upper`), **ADR 047 v2**
  (`ml.next_day_range.next_day_range_pct`), and **plain historical simulation**
  (`ml.weekly_range.base_range` at scale 1, applied at the decision day) — all on the same
  resolved decision days (same filter `scripts/run_next_day_range_shadow.py` uses:
  `decision_date >= ml.metrics.ADR_022_FIX_DATE`, resolved outcome) and the same
  `actual_next_22k` truth.

### Embargo / no look-ahead

Walk-forward throughout. Embargo >= horizon by construction, reusing existing mechanisms,
not a new one: View A's `conformal_scale` only counts a window once its own outcome day has
passed (`ml.weekly_range.walk_forward`'s "matured windows only" rule, unchanged); View B's
`< d` filtering (`ml.next_day_range`'s own no-look-ahead discipline) is the identical rule
applied at a displayed decision day instead of an IBJA row. `ml/fhs_ranges.py`'s own
`VolCache` additionally guarantees every volatility-model fit uses only returns strictly
before the position being scored (see `tests/test_fhs_ranges.py`'s no-look-ahead tests).

### Metrics and tests, per (method, view, horizon, baseline) cell

1. **Coverage**: n, k, coverage, Wilson 95% CI (`wilson_ci`), one-sided exact binomial test
   vs nominal 80% (`H1`: coverage < 80% — under-coverage is the concern, a too-wide range is
   a cost not a correctness failure, matching ADR 047's framing).
2. **Kupiec POF test** (`H0`: true exceedance probability = 20%) and **Christoffersen
   independence test** (`H0`: exceedances independent across time) — both reported
   informationally, as ADR 043/the range-forecast harness already does, not folded into the
   multiplicity-corrected family below (they test calibration and independence, a different
   null than "FHS beats baseline").
3. **Mean width** (₹ in View B, matching ADR 047's own units; % of price in View A, matching
   `ml.weekly_range`'s own `width_pct` units).
4. **Interval (Winkler) score**, one-sided Diebold-Mariano (HAC/Newey-West, truncation lag =
   `horizon - 1`, `ml.direction.evaluate_reframed.diebold_mariano_test(..., alternative=
   "less")`, `H1`: FHS's Winkler loss < baseline's) — FHS vs every in-scope baseline for that
   view/horizon. Winkler's scalar "actual", per window/day: the price known at the window's
   end (View A) or the decision day's next reading (View B). For View A "week" this is the
   window's ENDPOINT price, not the full 5-day path the coverage hit/miss itself checks — an
   explicit, pre-registered simplification applied IDENTICALLY to every candidate compared
   in that cell (FHS, plain HS, weekly-range method), so it cannot favor one candidate over
   another in the DM comparison even though the absolute Winkler number is not a literal
   score of the path-containment target.
5. **Multiplicity correction**: Bonferroni and Benjamini-Hochberg (`ml.direction.stats_
   corrections`, both reused, both reported side by side per that module's own convention),
   applied to the family of one-sided Winkler DM p-values ONLY (14 tests total, enumerated
   below), alpha = 0.05.
6. **Weekday/weekend split**, View B only: Mon-Thu vs Fri-Sun decision days (the exact
   stratification ADR 047 used), coverage with Wilson CI for FHS and live on each stratum.

**The 14-test family** (2 methods x): View A "1d" (vs plain HS, vs weekly-range method) = 4;
View A "week" (vs plain HS, vs weekly-range method) = 4; View B (vs live, vs v2, vs plain
HS) = 6. Total = 14. No other p-value in this ADR is corrected against this family — the
binomial vs-nominal tests, Kupiec, and Christoffersen are reported as separate, descriptive
diagnostics per standard practice in ADR 043/047, not as part of a "beats baseline" claim.

### Width hard gate (View B only, vs live — ADR 047's own reference, reused exactly)

`Target: coverage at nominal WITHOUT failing that same width limit`, measured exactly as
ADR 047 measured it: **FHS's mean width <= 1.25 x live's mean width**, on View B's 1-day
decision-day set. This gate applies ONLY at View B vs "live" — there is no displayed weekly
range in production to use as a width reference at horizon "week" (ADR 043 itself: "A second
range about next week's movement would compete with [the displayed range]... The proposal is
to show ONE range statement"), so inventing a new hard gate at "week" would not be reusing
ADR 047's gate, it would be a different, untested one. View A "week"'s width ratio vs the
weekly-range method is still measured and reported (secondary metric #3 above), just not
gated.

**Per-method success** (View B, mirroring ADR 047's own three-part gate exactly):

| # | criterion |
|---|---|
| 1 | FHS's coverage Wilson 95% CI contains 80% |
| 2 | FHS's coverage >= live's coverage |
| 3 | FHS's mean width <= 1.25 x live's mean width |
| **overall** | **all three** |

A method (ewma or garch) that passes all three on View B is the primary claim this ADR can
make; View A and the DM/multiplicity results are reported alongside as full context, not as
additional hard gates.

### What counts as a negative

- Neither `ewma` nor `garch` clears the View B three-part gate: FHS did not solve the
  coverage/width trade-off v2 hit, and this is reported as a negative, not reframed around
  whichever sub-metric happens to look best.
- A method that passes the width gate only by ALSO failing to beat v2/live on the Winkler DM
  test (i.e., it is narrower only because it is no better, or worse, at where the price
  actually lands) is not a clean win even if the three-part gate technically passes — the DM
  results are reported specifically to catch this.
- No result gates retuning `EWMA_LAMBDA_GRID`, `HS_WINDOW`, `REFIT_EVERY`, or any other
  constant after this freeze; any such change would be test-set tuning and could only be
  validated on the forward shadow (`scripts/run_fhs_shadow.py`), never on the retrospective
  reported in this PR.

## Alternatives considered

- **Bootstrap-resampled FHS paths** (draw many synthetic h-day paths from the pool of
  standardized residuals, take quantiles of the simulated path extrema) instead of the
  rolling-window historical-path construction actually used. Rejected for this pre-
  registration: it adds a resampling RNG (a new source of run-to-run variance to control for)
  for a benefit — smoother tail quantiles — that is speculative until the simpler, already-
  reviewed rolling-path construction (identical to `ml.weekly_range.base_range`'s own,
  differing only in what array it rolls over) has been measured.
- **A single conditional-vol estimator** instead of two (ewma and garch) competing variants.
  Rejected: GARCH and EWMA make different bias/variance trade-offs (GARCH mean-reverts
  toward a long-run level at longer horizons; EWMA extrapolates flat sqrt-time) and the
  brief specifically asks for both; reporting both, each against the full baseline set,
  costs little extra and settles which (if either) is preferable with real evidence instead
  of a guess.
- **An additional IBJA-only (no proxy) conditional-vol path**, since ADR 043 noted IBJA's own
  record is short (~170-300 days) for a 5-day path distribution. Rejected for now, same
  reasoning ADR 043 gave: not enough IBJA history for a stable filtered-residual quantile at
  the "week" horizon; worth revisiting after a year more data accrues.
- **A width hard gate at horizon "week" too**, invented fresh since no displayed weekly
  range exists to reference. Rejected: see "Width hard gate" above — this would not be
  "reusing ADR 047's gate", it would be a new, untested rule decided after seeing this ADR's
  own results were it introduced later, so it is deliberately left as a reported-only metric
  now, decided before any outcome was computed.

## Results

*Pending — computed after this pre-registration is committed and pushed (the freeze), from
`reports/fhs_ranges/results.json` (`scripts/analysis_fhs_ranges.py`). Appended as a follow-up
commit in this same PR, quoting the freeze commit SHA and this file's own SHA-256 at freeze,
per this repo's pre-registration standing rule.*
