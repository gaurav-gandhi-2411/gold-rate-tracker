# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Removed
- `ml/commentary.py` (Groq-generated "Today's read" blurb) — retired, no remaining consumer once the PWA moved to a deterministic client-side synthesis. See `docs/PROGRESS.md` Decision Log, 2026-08-10.

---

## [0.6.0] — 2026-05-15 — Phase 4: Ship (monitoring, error tracking, OG image)

Operational layer: UptimeRobot monitoring docs, Sentry browser error tracking,
auto-generated OG social image, deferred-domain runbook, and README polish.
No model changes, no frontend logic changes, no CI data pipeline changes.

### Added

- **OG / Twitter meta tags** in `index.html` — `og:title`, `og:description`,
  `og:image`, `og:type`, `og:url`, `twitter:card`, `twitter:title`,
  `twitter:description`, `twitter:image`. Image URL points to `/og.png` on
  the GitHub Pages domain.
- **Sentry browser SDK** in `index.html` — loaded from
  `cdn.jsdelivr.net/npm/@sentry/browser@7/build/bundle.min.js`. Initialised
  with `sampleRate: 1.0`, `tracesSampleRate: 0.0`, `environment: "production"`.
  Placeholder DSN documented with a `// TODO:` comment; site degrades silently
  if DSN is not replaced (all calls guarded by `typeof Sentry !== 'undefined'`).
- **Sentry captureException** calls in `app.js` at three fetch error paths:
  (a) critical prices fetch failure, (b) forecast fetch failure (with URL
  context before null-return), (c) optional data batch (backtest, commentary,
  drift) — rejected results reported individually with URL context.
- **`og.html`** — self-contained 1200×630 OG card page. Fetches
  `data/prices.json` + `data/forecast.json`, computes verdict with the same
  three-bucket rules as `app.js`, renders price (large, gold serif), verdict
  badge, SVG sparkline, and site URL. Sets `data-loaded="true"` on `<body>`
  when rendering is complete (used by Playwright to know when to screenshot).
- **`scripts/screenshot-og.mjs`** — Playwright script that serves the site,
  waits for `data-loaded`, and saves `og.png` at 1200×630. Accepts an optional
  base URL argument (`node scripts/screenshot-og.mjs http://localhost:8080`).
- **`.github/workflows/generate-og-image.yml`** — triggered by
  `workflow_run` on each successful `check-price.yml` run. Installs Playwright,
  serves the repo with Python `http.server`, screenshots `og.html`, and commits
  `og.png` to master with `[skip ci]`. Uses `git pull --rebase` before push
  (same guard as `weekly-backtest.yml`) to prevent non-fast-forward rejections.
- **Monitoring section** in `README.md` — documents the three UptimeRobot
  monitors (site uptime, forecast.json keyword, prices.json keyword) and the
  ntfy.sh webhook alert channel.
- **Error tracking section** in `README.md` — documents how to activate Sentry
  by replacing the placeholder DSN.
- **Honesty section** in `README.md` — states current model vs naive status,
  naive-blend behaviour, and quarterly review schedule. Includes "Not financial
  advice" disclaimer.
- **Custom domain (deferred)** section in `docs/RUNBOOK.md` — step-by-step
  instructions to buy a domain on Cloudflare, configure CNAME, add `CNAME`
  file to repo, and enable HTTPS in GitHub Pages. No purchase made.

### Changed

- **README.md** — full rewrite under 200 lines. Replaced mermaid diagram with
  3-bullet "How it works" text. Removed verbose MLflow tracking section and
  detailed features table (content lives in docs/). Kept setup, tweaking,
  troubleshooting, and ADR links. Added live URL prominently in the header.

### Notes

- `og.png` does not exist in the repo until the first successful
  `generate-og-image.yml` run (triggers automatically after the next
  `check-price.yml` success).
- Social platforms cache OG images by URL. To force a re-fetch after the image
  is first generated, use the Twitter Card Validator or Facebook Sharing
  Debugger (links in README troubleshooting section).
- Sentry is fully inert until the placeholder DSN is replaced. No events are
  sent to Sentry during this time.

---

## [0.5.0] — 2026-05-15 — Phase 3: Production hardening

Pipeline monitoring, safety nets, and operational docs. No model changes, no
feature changes, no UI redesign beyond the single forecast staleness banner.

### Added

