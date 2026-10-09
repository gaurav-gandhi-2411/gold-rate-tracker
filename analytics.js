// analytics.js -- cookieless, aggregate-only usage counting via GoatCounter, merged OFF. The option
// comparison and the questions it can and cannot answer are in docs/proposals/analytics-5c.md.
//
// OFF means off: while FEATURE_FLAGS.analytics is false OR ANALYTICS_SITE_CODE is empty,
// initAnalytics() returns before it touches the DOM, registers a listener, reads storage or makes
// a request. While ON the only visible change is one short privacy note in the footer (i18n key
// privacyNote, English; Hindi falls back to English); while OFF nothing is rendered.
//
// ON since 2026-10-09 (GG created the GoatCounter site `gold-rate-tracker` and set Data collection:
// User-Agent, Screen size and Country/Region/Location OFF; Referrer, Language and Sessions ON).
// Provider facts below were read from goatcounter.com/help/{sessions,gdpr,pixel} and /privacy on
// 2026-10-09; the footer note (i18n privacyNote) claims nothing beyond them.
//
// Privacy shape (what a count contains, and what it never does):
//   - ONE request per page load, and nothing else: the page path (no query string, no hash, no
//     title) and the referring page's origin and path (never its query string or hash). An
//     installed-app launch is the same single request under a distinct path (/app + path), so a
//     visit counts once. The language is not sent: GoatCounter reads it from the browser.
//   - Never reads or writes a cookie, localStorage, sessionStorage or IndexedDB, so it cannot
//     recognise a returning browser. "Returning visitors" is therefore not measurable by design.
//   - No script is loaded from the provider: the count is one image GET to the account's own
//     https://<code>.goatcounter.com/count (GoatCounter's documented tracking pixel,
//     https://www.goatcounter.com/help/pixel). No screen size, no fingerprinting inputs.
//   - Sends nothing if the browser sends a Do Not Track or Global Privacy Control signal, or
//     while offline (nothing is queued: a missed count is a missed count).
//   - GoatCounter builds a per-visit key from site + User-Agent + IP in memory only, keeps the
//     key -> random id mapping for 8 hours, and states that the IP address and User-Agent are
//     never stored to the database or disk (https://www.goatcounter.com/help/sessions). Its
//     privacy policy (https://www.goatcounter.com/privacy) adds that it stores no cookies,
//     localStorage, cache or other information in the visitor's browser, and keeps no IP
//     addresses; it states no retention period for the aggregate counts, so none is claimed.
//     Those are the provider's statements, not ours.
//
// Plain global script, no module system, loaded after flags.js, i18n.js and app.js (index.html);
// same convention as flags.js. The function names are prefixed `analytics` to avoid clashing with
// app.js globals.

// The GoatCounter site code (the "<code>" in https://<code>.goatcounter.com). Empty = disabled
// even if the flag is on.
const ANALYTICS_SITE_CODE = "gold-rate-tracker";

// Pure: the count endpoint for a site code, or "" when the code is not a plausible GoatCounter
// code (lowercase letters, digits, hyphens; DNS-label length). Always https, always the
// provider's own domain, so a bad constant can never point the beacon at an arbitrary host.
// (The exact character set GoatCounter accepts for codes is UNVERIFIED; this is the strict subset
// that is a valid DNS label, which any working code must be.)
function analyticsEndpoint(code) {
  if (typeof code !== "string" || !/^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/.test(code)) return "";
  return "https://" + code + ".goatcounter.com/count";
}

// Pure: the URL for one count. `kind` "view" is a page view; "event" is a named event (GoatCounter
// distinguishes the two with e=true, the event name goes in p). The title (t) is deliberately
// never sent. Exposed separately so tests can pin exactly what is sent.
function analyticsBuildUrl(endpoint, { kind, path, referrer }) {
  const q = new URLSearchParams();
  q.set("p", path);
  if (kind === "event") q.set("e", "true");
  if (referrer) q.set("r", referrer);
  return endpoint + "?" + q.toString();
}

