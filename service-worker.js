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
const VERSION = "v38-20260828-band-copy-nominal";
const SHELL_CACHE = `gold-shell-${VERSION}`;

const SHELL_FILES = [
  "./",
  "./index.html",
  "./style.css",
  "./app.js",
  "./i18n.js",
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

// All JSON data files get network-first treatment (same as prices.json).
const DATA_FILES = [
  "prices.json",
  "forecast.json",
  "backtest.json",
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

function isDataFile(url) {
  return DATA_FILES.some(
    (f) => url.pathname.endsWith(`/${f}`) || url.pathname.endsWith(`data/${f}`)
  );
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
    e.respondWith(
      fetch(e.request)
        .then((res) => {
          const copy = res.clone();
          caches.open(SHELL_CACHE).then((c) => c.put(e.request, copy));
          return res;
        })
        .catch(() => caches.match(e.request))
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