- **Forecast staleness banner** — amber banner in `index.html`/`app.js`/`style.css`
  shown when `forecast.json`'s `predicted_at` is >18h old. Hides automatically
  once CI delivers a fresh forecast.
- **Forecast staleness CI monitor** — "Forecast staleness monitor" step in
  `check-price.yml` sends ntfy.sh alert when forecast age >18h.
- **Macro cache age CI guard** — "Check macro cache age" step in `check-price.yml`
  reads `data/macro_status.json` (written by `load_macro_features()`), patches
  `macro_cache_age_days` into `data/forecast.json`, and fails CI (non-zero exit)
  if `cache_age_days > 14`. Logs WARNING if >7d.
- **`data/macro_status.json`** — written by `ml/macro.py:load_macro_features()`;
  fields: `cache_age_days`, `cache_exists`, `warn_threshold_days=7`,
  `fail_threshold_days=14`.
- **Groq commentary fallback** — `ml/commentary.py` now falls back to the last
  good commentary.json entry (with `commentary_age_hours` + `fallback: true` fields)
  instead of writing nothing on API failure.
- **Weekly-backtest rebase guard** — `git pull --rebase origin master` before
  `git push` in `weekly-backtest.yml` prevents non-fast-forward rejections when
  `check-price.yml` commits concurrently.
- **Ensemble EPS regression tests** — 4 new tests in `tests/test_ensemble.py`
  guard `_EPS >= 1.0`, near-zero MAE weight clamping, `_FLOOR_WEIGHT == 0.1`,
  and dominant-model floor invariant.
- **XSS safety audit comments** — inline comments at all 7 `innerHTML` sites in
  `app.js` confirm no LLM/external data reaches innerHTML.
- **`.gitattributes`** — marks `models/**/*.txt` and `models/**/*.onnx` as binary,
  preventing git CRLF conversion from corrupting LightGBM model files (KI-001).
- **`docs/KNOWN_ISSUES.md`** — documents KI-001 (STATUS_STACK_BUFFER_OVERRUN root
  cause, fix, and verification steps).
- **`tests/test_model_load.py`** — regression tests asserting no CRLF in model
  files and `lgb.Booster()` loads >0 trees for all production `.txt` models.
- **`scraper-canary.yml`** — weekly cron (Mon 03:00 UTC) runs Playwright against
  live Tanishq page; sends ntfy alert + opens GitHub issue on failure.
- **Canary checks in `scraper/test_scrape.js`** — 3 additional test cases: price
  in range, 22K/24K ratio, 18K/24K ratio (mirrors `scrape.js` validation thresholds).
- **`docs/RUNBOOK.md`** — Phase 3 ops sections: staleness alert guide, manual
  scraper re-run, roll-back bad `forecast.json` commit, contact/escalation via ntfy.sh.

### Calendar reminders — quarterly model honesty check

The minimal_v2 feature set was validated at ~120 real readings (as of 2026-05-15).
Re-run the Phase 2.5a A/B/C comparison when data volume increases:

- **~2026-07-15** (~200 real readings) — first re-run; decision may flip if
  macro features become more informative with more data.
- **Quarterly thereafter** — re-run `python ml/compare_feature_sets.py` and
  update this CHANGELOG with the outcome. If minimal_v2 still wins, no action.
  If a larger set wins by >3 pp dir-acc on |Δ|>₹50, update `active_set` in
  `configs/data/default.yaml` and retrain.

Command: `python ml/compare_feature_sets.py`

---

## [0.4.1] — 2026-05-15 — Phase 2.5a: Feature-set A/B/C comparison

Honest scoreboard for three candidate feature sets under identical bd602a6
hyperparams (num_leaves=16, lr=0.02, min_data_in_leaf=40, lambda_l2=1.0).
No changes to inference, frontend, or CI.

### Added

- **`TUNED_V1_FEATURE_COLS`** in `ml/features.py` — 40-feature set: `ALL_FEATURE_COLS`
  minus the 4 dead-weight features from `docs/FEATURE_INVENTORY.md`
  (`hour`, `akshaya_tritiya`, `dhanteras`; `regime` was never in `ALL_FEATURE_COLS`).
- **`_LGB_TUNED`** in `ml/forecast.py` — bd602a6 regularized params dict:
  `num_leaves=16, lr=0.02, min_child_samples=40, reg_lambda=1.0, colsample_bytree=0.6`.
  `n_estimators=500` approximates the 2000-iter + early_stop=100 effective budget
  without a validation split in the backtest harness.
