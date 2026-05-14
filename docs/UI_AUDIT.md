# UI Audit — Before Screenshots

Audit date: 2026-05-12
Screenshots: `docs/screenshots/before/`
Method: Playwright headless Chromium, iOS UA for ≤430px viewports, deviceScaleFactor 2× on mobile

---

## Summary of issues by severity

| # | Issue | Severity | Viewports |
|---|-------|----------|-----------|
| A1 | `--cream-mute` on `--surface-2` fails WCAG AA at 11px | **Critical** | all |
| A2 | Live perf `h2` uses different type scale than every other `h2` | **High** | all |
| A3 | `min-height: 100vh` — iOS Safari URL bar causes layout jump | **High** | 320–430 |
| A4 | 18K rate card promoted to full-width "hero row" on mobile | **High** | 320–430 |
| A5 | Rate card numbers tight at 320px (133px column, Fraunces 32px, 6-char value) | **High** | 320 |
| A6 | Freshness pill negative-margin bleed breaks in landscape safe-area | **Medium** | 320–430 |
| A7 | Duplicate "updated" timestamp — pill + topbar both visible in viewport | **Medium** | all |
| A8 | History card layout on mobile takes disproportionate vertical space | **Medium** | 320–430 |
| A9 | Section order (commentary after rate cards, before forecast) breaks hierarchy | **Medium** | all |
| A10 | "Live performance" label is opaque — users won't know it's drift/MAE | **Medium** | all |
| A11 | Forecast card flex layout: right meta column not aligned on 430px | **Low** | 430 |
| A12 | Chart fixed height (320px) doesn't adapt to aspect ratio on wide viewports | **Low** | 1280–1920 |
| A13 | Content capped at 980px — background gradient is viewport-relative, not column-relative | **Low** | 1440–1920 |
| A14 | `--cream-mute` on `--surface` at 11px is 4.77:1 — passes AA by 0.27 only | **Low** | all |
| A15 | No `font-feature-settings: "tnum"` — relies on `font-variant-numeric` only | **Low** | all |
| A16 | `<details>` forecast-why accordion: summary tap target is 44px min-height ✓ but chevron rotation on iOS Safari may flicker | **Low** | 320–430 |

---

## Per-viewport findings

### 320×568 — iPhone SE (1st gen)

**Layout:**
- Rate cards render as 2-col (22K + 24K) + full-width 18K. Each of the top two columns is ≈133px wide. `₹15,262` in Fraunces 32px is right at the boundary — no overflow observed but zero breathing room. On a real device with subpixel rendering this could clip. **[A5]**
- 18K card spanning full width signals visual importance it doesn't have — 18K is the least-purchased karat. The eye reads it as the primary row. **[A4]**
- History rows in card-list layout: 15 entries × ~80px each = ~1200px of history alone. At 568px screen height this requires 2+ screen-heights just for history. The section needs a `max-height` + overflow scroll or a show-more pattern.  **[A8]**
- Commentary card text is legible (15px) but the label "TODAY'S NOTE" and meta timestamp are 11px `--cream-mute` on `--surface-2` background. Contrast 4.33:1 — **WCAG AA fail at 11px.** **[A1]**

**Text:**
- Hero price `₹13,990` at clamp min (56px) is readable. Change pill visible but "vs previous reading" label is 12px `--cream-mute` — borderline. **[A14]**
- Freshness pill shows "UPDATED: 19 MIN AGO" and topbar inside main also shows same string. Both visible before first scroll. **[A7]**

**Safe area:**
- Screenshots use 0px safe-area-inset (no real iOS device). On real iPhone SE there's no notch; no safe-area concern. ✓
- `min-height: 100vh` will cause a 60–75px jump when Safari URL bar collapses. **[A3]**

**Tap targets:**
- `.logo-mark` is 44×44px at ≤600px ✓
- Range-toggle buttons are 44px min-height at ≤600px ✓
- `.forecast-why summary` min-height 44px ✓
- History rows have no interactive target — fine.

---

### 375×667 — iPhone SE 2/3

Identical layout to 320px. Additional space relaxes rate card column width to ≈155px — still tight but more comfortable. All issues from 320px apply. Commentary contrast **[A1]** persists.

---

### 390×844 — iPhone 12/13/14 (primary device)

