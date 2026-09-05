# Design System — Gold Rate Tracker

Direction: **Editorial refined** — dark + gold, FT-Weekend / Patek Philippe vibe. Tighten and refine; do not replace. Every decision should feel considered, not accidental.

---

## Resolved decisions

| # | Decision | Resolution | Phase |
|---|----------|------------|-------|
| D1 | Forecast card border | **Keep dashed (`border: 1px dashed var(--gold-soft)`).** The dashed line is a deliberate uncertainty signal — a forecast is not a fact. Every other card in the UI uses a solid border; the single dashed exception makes the card's provisional nature legible without any copy. Changing to solid would make it visually identical to a rate card, conflating a prediction with a measurement. | U3 |
| D2 | Section order | **Commentary before forecast** (rate cards → commentary → forecast → chart → drift). Commentary sets the scene; forecast is the conclusion the reader arrives at. Drift moves below the chart — it's diagnostic, not primary. | U3 |
| D3 | Hero clamp | **`clamp(56px, 16vw, 160px)`** — cap hits at 1000px viewport. 16vw at 768px = 123px (more measured on iPad than 153px from 20vw). | U2 |

---

## 1. Color tokens

### Applied tokens

```css
/* Backgrounds — dark warm near-black */
--ink:        #0e0c0a   /* page background */
--ink-2:      #14110d   /* defined but unused — candidate for removal */
--surface:    #1c1a16   /* default card background */
--surface-2:  #24211c   /* elevated card (commentary) */
--line:       #2e2a23   /* divider / card border */
--line-strong: #3d3830  /* stronger divider — reserved for U2 rate card border */

/* Text — warm cream scale */
--cream:      #f5ede0   /* primary text */
--cream-dim:  #c8bfae   /* secondary text, table body */
--cream-mute: #9a9282   /* labels, metadata, captions — lifted from #8a8273 for WCAG AA */

/* Accent */
--gold:       #c8a456   /* primary accent, headings, links */
--gold-soft:  #b69248   /* forecast card dashed border — deliberate uncertainty signal */
--gold-glow:  rgba(200,164,86,0.18)  /* shadow / ambient glow */

/* Semantic */
--up:         #c66a4b   /* terracotta — price increase */
--down:       #6a9a72   /* sage — price decrease */
```

### WCAG contrast ratios — `--cream-mute` calibration

`--cream-mute` was `#8a8273` (L≈0.253). Lifted to `#9a9282` (L≈0.290) in U1.
Ratios are relative luminance method (WCAG 2.1); marked *(approx)* — verify with a browser contrast tool.

**Old value `#8a8273`:**

| Background | Bg hex | Ratio | AA at 11px (needs 4.5:1) |
|------------|--------|-------|--------------------------|
| `--surface` | #1c1a16 | 4.77:1 | ✓ marginal |
| `--surface-2` | #24211c | 4.33:1 | **✗ FAIL** |
| `--ink` | #0e0c0a | 5.32:1 | ✓ |

**New value `#9a9282`** *(applied in U1)*:

| Background | Bg hex | Ratio | AA at 11px (needs 4.5:1) |
|------------|--------|-------|--------------------------|
| `--surface` | #1c1a16 | **5.35:1** *(approx)* | ✓ |
| `--surface-2` | #24211c | **4.86:1** *(approx)* | ✓ |
| `--ink` | #0e0c0a | **5.98:1** *(approx)* | ✓ |

Target was ~5.5:1 on `--surface`; achieved ~5.35:1. This is 0.15 below target but well above the 4.5:1 AA floor. Aesthetic check: #9a9282 is a warm medium gray, consistent with the warm-dark palette. Verdict: keep. If a future contrast audit using a calibrated tool measures lower than 4.5:1, the fallback is #9d9585 which hits ~5.57:1 on surface.

**All other tokens** (unchanged from audit):

| Token | Hex | Background | Ratio | AA | AAA |
|-------|-----|------------|-------|----|-----|
| `--cream` | #f5ede0 | `--ink` | **16.1:1** | ✓✓✓ | ✓ |
| `--cream-dim` | #c8bfae | `--surface` | **9.5:1** | ✓✓✓ | ✓ |
| `--gold` | #c8a456 | `--ink` | **8.19:1** | ✓✓✓ | ✓ |
| `--gold` | #c8a456 | `--surface` | **7.34:1** | ✓✓✓ | ✓ |
| `--gold` | #c8a456 | `--surface-2` | **6.66:1** | ✓✓✓ | ✗ |
| `--ink` | #0e0c0a | `--gold` | **8.19:1** | ✓✓✓ | ✓ |
| `--up` | #c66a4b | `--ink` | **5.22:1** | ✓ | ✗ |
| `--down` | #6a9a72 | `--ink` | **6.15:1** | ✓✓✓ | ✗ |

