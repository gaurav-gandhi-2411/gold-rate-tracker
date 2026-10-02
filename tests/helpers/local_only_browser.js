// tests/helpers/local_only_browser.js -- launch args that make a headless test independent of every
// third-party host (2026-09-21, AP6).
//
// The pwa-headless tests load the real index.html, which pulls Chart.js from cdn.jsdelivr.net and the
// Sentry bundle from browser.sentry-cdn.com. Both are `async`, and async scripts delay the page `load`
// event, so a slow or hung CDN made these tests slow or red for reasons that have nothing to do with
// this repo (the same single point of failure that caused the 2026-09-21 render-smoke incident). It is
// also why pwa-headless could not be considered as a required check.
//
// Since #1799 and #1802 the page renders correctly without either library, so the tests do not need
// them. The block is done at the BROWSER level (DNS), not with page.route(), on purpose: page.route()
// does not see requests the service worker makes itself, and the offline test keeps the worker active.
// Everything except loopback fails instantly with a name-resolution error instead of hanging.
export const LOCAL_ONLY_ARGS = ["--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost"];
