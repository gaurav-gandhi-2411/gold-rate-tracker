# ADR 016 — Tanishq Scraper Hardening (Retry, CF Detection, Fingerprint, Alert Dedup)

**Status:** Accepted

**Date:** 2026-05-31

**Deciders:** GG (owner), CC (implementor)

---

## Context

The Tanishq Playwright scraper (`scraper/scrape.js`) is the entry point for the entire
production pipeline. Every 6-hour CI run starts with `node scrape.js`; failure means no
new price reading, which propagates as stale data to the ML inference, notifications, and
PWA. Historical analysis of `data/prices.json` prior to this hardening showed **34 gaps
>9 hours** across 124 total readings (~27% of expected readings missing), including a 24.5h
gap representing 4 consecutive failed runs.

The primary observed failure mode was Cloudflare Bot Management serving a JS challenge
page instead of the Tanishq gold rate widget. Secondary modes (selector timeout from slow
load, network errors) were also unhandled — any error caused the scraper to immediately
exit 1 with no retry.

Additionally, the alert system had no deduplication: both the scraper-down alert and the
staleness guard fired independently on every failing run, producing 7–8 ntfy notifications
within 24 hours for a single sustained Cloudflare block — classic alert fatigue.

---

## Decision

Implement four targeted hardening measures (H1–H4). Defer the IBJA-calibrated fallback
reading (H5) until the calibration gate flips to valid.

### H1 — Retry with backoff (3 attempts, fresh browser context per retry)

`scrapeWithRetry()` wraps `scrapeAttempt()` in a loop: 3 attempts, with 5s and 15s
delays between attempts. Each retry launches a new `browser.newContext()` — a fresh
context gets a clean cookie/storage slate, reducing the chance a cached CF challenge
decision is replayed.

**HONEST FRAMING:** This retry logic reduces TRANSIENT Cloudflare failures — JS challenge
pages that resolve on a second request from the same IP. It does NOT defeat IP-level
blocking: if Cloudflare has flagged the GitHub Actions runner's outbound IP, all 3 retries
hit the same IP and all fail. The practical backstop for sustained IP blocks remains the
next scheduled cron run (~6 hours later on a likely-different runner IP). Retry is not a
"Cloudflare fix" — it is a gap-rate reducer for the majority case (transient JS challenge).

### H2 — Cloudflare challenge page detection (<100ms fast path)

Before calling `waitForSelector` (which would burn the full 30s timeout), a quick check
inspects the page title and first 1000 chars of the body for CF-specific markers:
`"Just a moment..."` title, `cf-challenge`, `_cf_chl_`, `cf-browser-verification`. If any
marker is found, the attempt throws immediately with `{ retryable: true, isCFBlock: true }`,
saving ~29s per blocked attempt and making the 3-attempt retry budget feasible within the
CI step's timeout.

### H3 — Incremental browser fingerprint hardening

Minimal changes only (per scope constraint — no anti-detection arms race):
- Updated User-Agent from Chrome/124 (stale) to Chrome/136 (current stable at
  implementation time). NOTE: UA strings decay. Bump the `USER_AGENTS` array in
  `scraper/scrape.js` when Playwright's bundled Chromium is >2 major versions behind the UA.
- Added `timezoneId: "Asia/Kolkata"` — CI runners default to UTC, which is detectable.
- Added `extraHTTPHeaders: { "Accept-Language": "en-IN,en;q=0.9" }` — real browsers send
  this; headless Chrome does not by default.
- Viewport rotates per attempt (1280×800, 1366×768, 1440×900).

These changes reduce fingerprint gap against basic detection. They do not mitigate
advanced CF Bot Management (ML-based behavioral scoring, TLS fingerprinting, IP reputation).
No stealth plugins, proxy rotation, or CAPTCHA solvers — those are out of scope and a
losing game for a solo portfolio project.

### H4 — Alert deduplication (prices.json age check + SCRAPER_DOWN_THIS_RUN env flag)

**H4a:** The scraper-down ntfy alert fires only if `prices.json` last entry is ≤12h old
(first or second consecutive failure). Beyond 12h, the sustained-failure case is handed
off to the staleness guard. This is implemented as an inline Python age-check in the
`check-price.yml` alert step — no new external state mechanism.