**Layout:**
- Layout looks intentional. Hero, 2+1 rate cards, commentary, forecast (stacked), live perf (1-col), chart, history (card), model stats.
- Forecast card stacks correctly at ≤540px — price top, meta bottom. No overflow. ✓
- Live performance single-column: three rows (₹84 / ₹226 / 0.37). Correct per media query but the labels are very small. The section heading "Live performance" at `clamp(18px, 3vw, 22px)` resolves to 18px at this width — **smaller than all other h2s** which use `clamp(22px, 3.5vw, 32px)`. **[A2]**
- Chart 260px height at ≤540px. Looks OK. Y-axis labels are ~9px in the rendered chart which is below any comfortable reading size — but this is a Chart.js config issue, not CSS.

**Text:**
- Commentary `--cream-mute` on `--surface-2` at 11px: **[A1]** persists.

**Safe area:**
- iPhone 14 has notch, `env(safe-area-inset-top)` ≈ 47px. Body has `padding-top: env(safe-area-inset-top)` ✓. Freshness pill sticky at `top: env(safe-area-inset-top)` ✓. Appears correctly. Can't verify in headless screenshot.
- `min-height: 100vh` → `100dvh` fix needed. **[A3]**

---

### 393×852 — iPhone 15 (second device, reported mismatch)

Layout is pixel-for-pixel identical to 390×844 in headless screenshots. The reported visual mismatch is almost certainly a **real-device rendering difference**, not a layout breakpoint issue. Likely causes:
1. iPhone 15 has a Dynamic Island (no notch) — `env(safe-area-inset-top)` = 59px vs 47px on iPhone 14. If the freshness pill's sticky threshold or body padding-top isn't handling this correctly, content will appear shifted 12px higher.
2. iPhone 15 is 393px logical width at 3× DPR vs 390px at 3×. The 3px width difference is irrelevant.
3. The Dynamic Island occupies more inset space — test specifically whether the freshness pill and topbar "Au" logo appear correctly below the island on the 15.

**Recommendation:** Test `env(safe-area-inset-top)` = 59px explicitly in dev tools before phase U5.

---

### 430×932 — iPhone 15 Pro Max

Widest mobile viewport. Rate cards are now ≈191px per column — much more comfortable. Hero price `clamp(56px, 16vw, 180px)` = 68.8px. Commentary and forecast both look well-proportioned. History card rows are nicely spaced.

- Forecast card at 430px is just below the 540px breakpoint that stacks it. Still stacked (price above, meta below). ✓
- Live performance h2 size issue **[A2]** still applies.
- Chart 260px ≤540px breakpoint. ✓

One observation: at 430px the page still renders the "mobile" breakpoint for history (card list). With 430px there's actually enough room for a narrow table. Not a bug, but something to consider.

---

### 768×1024 — iPad portrait

Layout transitions to full desktop grid at this width — no explicit breakpoint between 600px and 980px tablet range. The page looks strong at this size:
- Three rate cards in a row, good proportions ✓
- Forecast card in horizontal split (price left, target right) ✓
- Live performance 3-column grid ✓
- History full table with 5 columns ✓

**Issues:**
- `clamp(22px, 3.5vw, 32px)` for most h2s resolves to 26.9px here. Live perf h2 `clamp(18px, 3vw, 22px)` resolves to 23px — visibly smaller than adjacent headings. **[A2]**
- Commentary card between rate cards and forecast: reads as an interruption of the rate → forecast flow. **[A9]**
- "Live performance" section below forecast: the label is not intuitive. A user looking at ₹84 and ₹226 has no context that these are model error metrics. **[A10]**
- Freshness pill (sticky) and topbar timestamp both visible in first viewport. **[A7]**

**Tap targets:** All interactive elements (range toggle, forecast-why summary) are fine at tablet. ✓

---

### 1024×768 — iPad landscape

Content column (~980px) fills most of the viewport horizontally. Looks correct and intentional. The rate card `minmax(200px, 1fr)` auto-fit grid fills three equal columns well.

- Chart is 320px tall at this width. Proportion feels slightly short vs the wide chart wrap — could benefit from a taller height or aspect-ratio constraint at ≥1024px.
- History table rows are somewhat dense vertically (14px padding top/bottom, 14px font). Readable but could breathe more.

