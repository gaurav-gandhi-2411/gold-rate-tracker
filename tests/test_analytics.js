// tests/test_analytics.js -- analytics.js (GoatCounter pixel) behind its flag, no browser required.
//
// Shipped state (2026-10-09): flag ON, site code gold-rate-tracker. The OFF cases below patch the
// shipped flag/code back to off/empty in memory, so they still prove the stubs record nothing
// when disabled; `shipped: true` loads the files exactly as served.
//
// Loads the REAL flags.js + analytics.js into a fresh vm context with recording stubs. The
// property under test is "OFF means off": with the flag off, or the site code empty, nothing is
// requested, no listener registered, and the DOM/storage are never touched. The "on" cases use a
// source variant (flag + site code patched) to prove the same stubs DO record when enabled, so the
// off assertions cannot pass vacuously. GoatCounter URL shape: https://<code>.goatcounter.com/count
// with p (path or event name), e=true for events, r (referrer origin); never t (title), q, s.

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const FLAGS_SHIPPED = fs.readFileSync(path.join(ROOT, "flags.js"), "utf8");
const ANALYTICS_SHIPPED = fs.readFileSync(path.join(ROOT, "analytics.js"), "utf8");
const SHIPPED_CODE = "gold-rate-tracker";
// the OFF/empty baseline the patch-based cases start from
const FLAGS = FLAGS_SHIPPED.replace("analytics: true,", "analytics: false,");
const ANALYTICS = ANALYTICS_SHIPPED.replace(
  `const ANALYTICS_SITE_CODE = "${SHIPPED_CODE}";`,
  'const ANALYTICS_SITE_CODE = "";'
);
const CODE = "examplecode";
const ENDPOINT = `https://${CODE}.goatcounter.com/count`;

function boom(what) {
  return new Proxy({}, { get() { throw new Error(`touched ${what}`); }, set() { throw new Error(`touched ${what}`); } });
}

function load({
  shipped = false,
  flagOn = false,
  code = "",
  hostname = "gaurav-gandhi-2411.github.io",
  search = "",
  pathname = "/index.html",
  referrer = "",
  lang = "en",
  standalone = false,
  dnt = null,
  gpc = undefined,
  online = true,
  imageThrows = false,
} = {}) {
  const log = { images: [], listeners: [], domReads: 0, notes: [] };
  class Image {
    constructor() {
      if (imageThrows) throw new Error("no Image");
      log.images.push(this);
    }
  }
  const footer = { appendChild: (el) => log.notes.push(el) };
  const sandbox = {
    location: { hostname, search, pathname, origin: `https://${hostname}`, hash: "#section-trend" },
    URLSearchParams,
    URL,
    Image,
    t: (k) => `<<${k}>>`,
    navigator: { doNotTrack: dnt, globalPrivacyControl: gpc, onLine: online, standalone: false },
    document: {
      get referrer() { log.domReads++; return referrer; },
      get documentElement() { log.domReads++; return { lang }; },
      get cookie() { throw new Error("touched document.cookie"); },
      querySelector: (sel) => { log.domReads++; return sel === ".site-footer" ? footer : null; },
      getElementById: () => { log.domReads++; return null; },
      createElement: () => ({ setAttribute(k, v) { this[k] = v; } }),
    },
    window: {
      matchMedia: () => ({ matches: standalone }),
      addEventListener: (type, fn) => log.listeners.push({ type, fn }),
    },
    localStorage: boom("localStorage"),
    sessionStorage: boom("sessionStorage"),
    indexedDB: boom("indexedDB"),
    console,
  };
  vm.createContext(sandbox);
  let flags = FLAGS;
  let analytics = ANALYTICS;
  if (shipped) {
    flags = FLAGS_SHIPPED;
    analytics = ANALYTICS_SHIPPED;
  }
  if (flagOn) flags = flags.replace("analytics: false,", "analytics: true,");
  if (code) analytics = analytics.replace('const ANALYTICS_SITE_CODE = "";', `const ANALYTICS_SITE_CODE = "${code}";`);
  if (flagOn) assert.notEqual(flags, FLAGS, "precondition: flag patch applied");
  if (code) assert.notEqual(analytics, ANALYTICS, "precondition: site-code patch applied");
  vm.runInContext(flags, sandbox, { filename: "flags.js" });
  vm.runInContext(analytics, sandbox, { filename: "analytics.js" });
  return { sandbox, log, urls: () => log.images.map((i) => i.src) };
}

