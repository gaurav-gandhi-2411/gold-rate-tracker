# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

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

[Unreleased]: https://github.com/gaurav-gandhi-2411/gold-rate-tracker/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/gaurav-gandhi-2411/gold-rate-tracker/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/gaurav-gandhi-2411/gold-rate-tracker/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/gaurav-gandhi-2411/gold-rate-tracker/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/gaurav-gandhi-2411/gold-rate-tracker/releases/tag/v0.1.0
