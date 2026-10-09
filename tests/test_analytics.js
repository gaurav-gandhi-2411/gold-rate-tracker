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

test("SHIPPED (as served): view + language event go to the account's own endpoint, nothing else", () => {
  const { log, urls, sandbox } = load({ shipped: true });
  const endpoint = `https://${SHIPPED_CODE}.goatcounter.com/count`;
  assert.equal(sandbox.analyticsEndpoint(SHIPPED_CODE), endpoint);
  const u = urls();
  assert.equal(u.length, 2);
  assert.ok(u.every((x) => x.startsWith(endpoint + "?")));
  assert.equal(new URL(u[0]).searchParams.get("p"), "/index.html");
  assert.equal(new URL(u[1]).searchParams.get("p"), "lang/en");
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

test("ON: sends a page view, a language event, adds the privacy note, registers the install listener", () => {
  const { log, urls } = load({
    flagOn: true,
    code: CODE,
    referrer: "https://www.google.com/search?q=gold+rate+secret",
    lang: "hi",
    search: "?token=abc123&utm=x",
  });
  const u = urls();
  assert.equal(u.length, 2);
  const view = new URL(u[0]);
  assert.equal(view.protocol, "https:");
  assert.equal(view.origin + view.pathname, ENDPOINT);
  assert.equal(view.searchParams.get("p"), "/index.html");
  assert.equal(view.searchParams.get("e"), null);
  // Only p (and r when there is an outside referrer) -- no title, screen size, campaign query.
  assert.deepEqual([...view.searchParams.keys()].sort(), ["p", "r"]);
  for (const k of ["t", "s", "q", "b"]) assert.equal(view.searchParams.has(k), false, k);
  // Page query string and hash never leave the browser.
  assert.ok(!u[0].includes("token") && !u[0].includes("abc123") && !u[0].includes("section-trend"));
  // Referrer is reduced to its origin: the search query must never leave the browser.
  assert.equal(view.searchParams.get("r"), "https://www.google.com");
  assert.ok(!u[0].includes("secret"));
  const ev = new URL(u[1]);
  assert.equal(ev.searchParams.get("p"), "lang/hi");
  assert.equal(ev.searchParams.get("e"), "true");
  assert.deepEqual([...ev.searchParams.keys()].sort(), ["e", "p"]);
  assert.deepEqual(log.listeners.map((l) => l.type), ["appinstalled"]);
  assert.equal(log.images.every((i) => i.referrerPolicy === "no-referrer"), true);
  assert.equal(log.notes.length, 1);
  assert.equal(log.notes[0].id, "privacy-note");
  assert.equal(log.notes[0].textContent, "<<privacyNote>>");
});

test("ON: same-site referrer is dropped; standalone adds a mode event; install fires an event", () => {
  const { log, urls } = load({
    flagOn: true,
    code: CODE,
    referrer: "https://gaurav-gandhi-2411.github.io/gold-rate-tracker/how-we-know.html",
    standalone: true,
  });
  assert.equal(new URL(urls()[0]).searchParams.get("r"), null);
  assert.deepEqual(urls().map((x) => new URL(x).searchParams.get("p")), ["/index.html", "lang/en", "mode/standalone"]);
  log.listeners[0].fn();
  assert.equal(new URL(urls().at(-1)).searchParams.get("p"), "pwa/installed");
});

test("ON: Do Not Track, Global Privacy Control and offline each suppress every request", () => {
  for (const opts of [{ dnt: "1" }, { dnt: "yes" }, { gpc: true }, { online: false }]) {
    const { log } = load({ flagOn: true, code: CODE, ...opts });
    assert.equal(log.images.length, 0, JSON.stringify(opts));
  }
});

test("ON but refused by Do Not Track / GPC: no privacy note either (nothing is being counted)", () => {
  for (const opts of [{ dnt: "1" }, { gpc: true }]) {
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
  assert.ok(log.images.length >= 3);
});

test("analyticsBuildUrl / analyticsReferrerOrigin are pure and strip detail", () => {
  const { sandbox } = load();
  assert.equal(
    sandbox.analyticsBuildUrl(ENDPOINT, { kind: "view", path: "/x", referrer: "" }),
    ENDPOINT + "?p=%2Fx"
  );
  assert.equal(sandbox.analyticsReferrerOrigin("not a url", "https://a.b"), "");
  assert.equal(sandbox.analyticsReferrerOrigin("", "https://a.b"), "");
  assert.equal(sandbox.analyticsReferrerOrigin("https://t.co/abc?x=1#y", "https://a.b"), "https://t.co");
});
