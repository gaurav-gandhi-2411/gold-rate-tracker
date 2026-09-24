// flags.js — minimal feature-flag mechanism so new user-facing features can be merged OFF.
//
// FEATURE_FLAGS is the single source of truth for what ships to real visitors: every entry here
// stays false until GG deliberately flips it (and bumps service-worker.js's VERSION so the change
// actually reaches installed clients -- see the CACHE INVALIDATION CONTRACT there). A feature PR
// can land, pass CI, and merge with its code fully wired in and still show nothing to a real user,
// because the flag it's gated behind defaults off.
//
// isFeatureOn(name) also honours a `?ff=<name>[,<name>,...]` URL override, but ONLY when the page
// is being served from localhost/127.0.0.1 -- this is a LOCAL PREVIEW mechanism for GG/a reviewer
// to see an off-by-default feature before flipping the flag for everyone, never a production
// toggle. On any other host (including the live gaurav-gandhi-2411.github.io origin) the URL
// param is read but deliberately ignored, so a shared/crawled link can never turn a flag on for
// anyone else. Plain global script, no module system -- loaded before app.js (index.html) / before
// how-we-know.js (how-we-know.html), same convention i18n.js documents at its own top.
const FEATURE_FLAGS = Object.freeze({
  markup_meter: false,
  wait_or_buy: false,
  good_price_v2: false,
  event_watch: false,
  page_v2: false,
});

// Returns true only if:
//   (a) FEATURE_FLAGS[name] === true (the real, production-honoured switch), OR
//   (b) the page is on localhost/127.0.0.1 AND the URL has ?ff=<name> (comma list allowed) --
//       local-only preview, never honoured elsewhere (see file header).
// An unknown flag name always returns false, on every host, regardless of ?ff= -- there is
// nothing to preview if it isn't a real, declared flag.
function isFeatureOn(name) {
  if (!(name in FEATURE_FLAGS)) return false;
  if (FEATURE_FLAGS[name] === true) return true;
  try {
    const host = location.hostname;
    const isLocalHost = host === "localhost" || host === "127.0.0.1";
    if (!isLocalHost) return false;
    const requested = new URLSearchParams(location.search).get("ff");
    if (!requested) return false;
    return requested.split(",").map((s) => s.trim()).includes(name);
  } catch {
    // location/URLSearchParams access failing for any reason must never turn a flag ON --
    // fail closed, same as an unknown flag name.
    return false;
  }
}
