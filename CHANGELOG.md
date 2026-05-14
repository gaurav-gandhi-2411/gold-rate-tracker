# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

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

[Unreleased]: https://github.com/gaurav-gandhi-2411/gold-rate-tracker/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/gaurav-gandhi-2411/gold-rate-tracker/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/gaurav-gandhi-2411/gold-rate-tracker/releases/tag/v0.1.0
