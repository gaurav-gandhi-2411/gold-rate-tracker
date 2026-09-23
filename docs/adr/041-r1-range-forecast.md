# ADR 041 — R1: Range/Volatility Forecast Research, and the IBJA Dense-Segment Fix

**Status:** In progress. Shadow research only (`ml/range_forecast/`,
`scripts/analysis_range_forecast.py`) — no promotion, no gate/user-facing change, nothing here
touches `ml/direction/gate.py`, `app.js`, `index.html`, `i18n.js`, or any published data file.

---

## Context

The product question: "how much could the 22K price move over the next N days?" — a central
80%/90% interval on the log return from day t to day t+h, for h = 1/5/10/20 trading days.

Two price histories are available: the INR 22K proxy label series
(`ml.inr_proxy_labels`, 2013–2026, ~3,638 trading days, genuinely daily throughout, but per ADR
032 only ~44–54% direction-reliable below ~50 Rs/gram moves) and real IBJA rates
(`data/ibja_rates.parquet`, 2022–2026, short but the actual product-relevant series). Seven model
families were implemented from scratch (no `arch`/`statsmodels` dependency, not in
`ml/requirements-inference.lock`): historical volatility, historical simulation, RiskMetrics EWMA,
GARCH(1,1) via scipy MLE (normal/Student-t), HAR-RV via least squares, LightGBM quantile
regression on the M1 driver set, and Chronos-Bolt-Tiny quantile forecasts — each with embargo-aware
walk-forward split conformal (CQR) calibration.

## Data caveat: IBJA is not daily before 2025-Q2

The first full-scale Actions run (workflow run `35894266384`, commit `0d188fd2`) surfaced a data
bug in the original design: `data/ibja_rates.parquet` is **not daily** before 2025-Q2 — median gap
5–18 calendar days, and 2026-Q1 has exactly one row. The original IBJA arm treated consecutive
rows as consecutive trading days, so a nominal "1-day" horizon on IBJA often silently spanned
weeks. This inflated coverage and shrank width in a way that looked like a genuine result but
wasn't: `historical_vol` raw IBJA h1 80% measured 94.1% coverage at 6.27% width — a forecast that
appears both extremely safe and extremely tight, the classic signature of scoring against an
outcome that had far more time to happen than the label implied.