---

## 2. Type scale

### Current state (problem)

The codebase uses ad-hoc font sizes with no documented scale:

```
10px  — .topbar .updated at ≤600px
11px  — labels, freshness pill, forecast label, model note sub, rate card header
12px  — eyebrow, .change .vs, footer, history th, .updated
13px  — range toggle, forecast interval, history body, commentary meta, forecast-why-body
14px  — history font, change font
15px  — commentary text
16px  — body base
18px  — live-perf-value at ≤540px (also live-perf h2 clamp minimum)
22px  — logo-mark, live-perf-value, live-perf h2 clamp max
24px  — (none currently)
28px  — model-stat-value
32px  — forecast-price clamp min; rate-card .rate at ≤540px; h2 clamp max
38px  — rate-card .rate (desktop)
56px  — forecast-price clamp max; hero price clamp min at ≤600px
72px  — hero price clamp min (>600px)
180px — hero price clamp max
```

Problems:
- Three sizes in the 11–13px range with no principled distinction
- Live perf h2 uses `clamp(18px, 3vw, 22px)` — different from every other h2's `clamp(22px, 3.5vw, 32px)`
- Model stat value (28px) doesn't fit any round-number step
- Intermediate sizes (18px, 24px) have inconsistent roles

### Proposed scale

Based on a **1.25 ratio (Major Third)** scale anchored at 16px, snapped to whole pixels:

```
Step  Size    Role
--ts-xs    11px    Uppercase labels, captions, meta (WCAG note: use ≥--cream-dim here)
--ts-sm    13px    Secondary body, table cells, button text, commentary meta
--ts-base  16px    Body text, input, commentary
--ts-md    20px    Card stat values on mobile (replaces 18px and 22px ad-hoc)
--ts-lg    24px    Section h2 floor (replaces 22px clamp min)
--ts-xl    32px    Section h2 cap; forecast price mobile; rate card desktop
--ts-2xl   40px    Forecast price desktop (replaces 56px — brings it closer to rate card)
--ts-3xl   56px    Hero price mobile floor
--ts-hero  96px    Hero price mid-range  ← replaces the ad-hoc clamp(72,18vw,180)
--ts-disp  160px   Hero price desktop cap
```

**Proposed clamp replacements:**

```css
/* Hero price — responsive between 56px and 160px */
font-size: clamp(var(--ts-3xl), 20vw, var(--ts-disp));
/* Previously: clamp(56px, 16vw, 180px) at ≤600; clamp(72px, 18vw, 180px) at >600 */
/* Consolidate to one rule: clamp(56px, 20vw, 160px) */

/* Section headings h2 — all of them, including live-perf */
font-size: clamp(var(--ts-lg), 3.5vw, var(--ts-xl));   /* 24–32px */

/* Forecast price */
font-size: clamp(var(--ts-xl), 6vw, var(--ts-2xl));    /* 32–40px */

/* Rate card value — desktop: 38→40px (snap to scale); mobile ≤540px: 32px */
font-size: var(--ts-xl);   /* 32px, override in rate-card.primary or large breakpoint */
```

**Type roles (which elements use which step):**

| Step | Elements |
|------|----------|
| `--ts-xs` (11px) | All uppercase labels (`eyebrow`, `commentary-label`, `forecast-label`, `live-perf-label`, `model-stat-label`, `rate-card header`, `history th`, `freshness-pill .updated`) |
| `--ts-sm` (13px) | Forecast interval, forecast-why-body, commentary meta, history body, range-toggle buttons, `.updated` in topbar, `.change .vs` |
| `--ts-base` (16px) | Body, commentary text, change amount |
| `--ts-md` (20px) | Live perf values, model stat values (replaces 22px + 28px inconsistency) |
| `--ts-xl` (32px) | All section h2, rate card values, forecast price (mobile) |
| `--ts-2xl` (40px) | Forecast price (desktop) |
| `--ts-3xl` (56px) | Hero price (minimum) |
| `--ts-hero` (96px) | Hero price (viewport-responsive midpoint) |
| `--ts-disp` (160px) | Hero price (desktop cap) |

---

## 3. Spacing scale

### Current state (problem)

Spacing is entirely ad-hoc: 4px, 6px, 8px, 10px, 12px, 14px, 16px, 18px, 20px, 22px, 24px, 30px, 60px, 80px. At least 14 distinct spacing values, none obviously related.

### Proposed scale (8px base unit)

