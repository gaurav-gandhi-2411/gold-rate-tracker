# ADR 049 — Pre-registration: "Buy now or wait?" (F2)

**Status:** Accepted for shadow, 2026-09-24. **Frozen before any result was seen.** Shadow research
only: nothing a user sees changes until GG approves a separate promotion PR (ADR 036's pattern).
No F2 component has been evaluated on any day when this ADR is merged.

**Builds on:** ADR 041/043 (R1: historical simulation calibrated on real IBJA is the shape/scale
method for a price range), ADR 042 (the consecutive-publication-day rule for "N trading days"),
ADR 039/040 (R3: no buyer rule saves money reliably; direction is close to noise at this horizon).

---

## Context

The brief: a card that states the gamble honestly, not a prediction —

> "Waiting *N* days: prices are about equally likely to go up or down. This week they're moving
> more than usual — up to about ₹X/g either way."

Three things must be true before this ships: the "about equally likely" claim is measured, not
assumed; the "up to about ₹X" claim is a calibrated range with checked coverage; the "moving more
than usual" claim is a plain, descriptive volatility read. It must never imply a rule that saves
money (ADR 039/R3 already closed that door) or a directional edge (ADR 040).

**Why pre-register.** Same reason as ADR 039/042: freeze the method, the datasets, the tests, the
multiplicity correction and the copy rules in writing, before any test-period day is scored. Any
later deviation is a dated, logged amendment made before the affected data is scored.

## Decision

### Series and horizons (frozen)

- **Series.** Real IBJA `pm_916 / 10` (`data/ibja_rates.parquet`, via
  `ml.range_forecast.data.load_ibja_price_series` — drops carried-forward non-publication rows).
  INR proxy `label_22k_per_10g / 10` (`ml.inr_proxy_labels`, via
  `ml.range_forecast.data.load_proxy_price_series`), 2013-01-01 to date.
- **N = 1, 2 trading days.** A trading day is a real IBJA (or proxy) publication day. "N trading
  days later" is built only across a chain of **consecutive** publication days — ADR 042's rule,
  reused verbatim: each step's `np.busday_count(prev, next) <= MAX_WEEKDAYS_PER_STEP` (= 2, i.e. at
  most one weekday skipped — one holiday, not a hole). A window whose chain crosses a hole is
  dropped, never bridged. `ml.wait_or_buy.trading_day_windows` implements this directly against the
  price series (same rule as `ml.direction.dataset`'s `_consecutive`, applied without needing the
  snapshot-feature machinery that module also carries).
- **N = 7 calendar days.** ADR 043's own "week" window, reused unchanged:
  `ml.weekly_range.complete_windows(series, "week")`. The target day is the last real publication
  day inside `(t, t+7]`; a window crossing a hole in the record is never scored.

### (a) "About equally likely" — measured, not assumed

For each (series, N) pair: `indicator_t = 1[price_target < price_t]` over every window defined
above (ties, `price_target == price_t`, count as 0 — not lower — matching the direction dataset's
`label_binary` convention of `>` deciding the tie).