test("the shipped flag is on and the shipped site code is gold-rate-tracker", () => {
  assert.match(FLAGS_SHIPPED, /analytics: true/);
  assert.match(ANALYTICS_SHIPPED, /const ANALYTICS_SITE_CODE = "gold-rate-tracker";/);
  assert.notEqual(FLAGS, FLAGS_SHIPPED, "baseline really is the off state");
  assert.notEqual(ANALYTICS, ANALYTICS_SHIPPED);
});

test("SHIPPED (as served): exactly one page view goes to the account's own endpoint, nothing else", () => {
  const { log, urls, sandbox } = load({ shipped: true });
  const endpoint = `https://${SHIPPED_CODE}.goatcounter.com/count`;
  assert.equal(sandbox.analyticsEndpoint(SHIPPED_CODE), endpoint);
  const u = urls();
  assert.equal(u.length, 1);
  assert.ok(u.every((x) => x.startsWith(endpoint + "?")));
  assert.equal(new URL(u[0]).searchParams.get("p"), "/index.html");
  assert.equal(log.listeners.length, 0);
  assert.equal(log.notes.length, 1);
  assert.equal(log.notes[0].id, "privacy-note");
});

test("OFF (shipped defaults): no request, no listener, no DOM read, no note, no storage touch", () => {
  const { log } = load();
  assert.equal(log.images.length, 0);
  assert.equal(log.listeners.length, 0);
  assert.equal(log.domReads, 0);
  assert.equal(log.notes.length, 0);
});

test("flag off but site code set: still nothing", () => {
  const { log } = load({ code: CODE });
  assert.equal(log.images.length, 0);
  assert.equal(log.listeners.length, 0);
  assert.equal(log.domReads, 0);
  assert.equal(log.notes.length, 0);
});

test("flag on but site code empty: still nothing, and no privacy note", () => {
  const { log } = load({ flagOn: true });
  assert.equal(log.images.length, 0);
  assert.equal(log.listeners.length, 0);
  assert.equal(log.notes.length, 0);
});

test("?ff=analytics on localhost with the shipped empty site code: still nothing", () => {
  const { log } = load({ hostname: "localhost", search: "?ff=analytics" });
  assert.equal(log.images.length, 0);
});

test("?ff=analytics on the live host is ignored even with a site code", () => {
  const { log } = load({ code: CODE, search: "?ff=analytics" });
  assert.equal(log.images.length, 0);
});

test("a site code that is not a plain DNS label is refused (no request to any other host)", () => {
  for (const bad of ["a.evil.example", "evil.example/x", "-abc", "abc-", "UPPER", "a b", "x".repeat(64)]) {
    const { log } = load({ flagOn: true, code: bad });
    assert.equal(log.images.length, 0, bad);
  }
});

test("analyticsEndpoint builds only https://<code>.goatcounter.com/count", () => {
  const { sandbox } = load();
  assert.equal(sandbox.analyticsEndpoint("examplecode"), ENDPOINT);
  assert.equal(sandbox.analyticsEndpoint("a-1"), "https://a-1.goatcounter.com/count");
  assert.equal(sandbox.analyticsEndpoint(""), "");
  assert.equal(sandbox.analyticsEndpoint(undefined), "");
  assert.equal(sandbox.analyticsEndpoint("http://x"), "");
});

test("ON: ONE request per visit (page view with a cleaned referrer), plus the privacy note", () => {
  const { log, urls } = load({
    flagOn: true,
    code: CODE,
    referrer: "https://www.google.com/search?q=gold+rate+secret#frag",
    lang: "hi",
    search: "?token=abc123&utm=x",
  });
  const u = urls();
  assert.equal(u.length, 1);
  const view = new URL(u[0]);
  assert.equal(view.protocol, "https:");
  assert.equal(view.origin + view.pathname, ENDPOINT);
  assert.equal(view.searchParams.get("p"), "/index.html");
  assert.equal(view.searchParams.get("e"), null);
  // Only p and r -- no title, screen size, campaign query, language or app-mode event.
  assert.deepEqual([...view.searchParams.keys()].sort(), ["p", "r"]);
  for (const k of ["t", "s", "q", "b", "e"]) assert.equal(view.searchParams.has(k), false, k);
  // Page query string and hash never leave the browser.
  assert.ok(!u[0].includes("token") && !u[0].includes("abc123") && !u[0].includes("section-trend"));
  // Referrer: origin + path; its query string and hash never leave the browser.
  assert.equal(view.searchParams.get("r"), "https://www.google.com/search");
  assert.ok(!u[0].includes("secret") && !u[0].includes("frag"));
  assert.equal(log.listeners.length, 0, "no install listener: no second event");
  assert.equal(log.images.every((i) => i.referrerPolicy === "no-referrer"), true);
  assert.equal(log.notes.length, 1);
  assert.equal(log.notes[0].id, "privacy-note");
  assert.equal(log.notes[0].textContent, "<<privacyNote>>");
});