```css
--space-1:   4px    /* micro — icon gap, tight inline spacing */
--space-2:   8px    /* small — gap within a component */
--space-3:  12px    /* default — card internal gaps, label→value */
--space-4:  16px    /* medium — card padding (compact card) */
--space-5:  20px    /* standard — card padding (default card) */
--space-6:  24px    /* comfortable — section internal padding */
--space-7:  32px    /* section gap (mobile) */
--space-8:  48px    /* section gap (desktop) */
--space-9:  64px    /* large section gap (hero → cards) */
--space-10: 80px    /* page bottom padding */
```

**Where each step applies:**

| Token | Currently | Where to apply |
|-------|-----------|----------------|
| `--space-1` (4px) | `margin-bottom: 4px` in model stat | within-component nudges |
| `--space-2` (8px) | `gap: 6px` (live-perf-stat), `margin: 0 0 8px` (commentary-label) | gap between label+value inside a stat |
| `--space-3` (12px) | `margin-bottom: 12px` (rate card header), `gap: 12px` (model-cards) | standard gap between card elements |
| `--space-4` (16px) | `padding: 16px 18px` (model-stat) | compact card padding |
| `--space-5` (20px) | `padding: 22px 22px 18px` (rate-card) → snap to 20/20/16 | standard card padding |
| `--space-6` (24px) | `padding: 20px 24px` (commentary-card) → snap to 24 | comfortable card padding |
| `--space-7` (32px) | `clamp(32px, 6vw, 56px)` bottom on commentary | section gap mobile floor |
| `--space-8` (48px) | `clamp(40px, 8vw, 72px)` bottom on most sections → snap max to 48 | section gap desktop cap |
| `--space-9` (64px) | topbar `margin-bottom: clamp(40px, 8vw, 80px)` → 64px max | hero breathing room |
| `--space-10` (80px) | `padding-bottom: 80px` on main | page bottom |

**Card radius unification:**

Currently: 10px, 12px, 16px. Propose:
- `--radius-sm: 10px` — model note (prose block, low visual weight)
- `--radius-md: 12px` — stat chips (compact components)
- `--radius-lg: 16px` — primary cards (rate, chart, history, commentary, forecast)

---

## 4. Component inventory

| # | Component | HTML section | Single thing it communicates | Current gap |
|---|-----------|-------------|-------------------------------|-------------|
| 1 | Freshness pill | `.freshness-pill` | "Data is fresh / stale" | Redundant with topbar timestamp; both always visible |
| 2 | Topbar | `.topbar` | "This is Gold Rate; last updated X" | Logo is fine; the timestamp here may be demoted once pill is refined |
| 3 | Hero | `.hero` | "22K gold costs ₹X right now" | ✓ Works well. Change pill direction is semantic |
| 4 | Rate cards (×3) | `.rate-cards` | "22K / 24K / 18K prices at a glance" | 18K card gets undue prominence as full-width row on mobile |
| 5 | Commentary | `.commentary-section` | "One-sentence market context for today" | Placed between rate cards and forecast — interrupts the price→prediction flow |
| 6 | Forecast card | `.forecast-section` | "Model's best guess for tomorrow" | ✓ Price + interval + target date is clear. "Why?" accordion is good UX |
| 7 | Live performance | `.live-perf-section` | "How accurate is the model right now?" | Label "Live performance" is ambiguous. Values (₹84, ₹226, 0.37) have no context without knowing what they are |
| 8 | Trend chart | `.chart-section` | "Direction over 7d / 30d / all time" | ✓ Chart.js chart with toggles works well. Axis label density at mobile needs attention |
| 9 | History table | `.history-section` | "Every reading in tabular form" | 15 rows × 80px on mobile = too long; needs max-height + scroll or collapse |
| 10 | Model performance | `.model-section` | "How did the model do in backtesting?" | Separate from "Live performance" but similar visual treatment → users may conflate them. Ordering: live perf comes before backtest perf. ✓ |
| 11 | Footer | `.site-footer` | "Data source and legal disclaimer" | ✓ Minimal and correct |

**Recommended section order (revised):**
1. Freshness pill (sticky)
2. Topbar (logo + updated — or remove topbar updated once pill is refined)
3. Hero
4. Rate cards
5. **Forecast** ← move above commentary
6. Commentary ("Today's note" reads as context for the forecast)
7. Live performance (drift)
8. Trend chart
9. History
10. Model performance (backtest)
11. Footer

Rationale: a user visiting to check price → want prediction → commentary contextualizes the prediction → then supporting evidence (drift, chart, history, backtest stats). Currently commentary interrupts between rates and forecast.

