# UI Implementation Plan

Direction: editorial refined — dark + gold, tighten and refine.
Prerequisite: review and approve this plan before any code changes.
Each phase: self-contained PR, no breaking changes to data contract or JS logic.

---

## Phase U1 — Foundation
**Theme:** CSS custom properties for scale and spacing. No visual changes visible to users except the one contrast fix.
**Estimated diff:** ~80 lines changed in `style.css`, no HTML changes.

### What changes

1. **Add spacing custom properties** to `:root`:
   ```css
   --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px;
   --space-5: 20px; --space-6: 24px; --space-7: 32px; --space-8: 48px;
   --space-9: 64px; --space-10: 80px;
   ```

2. **Add type-scale custom properties** to `:root`:
   ```css
   --ts-xs: 11px; --ts-sm: 13px; --ts-base: 16px; --ts-md: 20px;
   --ts-lg: 24px; --ts-xl: 32px; --ts-2xl: 40px; --ts-3xl: 56px;
   --ts-hero: 96px; --ts-disp: 160px;
   ```

3. **Add radius custom properties**:
   ```css
   --radius-sm: 10px; --radius-md: 12px; --radius-lg: 16px;
   ```

4. **Fix `--cream-mute` contrast** (WCAG AA failure — [A1]):
   Change `--cream-mute: #8a8273` → `--cream-mute: #968e7e`
   Rationale: brings contrast on `--surface-2` from 4.33:1 to ~5.1:1. Passes AA at 11px.

5. **Fix `min-height: 100vh` → `100dvh`** ([A3]):
   ```css
   min-height: 100vh;   /* keep as fallback */
   min-height: 100dvh;  /* progressive enhancement */
   ```

6. **Add `font-feature-settings`** to all price-displaying classes:
   ```css
   font-feature-settings: "tnum" 1, "lnum" 1;
   ```
   Add alongside existing `font-variant-numeric: tabular-nums` on: `.price`, `.rate`, `.forecast-price`, `.history`.

7. **Standardize the live-perf h2 size** ([A2]):
   `.live-perf-section h2` currently uses `clamp(18px, 3vw, 22px)`. Change to match all other h2s: `clamp(var(--ts-lg), 3.5vw, var(--ts-xl))` = `clamp(24px, 3.5vw, 32px)`.

### Expected before/after
- Visual change: labels on commentary card become very slightly lighter (cream-mute fix). Live perf "Live performance" heading grows ~2–4px. No layout shift.
- No screenshots needed for this phase — it's a code baseline.

### What could go wrong
- `--cream-mute` lightening could affect visual hierarchy subtly (labels look less quiet). Accept this — WCAG compliance is not optional.
- `100dvh` on very old iOS (pre-16) falls back gracefully to `100vh` ✓.

---

## Phase U2 — Hero + rate cards
**Theme:** The above-fold experience. Everything a user sees before scrolling on their primary device (390×844).
**Estimated diff:** ~60 lines in `style.css`, minor HTML attribute changes (no new elements).

### What changes

1. **Remove duplicate "updated" from topbar** ([A7]):
   - Remove `<div class="updated" id="updated">—</div>` from `.topbar` in `index.html`.
   - The freshness pill handles this role. The topbar becomes logo-only — cleaner.
   - Update `app.js`: remove the `#updated` DOM reference.

2. **Consolidate hero font-size** ([A5 related]):
   Replace two separate clamp rules with one:
   ```css
   .price { font-size: clamp(var(--ts-3xl), 20vw, var(--ts-disp)); }
   /* Removes the ≤600px override — one rule covers all */
   ```
   At 320px: 20vw = 64px (above 56px floor). At 390px: 78px. At 1440px: 160px cap.

3. **Fix 18K card prominence on mobile** ([A4]):
   Change the 18K card from full-width bottom row to a compact "secondary" row:
   ```css
   @media (max-width: 600px) {
     .rate-cards { grid-template-columns: 1fr 1fr; }
     .rate-cards .rate-card:nth-child(3) {
       grid-column: auto;  /* ← remove 1/-1, let it be a 3rd half-width card */
       /* or: span 2 but apply smaller font */
     }
   }
   ```
   Alternatively: keep the 2+1 but reduce the 18K card's visual weight (smaller font, no border-radius on top corners to visually connect it as subordinate). Decision for implementation: prefer `grid-column: auto` so all 3 cards are equal width but 18K is naturally last.

