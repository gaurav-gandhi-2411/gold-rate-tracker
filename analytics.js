// analytics.js -- cookieless, aggregate-only usage counting, merged OFF. The option comparison
// and the questions it can and cannot answer are in docs/proposals/analytics-5c.md.
//
// OFF means off: while FEATURE_FLAGS.analytics is false OR ANALYTICS_ENDPOINT is empty,
// initAnalytics() returns before it touches the DOM, registers a listener, reads storage or makes
// a request. Nothing here is user-visible even when on: there is no banner, no text, no element.
//
// Privacy shape (what a count contains, and what it never does):
//   - Sends only: the page path (no query string, no hash), the page language (en/hi), whether the
//     page runs as an installed app, and the referring ORIGIN (never the full referrer URL).
//   - Never reads or writes a cookie, localStorage, sessionStorage or IndexedDB, so it cannot
//     recognise a returning browser. "Returning visitors" is therefore not measurable by design.
//   - Sends nothing if the browser says Do Not Track or Global Privacy Control, or while offline
//     (nothing is queued: a missed count is a missed count).
//   - The counting provider (GoatCounter's documented /count pixel) sees the request's IP and
//     User-Agent transiently to make a per-visit key and says it stores neither
//     (https://www.goatcounter.com/help/sessions). That is the provider's statement, not ours.
//
// Plain global script, no module system, loaded after flags.js (index.html); same convention as
// flags.js. The function names are prefixed `analytics` to avoid clashing with app.js globals.

// Empty = disabled even if the flag is on. GG sets this (and flips the flag) only after creating
// the counting account; e.g. "https://<code>.goatcounter.com/count". No account exists yet.
const ANALYTICS_ENDPOINT = "";

// Pure: the URL for one count. `kind` "view" is a page view; "event" is a named event (GoatCounter
// distinguishes the two with e=true). Exposed separately so tests can pin exactly what is sent.
function analyticsBuildUrl(endpoint, { kind, path, referrer }) {
  const q = new URLSearchParams();
  q.set("p", path);
  if (kind === "event") q.set("e", "true");
  if (referrer) q.set("r", referrer);
  return endpoint + "?" + q.toString();
}

// Pure: reduce a document.referrer to its origin, or "" when same-site/empty/unparseable.
function analyticsReferrerOrigin(referrer, ownOrigin) {
  try {
    if (!referrer) return "";
    const o = new URL(referrer).origin;
    return o === ownOrigin ? "" : o;
  } catch {
    return "";
  }
}

// True only if every gate passes. Fails closed: any exception means "not enabled".
function analyticsEnabled() {
  try {
    if (typeof isFeatureOn !== "function" || !isFeatureOn("analytics")) return false;
    if (typeof ANALYTICS_ENDPOINT !== "string" || !/^https:\/\//.test(ANALYTICS_ENDPOINT)) return false;
    if (navigator.doNotTrack === "1" || navigator.globalPrivacyControl === true) return false;
    return true;
  } catch {
    return false;
  }
}

// Fire-and-forget image GET. Skipped offline (nothing queued). Returns whether a request was
// attempted, for tests. The service worker never caches or intercepts-with-a-response a
// cross-origin image (its fetch handler only passes it through), so this cannot touch the shell
// or data caches.
function analyticsSend(params) {
  try {
    if (!analyticsEnabled()) return false;
    if (navigator.onLine === false) return false;
    const img = new Image();
    img.referrerPolicy = "no-referrer";
    img.src = analyticsBuildUrl(ANALYTICS_ENDPOINT, params);
    return true;
  } catch {
    return false;
  }
}

function initAnalytics() {
  if (!analyticsEnabled()) return;
  const ref = analyticsReferrerOrigin(document.referrer, location.origin);
  analyticsSend({ kind: "view", path: location.pathname, referrer: ref });
  const lang = document.documentElement.lang === "hi" ? "hi" : "en";
  analyticsSend({ kind: "event", path: "lang/" + lang });
  const standalone =
    (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
    navigator.standalone === true;
  if (standalone) analyticsSend({ kind: "event", path: "mode/standalone" });
  window.addEventListener("appinstalled", () => analyticsSend({ kind: "event", path: "pwa/installed" }));
}

initAnalytics();