- **`_make_lgb_tuned()`** in `ml/forecast.py` — companion to `_make_lgb()`, uses
  `_LGB_TUNED` params.
- **`ml/compare_feature_sets.py`** — standalone comparison runner: loops over all
  three feature sets, prints side-by-side markdown table with primary/secondary/
  paired-diff stats, applies the decision rule, prints winner + rationale.
- **`run_backtest()` extensions** in `ml/backtest.py`:
  - `feature_cols_override` param — replaces hard-coded `MINIMAL_FEATURE_COLS`.
  - `use_tuned` param — switches between `_make_lgb` and `_make_lgb_tuned`.
  - Stratified direction accuracy: `direction_acc_big_move` (|Δ|>₹50) and
    `direction_acc_small_move` (|Δ|≤₹50) in the returned dict.
  - Per-fold MAE std (`mae_std`) for uncertainty reporting.
  - Rolling blend weight simulation: `blend_weight_lgbm_mean/std` per feature set.
  - Paired fold differences (model − naive): median + IQR [25, 75].

### Results (69 / 61 / 61 folds, bd602a6 params)

| Metric                          | full_v1 (44) | tuned_v1 (40) | **minimal_v2 (8)** |
|---------------------------------|-------------:|--------------:|-------------------:|
| Folds completed                 |           61 |            61 |                 69 |
| Dir-acc overall                 |        42.6% |         42.6% |          **46.4%** |
| Dir-acc \|Δ\|>₹50               |        48.9% |         51.1% |          **56.9%** |
| n folds (big-move bucket)       |           45 |            45 |                 51 |
| MAE model (Rs)                  |        194.5 |         194.7 |          **167.3** |
| MAE model std (Rs)              |        178.8 |         178.7 |              153.3 |
| MAE naive (Rs)                  |        172.7 |         172.7 |              165.6 |
| MAE ratio (model/naive)         |        1.126 |         1.127 |          **1.010** |
| MAPE model (%)                  |         1.37 |          1.37 |           **1.18** |
| blend_weight_lgbm (mean ± std)  | 0.466 ± 0.084| 0.468 ± 0.084 |    **0.492 ± 0.044** |
| Paired diff median (Rs)         |        18.12 |         19.52 |          **12.35** |
| Paired diff IQR [25,75] (Rs)    | [−43, +93]   | [−40, +98]    |   **[−32, +29]**   |

### Decision

**Winner: `minimal_v2` — retains current default in `configs/data/default.yaml`.**

Decision rule a (highest dir-acc on |Δ|>₹50 bucket): minimal_v2 scores 56.9%,
tuned_v1 51.1%, full_v1 48.9%. Gap of 5.8 pp over 2nd place exceeds the 3 pp
noise floor. Rule a is decisive — no tiebreaks needed.

**Phase 2 was not over-pruned.** Dropping from 44→8 features was correct:
the regularized tuned params with minimal_v2 achieve a MAE ratio of 1.010
(essentially matching naive), while full_v1 and tuned_v1 trail naive by 12-13%.
The smaller feature set leaves the model less room to overfit daily noise.

**Fold discrepancy note:** full_v1 and tuned_v1 completed 61/69 folds (8 skipped
due to NaN macro features in early folds). minimal_v2 ran on the full 69-fold set,
including the 8 harder folds. The win holds despite the harder evaluation set.

**`tuned_v1` finding:** Dropping the 4 dead-weight features from `full_v1` made
no measurable difference (MAE 194.5 vs 194.7, dir-acc identical at 42.6%). This
validates the FEATURE_INVENTORY.md analysis: these features were truly dead weight,
but their removal doesn't unlock signal — the bottleneck is data volume, not
feature engineering at this scale.

---

## [0.4.0] — 2026-05-15 — Phase 2: Model simplification & honest forecast

ML-only changes. No changes to `index.html`, `app.js`, `style.css`, or manifest.
All `data/*.json` schemas remain backward-compatible (new fields only).

### Added

- **`MINIMAL_FEATURE_COLS`** in `ml/features.py` — 8-feature set (`lag_1`, `lag_7d`,
  `roll_7d_mean`, `roll_30d_mean`, `gold_usd`, `usd_inr`, `regime`, `dow`) at
  45:1 row/feature ratio vs prior 8.3:1 (44 features on 367 rows).