test("ON: a referrer from this site is dropped; another site on the same github.io origin is kept", () => {
  const own = load({
    flagOn: true,
    code: CODE,
    referrer: "https://gaurav-gandhi-2411.github.io/gold-rate-tracker/how-we-know.html",
    pathname: "/gold-rate-tracker/",
  });
  assert.equal(new URL(own.urls()[0]).searchParams.get("r"), null);
  const other = load({
    flagOn: true,
    code: CODE,
    referrer: "https://gaurav-gandhi-2411.github.io/portfolio/?a=1",
    pathname: "/gold-rate-tracker/",
  });
  assert.equal(new URL(other.urls()[0]).searchParams.get("r"), "https://gaurav-gandhi-2411.github.io/portfolio/");
});

test("ON: an installed-app launch is the SAME single request under a distinct path", () => {
  const { urls, log } = load({ flagOn: true, code: CODE, standalone: true, pathname: "/gold-rate-tracker/" });
  assert.equal(urls().length, 1);
  assert.equal(new URL(urls()[0]).searchParams.get("p"), "/app/gold-rate-tracker/");
  assert.equal(log.listeners.length, 0);
});

test("ON: Do Not Track, Global Privacy Control and offline each suppress every request and the note", () => {
  const cases = [{ dnt: "1" }, { dnt: "yes" }, { gpc: true }, { online: false }];
  for (const opts of cases) {
    const { log } = load({ flagOn: true, code: CODE, ...opts });
    assert.equal(log.images.length, 0, JSON.stringify(opts));
    assert.equal(log.notes.length, opts.online === false ? 1 : 0, JSON.stringify(opts));
  }
  // Do Not Track "0" / unset is not a signal.
  for (const dnt of ["0", null, "unspecified"]) {
    const { urls } = load({ flagOn: true, code: CODE, dnt });
    assert.equal(urls().length, 1, String(dnt));
  }
});

test("ON but refused by GPC: no privacy note either (nothing is being counted)", () => {
  for (const opts of [{ gpc: true }]) {
    const { log } = load({ flagOn: true, code: CODE, ...opts });
    assert.equal(log.notes.length, 0, JSON.stringify(opts));
  }
});

test("ON: a failing Image constructor never throws out of the page script", () => {
  assert.doesNotThrow(() => load({ flagOn: true, code: CODE, imageThrows: true }));
});

test("ON never touches cookies or storage (the stubs throw on any access)", () => {
  // load() would throw if analytics.js read document.cookie / localStorage / sessionStorage /
  // indexedDB; reaching the assertion proves it did not.
  const { log } = load({ flagOn: true, code: CODE, standalone: true });
  assert.equal(log.images.length, 1);
});

test("analyticsBuildUrl / analyticsReferrerClean are pure and strip detail", () => {
  const { sandbox } = load();
  assert.equal(
    sandbox.analyticsBuildUrl(ENDPOINT, { kind: "view", path: "/x", referrer: "" }),
    ENDPOINT + "?p=%2Fx"
  );
  const c = (r) => sandbox.analyticsReferrerClean(r, "https://a.b", "/site");
  assert.equal(c("not a url"), "");
  assert.equal(c(""), "");
  assert.equal(c("javascript:alert(1)"), "");
  assert.equal(c("https://t.co/abc?x=1#y"), "https://t.co/abc");
  assert.equal(c("https://u:p@t.co/"), "https://t.co");
  assert.equal(c("https://a.b/site/x.html"), "");
  assert.equal(c("https://a.b/sitemap"), "https://a.b/sitemap");
});
