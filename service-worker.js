// service-worker.js
//
// CACHE INVALIDATION CONTRACT (PR Ψ3A):
// 1. Bump VERSION on every deploy that changes shell assets (HTML, CSS, JS, icons).
// 2. install → skipWaiting() so new SW takes control immediately without waiting.
// 3. activate → evict all caches not matching current VERSION (old shell cleared).
// 4. index.html → registration.update() on load forces an immediate SW byte-check;
//    controllerchange listener reloads the page so new shell assets are served.
// Updating ONE of these without the others reintroduces stale-cache symptoms.
// See also CURRENT_STATE.md norm #13: squash-merge commits must not carry [skip ci]
// in the body — a [skip ci] merge skips master Lint for up to ~24h (only the daily
// 06:00 UTC schedule backstop in lint.yml recovers it).
//
// 2026-07-19: VERSION had gone unbumped since 2026-06-08 (v16-20260608-phi22)
// across 11 merged PRs that changed index.html/app.js/style.css (#122 through
// #237). Because this file's own bytes never changed, registration.update()
// never detected a diff and never re-installed — any client that had this SW
// installed anytime in that window is frozen on whichever shell snapshot was
// live at that moment and never received a single one of those 11 shell
// updates. Bumping VERSION now forces every such client to evict the stale
// cache and re-fetch the current shell on next load. See lint.yml's
// sw-version-guard job, added the same day, which now fails CI when shell
// files change without a VERSION bump.
// 2026-08-10 (v35): Hindi/English polish -- letter-spacing fix for broken
// Devanagari conjuncts, Noto Serif Devanagari added for --display, "Gold
// Tracker" header rename, copy tightening. Shell files (index.html, app.js,
// i18n.js, style.css) all changed; bumping so every installed client
// re-fetches instead of serving the pre-fix shell indefinitely.
// 2026-08-11 (v36): insights + UX batch -- gram-quantity calculator, coverage%/
// accuracy-drift promoted out of the methodology accordion, typical-weekly-
// movement historical stat, calibration-confidence line on estimated-price
// banners, first-visit orientation strip, share-a-snapshot. Shell files
// (index.html, app.js, i18n.js, style.css) all changed; bumping so every
// installed client re-fetches instead of serving the pre-batch shell
// indefinitely.
// 2026-08-28 (v37): app.js fix -- the estimate-tier hero render no longer
// requires est_low/est_high to show the calibrated price. A band-suppressed
// forecast (new possible state as of this change) used to fall through to
// rendering the stale last-confirmed Tanishq reading as an unqualified
// current price; now it correctly shows "≈" + current_22k with the range
// line hidden. app.js changed; bumping so every installed client re-fetches.
// 2026-08-28 (v38): G2 -- the estimated-price banner's confidence sentence
// now states its actual nominal confidence level (e.g. "about 80% of the
// time") instead of an unqualified Rs/gram number, and is driven entirely by
// forecast.json's nominal_coverage/band_half_width rather than independently
// recomputed from calibration.json. app.js + i18n.js (EN+HI) changed.
//
// 2026-09-04 (v39): R3 -- the ibja_calibrated banner now names it when
// Tanishq confirmation itself has been silent for TIER_DEGRADED_THRESHOLD_H
// (48h), instead of rendering identically whether Tanishq confirmed 2h ago
// or 3 weeks ago. app.js + i18n.js (EN+HI) changed.
// 2026-09-04 (v40): R3 -- firstVisitText/footerBody (EN+HI) no longer
// hand-type "checked every 3 hours"; both render the real measured cadence
// from a new data/cadence_metrics.json fetch. app.js + i18n.js changed.
// 2026-09-03 (v41): fix(app) vol-regime and driver-context claims fail loud
// instead of defaulting to a plausible value (audit finding (e) + two
// siblings). app.js + i18n.js (EN+HI) changed. (Rebased onto master
// 2026-09-05 -- see W5 in the session history for why this lands after v40
// despite its own dated comment predating it.)
// 2026-09-05 (v42): X1 -- the injected cadence claim now states the p90
// worst case alongside the median (a median alone hides the tail a real
// visitor can land on). app.js + i18n.js (EN+HI) changed.
// 2026-09-10 (v43): AE1 -- calibration-band confidence clause now renders the
// real walk-forward measured coverage (data/calibration_band_coverage.json),
// never the hardcoded 80% design target. app.js + i18n.js (EN+HI) changed.
// 2026-09-10 (v44): AE2 -- two stale/desynced hardcoded claims fixed: (1)
// index.html's static firstVisitText/footerBody pre-hydration fallback text
// still said "checked every 3 hours" even after i18n.js's dynamic version
// was fixed (PR #1406) -- view-source/no-JS/crawlers/the pre-hydration
// flash all still asserted it. (2) methAccurateP3's hardcoded "roughly 70%"
// direction base-rate aside was a frozen 2026-06-02 one-time snapshot,
// never sourced from any field this codebase currently tracks live.
// index.html + i18n.js (EN+HI) changed.
// 2026-09-21 (v45): app.js no longer fetches data/calibration.json. Nothing consumed it
// (renderStaleBanner's `calibration` parameter was never read after G2), and _config.yml
// excludes it from the Pages build, so every load made a request that 404'd live.
// app.js changed; bumping so every installed client re-fetches.
// 2026-09-21 (v46): renderChart()/renderForecastVsActual() no longer throw when the Chart.js
// CDN request fails (a throw there blanked the hero -- render-smoke run 35511515077).
// app.js changed; bumping so every installed client re-fetches.
// 2026-09-21 (v47): data files are cached under a query-free key and every /data/*.json
// is network-first. Offline, ALL data requests used to fail (the ?t= cache-buster made the
// fallback lookup never match) and each load added a new cache entry per file. See
// isDataFile()/the fetch handler below. service-worker.js changed; bumping so every
// installed client re-installs and evicts the per-load entries.
// 2026-09-21 (v48): app.js no longer defines computeTrendDescription (dead: nothing called it;
// hard-coded English, never i18n'd). app.js changed; bumping so every installed client re-fetches.
// 2026-09-21 (v49): Sentry is now actually initialised (real DSN, and an onload hook so init no
// longer depends on the async bundle winning the race with app.js). app.js + index.html changed.
// 2026-09-21 (v50): index.html no longer reloads a first-time visitor's page when the worker first
// takes control (it flashed the price to the loading skeleton ~1s after first paint, and raced the
// post-deploy render smoke test into a false URGENT). Only a worker REPLACING an existing one reloads.
// 2026-09-23 (v52): the purchase calculator switches from a flat 6-25% making-charge range
// to jewellery-type PRESETS (coins & plain chains / plain bangles & rings / intricate or
// antique designs / custom), each showing a typical total plus a low-high range, with a
// visible "rate used" source line and a prominent "Estimate — stores vary" disclaimer.
// The v51 WIP commit (computePurchaseCostRange()/makingPerGram + the range-slider UI it
// backed) never shipped separately -- this single entry covers everything that changed on
// this branch, from master's v50 baseline. index.html + app.js + i18n.js + style.css changed.
// 2026-09-23 (v53): U2 plain-language rework -- the methodology accordion's full technical
// breakdown moved off the main page onto a new how-we-know.html, rendered by
// how-we-know.js/how-we-know-strings.js from the same data files. All three are new
// precached shell files (below). index.html + app.js + i18n.js + style.css changed too
// (banned-jargon rewrites, accordion body replaced with a plain summary + link).
// 2026-09-24 (v56): new flags.js -- minimal feature-flag mechanism (FEATURE_FLAGS/
// isFeatureOn()) so future user-facing features can merge OFF by default. Loaded before
// app.js/how-we-know.js on both pages; new precached shell file (below). index.html +
// how-we-know.html + app.js changed too (script tag + renderFlaggedFeatures() hook).
// 2026-09-24 (v57): p-value display fix -- how-we-know.js's Wilcoxon p-value
// no longer renders a misleading "p = 0.0000" when it rounds to zero at 4
// decimal places (formatPValue() in i18n.js, added same PR); renders
// "p < 0.0001" instead. i18n.js + how-we-know.js + how-we-know-strings.js
// (EN+HI) changed -- all three are precached shell files.
// 2026-09-25 (v58): retailer takedown switch (ADR 059) -- app.js stops naming Tanishq
// (hero location line, last-confirmed line, long-silent banner clause, calculator label)
// when prices.json holds IBJA-derived rows; new i18n key heroLocationDerived (EN+HI).
// Inert on today's data. app.js + i18n.js changed.
// 2026-09-25 (v60; v59 left for #2053's own rebase onto master's v58): E2 hero shows
// Tanishq's live listed rate only when fresh + plausible, otherwise our labelled estimate
// (heroDisplayState). app.js + i18n.js + index.html changed.
const VERSION = "v60-20260925-hero-tanishq-live";
const SHELL_CACHE = `gold-shell-${VERSION}`;