- **`roll_30d_mean`** added to `build_feature_matrix()` — 30-day rolling mean,
  right-aligned on UTC calendar date. Required by minimal_v2 feature set.
- **`features.active_set: "minimal_v2"`** in `configs/data/default.yaml` — config
  flag selecting the active feature set; `"full_v1"` retains the old 44-feature path.
- **Naive-blend safety net** in `ml/inference.py` — blends LightGBM and naive
  (delta=0) forecasts using rolling inverse-MAE weights (eps=1.0, clamp [0.1, 0.9]).
  Surfaces `blend_weight_lgbm`, `blend_weight_naive`, `lgbm_pred_raw`,
  `naive_pred_raw` in `forecast.json`.
- **Conformal PI calibration** in `ml/inference.py` — holds back last ~20% of
  training rows as calibration set; sets PI = blended_pred ± 80th-percentile
  of `|residual|` on that set. Surfaces `conformal_pi_half`,
  `pi_coverage_80_empirical`, `pi_coverage_80_calibrated` in `forecast.json`.
- **`seed_calibration_scale`** in `forecast.json` — the multiplicative factor
  applied to seed data at the boundary with live data (via `_calibrate_seed`).
- **`test_calibrate_seed_scale_in_range`** in `tests/test_forecast.py` — asserts
  `scale_factor` in `[0.85, 1.15]` for realistic seed-to-live divergence.
- **Inline model retraining** in `ml/inference.py` — model retrains on all data
  at each 6h CI run using `minimal_v2` feature set; saves refreshed `lgbm.txt`
  and `lgbm-meta.json` to `models/production/`.

### Changed

- **`ml/inference.py:main()`** restructured — replaces load-pre-trained-model path
  with inline train+calibrate+predict path. TFT/N-BEATS helpers kept for Phase 6.
- **`ml/forecast.py:_calibrate_seed()`** now returns `(list[dict], float)` tuple.
  `load_combined_history()` unpacks and ignores the scale; scale threaded through
  `inference.py` explicitly.
