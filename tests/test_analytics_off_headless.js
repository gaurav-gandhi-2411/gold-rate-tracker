// tests/test_analytics_off_headless.js -- with FEATURE_FLAGS.analytics off (as shipped), the REAL
// page is byte-for-byte the same DOM with analytics.js present as with analytics.js blocked, and
// makes no request an analytics call would make. This is the evidence for "flag off = no visual
// change" in the PR body (rule 15c substitute evidence), and a leak check: it also proves the
// check can fail by serving a variant with the flag and an endpoint switched on and asserting the
// beacon request IS seen.
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
const BEACON_HOST = "beacon.example.test";
const ARGS = [
  `--host-resolver-rules=MAP ${NONLOCAL_HOST} 127.0.0.1, MAP ${BEACON_HOST} 127.0.0.1, MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost`,
];

const ROOT = path.resolve(".");
let failures = 0;
function assert(label, ok, detail = "") {
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${label}${!ok && detail ? " -- " + detail : ""}`);
  if (!ok) failures++;
}

async function load(browser, base, { blockAnalytics = false, variant = false } = {}) {
  const ctx = await browser.newContext({ serviceWorkers: "block" });
  const page = await ctx.newPage();
  const requests = [];
  page.on("request", (r) => requests.push(r.url()));
  if (blockAnalytics) await page.route("**/analytics.js", (r) => r.abort());
  if (variant) {
    const flags = fs.readFileSync(path.join(ROOT, "flags.js"), "utf8").replace("analytics: false,", "analytics: true,");
    const an = fs.readFileSync(path.join(ROOT, "analytics.js"), "utf8")
      .replace('const ANALYTICS_ENDPOINT = "";', `const ANALYTICS_ENDPOINT = "https://${BEACON_HOST}/count";`);
    await page.route("**/flags.js", (r) => r.fulfill({ status: 200, contentType: "application/javascript", body: flags }));
    await page.route("**/analytics.js", (r) => r.fulfill({ status: 200, contentType: "application/javascript", body: an }));
  }
  await page.goto(base, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);
  const html = await page.evaluate(() => document.documentElement.outerHTML);
  const storage = await page.evaluate(() => ({ ls: localStorage.length, cookie: document.cookie }));
  await ctx.close();
  return { html, requests, storage };
}

async function run() {
  const { server, port } = await startServer(ROOT);
  const browser = await chromium.launch({ headless: true, args: ARGS });
  const base = `http://${NONLOCAL_HOST}:${port}`;
  try {
    console.log("\nFlag OFF (as shipped): with analytics.js vs analytics.js blocked");
    const withJs = await load(browser, base);
    const without = await load(browser, base, { blockAnalytics: true });
    assert("analytics.js was requested", withJs.requests.some((u) => u.endsWith("/analytics.js")));
    assert("DOM is identical with and without analytics.js", withJs.html === without.html,
      `len ${withJs.html.length} vs ${without.html.length}`);
    assert("no request to any beacon/count URL",
      !withJs.requests.some((u) => u.includes("/count") || u.includes(BEACON_HOST)));
    assert("analytics.js adds no cookie and no localStorage entry",
      withJs.storage.cookie === without.storage.cookie && withJs.storage.ls === without.storage.ls);

    console.log("\nViolation check: flag ON + endpoint set (variant) DOES send a beacon");
    const on = await load(browser, base, { variant: true });
    assert("the beacon request is observed (the check can fail)",
      on.requests.some((u) => u.startsWith(`https://${BEACON_HOST}/count?`)));
    assert("the beacon carries no query string from the page URL",
      !on.requests.some((u) => u.includes(BEACON_HOST) && u.includes("ff=")));
  } finally {
    await browser.close();
    server.close();
  }
  console.log(`\n${failures === 0 ? "PASS" : "FAIL"}  ${failures === 0 ? "All analytics-off checks passed." : `${failures} check(s) failed.`}\n`);
  process.exit(failures === 0 ? 0 : 1);
}

run().catch((err) => { console.error(err); process.exit(1); });
