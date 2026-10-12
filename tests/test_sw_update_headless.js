// tests/test_sw_update_headless.js -- the service-worker UPDATE path, for real (2026-10-12).
//
// Why: #2732 fixed "a returning visitor runs the previous deploy's code" but its tests only grep the
// source; the update itself was proven by hand. This drives real Chromium through the real thing:
//   1. install an OLDER build's worker, then serve a NEWER build (Pages-like Cache-Control: max-age=600)
//   2. visit once and assert: exactly one update (one self-reload), the new app.js in the cache, the old
//      cache evicted, the price rendering, no uncaught errors
//   3. offline: the cached shell still renders; slow network: the 3 s timeout serves the cached shell
//   4. build skew: with a SLOW page but FAST scripts, the page and its scripts are still one build
// Every guarantee has a CONTROL that must FAIL on the worker that lacked it (the pre-#2732 worker for the
// update, the #2732 worker for skew), so the test cannot pass vacuously. The old workers are frozen in
// tests/fixtures/sw_history/.
//
// Run: node tests/test_sw_update_headless.js [ROOT]   (HEADED=1 shows the browser; GRT_CHROME=<path to
// chrome.exe> uses an installed Chrome instead of Playwright's Chromium)
// Requires: scraper/node_modules (npm ci in scraper/ first)

import http from "node:http";
import path from "node:path";
import fs from "node:fs";
import os from "node:os";
import { fileURLToPath } from "node:url";
import pkg from "../scraper/node_modules/playwright/index.js";
import { LOCAL_ONLY_ARGS } from "./helpers/local_only_browser.js";
const { chromium } = pkg;

const ROOT = path.resolve(process.argv[2] || ".");
const FIX = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures", "sw_history");
const MIME = {
  ".html": "text/html", ".js": "application/javascript", ".css": "text/css",
  ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml",
  ".webmanifest": "application/manifest+json", ".woff2": "font/woff2",
};
const PASS = "\x1b[32mPASS\x1b[0m";
const FAIL = "\x1b[31mFAIL\x1b[0m";
let failures = 0;
function assert(label, ok, detail = "") {
  console.log(`  ${ok ? PASS : FAIL}  ${label}${!ok && detail ? " — " + detail : ""}`);
  if (!ok) failures++;
}

// ---- building "deploys" ---------------------------------------------------------------------------
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "grt-swupdate-"));
const SHELL = [
  "index.html", "app.js", "i18n.js", "flags.js", "analytics.js", "style.css",
  "manifest.webmanifest", "how-we-know.html", "how-we-know.js", "how-we-know-strings.js",
];
const TAG = /(\b(?:src|href)="[A-Za-z0-9_.-]+\.(?:js|css))(\?v=[^"&]*)?"/g;

/** One deployed build: the repo's shell with a marker per file, a worker, and matching tags. */
function build(name, { version, worker, tagged }) {
  const dir = path.join(TMP, name);
  fs.mkdirSync(path.join(dir, "data"), { recursive: true });
  for (const f of SHELL) fs.copyFileSync(path.join(ROOT, f), path.join(dir, f));
  for (const d of ["icons", "fonts"]) fs.cpSync(path.join(ROOT, d), path.join(dir, d), { recursive: true });
  for (const f of fs.readdirSync(path.join(ROOT, "data"))) {
    if (f.endsWith(".json")) fs.copyFileSync(path.join(ROOT, "data", f), path.join(dir, "data", f));
  }
  // The marker each file carries lets the test see which build a piece of the page came from.
  fs.appendFileSync(path.join(dir, "app.js"), `\nwindow.__JS_BUILD__ = ${JSON.stringify(name)};\n`);
  let html = fs.readFileSync(path.join(dir, "index.html"), "utf8");
  html = html.replace(TAG, (_m, pre) => (tagged ? `${pre}?v=${version}"` : `${pre}"`));
  html = html.replace("</body>", `<script>window.__HTML_BUILD__ = ${JSON.stringify(name)};</script></body>`);
  fs.writeFileSync(path.join(dir, "index.html"), html);
  // The worker also checks how-we-know.html at install, so it carries the same tags.
  const hwk = fs.readFileSync(path.join(dir, "how-we-know.html"), "utf8")
    .replace(TAG, (_m, pre) => (tagged ? `${pre}?v=${version}"` : `${pre}"`));
  fs.writeFileSync(path.join(dir, "how-we-know.html"), hwk);
  let sw = worker === "current"
    ? fs.readFileSync(path.join(ROOT, "service-worker.js"), "utf8")
    : fs.readFileSync(path.join(FIX, worker), "utf8");
  sw = sw.replace(/const VERSION = "[^"]*";/, `const VERSION = "${version}";`);
  fs.writeFileSync(path.join(dir, "service-worker.js"), sw);
  return dir;
}