4. **Rate card min-size safety at 320px** ([A5]):
   At 320px with 2-col layout each card is ~133px wide. Add explicit font-size floor:
   ```css
   @media (max-width: 360px) {
     .rate-card .rate { font-size: 26px; }
   }
   ```
   26px Fraunces at 133px container: "₹15,262" ≈ 5 chars × ~15px = 75px. Safe.

5. **Hero change pill — tighten spacing**:
   Currently `margin-top: 18px`. Change to `margin-top: var(--space-3)` (12px). The 18px gap is too loose relative to the 0.95 line-height of the price above.

6. **Eyebrow spacing**:
   Currently `margin: 0 0 12px`. Keep at `--space-3`.

### Expected before/after
- Mobile (390px): hero and rate cards feel tighter and more purposeful. 18K card no longer dominates.
- Topbar is cleaner — logo only, freshness pill handles freshness.
- 320px: rate card numbers no longer at risk of clipping.

### What could go wrong
- Removing `#updated` from topbar: verify `app.js` doesn't throw on missing DOM element (it uses `getElementById` — add a null check or remove the reference).
- Reducing hero gap from 18px to 12px may feel abrupt if the price clamp is large. Review at 1440px.

---

## Phase U3 — Forecast + drift + commentary cards
**Theme:** The "so what?" section. Reordering + relabeling.
**Estimated diff:** ~40 lines CSS, significant HTML reorder, minor app.js change.

### What changes

1. **Reorder HTML sections** ([A9]):
   Move `<section class="commentary-section">` to after `<section class="forecast-section">`.
   New order: Hero → Rate cards → Forecast → Commentary → Live performance → Chart → History → Model → Footer.
   Rationale: forecast answers "what next?", commentary contextualizes it. Commentary between rate cards and forecast breaks the prediction flow.
   Note: reordering HTML only — no CSS changes needed. No JS changes needed.

2. **Rename "Live performance" → "Model drift"** ([A10]):
   ```html
   <section class="live-perf-section" id="live-perf-section" hidden>
     <h2>Model drift</h2>
   ```
   The heading "Model drift" is honest about what the metrics are. "Live performance" sounds like it could mean the price.

3. **Add context to drift stat labels** ([A10]):
   Current labels: "Rolling 7-day MAE", "Training baseline MAE", "Drift ratio". These are accurate but require ML knowledge.
   Revised labels (keep uppercase style, add parenthetical):
   - "7-day error (MAE)"
   - "Baseline error (MAE)"
   - "Drift ratio"
   Add a single subtitle line below the h2: "How far off the model's forecasts have been this week vs training."

4. **Forecast card — remove dashed border in favor of solid gold border** (aesthetic):
   Current: `border: 1px dashed var(--gold-soft)`. The dashed border is the only dashed element in the entire UI — it reads as "temporary" or "uncertain." The forecast *is* uncertain, but communicating that through a broken border feels unintentional. Replace with the same solid gold border treatment as the primary rate card:
   ```css
   .forecast-card { border: 1px solid var(--gold-soft); }
   ```

5. **Commentary card — use `--cream-dim` for label and meta instead of `--cream-mute`** ([A1] alternate fix):
   If `--cream-mute` lightening in U1 doesn't fully satisfy the aesthetic, alternatively override just on the commentary card:
   ```css
   .commentary-label { color: var(--gold); }        /* already correct ✓ */
   .commentary-meta  { color: var(--cream-dim); }   /* was cream-mute → promote to dim */
   ```
   The commentary-meta is already 11px; `--cream-dim` on `--surface-2` is 9.5:1 — comfortably passes.

6. **Apply spacing tokens** to forecast and commentary:
   - Commentary card padding: `var(--space-5) var(--space-6)` (20px 24px → no change, already correct)
   - Forecast label margin-bottom: `var(--space-3)` (10px → 12px, minor)
   - Section bottom margins: `clamp(var(--space-7), 8vw, var(--space-8))` = `clamp(32px, 8vw, 48px)` (currently clamp(40px, 8vw, 72px))