---

## 5. Typography

### Current fonts

- **Display:** `Fraunces` (Google Fonts) — optical-size variable serif, italic variant used on rupee symbol. Good FT-editorial character. The optical size axis (`opsz`) enables crisp rendering at display sizes. **Keep.**
- **Sans:** `DM Sans` (Google Fonts) — clean geometric humanist. Pairs well with Fraunces. **Keep.**

### Why Fraunces + DM Sans is correct

Fraunces is distinctive at large display sizes (the ₹13,990 hero), has a genuine editorial quality, and its italic is expressive without being fussy (good for the ₹ rupee symbol treatment). DM Sans is neutral enough not to compete. This is the right pair for "editorial refined, dark + gold."

### One alternative worth knowing

If the direction ever shifts more toward classical FT/WSJ (less personality, more institution): **Cormorant Garamond** (already in the display fallback stack) + **Libre Franklin**. More restrained, less distinctive. Not recommended — Fraunces is the better editorial call for a product this specific.

### Fallback chain refinement

```css
--display: "Fraunces", "Cormorant Garamond", Georgia, serif;
/* ✓ Current — Cormorant is a good optical fallback for Fraunces */

--sans: "DM Sans", system-ui, -apple-system, sans-serif;
/* ✓ Current — system-ui covers modern iOS/Android well */
```

### Missing: tabular numerals on all price elements

`font-variant-numeric: tabular-nums` is set on individual elements (`.price`, `.rate`, `.forecast-price`, `.history`). This is good. But:

1. `font-variant-numeric` is not supported on all Android WebViews pre-2021. Add `font-feature-settings: "tnum" 1, "lnum" 1` as a fallback on any element displaying prices.
2. `lnum` (lining numerals) ensures numerals sit on the baseline uniformly — important for large display prices where old-style figures could look misaligned.

```css
/* Add to all price-displaying elements alongside font-variant-numeric */
font-feature-settings: "tnum" 1, "lnum" 1;
```

---

## 6. iOS Safari specific concerns

### `dvh` vs `vh`

```css
/* Current */
min-height: 100vh;

/* Fix */
min-height: 100dvh;  /* dvh = dynamic viewport height, accounts for collapsing URL bar */
```

On iOS Safari, `100vh` is the full height including the URL bar, which causes a layout jump when the bar collapses. `100dvh` tracks the current visible height. Supported in iOS Safari 16+ (2022). Use `100vh` as fallback:
```css
min-height: 100vh;
min-height: 100dvh;
```

### Dynamic Island — `env(safe-area-inset-top)`

iPhone 14 Pro / 15 / 15 Pro: `env(safe-area-inset-top)` = 59px (Dynamic Island)
iPhone 12 / 13 / 14: `env(safe-area-inset-top)` = 47px (notch)
iPhone SE: `env(safe-area-inset-top)` = 20px (status bar only)

Current code correctly applies `padding-top: env(safe-area-inset-top)` on `body` and `top: env(safe-area-inset-top, 0px)` on `.freshness-pill`. **This should work correctly** but requires real-device verification on the iPhone 15 given the reported mismatch (see audit A3/iPhone 15 section).

### Freshness pill in landscape

In iPhone landscape, `env(safe-area-inset-left/right)` = 44px. The body gets 44px side padding. The freshness pill's `margin: 0 calc(-1 * clamp(20px, 5vw, 56px))` negates at most 20px on each side (at 375px width, 5vw = 18.75 → clamped to 20px). The pill will be 24px short of full-width on each side in landscape.

Fix: use viewport-unit bleed instead:
```css
.freshness-pill {
  margin-left:  calc(-1 * env(safe-area-inset-left,  0px) - clamp(20px, 5vw, 56px));
  margin-right: calc(-1 * env(safe-area-inset-right, 0px) - clamp(20px, 5vw, 56px));
}
```

### `font-size-adjust` for cross-device weight consistency

Fraunces at the same `font-size` will appear slightly larger on Android (different metrics) than iOS. `font-size-adjust` lets you normalize by x-height. Fraunces has an x-height ratio of approximately 0.47.

```css
/* Experimental but progressive-enhancement safe: */
.price, .rate, .forecast-price {
  font-size-adjust: 0.47;
}
```

Not strictly necessary given the use of large display sizes, but worth noting for small text in Fraunces (not currently used).

### `font-feature-settings` for tabular / lining numerals

See §5 above. Add `font-feature-settings: "tnum" 1, "lnum" 1` to all price-displaying elements.

<!-- scratch test line, Z1c legitimately-rebased-PR test, will be reverted -->
