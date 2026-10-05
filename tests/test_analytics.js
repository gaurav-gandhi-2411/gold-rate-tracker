// tests/test_analytics.js -- analytics.js behind its flag, no browser required.
//
// Loads the REAL flags.js + analytics.js into a fresh vm context with recording stubs. The
// property under test is "OFF means off": with the flag off, or the endpoint empty, nothing is
// requested, no listener registered, and the DOM/storage are never touched. The "on" cases use a
// source variant (flag + endpoint patched) to prove the same stubs DO record when enabled, so the
// off assertions cannot pass vacuously.

import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const FLAGS = fs.readFileSync(path.join(ROOT, "flags.js"), "utf8");
const ANALYTICS = fs.readFileSync(path.join(ROOT, "analytics.js"), "utf8");
const ENDPOINT = "https://example.goatcounter.com/count";

function boom(what) {
  return new Proxy({}, { get() { throw new Error(`touched ${what}`); }, set() { throw new Error(`touched ${what}`); } });
}

function load({
  flagOn = false,
  endpoint = "",
  hostname = "gaurav-gandhi-2411.github.io",
  search = "",
  referrer = "",
  lang = "en",
  standalone = false,
  dnt = null,
  gpc = undefined,
  online = true,
  imageThrows = false,
} = {}) {
  const log = { images: [], listeners: [], domReads: 0 };
  class Image {
    constructor() {
      if (imageThrows) throw new Error("no Image");
      log.images.push(this);
    }
  }
  const sandbox = {
    location: { hostname, search, pathname: "/index.html", origin: `https://${hostname}` },
    URLSearchParams,
    URL,
    Image,
    navigator: { doNotTrack: dnt, globalPrivacyControl: gpc, onLine: online, standalone: false },
    document: {
      get referrer() { log.domReads++; return referrer; },
      get documentElement() { log.domReads++; return { lang }; },
      get cookie() { throw new Error("touched document.cookie"); },
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
  if (flagOn) flags = flags.replace("analytics: false,", "analytics: true,");
  if (endpoint) analytics = analytics.replace('const ANALYTICS_ENDPOINT = "";', `const ANALYTICS_ENDPOINT = "${endpoint}";`);
  if (flagOn) assert.notEqual(flags, FLAGS, "precondition: flag patch applied");
  if (endpoint) assert.notEqual(analytics, ANALYTICS, "precondition: endpoint patch applied");
  vm.runInContext(flags, sandbox, { filename: "flags.js" });
  vm.runInContext(analytics, sandbox, { filename: "analytics.js" });
  return { sandbox, log, urls: () => log.images.map((i) => i.src) };
}

test("the shipped flag is off and the shipped endpoint is empty", () => {
  assert.match(FLAGS, /analytics: false/);
  assert.match(ANALYTICS, /const ANALYTICS_ENDPOINT = "";/);
});

test("OFF (shipped defaults): no request, no listener, no DOM read, no storage touch", () => {
  const { log } = load();
  assert.equal(log.images.length, 0);
  assert.equal(log.listeners.length, 0);
  assert.equal(log.domReads, 0);
});

test("flag off but endpoint set: still nothing", () => {
  const { log } = load({ endpoint: ENDPOINT });
  assert.equal(log.images.length, 0);
  assert.equal(log.listeners.length, 0);
  assert.equal(log.domReads, 0);
});

test("flag on but endpoint empty: still nothing", () => {
  const { log } = load({ flagOn: true });
  assert.equal(log.images.length, 0);
  assert.equal(log.listeners.length, 0);
});

test("?ff=analytics on localhost with the shipped empty endpoint: still nothing", () => {
  const { log } = load({ hostname: "localhost", search: "?ff=analytics" });
  assert.equal(log.images.length, 0);
});

test("?ff=analytics on the live host is ignored even with an endpoint", () => {
  const { log } = load({ endpoint: ENDPOINT, search: "?ff=analytics" });
  assert.equal(log.images.length, 0);
});

test("non-https endpoint is refused", () => {
  const { log } = load({ flagOn: true, endpoint: "http://example.com/count" });
  assert.equal(log.images.length, 0);
});

test("ON: sends a page view, a language event and registers the install listener; nothing else", () => {
  const { log, urls } = load({
    flagOn: true,
    endpoint: ENDPOINT,
    referrer: "https://www.google.com/search?q=gold+rate+secret",
    lang: "hi",
  });
  const u = urls();
  assert.equal(u.length, 2);
  const view = new URL(u[0]);
  assert.equal(view.origin + view.pathname, ENDPOINT);
  assert.equal(view.searchParams.get("p"), "/index.html");
  assert.equal(view.searchParams.get("e"), null);
  // Referrer is reduced to its origin: the search query must never leave the browser.
  assert.equal(view.searchParams.get("r"), "https://www.google.com");
  assert.ok(!u[0].includes("secret"));
  const ev = new URL(u[1]);
  assert.equal(ev.searchParams.get("p"), "lang/hi");
  assert.equal(ev.searchParams.get("e"), "true");
  assert.deepEqual(log.listeners.map((l) => l.type), ["appinstalled"]);
  assert.equal(log.images.every((i) => i.referrerPolicy === "no-referrer"), true);
});

test("ON: same-site referrer is dropped; standalone adds a mode event; install fires an event", () => {
  const { log, urls } = load({
    flagOn: true,
    endpoint: ENDPOINT,
    referrer: "https://gaurav-gandhi-2411.github.io/gold-rate-tracker/how-we-know.html",
    standalone: true,
  });
  assert.equal(new URL(urls()[0]).searchParams.get("r"), null);
  assert.deepEqual(urls().map((x) => new URL(x).searchParams.get("p")), ["/index.html", "lang/en", "mode/standalone"]);
  log.listeners[0].fn();
  assert.equal(new URL(urls().at(-1)).searchParams.get("p"), "pwa/installed");
});

test("ON: Do Not Track, Global Privacy Control and offline each suppress every request", () => {
  for (const opts of [{ dnt: "1" }, { gpc: true }, { online: false }]) {
    const { log } = load({ flagOn: true, endpoint: ENDPOINT, ...opts });
    assert.equal(log.images.length, 0, JSON.stringify(opts));
  }
});

test("ON: a failing Image constructor never throws out of the page script", () => {
  assert.doesNotThrow(() => load({ flagOn: true, endpoint: ENDPOINT, imageThrows: true }));
});

test("ON never touches cookies or storage (the stubs throw on any access)", () => {
  // load() would throw if analytics.js read document.cookie / localStorage / sessionStorage /
  // indexedDB; reaching the assertion proves it did not.
  const { log } = load({ flagOn: true, endpoint: ENDPOINT, standalone: true });
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