### Expected before/after
- Forecast section appears higher on the page — users on mobile get the prediction sooner.
- Commentary reads as "context for the above forecast" — better narrative flow.
- Drift section has a clear label. Users can understand it without ML background.
- Forecast card feels more confident (solid border).

### What could go wrong
- HTML reorder: test that hidden sections (`forecast-section`, `live-perf-section`, `commentary-section`) still become visible correctly when `app.js` removes the `hidden` attribute. The JS uses `getElementById` — reordering DOM doesn't affect this. ✓
- "Model drift" rename: the section id (`live-perf-section`) and JS references don't change — only the visual h2 text.

---

## Phase U4 — Chart + history
**Theme:** Data-dense sections. Make them readable without overwhelming.
**Estimated diff:** ~50 lines CSS, minor HTML for history scroll container.

### What changes

1. **Chart height — responsive aspect ratio** ([A12]):
   Replace fixed height with aspect-ratio:
   ```css
   .chart-wrap {
     height: auto;
     aspect-ratio: 16 / 5;  /* ≈ 320px at 1000px width; ≈ 220px at 700px width */
   }
   @media (max-width: 540px) {
     .chart-wrap { aspect-ratio: 4 / 3; }  /* taller on phone for better readability */
   }
   ```
   At 390px × 4/3: chart height ≈ 292px. More vertical space for Y-axis labels on mobile.

2. **History section — max-height + scroll on mobile** ([A8]):
   ```css
   @media (max-width: 600px) {
     .history-wrap {
       max-height: 420px;   /* roughly 5 card rows */
       overflow-y: auto;
       -webkit-overflow-scrolling: touch;
     }
   }
   ```
   420px ≈ 5 history rows × 84px. Shows recent data, hides the scroll depth.
   Consider adding a subtle fade-out gradient at the bottom of the wrap to signal more content below.

3. **History table on desktop — column widths**:
   The "When" column currently takes too much space. Pin the numeric columns:
   ```css
   .history th:first-child, .history td:first-child { width: 40%; }
   .history th.num, .history td.num { width: 15%; }
   ```

4. **Chart Y-axis and X-axis label font size** (Chart.js config, not CSS):
   Currently Chart.js uses default tick sizes. The axis labels are ~9px at mobile widths — unreadable. In `app.js`, add to chart config:
   ```js
   ticks: { font: { size: 11 }, color: '#8a8273' }
   ```
   This is a JS change, not CSS — include in this phase since it's purely presentational.

5. **History table desktop row height**:
   Current: `padding: 14px 18px`. Increase to `padding: var(--space-4) var(--space-5)` = 16px 20px. Slightly more breathing room per row.

6. **Apply spacing tokens** to chart and history sections:
   - `.chart-section` margin-bottom: `clamp(var(--space-7), 8vw, var(--space-8))`
   - `.chart-header` margin-bottom: `var(--space-6)` (24px, was 22px)
   - `.history-section h2` margin-bottom: `var(--space-6)`

### Expected before/after
- Mobile: chart is taller relative to width → better data density and legible axes.
- History on mobile is no longer a bottomless scroll — shows 5 rows with scroll.
- Desktop: history table rows breathe more, column widths feel more intentional.

### What could go wrong
- `aspect-ratio` on `.chart-wrap`: Chart.js reads the canvas container dimensions on init. If the container height is determined by aspect-ratio (not a fixed px), Chart.js should handle this correctly as of v4.x. Test after implementation.
- `max-height` + scroll on history: the `history-wrap` uses `overflow: hidden` today (for border-radius on the table). Change to `overflow: hidden auto` (hidden on x, auto on y) to preserve the rounded corners while allowing vertical scroll.

---

## Phase U5 — Footer + safe areas + iOS polish
**Theme:** Final fit and finish. Everything that requires real-device testing.
**Estimated diff:** ~30 lines CSS, 0 JS changes.

### What changes