- **Point estimate:** mean(indicator).
- **Two intervals, both reported:**
  1. **Naive Wilson 95%** on the raw window count `n` (`ml.range_forecast.metrics.wilson_ci`) —
     ignores that consecutive windows overlap and share outcome days, so it is too narrow; reported
     for context only.
  2. **Overlap-corrected Wilson 95%** on the **effective n** from the HAC long-run variance of the
     indicator series around 0.5 (`ml.direction.evaluate_reframed.diebold_mariano_test(indicator,
     [0.5]*n, horizon=N, alternative="two-sided")`'s `effective_n`, with `successes = round(p̂ *
     effective_n)`). **This is the interval the copy rule reads** (see below).
  3. **Non-overlapping-windows check:** the same indicator subsampled at stride N (trading-day
     horizons) or stride 5 (the 7-day horizon — same "every 5th weekly window" convention
     `scripts/analysis_weekly_range.py` already uses), Wilson 95% on that genuinely-independent
     subsample. Reported as a corroborating check, not the deciding interval — its n is small.
- **Test, H0: P = 0.5, two-sided, HAC-corrected:** the same `diebold_mariano_test(..., alternative=
  "two-sided")` call used for the effective-n interval — `dm_stat`, `p_value`, `mean_diff`,
  `effective_n` all reported.
- **Multiplicity.** Family = 3 horizons × 2 datasets = 6 tests. Bonferroni threshold = 0.05/6 =
  0.00833. Benjamini-Hochberg reported alongside (`ml.direction.stats_corrections.bonferroni` /
  `benjamini_hochberg`).
- **Copy rule (mechanical, frozen):** say "about equally likely" only when the
  **overlap-corrected (effective-n) Wilson interval contains 0.50**. Otherwise the card states the
  measured figure instead — "prices were lower after *N* days about *K* times out of 10" (`K =
  ml.weekly_range.times_out_of_ten(mean(indicator))`, rounded down, the same mechanical rule ADR 043
  uses so no one chooses the number by hand). No one gets to pick which sentence half by judgement.

### (b) "Up to about ₹X/g" — calibrated endpoint range

This is a statement about where the price **lands** at t+N, not a path-containment promise (ADR
043's "stay between" claim is a different, stronger thing) — so it is measured as an **endpoint**
range: historical-simulation shape on the proxy (quantiles of the single N-day-ahead cumulative log
return, not the path min/max ADR 043 uses for its "stay between" claim), scaled by split conformal
on real IBJA. Same method, same constants (`LEVEL = 0.80`, `HS_WINDOW = 500`, `HS_MIN_SAMPLE = 60`,
`CAL_WINDOW = 250`, `MIN_CAL = 30`) as `ml.weekly_range` (ADR 043), reused directly:

- **N = 1 trading day.** `ml.weekly_range.walk_forward(proxy, ibja, "1d")` unchanged — a 1-day
  path and a 1-day endpoint are the same event by construction (there is only one day in the path).
- **N = 2 trading days.** New. `ml.weekly_range` gains two additive functions (existing names,
  `HORIZONS`, and every existing caller are unchanged — `scripts/analysis_weekly_range.py` and
  `scripts/run_weekly_range_shadow.py` still iterate only `HORIZONS = ("1d", "week")` and see no
  behaviour change):
  - `base_range_endpoint(proxy, as_of, k)` — 10th/90th percentile of the single k-day-ahead
    cumulative log return (`hist[k:k+n] - hist[:n]`), not `base_range`'s path min/max over days
    1..k.
  - `endpoint_windows(price, k)` / `walk_forward_endpoint(proxy, ibja, k)` — the same
    `Window`/split-conformal-scale machinery as `complete_windows`/`walk_forward`, with `days =
    (target_day,)` and `path_min == path_max ==` that single k-day return (so the existing `score`
    and `conformal_scale` functions work unchanged).
  - Called as `walk_forward_endpoint(proxy, ibja, 2)`.
- **N = 7 calendar days.** Reuses `ml.weekly_range.walk_forward(proxy, ibja, "week")` **as-is** —
  ADR 043's calibrated path range, not a new endpoint-only calibration. This is deliberate, not a
  shortcut: path-containment implies endpoint-containment (`hit_path ⟹ hit_endpoint`, since the
  final day is one of the path days), so the path range's measured coverage is a valid **lower
  bound** on the true endpoint coverage — safe for an "at least 8 in 10" promise. `X` for N = 7 may
  therefore be wider than a bespoke 7-day endpoint range would be; that is conservative, not wrong.
- **X = the larger absolute side in ₹, at today's price:** `lo_rs = P_t · (exp(s·lo) − 1)`,
  `hi_rs = P_t · (exp(s·hi) − 1)`, `X = max(|lo_rs|, |hi_rs|)`.
- **Coverage measured two ways, both against Wilson 95%, target 80%:**
  1. **Walk-forward** (every window's scale from windows that matured strictly before it).
  2. **Chronological hold-out** (scale frozen from the first half of scored windows, applied
     unchanged to the second half) — same construction as `scripts/analysis_weekly_range.py`.
  A cell **meets 80%** when its Wilson 95% CI contains or exceeds 0.80. Reported plainly either way
  — this is a report, not a gate with a pass/fail exit code.

### (c) "Moving more than usual" — descriptive, not a forecast

- **Today's realised volatility:** std of daily log returns over the most recent run of **20
  consecutive** real IBJA publication days (`ml.range_forecast.data.dense_segments`, `max_gap_days
  = 4`, its existing ADR 039/040/043 definition of "consecutive" for this file). `None` — no
  category — when fewer than 20 consecutive IBJA days are available ending at `as_of`.
- **Long-run reference distribution:** the same rolling-20-consecutive-day realised-volatility
  series computed on the **INR proxy** (2013 to `as_of`, exclusive of `as_of` itself) — the proxy is
  used for the reference distribution specifically because it has the multi-year depth a "long run"
  needs; real IBJA's own ~19-month dense record is too short to define one honestly. This is a
  frozen design choice (rule 54a autonomy), stated here before any result is seen.
- **Percentile:** the fraction of that **proxy** reference series' values, known strictly before
  `as_of`, that are ≤ today's IBJA realised volatility.
- **Category (fixed in code, `ml.wait_or_buy.vol_category`):** percentile < 25 → "calmer than
  usual"; percentile > 75 → "moving more than usual"; else → "about as usual". Purely descriptive —
  no directional or magnitude claim beyond "more/less/same movement than the recent past", and it
  is not scored for "accuracy" (there is no forecast to be right or wrong about).

### (d) No expected cost of waiting

The card never computes or shows an expected ₹ saving/cost of waiting. R3 (ADR 039) already found
no policy saves money reliably; showing an expected-cost number here — even framed as neutral
information — would silently re-introduce the "wait signal" R3 closed. `ml.wait_or_buy` and its
scripts contain no such computation, and the sentence template has no slot for one.

### Copy: the exact sentence, built only from computed values

```
Waiting {N} day{s}: {equal_or_measured_clause} {volatility_clause}
```

- `equal_or_measured_clause`:
  - if the effective-n Wilson interval contains 0.50: `"prices are about equally likely to go up
    or down."`
  - else: `"prices were lower after {N} day{s} about {K} times out of 10 (based on the last
    {years} years)."` (`K` = `times_out_of_ten`, `years` = the dataset's span rounded to the
    nearest year.)
