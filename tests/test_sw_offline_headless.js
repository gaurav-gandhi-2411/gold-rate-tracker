// tests/test_sw_offline_headless.js — the service worker's offline data fallback, for real.
// Loads the app in Chromium with the real service worker, then goes offline and reloads.
//
// Why this exists: service-worker.js claimed "network-first for all data files; fall back to
// cache when offline", but app.js's loadJSON() appends ?t=<Date.now()> as a cache-buster and the
// worker cached under the full request URL. Every load stored a NEW entry per file, and offline
// the lookup used a different ?t= than any stored entry, so it never matched: ALL data requests
// failed offline (verified against the live origin, 2026-09-21). Nothing tested the fallback.
//
// Run: node tests/test_sw_offline_headless.js [ROOT]   (ROOT defaults to the repo root)
// Requires: scraper/node_modules (npm ci in scraper/ first)

import http from "node:http";
import path from "node:path";
import fs from "node:fs";
import pkg from "../scraper/node_modules/playwright/index.js";
import { LOCAL_ONLY_ARGS } from "./helpers/local_only_browser.js";
const { chromium } = pkg;

const ROOT = path.resolve(process.argv[2] || ".");
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

const server = http.createServer((req, res) => {
  const p = req.url.split("?")[0];
  const f = path.join(ROOT, p === "/" ? "index.html" : p);
  try {
    const d = fs.readFileSync(f);
    res.writeHead(200, { "Content-Type": MIME[path.extname(f)] || "application/octet-stream" });
    res.end(d);
  } catch {
    res.writeHead(404);
    res.end("Not found");
  }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}`;

// Third-party hosts (Chart.js / Sentry CDNs) are unreachable by design: see helpers/local_only_browser.js.
const browser = await chromium.launch({ headless: true, args: LOCAL_ONLY_ARGS });
try {
  // A first-time visitor's page must not reload itself. controllerchange also fires for the very first
  // clients.claim(); reloading then flashed every new visitor's price back to the loading skeleton and
  // made the live render smoke test race the reload and page URGENT on a healthy site (2026-09-21).
  console.log(`\nFirst visit does not reload itself (ROOT=${ROOT})`);
  {
    const fctx = await browser.newContext({ serviceWorkers: "allow" });
    const fpage = await fctx.newPage();
    let navs = 0;
    fpage.on("framenavigated", (f) => { if (f === fpage.mainFrame()) navs++; });
    await fpage.goto(base, { waitUntil: "load" });
    await fpage.evaluate(async () => { await navigator.serviceWorker.ready; });
    await fpage.waitForTimeout(4000); // well past SW install + claim (the old reload landed at ~1-2s)
    const controlled = await fpage.evaluate(() => !!navigator.serviceWorker.controller);
    assert("the worker took control of the first visit (test is not vacuous)", controlled);
    assert("the first visit navigated exactly once (no self-reload)", navs === 1, `navigations: ${navs}`);
    await fctx.close();
  }

  const ctx = await browser.newContext({ serviceWorkers: "allow" });
  const page = await ctx.newPage();

  console.log(`\nService-worker offline data fallback (ROOT=${ROOT})`);
  await page.goto(base, { waitUntil: "networkidle" });
  await page.evaluate(async () => { await navigator.serviceWorker.ready; });
  await page.waitForTimeout(1500);
  // Second online load, controlled by the worker: this is what would add a duplicate entry per file.
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForTimeout(1500);

  const keys = await page.evaluate(async () => {
    const out = [];
    for (const k of await caches.keys()) {
      const c = await caches.open(k);
      for (const r of await c.keys()) out.push(r.url);
    }
    return out.filter((u) => u.includes("/data/"));
  });
  const perFile = {};
  for (const u of keys) {
    const name = new URL(u).pathname.split("/").pop();
    perFile[name] = (perFile[name] || 0) + 1;
  }
  console.log(`  data cache entries after 2 online loads: ${JSON.stringify(perFile)}`);
  assert("data files are cached at all (test is not vacuous)", Object.keys(perFile).length >= 3);
  assert("exactly one cache entry per data file (no growth per load)",
    Object.values(perFile).every((n) => n === 1), JSON.stringify(perFile));
  assert("no cached data key carries a query string", keys.every((u) => !u.includes("?")),
    keys.filter((u) => u.includes("?")).slice(0, 2).join(", "));

  await ctx.setOffline(true);
  const seen = {};
  page.on("response", (r) => {
    const u = new URL(r.url());
    if (u.pathname.includes("/data/")) seen[u.pathname.split("/").pop()] = { status: r.status(), sw: r.fromServiceWorker() };
  });
  page.on("requestfailed", (r) => {
    const u = new URL(r.url());
    if (u.pathname.includes("/data/")) seen[u.pathname.split("/").pop()] = { status: 0, sw: false, err: r.failure()?.errorText };
  });
  await page.reload({ waitUntil: "load" }).catch(() => {});
  await page.waitForTimeout(2500);

  const names = Object.keys(seen);
  console.log(`  OFFLINE data requests: ${JSON.stringify(seen)}`);
  assert("the app made data requests while offline (test is not vacuous)", names.length >= 3,
    `saw ${names.length}`);

  // page_v2 (item 6, flagged OFF) added 5 new data/*.json fetches, some of which had no
  // producing pipeline on master yet at the time (see app.js's own comment on MARKUP_TODAY_URL
  // etc.) -- those always 404 online, so the network-first handler's `if (res.ok)` guard (see
  // service-worker.js) correctly never caches them, and they correctly fail offline too: no
  // network, nothing cached, nothing honest to serve. A file with no pipeline yet has no reason
  // to exist in the checked-out tree either, so "shipped" here is derived straight from the
  // filesystem this test's own local server reads from (see the `http.createServer` handler
  // above: it serves `path.join(ROOT, p)` directly, no fixture copy) rather than a hand-maintained
  // list of file names.
  //
  // This replaces an earlier hardcoded NOT_YET_SHIPPED_DATA_FILES set that had to be edited by
  // hand every time a file's producing pipeline shipped -- `next_day_range_shadow.json` (ADR
  // 047/#2017), `wait_or_buy_today.json` (ADR 049/#2020), then `markup_today.json` (F1/#2022) and
  // `event_watch_today.json` (F4/#2028) all moved out of it one at a time, and each move was a
  // real merge-train dry-run finding (2026-09-25) rather than something caught by this PR's own
  // CI -- #2037's own CI only ever runs against the master of the day, which never has the NEXT
  // PR's files yet. Deriving from the filesystem means this assertion is correct on every day's
  // master, and after every future PR ships another page_v2 producer, with no edit here at all.
  const shipped = names.filter((n) => fs.existsSync(path.join(ROOT, "data", n)));
  const bad = shipped.filter((n) => !(seen[n].status === 200 && seen[n].sw));
  assert("every SHIPPED data request is answered from the service worker cache while offline", bad.length === 0,
    `failed: ${bad.join(", ")}`);
  const notYetShipped = names.filter((n) => !fs.existsSync(path.join(ROOT, "data", n)));
  assert("not-yet-shipped page_v2 data files fail gracefully offline (no crash, no stale 200)",
    notYetShipped.every((n) => seen[n].status !== 200), JSON.stringify(seen));
} finally {
  await browser.close();
  server.close();
}

console.log(`\n${failures === 0 ? PASS : FAIL}  ${failures === 0 ? "Offline data fallback works." : `${failures} check(s) failed.`}\n`);
process.exit(failures === 0 ? 0 : 1);