**H4b:** The `Alert on scraper failure` step writes `SCRAPER_DOWN_THIS_RUN=true` to
`$GITHUB_ENV` whenever the scraper step fails (regardless of whether ntfy was sent). The
`Staleness guard` step reads this env var and skips its own ntfy send when it is set,
preventing both alerts from firing in the same CI cycle.

Result: a 24h sustained failure now produces at most 3 ntfy alerts instead of 7–8:
- Runs 1–2 (0–12h): scraper-down alert (first failure, then second)
- Run 3+ (≥12h): staleness guard takes over (1 alert per 6h cycle, still noisy, but
  without the duplicate from the scraper-down alert firing simultaneously)

---

## H5 — IBJA-calibrated fallback reading (DEFERRED)

**Decision: Defer indefinitely, revisit only when `calibration.json.valid` flips to True.**

Rationale:
- `calibration.json` is currently `valid: false` (21 overlap pairs, needs 30). A calibrated
  reading from an invalid calibration is noise, not data.
- An honest gap in `prices.json` is preferable to a noisy fabricated reading. Per norm #8
  (no silent fallback), any IBJA-derived reading would require a distinct `"source"` value
  and consumer updates across `inference.py`, `notifications.py`, `drift.py`, and `app.js`.
- The operational benefit arrives only after ~9 more overlap pairs accumulate (~3 months
  at the current rate of ~1 pair/day). By that point, H1+H2 should have substantially
  reduced the gap rate from transient failures anyway.

When to revisit: calibration flips to `valid: true`. At that point, open a dedicated ADR
for the data-provenance decision (what does it mean for a `prices.json` entry to be
IBJA-derived rather than directly scraped?).

---

## Error taxonomy (retryable vs. non-retryable)

| Error type | Retryable? | Rationale |
|---|---|---|
| CF challenge page (detected by H2) | Yes | Transient JS challenge; resolves on retry |
| `page.goto()` navigation error (DNS, connection refused, nav timeout) | Yes | Transient network condition |
| `waitForSelector` timeout (selector absent or slow JS) | **No** | FM-2 (DOM change) and FM-5 (partial load) look identical from code perspective. Retrying FM-2 wastes 3×30s on a persistent failure. Since FM-2 dominates after a page redesign (persistent, not transient), the conservative choice is no-retry and wait for the next 6h run. |
| `validate()` failure (NaN, out-of-range, ratio violation) | **No** | Deterministic data problem; retry won't produce different data. |

---

## Alternatives considered

**Stealth Playwright plugins (playwright-extra + puppeteer-stealth):**
Rejected. These plugins add complexity, have inconsistent maintenance, and are well-known
to CF — they fight an adversarial system that actively patches against them. Scope creep
relative to the marginal gain.

**Proxy rotation (residential proxies):**
Rejected. Monthly cost, added latency, third-party dependency. Breaks the ₹0/month
free-tier constraint.

**Switching to an unofficial API endpoint:**
Dead end explored in the original Phase 3 audit. The Tanishq site has no public API.
DOM scraping is the only available path.

**Alert rate-limiting via `actions/cache` state:**
Considered but rejected in favour of the simpler `prices.json` age check (H4a) and
`GITHUB_ENV` flag (H4b). No new external state required; logic is readable inline in the
YAML.

---

## Consequences

**Positive:**
- Transient CF challenges auto-recovered in ~60s (two retries) instead of propagating to a
  data gap and ntfy alert.
- Alert fatigue reduced: 7–8 ntfy → ≤3 ntfy per 24h sustained failure.
- Test coverage added for all catalogued failure modes (FM-1 through FM-8).
- Scraper-canary.yml runs on PRs touching scraper/ paths — hardening tests are verified
  pre-merge without live network calls.

**Negative / honest limits:**
- IP-level CF blocking still produces 3-attempt failure; the next cron run is the backstop.
  No code can change this without proxy rotation or an alternative data source.
- UA strings decay and need periodic manual bumps. Added a comment in `scrape.js` with
  the bump procedure.
- Hardening tests require Playwright browser install in `scraper-canary.yml` on every PR
  touching `scraper/`. Estimated overhead: ~60s (Playwright install is cached after first run).

**Re-evaluation triggers:**
- Gap rate remains >15% after 4 weeks of production data post-merge → reassess H5 or
  investigate proxy options.
- Calibration flips to `valid: true` → open H5 ADR.
- Chrome stable advances >2 major versions past pinned UA → bump `USER_AGENTS` in
  `scraper/scrape.js`.