- `volatility_clause`:
  - "calmer than usual": `"This week they've been calmer than usual."`
  - "about as usual": `"This week they've been moving about as usual."`
  - "moving more than usual": `"This week they're moving more than usual — up to about ₹{X}/g
    either way."`
  - `None` (fewer than 20 consecutive days): no volatility clause; the sentence ends after the
    first clause.
  - The ₹X figure is only ever attached to the "moving more than usual" category in the literal
    brief sentence; the calmer/normal categories state the read without a number, since "up to
    about ₹X" is the same calibrated range regardless of category (X is not conditioned on the
    category) and repeating it in all three would overstate the "unusual" framing that number is
    tied to in the brief's own example sentence. **Deviation flagged for GG's copy review in the
    promotion PR**, not decided unilaterally beyond shadow: an alternative is showing ₹X in every
    category, unconditioned on the read. Both are computed and available; the promotion PR picks.

No number in the sentence is hand-typed; every one comes from `data/wait_or_buy_today.json`'s
computed fields (`ml.wait_or_buy.build_sentence`).

## Forward shadow plan

`scripts/run_wait_or_buy_shadow.py` (added to `weekly-backtest.yml` in a follow-up PR GG wires,
same pattern as `run_weekly_range_shadow.py`): each run issues today's card numbers (per N) for
every real-IBJA publication day after `CONFIRMATORY_AFTER = "2026-09-24"` (this ADR's commit date)
that doesn't have an entry yet, using only data known that day, and scores every issued entry whose
window has since matured. Append-only — an issued entry is never recomputed, a score never changed.
Logs `"[adr049] issued=<n> scored=<n>"` per run.

**Success for promotion:** forward coverage of (b) — the calibrated endpoint range — consistent
with 80% (Wilson 95% CI contains or exceeds 0.80) over **≥ 8 weeks** of forward decision days, for
each of N = 1, 2, 7 on real IBJA. (a)'s forward point estimate and its effective-n interval are
reported alongside at that point, not gated — R3/R4 (ADR 039) already found no exploitable edge at
this horizon, so (a) is expected to keep landing near "about equally likely"; the promotion decision
rests on (b)'s coverage and GG's copy sign-off, not on (a) turning up an edge it isn't looking for.

## What would count as a deviation

Any change to a horizon, a dataset, the windowing rule, the copy-decision interval, the volatility
window/percentile method, the category thresholds, or the multiplicity family. Fixing a bug that
makes the code differ from this text is not a deviation — the text is the spec — and is logged in
the PR with before/after behaviour, before test-period data is scored.

## Alternatives considered

- **One shared sentence at a fixed N (e.g. always N = 2), no per-N measurement.** Rejected: the
  brief's own example names N = 2 but the underlying claims ("about equally likely", "up to about
  ₹X") depend on the horizon, and ADR 043 already established that these ranges widen sharply with
  N (its 7-day range is roughly 2.5× its 1-day width) — showing one N without checking whether the
  claims hold at the others would risk silently shipping copy that is right at one horizon and wrong
  at another. Measuring all three now is cheap and the promotion PR can still pick one N to show.
- **A single combined interval instead of naive + effective-n + non-overlapping.** Rejected: R3/R4's
  own effective-n construction (ADR 039/042) can itself be over- or under-conservative depending on
  how strong the true autocorrelation is; reporting three views (like ADR 043 reports walk-forward +
  hold-out + non-overlapping for its own coverage claim) is cheap and lets a reader see whether they
  agree, rather than trusting one construction blind.
- **Score endpoint coverage for N = 7 with a bespoke endpoint-only calibration**, mirroring N = 2.
  Rejected for now: reusing ADR 043's already-calibrated, already-shadow-running "week" path range
  is a valid conservative bound (see above) and avoids running a second parallel conformal
  calibration against the same small real-IBJA record ADR 043 already found width-unstable at 7
  days (n ≈ 24 honest non-overlapping weeks). Worth revisiting once more real IBJA history
  accumulates.