- **`ml/ensemble.py:compute_weights()`** — added `_EPS = 1.0` to MAE before
  inversion, preventing near-zero division blow-up (issue #11).
- **`ml/backtest.py`** updated to use `MINIMAL_FEATURE_COLS` and accept optional
  `macro_df`; loads macro once and passes per fold.
- **`MIN_REAL_READINGS_FOR_WARMUP_CLEAR`** raised from 30 → 100 in `ml/inference.py`
  to reflect the larger real-data corpus needed before the warmup flag clears.

### Backtest comparison (walk-forward, last 90 days)

| Metric          | full_v1 (44 feat) | minimal_v2 (8 feat) |
|-----------------|------------------:|--------------------:|
| Model MAE (Rs)  |           247.36  |               237.2 |
| Model MAPE      |            1.75%  |               1.64% |
| Dir-accuracy    |           54.24%  |               49.3% |
| Baseline MAE    |           186.41  |               165.6 |
| Folds           |               59  |                  69 |

### Honest assessment

The model still **trails the naive baseline** on MAE (237 vs 166). Reducing features
cut the overfit penalty (247 → 237) but did not close the gap. The naive-blend
safety net means the live forecast now hedges toward last-value (w_lgbm ≈ 0.50 at
first run), capping the worst-case downside. Conformal PI is wide (~±294) and
honest — coverage equals 80% by construction on the calibration set. Direction
accuracy (49%) is near coin-flip; the model has learned no durable directional
signal at daily granularity with 65 real readings. Improvement requires either
more data (>200 real readings) or a different signal source.

---

## [0.3.0] — 2026-05-15 — Phase 1: UI overhaul ("Should I buy today?")

Frontend-only reframe from ML report card to buyer-facing verdict. No changes to
`ml/`, `scraper/`, `.github/workflows/`, or any `data/*.json` schemas.
No new fields added to `forecast.json`.

### Added

- **`computeVerdict(prices, forecast)`** in `app.js` — deterministic three-bucket
  classification (down / flat / up) using 7-day slope ±₹100 threshold confirmed
  by a second signal (forecast direction or 30d mean deviation). Documented with
  inline comments explaining each bucket and the dual-signal rationale.
- **Verdict banner** — most prominent new element inside the hero card. Shows
  icon + headline + 1-sentence reason. Color-coded: green (down/buyer-favorable),
  amber (flat), red (up/buyer-unfavorable). `data-type` attribute drives CSS.
- **7-day SVG sparkline** — pure SVG (no Chart.js), 300×56 viewBox, polyline +
  gradient fill, color-matched to trend direction. Includes `aria-label` with
  direction and ₹ change for screen readers.
- **Decision-anchored comparison cards** — 3-up grid: vs 7d avg, vs 30d avg,
  vs period low. `data-sentiment` attribute (good / caution / neutral) drives
  color. All values derived from `prices.json` — no new data files.
- **"Today's change"** in hero — compared to earliest IST-day reading (not just
  previous reading), with ↑/↓ arrow, amount, and "today" label.
- **Compact karat strip** — 24K and 18K displayed as a lean 2-up grid beneath
  comparison cards. Replaces the old 3-card rate section.
- **Methodology `<details>` accordion** — all ML content (verdict rules, forecast
  point estimate + PI, backtest stats, live drift, model status banners) moved
  inside. Keyboard-accessible by default (`<details>/<summary>`).
- **Mobile history card list** — at <640px, table is hidden and a `<ul>` of
  cards replaces it: timestamp left, 22K price right (serif), delta below. No
  horizontal scroll.
- **Utility row** (sticky top) — freshness pill on left with three states:
  green "Updated Xm ago", amber "Stale — Xh ago", red "Stuck — Xh+ ago".
  Location label "Bengaluru · Tanishq retail" on right.
- **Skeleton shimmer** — 5 placeholder elements animate while hero data loads,
  hidden once `renderHero` fires.
- **Commentary always visible** — card never hidden. Shows last good commentary
  with "(from Xh ago · may be stale)" label in red if >12h old. `textContent`
  (not `innerHTML`) used to prevent XSS from LLM output.

### Changed

- **Palette** — full umber-gold spectrum: `--ink` → `#1A1612`, `--gold` →
  `#D4932A` (dark mode), hero price now gold-coloured not cream. Light mode
  overrides via `@media (prefers-color-scheme: light)` with `--gold-deep: #633806`.
- **Section order** — Utility row → Hero (price + today's change + verdict +
  sparkline) → Comparison cards → Karat strip → Commentary → Chart → History →
  Methodology accordion → Footer.
- **Chart restyled** — updated gold line `#D4932A`, grid `#3A3028`, tooltip bg
  `#241E16` to match new palette.
- **All ML jargon removed from default view** — "walk-forward", "HMM regime",
  "ensemble-inv-mae", "real-data track", "model drift 7d MAE" only appear inside
  the methodology accordion.
- **`renderCommentary`** always renders (no `hidden = true` fallback).
- **Range-toggle buttons** — `aria-selected` attribute updated on click for
  screen reader state.

### Removed

- Old rate-cards 3-up section (22K/24K/18K equal grid) — 22K moved to hero,
  24K/18K moved to karat strip.
- Forecast section as standalone visible card — moved into methodology accordion.
- Model accuracy, model performance, model drift as always-visible sections —
  all moved into methodology accordion.
- Warmup banner and model-status banner from sticky utility row — both now live
  inside methodology accordion.

---

## [0.2.0] — 2026-05-12

Full UI redesign across five phases (U1–U5). No changes to the data pipeline,
ML model, or API contract. All `data/*.json` files remain backward-compatible.

### Added

- **CSS custom-property scale** — spacing (`--space-1` through `--space-10`),
  type (`--ts-xs` through `--ts-disp`), and radius (`--radius-sm/md/lg`) tokens
  used throughout all subsequent phases.
- **`font-feature-settings: "tnum" 1, "lnum" 1`** on all price-displaying
  elements (`.price`, `.rate-card .rate`, `.forecast-price`, `.history`) for
  true tabular numerals alongside the existing `font-variant-numeric`.
- **"Model drift" section** — renamed from "Live performance", now carries a
  plain-language subtitle ("This week's forecast accuracy vs training baseline")
  and de-clinicalised stat labels ("7-day error (MAE)", "Baseline error (MAE)").
- **History "Show all (N)" toggle button** on mobile — history container is
  capped at 420 px / ~5 rows with overflow scroll; button expands to full list.
- **`docs/screenshots/after-u5/`** — full 10-viewport Playwright screenshot set
  (320×568 through 1920×1080) plus a Dynamic Island simulation at 393×852 with
  `env(safe-area-inset-top)` = 59 px injected.
- **`docs/UI_AUDIT.md`**, **`docs/DESIGN.md`**, **`docs/UI_PLAN.md`** — design
  system documentation, per-viewport audit (16 issues catalogued), and phased
  implementation plan.

### Changed

- **Section order** — Commentary now precedes Forecast (scene-setting →
  prediction). Model drift section moved below the chart (diagnostic, not
  primary). New order: Hero → Rate cards → Commentary → Forecast → Chart →
  Model drift → History → Model performance → Footer.
- **Hero price clamp** — consolidated from two separate rules to a single
  `clamp(56px, 16vw, 160px)` covering all viewports; cap hits at ~1000 px.
- **18K rate card on mobile** — changed from `grid-column: 1 / -1` (full-width
  hero row) to `grid-column: auto`; all three karat cards are equal-width.
- **Chart sizing** — replaced fixed `height: 320px` with `aspect-ratio: 16/5`
  on desktop and `aspect-ratio: 4/3` on ≤540 px; Chart.js axis tick font set
  to 11 px.
- **History table** — desktop column widths pinned (When: 40%, numeric cols:
  15%); row padding increased to `var(--space-4) var(--space-5)` (16 px 20 px).
- **Section bottom margins** — all sections unified to
  `clamp(var(--space-7), 8vw, var(--space-8))` (32–48 px); previously three
  inconsistent patterns (clamp(40,8vw,72), 60 px fixed, clamp(32,6vw,56)).
- **Freshness pill** — sole "updated" timestamp display; duplicate topbar
  `#updated` element removed from HTML and `app.js`.
- **Live-perf h2 size** — standardised to `clamp(22px, 3.5vw, 32px)` matching
  all other section headings (was `clamp(18px, 3vw, 22px)`).
- **Commentary meta** — colour promoted from `--cream-mute` to `--cream-dim`
  for stronger contrast at 11 px.
- **`main` padding-bottom** — changed from `80px` to
  `calc(var(--space-7) + env(safe-area-inset-bottom, 0px))` (32 px + home
  indicator clearance).
- **Footer** — `padding-top` tokenised to `var(--space-6)`; `padding-bottom`
  set to `calc(var(--space-5) + env(safe-area-inset-bottom, 0px))` so content
  clears the iPhone home indicator.
- **Model performance section** — `margin-bottom` changed from `60px` fixed to
  `clamp(var(--space-7), 8vw, var(--space-8))` for consistency.
- **Forecast card border** — kept as `1px dashed var(--gold-soft)` (deliberate
  uncertainty signal; the only dashed border in the UI). Rationale documented
  in `docs/DESIGN.md` D1.

### Fixed

- **WCAG AA failure** (A1) — `--cream-mute` lifted from `#8a8273` (4.33:1 on
  `--surface-2`) to `#9a9282` (4.86:1); passes AA at 11 px. Commentary meta
  further promoted to `--cream-dim` (9.5:1).
- **iOS Safari URL-bar layout jump** (A3) — `min-height: 100vh` supplemented
  with `min-height: 100dvh` as progressive enhancement.
- **Freshness pill landscape bleed** (A6) — pill negative margins now include
  `env(safe-area-inset-left/right, 0px)` so the pill bleeds correctly in
  landscape on notched devices.
- **Safe-area double-stacking** — removed `padding-bottom: env(safe-area-inset-bottom)`
  from the `body` safe-area block; inset is now applied once, on `main`.
- **Rate card floor at 320 px** (A5) — `@media (max-width: 360px)` sets
  `.rate-card .rate { font-size: 26px }` to prevent potential clipping on
  iPhone SE (1st gen).
- **360 px breakpoint gap** — new `@media (max-width: 360px)` block added;
  previously the smallest breakpoint was 540 px.

---

## [0.1.0] — 2026-04-28

Initial working release: LightGBM forecaster, Groq LLM commentary, ensemble
weighting, champion/challenger model gate, live drift monitoring, GitHub Actions
pipeline, PWA manifest.

[Unreleased]: https://github.com/gaurav-gandhi-2411/gold-rate-tracker/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/gaurav-gandhi-2411/gold-rate-tracker/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/gaurav-gandhi-2411/gold-rate-tracker/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/gaurav-gandhi-2411/gold-rate-tracker/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/gaurav-gandhi-2411/gold-rate-tracker/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/gaurav-gandhi-2411/gold-rate-tracker/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/gaurav-gandhi-2411/gold-rate-tracker/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/gaurav-gandhi-2411/gold-rate-tracker/releases/tag/v0.1.0