const SHELL_FILES = [
  "./",
  "./index.html",
  "./style.css",
  "./flags.js",
  "./app.js",
  "./i18n.js",
  "./how-we-know.html",
  "./how-we-know.js",
  "./how-we-know-strings.js",
  "./manifest.webmanifest",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./fonts/fraunces-variable-latin.woff2",
  "./fonts/dmsans-variable-latin.woff2",
  "./fonts/syne-variable-latin.woff2",
  "./fonts/rupee-sign.woff2",
  // rupee-sign.woff2 (~1.1KB, ₹ only) belongs in this unconditional tier, not
  // the Devanagari one below it -- ₹ appears in every price display
  // regardless of language, so every visitor needs it, same as the three
  // Latin faces above. See style.css's Rupee Sign @font-face comment for why
  // it exists as its own tiny face instead of just being part of one of the
  // Latin faces.
  // Deliberately NOT the two Devanagari fonts (Sans + Serif) — see
  // isDevanagariFont() below. Unlike the four fonts above (needed by every
  // visitor, so precaching them at install is correct), unconditionally
  // precaching these would cost every English-only visitor a ~248KB fetch
  // they'll never use. They're cached on first actual use instead (Hindi
  // visitors only).
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(SHELL_CACHE).then((c) =>
      // Don't fail install if one optional asset misses.
      Promise.all(SHELL_FILES.map((f) => c.add(f).catch(() => null)))
    )
  );
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Every JSON file under /data/ gets network-first treatment. This used to be a hand-typed
// list of 3 names while app.js requests 7 on every load (2026-09-21 sweep), so the other 4 fell
// through to the cache-first branch below and had no offline fallback at all.
function isDataFile(url) {
  return url.pathname.includes("/data/") && url.pathname.endsWith(".json");
}