1. **Fix freshness pill landscape bleed** ([A6]):
   ```css
   .freshness-pill {
     margin-left:  calc(-1 * env(safe-area-inset-left,  0px) - clamp(20px, 5vw, 56px));
     margin-right: calc(-1 * env(safe-area-inset-right, 0px) - clamp(20px, 5vw, 56px));
   }
   ```
   Adds safe-area compensation so the pill correctly bleeds edge-to-edge in landscape.

2. **Apply `dvh` fix** (from U1, confirm on real device):
   The `min-height: 100vh; min-height: 100dvh` double-declaration (already in U1) prevents the height jump on iOS Safari. Verify the page doesn't have unintended blank space at bottom on iPhone 15 Safari after this change.

3. **Footer spacing**:
   Current: `padding-top: 24px`. Change to `var(--space-6)`. Margin from last section: confirm `--space-8` (48px) feels right vs current 60px on history/model sections.
   Add `padding-bottom: env(safe-area-inset-bottom, var(--space-5))` to footer so content doesn't hide behind the iPhone home indicator.

4. **Body bottom padding**:
   Current: `padding-bottom: env(safe-area-inset-bottom)`. The `main` element also has `padding-bottom: 80px`. These stack. On iPhone with home indicator (`env(safe-area-inset-bottom)` = 34px): total = 80 + 34 = 114px. This is generous. Change `main` padding-bottom to `var(--space-7)` (32px) + safe area:
   ```css
   main { padding-bottom: calc(var(--space-7) + env(safe-area-inset-bottom, 0px)); }
   ```

5. **Verify Dynamic Island (iPhone 15) freshness pill positioning**:
   With `env(safe-area-inset-top)` = 59px, the pill sticks at 59px from viewport top when scrolled. At rest (page load), it's in normal flow below the body top padding (also 59px). The effective pill position should be correct. Verify by testing:
   - Open on iPhone 15 Safari
   - Scroll to mid-page
   - Pill should appear just below the Dynamic Island
   - Scroll back to top
   - Pill should return to natural position

6. **Model performance section spacing**:
   Currently `margin-bottom: 60px` — doesn't use `clamp`. Change to `clamp(var(--space-7), 8vw, var(--space-8))` for consistency with other sections.

7. **Freeze and screenshot**:
   After U5 is merged, re-run `node scripts/capture-screenshots.js` and save to `docs/screenshots/after/`. Diff against `before/` to confirm all intentional changes and no regressions.

### Expected before/after
- iPhone landscape: freshness pill no longer clips at left/right safe-area boundaries.
- iPhone home indicator: footer content no longer potentially hidden at bottom.
- All section bottom margins are now consistent and token-driven.

### What could go wrong
- `calc` with `env()` values: supported in iOS Safari 11.2+. ✓
- The `dvh` change might affect the page differently on some Android browsers (Samsung Internet). Fallback `100vh` is in place. ✓
- Body `padding-bottom` + main `padding-bottom` stacking: be careful not to double-apply. After U5, body should only carry safe-area insets, and main carries the design space. Currently: `body { padding-bottom: env(safe-area-inset-bottom) }` + `main { padding-bottom: 80px }`. After: `body` keeps the env() padding, `main` changes to `calc(32px + env(...))` BUT this would double-add the safe area (body adds it to body, main adds it to its own padding). Fix: remove `padding-bottom` from body, put full `calc(var(--space-7) + env(safe-area-inset-bottom, 0px))` only on `main`.

---

## Phase order rationale

U1 before everything: establishes the tokens that all subsequent phases reference. If tokens are wrong, later phases inherit the mistake.

U2 before U3: above-fold matters most. Ship something visible quickly.

U3 before U4: the "so what?" cards are higher-value than chart/history polish.

U4 before U5: data sections before final finish.

U5 last: requires real-device testing, can't be validated in headless screenshots.

---

## Definition of done (per phase)

- [ ] All 10 Playwright viewports re-captured in `docs/screenshots/after/`
- [ ] No new WCAG AA failures introduced
- [ ] `git diff --stat` shows only `style.css`, `index.html`, `app.js` (no data files, no ML files)
- [ ] Push to master, CI green (the push trigger doesn't affect ML pipeline since CSS/HTML files are not in the workflow paths filter)
- [ ] User confirms on real iPhone 14 (390) and iPhone 15 (393) before calling U5 done
