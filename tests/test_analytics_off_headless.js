// tests/test_analytics_off_headless.js -- the REAL page with visit counting as shipped (2026-10-09:
// flag ON, site code gold-rate-tracker), in headless Chromium. (File name kept: lint.yml runs it.)
//
// Proves, in a real browser:
//   1. as shipped, the page renders identically with analytics.js present as with it blocked, except
//      for exactly one added element: the privacy note in the footer;
//   2. the counter sends view + language requests to https://gold-rate-tracker.goatcounter.com/count
//      with only p/e/r parameters, sets no cookie, writes no localStorage, raises no page error;
//   3. violation check: a variant served with the flag OFF sends nothing, renders no note, and
//      its DOM equals the analytics-blocked DOM (so the checks above can fail).
//
// Run: node tests/test_analytics_off_headless.js  (from repo root)
// Requires: scraper/node_modules (npm ci in scraper/ first)

import http from "node:http";
import path from "node:path";
import fs from "node:fs";
import pkg from "../scraper/node_modules/playwright/index.js";
const { chromium } = pkg;

const MIME = {
  ".html": "text/html", ".js": "application/javascript", ".css": "text/css",
  ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml",
  ".ico": "image/x-icon", ".webmanifest": "application/manifest+json", ".woff2": "font/woff2",
};

function startServer(root) {
  const server = http.createServer((req, res) => {
    const urlPath = req.url.split("?")[0];
    const filePath = path.join(root, urlPath === "/" ? "index.html" : urlPath);
    try {
      const data = fs.readFileSync(filePath);
      res.writeHead(200, { "Content-Type": MIME[path.extname(filePath)] || "application/octet-stream" });
      res.end(data);
    } catch {
      res.writeHead(404);
      res.end("Not found");
    }
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve({ server, port: server.address().port }));
  });
}

const NONLOCAL_HOST = "example.test";
const SITE_CODE = "gold-rate-tracker";
const BEACON_HOST = `${SITE_CODE}.goatcounter.com`;
const ARGS = [
  `--host-resolver-rules=MAP ${NONLOCAL_HOST} 127.0.0.1, MAP ${BEACON_HOST} 127.0.0.1, MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost`,
];

const ROOT = path.resolve(".");
let failures = 0;
function assert(label, ok, detail = "") {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${!ok && detail ? " -- " + detail : ""}`);
  if (!ok) failures++;
}

const NOTE_RE = /<p [^>]*id="privacy-note"[^>]*>[\s\S]*?<\/p>/;

async function load(browser, base, { blockAnalytics = false, flagOff = false } = {}) {
  const ctx = await browser.newContext({ serviceWorkers: "block" });
  const page = await ctx.newPage();
  const requests = [];
  const pageErrors = [];
  page.on("request", (r) => requests.push(r.url()));
  page.on("pageerror", (e) => pageErrors.push(String(e)));
  if (blockAnalytics) await page.route("**/analytics.js", (r) => r.abort());
  if (flagOff) {
    const flags = fs.readFileSync(path.join(ROOT, "flags.js"), "utf8").replace("analytics: true,", "analytics: false,");
    await page.route("**/flags.js", (r) => r.fulfill({ status: 200, contentType: "application/javascript", body: flags }));
  }
  await page.goto(base, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);
  const html = await page.evaluate(() => document.documentElement.outerHTML);
  const storage = await page.evaluate(() => ({
    ls: localStorage.length,
    ss: sessionStorage.length,
    cookie: document.cookie,
  }));
  const cookies = await ctx.cookies();
  await ctx.close();
  return { html, requests, storage, cookies, pageErrors };
}

async function run() {
  const { server, port } = await startServer(ROOT);
  const browser = await chromium.launch({ headless: true, args: ARGS });
  const base = `http://${NONLOCAL_HOST}:${port}`;
  try {
    console.log("\nAs shipped (flag ON, site code gold-rate-tracker): with analytics.js vs analytics.js blocked");
    const on = await load(browser, base);
    const blocked = await load(browser, base, { blockAnalytics: true });
    assert("analytics.js was requested", on.requests.some((u) => u.endsWith("/analytics.js")));
    const beacons = on.requests.filter((u) => u.includes(BEACON_HOST));
    assert("a view and a language request go to https://gold-rate-tracker.goatcounter.com/count",
      beacons.length >= 2 && beacons.every((u) => u.startsWith(`https://${BEACON_HOST}/count?`)), JSON.stringify(beacons));
    assert("every request carries only p / e / r parameters (no title, screen size, query string)",
      beacons.every((u) => [...new URL(u).searchParams.keys()].every((k) => ["p", "e", "r"].includes(k))));
    assert("the language event is sent", beacons.some((u) => new URL(u).searchParams.get("p") === "lang/en"));
    assert("the privacy note is rendered once in the footer",
      (on.html.match(/id="privacy-note"/g) || []).length === 1 && on.html.includes("GoatCounter"));
    assert("the blocked-analytics page has no note and sent no beacon",
      !blocked.html.includes("privacy-note") && !blocked.requests.some((u) => u.includes(BEACON_HOST)));
    assert("the page is identical except for exactly the one privacy-note element",
      on.html.replace(NOTE_RE, "") === blocked.html,
      `len ${on.html.replace(NOTE_RE, "").length} vs ${blocked.html.length}`);
    assert("no cookie is set (browser cookie jar and document.cookie)",
      on.cookies.length === 0 && on.storage.cookie === "");
    assert("no localStorage or sessionStorage entry is written",
      on.storage.ls === blocked.storage.ls && on.storage.ss === blocked.storage.ss);
    assert("no page error is raised", on.pageErrors.length === 0, on.pageErrors.join(" | "));

    console.log("\nViolation check: the same page served with the flag OFF sends nothing");
    const off = await load(browser, base, { flagOff: true });
    assert("no request to any beacon/count URL",
      !off.requests.some((u) => u.includes("/count") || u.includes(BEACON_HOST)));
    assert("no privacy note is rendered while the flag is off",
      !off.html.includes("privacy-note"));
    assert("the flag-off DOM is identical to the analytics-blocked DOM", off.html === blocked.html);
  } finally {
    await browser.close();
    server.close();
  }
  console.log(`\n${failures === 0 ? "PASS" : "FAIL"}  ${failures === 0 ? "All analytics checks passed." : `${failures} check(s) failed.`}\n`);
  process.exit(failures === 0 ? 0 : 1);
}

run().catch((err) => { console.error(err); process.exit(1); });