// Pure: reduce a document.referrer to origin + path (no query string, no hash, no credentials),
// or "" when empty/unparseable/not http(s) or when it is this site itself. `ownBase` is this
// site's path prefix (github.io hosts many sites on one origin, so the origin alone is not
// "this site").
function analyticsReferrerClean(referrer, ownOrigin, ownBase) {
  try {
    if (!referrer) return "";
    const u = new URL(referrer);
    if (u.protocol !== "https:" && u.protocol !== "http:") return "";
    if (u.origin === ownOrigin && (u.pathname === ownBase || u.pathname.startsWith(ownBase + "/"))) {
      return "";
    }
    return u.origin + (u.pathname === "/" ? "" : u.pathname);
  } catch {
    return "";
  }
}

// Pure: this site's path prefix ("/gold-rate-tracker") from a page path.
function analyticsSiteBase(pathname) {
  const seg = String(pathname || "").split("/")[1] || "";
  return seg && !seg.includes(".") ? "/" + seg : "";
}

// True only if every gate passes. Fails closed: any exception means "not enabled".
function analyticsEnabled() {
  try {
    if (typeof isFeatureOn !== "function" || !isFeatureOn("analytics")) return false;
    if (!analyticsEndpoint(ANALYTICS_SITE_CODE)) return false;
    // The site's stance is no tracking, so an explicit "don't track me" is respected: Do Not Track
    // (restored 2026-10-09 after a brief removal; GoatCounter itself ignores it, we do not) and Global
    // Privacy Control. Consequence: a browser with either signal on is never counted, so the counts
    // understate visitors who have a signal on.
    if (navigator.doNotTrack === "1" || navigator.doNotTrack === "yes") return false;
    if (window.doNotTrack === "1" || (navigator.msDoNotTrack === "1")) return false;
    if (navigator.globalPrivacyControl === true) return false;
    return true;
  } catch {
    return false;
  }
}

// Fire-and-forget image GET. Skipped offline (nothing queued). Returns whether a request was
// attempted, for tests. The service worker's fetch handler does not special-case a cross-origin
// image: it falls through to cache-miss + network fetch and never stores it, so this cannot touch
// the shell or data caches.
function analyticsSend(params) {
  try {
    if (!analyticsEnabled()) return false;
    if (navigator.onLine === false) return false;
    const img = new Image();
    img.referrerPolicy = "no-referrer";
    img.src = analyticsBuildUrl(analyticsEndpoint(ANALYTICS_SITE_CODE), params);
    return true;
  } catch {
    return false;
  }
}

// The privacy note exists only while counting is on, so the page never claims counting while it
// is off and never says "no analytics" while it is on. data-i18n lets the language switch
// re-render it; English text is the fallback for Hindi.
function analyticsRenderPrivacyNote() {
  try {
    if (typeof t !== "function") return;
    const footer = document.querySelector(".site-footer");
    if (!footer || document.getElementById("privacy-note")) return;
    const p = document.createElement("p");
    p.id = "privacy-note";
    p.className = "muted";
    p.setAttribute("data-i18n", "privacyNote");
    p.textContent = t("privacyNote");
    footer.appendChild(p);
  } catch {
    // A missing note must never break the page.
  }
}

function initAnalytics() {
  if (!analyticsEnabled()) return;
  analyticsRenderPrivacyNote();
  const ref = analyticsReferrerClean(document.referrer, location.origin, analyticsSiteBase(location.pathname));
  const standalone =
    (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
    navigator.standalone === true;
  // One request per visit. GoatCounter counts every request (events included) as a visit, so the
  // language and app-mode used to inflate the total about 2.5x; the language comes from the browser.
  analyticsSend({ kind: "view", path: (standalone ? "/app" : "") + location.pathname, referrer: ref });
}

initAnalytics();