---

### 1280×800 — Laptop

Content is now clearly letter-boxed within the 980px max-width. The background gradients (radial at `90% -10%` and `-10% 110%`) are viewport-relative, meaning the gold glow appears in the far-right third of the viewport — not aligned with the content column. This still looks elegant but the gradient hotspot is off-center relative to the reading area. **[A13]**

Hero price at `clamp(72px, 18vw, 180px)` = 180px cap hit at 1000px+ viewport. So from 1000px up, the hero price is 180px. Looks intentional and bold. ✓

---

### 1440×900 and 1920×1080 — Desktop

These two layouts are nearly identical — both have hit the 180px hero price cap and the 980px max-width container. Side margins grow proportionally. No content issues.

At 1920px: side margins ≈ (1920 - 980) / 2 = 470px each. The page is a narrow column in a very wide space. The background gradients are the main visual interest on the margins. The gradient at `90% -10%` of a 1920px viewport has its hotspot at `1728px, -108px` — far right. This still creates a nice ambient glow in the upper-right even at this width. ✓

Chart at 320px height in a 980px wide wrap: aspect ratio ≈ 3:1. This feels a bit flat on desktop. Consider 360–400px for desktop. **[A12]**

---

## Alignment and consistency audit

| Element | Current behavior | Issue |
|---------|-----------------|-------|
| Section `h2` font size | Most: `clamp(22,3.5vw,32px)`. Live perf: `clamp(18,3vw,22px)` | Inconsistent — live perf h2 is visually subordinate |
| Section bottom margin | `clamp(40px, 8vw, 72px)` for most. History: `60px` fixed. Model: `60px` fixed. Commentary: `clamp(32px, 6vw, 56px)` | Three different patterns, not obviously intentional |
| Card border-radius | Rate card, chart wrap, history wrap, commentary, forecast: `16px`. Model stat: `12px`. Live perf stat: `12px`. Model note: `10px` | Three radii in use (10/12/16) — no documented reason |
| Card padding | Rate card: `22px 22px 18px`. Commentary: `20px 24px`. Forecast: `22px 24px`. Model stat: `16px 18px`. Live perf stat: `16px` | No consistent internal spacing system |
| Label style | Many components use 11px uppercase letter-spacing 0.16–0.22em. Values vary: 0.16em, 0.18em, 0.22em | Should be one value |
| Topbar updated vs freshness pill | Both show identical "updated X min ago" string | Redundant on all viewports |

---

## Tap target audit (mobile)

| Element | Size | Pass ≥44×44px? |
|---------|------|----------------|
| `.logo-mark` at ≤600px | 44×44px | ✓ |
| Range toggle buttons at ≤600px | `min-height: 44px; min-width: 44px` | ✓ |
| `.forecast-why summary` | `min-height: 44px` | ✓ |
| `.logo-mark` at >600px | 36×36px | ✗ — desktop only, lower risk |
| History rows (no interaction) | — | n/a |
| Freshness pill (pointer-events: none) | — | n/a |

---

## iOS Safari specific concerns

| Concern | Current state | Status |
|---------|--------------|--------|
| `viewport-fit=cover` | Set in `<meta>` | ✓ |
| `apple-mobile-web-app-capable` | Set | ✓ |
| `status-bar-style: black-translucent` | Set | ✓ |
| `env(safe-area-inset-top)` on body padding | Set | ✓ |
| `env(safe-area-inset-left/right)` on body | Set | ✓ |
| `env(safe-area-inset-bottom)` on body | Set | ✓ |
| Freshness pill `top: env(safe-area-inset-top)` | Set | ✓ |
| `min-height: 100vh` → should be `100dvh` | Using `100vh` | **⚠ Fix needed** |
| Dynamic Island inset (59px) vs notch (47px) | Handled via env() | Needs real-device test |
| Freshness pill landscape bleed | Negative margin only 20–56px vs up to 44px side insets | **⚠ May clip in landscape** |
| Font size ≥16px on form inputs | No inputs in app | n/a |
| `font-variant-numeric: tabular-nums` | Set on price elements | ✓ |
| `font-feature-settings: "tnum" 1, "lnum" 1` | Not set | **⚠ Add for broader compat** |