// ---- a Pages-like server whose document root (the "deploy") can be swapped ------------------------
let current = null;
const delays = new Map(); // pathname -> ms
const server = http.createServer((req, res) => {
  const p = decodeURIComponent(req.url.split("?")[0]);
  const wait = delays.get(p === "/" ? "/index.html" : p) || 0;
  setTimeout(() => {
    const f = path.join(current, p === "/" ? "index.html" : p);
    try {
      const d = fs.readFileSync(f);
      res.writeHead(200, {
        "Content-Type": MIME[path.extname(f)] || "application/octet-stream",
        "Cache-Control": "max-age=600", // what GitHub Pages sends; the cause of the stale precache
      });
      res.end(d);
    } catch {
      res.writeHead(404);
      res.end("Not found");
    }
  }, wait);
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}`;

// One browser per scenario: an installed worker and its caches must not leak between scenarios that reuse
// the same origin and cache names.
const browsers = [];
async function launch() {
  const b = await chromium.launch({
    headless: !process.env.HEADED,
    args: LOCAL_ONLY_ARGS,
    ...(process.env.GRT_CHROME ? { executablePath: process.env.GRT_CHROME } : {}),
  });
  browsers.push(b);
  return b;
}

/** A page that records uncaught errors and unhandled rejections from the first byte. */
async function newTrackedPage(ctx) {
  const page = await ctx.newPage();
  page.errors = [];
  page.navs = 0;
  page.on("pageerror", (e) => page.errors.push(String(e).slice(0, 160)));
  page.on("framenavigated", (f) => { if (f === page.mainFrame()) page.navs++; });
  await page.addInitScript(() => {
    window.addEventListener("unhandledrejection", (e) => {
      (window.__rej = window.__rej || []).push(String(e.reason).slice(0, 160));
    });
  });
  return page;
}

async function state(page) {
  return page.evaluate(async () => {
    const keys = await caches.keys();
    let cachedApp = "";
    if (keys[0]) {
      const r = await (await caches.open(keys[0])).match("./app.js");
      cachedApp = r ? await r.text() : "";
    }
    const price = document.getElementById("hero-price")?.textContent || "";
    return {
      keys, htmlBuild: window.__HTML_BUILD__, jsBuild: window.__JS_BUILD__,
      cachedJsBuild: (/__JS_BUILD__ = "([^"]+)"/.exec(cachedApp) || [])[1],
      price, priceOk: /₹\s?[\d,]{4,}/.test(price), rej: window.__rej || [],
    };
  });
}

/** Install `oldDir`'s worker, "deploy" `newDir`, visit once; return the observed state. */
async function updateScenario(oldDir, newDir) {
  const browser = await launch();
  const ctx = await browser.newContext({ serviceWorkers: "allow" });
  const page = await newTrackedPage(ctx);
  current = oldDir;
  await page.goto(base, { waitUntil: "load" });
  await page.evaluate(async () => { await navigator.serviceWorker.ready; });
  await page.waitForTimeout(2500); // install + claim + first data
  current = newDir; // the deploy
  page.navs = 0;
  page.errors = [];
  await page.goto(base, { waitUntil: "load" });
  await page.waitForTimeout(7000); // the new worker installs, takes over, and the page reloads once
  return { browser, ctx, page, st: await state(page) };
}

try {
  const oldBuild = build("old", { version: "va", worker: "sw_pre_2732.js", tagged: false });
  // GRT_NEW_WORKER=sw_pre_2732.js swaps the worker under test for the pre-#2732 one: the run must then FAIL.
  const newBuild = build("new", { version: "vb", worker: process.env.GRT_NEW_WORKER || "current", tagged: true });
  const newOnOldWorker = build("new-on-pre2732-worker", { version: "vb", worker: "sw_pre_2732.js", tagged: false });

  console.log(`\nUpdate: an older build's worker, then a newer build is deployed (ROOT=${ROOT})`);
  const upd = await updateScenario(oldBuild, newBuild);
  console.log(`  ${JSON.stringify({ ...upd.st, rej: upd.st.rej.length, navs: upd.page.navs })}`);
  assert("exactly one update: the visit loaded the page twice (stale paint, then one self-reload)", upd.page.navs === 2,
    `navigations: ${upd.page.navs}`);
  assert("the page now running is the NEW build", upd.st.htmlBuild === "new" && upd.st.jsBuild === "new",
    `${upd.st.htmlBuild}/${upd.st.jsBuild}`);
  assert("the new worker's cache holds the NEW app.js", upd.st.cachedJsBuild === "new", String(upd.st.cachedJsBuild));
  assert("only the new cache is left (the old one was evicted)", upd.st.keys.length === 1 && upd.st.keys[0] === "gold-shell-vb",
    JSON.stringify(upd.st.keys));
  assert("the price renders", upd.st.priceOk, upd.st.price);
  assert("no uncaught errors or unhandled rejections", upd.page.errors.length === 0 && upd.st.rej.length === 0,
    JSON.stringify([...upd.page.errors, ...upd.st.rej]));

  console.log("\nOffline: the cached shell still renders");
  await upd.ctx.setOffline(true);
  upd.page.errors = [];
  if (process.env.DEBUG_SW) upd.page.on("response", (r) => { if (/.(js|css)/.test(r.url())) console.log("    resp", r.url().replace(base, ""), r.status(), "fromSW=" + r.fromServiceWorker()); });
  await upd.page.reload({ waitUntil: "load" }).catch(() => {});
  await upd.page.waitForTimeout(2500);
  if (process.env.DEBUG_SW) console.log("    markers via page fetch:", await upd.page.evaluate(async () => { const t = await (await fetch("app.js?v=vb")).text(); return [...t.matchAll(/__JS_BUILD__ = "([^"]+)"/g)].map((m) => m[1]); }));
  const off = await state(upd.page);
  console.log(`  ${JSON.stringify({ ...off, rej: off.rej.length })}`);
  assert("offline reload serves the cached page and its scripts (one build)", off.htmlBuild === "new" && off.jsBuild === "new");
  assert("offline: the price renders from the cached data", off.priceOk, off.price);
  assert("offline: no uncaught errors or unhandled rejections", upd.page.errors.length === 0 && off.rej.length === 0,
    JSON.stringify([...upd.page.errors, ...off.rej]));
  await upd.ctx.setOffline(false);

  console.log("\nSlow network: the 3 s timeout serves the cached shell instead of waiting");
  {
    const slowDeploy = build("slow", { version: "vb", worker: "current", tagged: true }); // same VERSION, newer content
    current = slowDeploy;
    delays.set("/index.html", 7000);
    const t0 = Date.now();
    await upd.page.goto(base, { waitUntil: "load", timeout: 30000 });
    const took = Date.now() - t0;
    const st = await state(upd.page);
    delays.clear();
    console.log(`  load took ${took} ms; ${JSON.stringify({ htmlBuild: st.htmlBuild, jsBuild: st.jsBuild })}`);
    assert("the cached page was served (not the 7 s-late network copy)", st.htmlBuild === "new", String(st.htmlBuild));
    assert("it arrived at about the 3 s timeout (2.8 s .. 6 s)", took >= 2800 && took <= 6000, `${took} ms`);
  }
  await upd.browser.close();

  // Run after the scenarios above are closed: a still-open page would update itself to this worker.
  console.log("\n  CONTROL: the same deploy handled by the PRE-#2732 worker (cache.add through the HTTP cache) must FAIL");
  const ctl = await updateScenario(oldBuild, newOnOldWorker);
  console.log(`  ${JSON.stringify({ ...ctl.st, rej: ctl.st.rej.length })}`);
  assert("control: the pre-#2732 worker leaves the OLD app.js in its fresh cache (the bug this test pins)",
    ctl.st.cachedJsBuild === "old", String(ctl.st.cachedJsBuild));
  await ctl.browser.close();


  console.log("\nBuild skew: slow PAGE, fast SCRIPTS (the case that paired a cached page with a fresh script)");
  async function skewScenario(name, worker, tagged) {
    const first = build(`${name}-1`, { version: "vb", worker, tagged });
    const second = build(`${name}-2`, { version: "vc", worker, tagged }); // the next deploy
    const browser = await launch();
    const ctx = await browser.newContext({ serviceWorkers: "allow" });
    const page = await newTrackedPage(ctx);
    current = first;
    await page.goto(base, { waitUntil: "load" });
    await page.evaluate(async () => { await navigator.serviceWorker.ready; });
    await page.waitForTimeout(2500);
    current = second;
    delays.set("/index.html", 7000); // only the page is slow; app.js and the rest answer at once
    delays.set("/service-worker.js", 7000); // so the worker does not swap builds mid-measurement
    await page.goto(base, { waitUntil: "load", timeout: 30000 });
    const st = await state(page);
    delays.clear();
    await browser.close();
    return st;
  }
  const good = await skewScenario("tagged", "current", true);
  console.log(`  tagged build: html=${good.htmlBuild} js=${good.jsBuild}`);
  assert("this worker: the page and its scripts are the same build", good.htmlBuild === good.jsBuild && good.htmlBuild === "tagged-1",
    `${good.htmlBuild}/${good.jsBuild}`);
  const bad = await skewScenario("untagged", "sw_2732.js", false);
  console.log(`  CONTROL (#2732 worker, untagged): html=${bad.htmlBuild} js=${bad.jsBuild}`);
  assert("control: the #2732 worker DID pair a cached page with a newer script (the bug this pins)",
    bad.htmlBuild !== bad.jsBuild, `${bad.htmlBuild}/${bad.jsBuild}`);
} finally {
  for (const b of browsers) await b.close().catch(() => {});
  server.close();
  fs.rmSync(TMP, { recursive: true, force: true });
}

console.log(`\n${failures === 0 ? PASS : FAIL}  ${failures === 0 ? "Service-worker update path works." : `${failures} check(s) failed.`}\n`);
process.exit(failures === 0 ? 0 : 1);