function isDevanagariFont(url) {
  return (
    url.pathname.endsWith("/fonts/notosans-devanagari-variable.woff2") ||
    url.pathname.endsWith("/fonts/notoserif-devanagari-variable.woff2")
  );
}

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);

  if (isDataFile(url)) {
    // Network-first for all data files; fall back to cache when offline.
    // The cache key drops the query string: app.js's loadJSON() appends ?t=<Date.now()>
    // as a cache-buster, so keying on the full request URL stored a NEW entry per file per
    // load (unbounded until the next VERSION bump) and made the offline fallback look up a
    // different ?t= than any stored entry -- it never matched, so offline every data
    // request failed. Verified in Chromium against the live origin, 2026-09-21.
    const cacheKey = url.origin + url.pathname;
    e.respondWith(
      fetch(e.request)
        .then((res) => {
          // Only a good response may replace the offline copy: with a stable key a
          // transient 5xx/404 would otherwise overwrite the last known-good data.
          if (res.ok) {
            const copy = res.clone();
            caches.open(SHELL_CACHE).then((c) => c.put(cacheKey, copy));
          }
          return res;
        })
        .catch(() => caches.match(cacheKey))
    );
    return;
  }

  if (isDevanagariFont(url)) {
    // Not in SHELL_FILES (see comment there) -- cached the first time it's
    // actually requested instead, so only visitors who ever switch to Hindi
    // ever store it, while repeat Hindi visits still hit cache like the rest
    // of the shell (same "installed, habitually-checked PWA" pattern the
    // other fonts are precached for).
    e.respondWith(
      caches.match(e.request).then((cached) => {
        if (cached) return cached;
        return fetch(e.request).then((res) => {
          const copy = res.clone();
          caches.open(SHELL_CACHE).then((c) => c.put(e.request, copy));
          return res;
        });
      })
    );
    return;
  }

  // Cache-first for shell assets.
  e.respondWith(
    caches.match(e.request).then((cached) => cached || fetch(e.request))
  );
});