This is the same failure shape independently found and fixed in R3 (`scripts/
analysis_buyer_policy.py`, commit `d092c342`, "R3 scores real IBJA only on dense (daily)
segments") and documented in ADR 039/040 — the same underlying file, hit by two independent
analyses on the same day.

## Decision

**Dense segments.** `ml.range_forecast.data.dense_segments` splits the IBJA series wherever the
calendar gap between consecutive rows exceeds `MAX_GAP_DAYS = 4` (a weekend plus a holiday) —
identical logic to `scripts/analysis_buyer_policy.py`'s `dense_segments`, so the same fix isn't
reinvented a third time if a third analysis needs it.

**New IBJA protocol** (product question: do proxy-fitted forecasts work on real IBJA prices?):
every model forecasts **only** from the daily proxy series — IBJA is never walked forward
independently, which also retires the earlier `min_train_size_ibja` warm-up workaround (IBJA no
longer needs its own warm-up at all, since it doesn't get its own walk-forward). The identical
proxy-fitted forecast (same interval bounds, same `scale`) is then **rescored** against the real
IBJA h-day log return, kept only for as-of days that are genuine IBJA rows whose h-IBJA-**row**-
ahead window (h counted in rows within one dense segment, not calendar or trading days) never
crosses a segment boundary. `ml.range_forecast.data.score_against_ibja` implements this; it copies
bounds/scale unchanged from the proxy forecast and only changes the row subset, the actual return,
and `current_price` (now the real IBJA price, for honest Rs/gram width reporting).

**IBJA-arm conformal calibration** uses matured IBJA-scored CQR errors (same embargo rule as the
proxy arm: a forecast matures only once `position + horizon <= current position`, in IBJA global
row-index units), falling back to the proxy arm's own calibrated bounds for the same forecast date
when fewer than `IBJA_FALLBACK_MIN_N = 30` IBJA-scored errors have matured yet.
`ml.range_forecast.conformal.apply_ibja_fallback` implements this and records which source was
used per forecast: `"ibja"`, `"proxy_fallback"`, or `"insufficient_history"` (neither arm had
enough matured errors — a genuine early-warm-up gap, not an error).

## Consequences

- The "ibja" dataset entry in every shard's output is now a *subset* of the "proxy" entry's
  forecast dates (only genuine IBJA rows, only where the window fits inside one dense segment),
  not an independently-produced series. `n` per (model, horizon, level) on the IBJA arm is
  reported in the aggregate output and is materially smaller than the proxy arm's `n` — this is
  expected and correct, not a bug.
- Efficiency side-effect: `quantile_gbm` no longer fetches macro features twice (once per
  dataset) and `chronos` no longer runs a second, fully independent inference pass over IBJA —
  both shards get faster as a direct consequence of the correctness fix, not a separate
  optimization.
- A verified local smoke run (`--smoke --max-rows 500`, all 7 shards + aggregate) after this fix
  shows IBJA raw coverage in the 84–99% range at nominal 80/90% (still somewhat conservative, a
  genuine model-calibration property now, not a data-alignment artifact) and a plausible
  `source` mix (mostly `"ibja"` once warmed up, some `"proxy_fallback"` during the early history,
  a handful of `"insufficient_history"` at the very start) — see the module docstrings in
  `ml/range_forecast/data.py` and `ml/range_forecast/conformal.py` for the exact mechanics.
- 12 new unit tests (`tests/test_range_forecast.py`) cover: dense-segment splitting on a
  synthetic gappy series, that no scored window crosses a segment boundary, that h is counted in
  IBJA rows (not calendar days — a 3-calendar-day gap that is only 1 row apart is correctly
  included), that raw bounds are copied unchanged from the proxy forecast, and the three-way
  conformal fallback source selection.

## Results

**Provenance:** GitHub Actions run **35898934718** at `1ca52d17`, all 7 shards plus the aggregate.
Report: `reports/r1_range_forecast_run_35898934718.json`. The test family has 320 DM tests
(Bonferroni 0.000156) plus BH. Success means nominal coverage (Kupiec not rejected and inside the
binomial CI) **and** a significantly lower Winkler score than historical volatility.

**In plain words: on real IBJA prices, nothing forecasts "how much could the price move" better
than simple historical baselines.**

### Real IBJA (product-relevant; dense segments 2025–26; n = 194 at 1 day, 159 at 5, 133 at 10, 83 at 20)

- Raw historical volatility **over-covers** on IBJA: 91.8% at nominal 80% for 1 day, 100% at 90%
  for 10 days. Most models inherit this, because they learn their scale from the INR proxy, which
  is noisier day to day than real IBJA (ADR 040, D4).
- **Best interval score at 1 day:** historical simulation (empirical quantiles), 80%: coverage
  85.6%, width 3.34%, Winkler 0.042. No model beats it.
- **One Bonferroni-significant cell, and it doesn't hold up.** Chronos, uncalibrated, 10 days,
  90%: coverage 92.5% (Kupiec not rejected), width 12.9% vs 17.4%, Winkler 0.135 vs 0.174,
  p = 2 × 10⁻⁷, effective n 31.
  - It beats only the over-wide historical-vol baseline.
  - Against historical simulation, which is reasonably calibrated here, p = 0.76.
  - Not a robust improvement: this is not a promotion candidate.
- Conformal calibration on IBJA errors mostly falls back to proxy errors in the early folds. It
  doesn't bring 1-day coverage to nominal (80% rows at 83–92%).

### INR proxy (2013–2026, daily, n ≈ 3,300–3,560)

- **Conformal calibration works:** conformalized models are within the binomial CI at almost
  every horizon and level. Raw historical volatility over-covers (82–93%).
- **Only at 1 day does anything beat historical volatility after Bonferroni: GARCH(1,1).**
  - 80%: coverage 79.8%, width 2.58% vs 2.76%, p = 0.00013.
  - 90%: coverage 89.8%, p = 0.00004.
  - The gain is small, about 2% on the Winkler score.
  - HAR at 90% is BH-significant only (p = 0.00087).
- From 5 days out, no model beats historical volatility. Chronos never does on the proxy
  (p ≈ 1 at 1 day).

### What this means for the product

The site's range and volatility notes can keep a simple baseline. Historical simulation is the
most honest cheap choice on real IBJA at short horizons. A GARCH-style 1-day range is the only
model-based improvement found, only on the proxy, and small. Nothing here is a promotion
candidate. A proper real-IBJA test needs more dense days, which accrue daily from 2025-Q2.

## Alternatives considered

- **Give IBJA its own shorter warm-up and keep walking it forward independently** (the immediate
  prior fix, superseded by this ADR). Rejected: it fixed the "near-zero forecastable days" symptom
  but not the root cause — IBJA still had genuinely gappy data, so even a shorter independent
  walk-forward would keep mislabeling multi-week gaps as 1-day horizons.
- **Drop the IBJA arm entirely and report proxy-only results.** Rejected: the task brief is
  explicit that "the real-IBJA results are the product-relevant ones" — the whole point of R1 is
  answering whether forecasts hold up on real prices, not just on the proxy's own (noisier, per
  ADR 032) shape.
- **A continuous recency-weighted blend of proxy and IBJA calibration instead of a hard 30-error
  fallback threshold.** Simpler to reason about and matches the task brief's literal instruction
  ("falling back ... if fewer than 30 IBJA errors have matured"); a smooth blend is a reasonable
  future refinement if the hard cutoff produces a visible discontinuity in the real report, but
  isn't justified before that evidence exists.
