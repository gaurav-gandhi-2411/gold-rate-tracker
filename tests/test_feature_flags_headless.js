// tests/test_feature_flags_headless.js — flags.js's ?ff= URL override must never leak on a
// production-shaped origin, proven end to end against the REAL app.js/flags.js (modelled on
// tests/test_stale_banner_headless.js -- see that file for the serve/mock-fetch pattern this
// reuses).
//
// Run: node tests/test_feature_flags_headless.js  (from repo root)
// Requires: scraper/node_modules (npm ci in scraper/ first)

import http from "node:http";
import path from "node:path";
import fs from "node:fs";
import pkg from "../scraper/node_modules/playwright/index.js";
const { chromium } = pkg;

// ─── Local HTTP server (serves repo root) ────────────────────────────────────

function startServer(root) {
  const MIME = {
    ".html": "text/html",
    ".js":   "application/javascript",
    ".css":  "text/css",
    ".json": "application/json",
    ".png":  "image/png",
    ".svg":  "image/svg+xml",
    ".ico":  "image/x-icon",
    ".webmanifest": "application/manifest+json",
    ".woff2": "font/woff2",
  };
  const server = http.createServer((req, res) => {
    const urlPath = req.url.split("?")[0];
    const filePath = path.join(root, urlPath === "/" ? "index.html" : urlPath);
    const ext = path.extname(filePath);
    try {
      const data = fs.readFileSync(filePath);
      res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
      res.end(data);
    } catch {
      res.writeHead(404);
      res.end("Not found");
    }
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      resolve({ server, port: server.address().port });
    });
  });
}

// ─── Non-localhost origin, still on the sandbox's own machine ────────────────
// `example.test` is mapped to loopback ONLY -- everything else (real third-party hosts, e.g.
// the Chart.js/Sentry CDNs app.js loads) still resolves to NXDOMAIN, same isolation property
// as tests/helpers/local_only_browser.js's LOCAL_ONLY_ARGS, just with one extra allowed name.
// This is what makes `location.hostname` genuinely "example.test" in-page -- not "localhost" or
// "127.0.0.1" -- so isFeatureOn()'s local-only branch is provably NOT taken.
const NONLOCAL_HOST = "example.test";
const NONLOCAL_ARGS = [
  `--host-resolver-rules=MAP ${NONLOCAL_HOST} 127.0.0.1, MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost`,
];

// ─── Helpers ─────────────────────────────────────────────────────────────────

async function countFeatureElements(page) {
  return page.evaluate(() => document.querySelectorAll("[data-feature]").length);
}

async function countAllElements(page) {
  return page.evaluate(() => document.querySelectorAll("*").length);
}

// ─── Main ─────────────────────────────────────────────────────────────────────

const ROOT  = path.resolve(".");
const PASS  = "\x1b[32mPASS\x1b[0m";
const FAIL  = "\x1b[31mFAIL\x1b[0m";
let failures = 0;

function assert(label, condition, detail = "") {
  if (condition) {
    console.log(`  ${PASS}  ${label}`);
  } else {
    console.log(`  ${FAIL}  ${label}${detail ? " — " + detail : ""}`);
    failures++;
  }
}

async function run() {
  const { server, port } = await startServer(ROOT);
  const browser = await chromium.launch({ headless: true, args: NONLOCAL_ARGS });
  const base = `http://${NONLOCAL_HOST}:${port}`;

  try {
    // ── Case 1: no ?ff= at all — baseline element count, no data-feature elements ──
    console.log(`\nBaseline load (host=${NONLOCAL_HOST}, no ?ff=)`);
    let baselineCount, baselineFeatureCount;
    {
      const ctx  = await browser.newContext();
      const page = await ctx.newPage();
      await page.goto(base, { waitUntil: "networkidle" });
      await page.waitForTimeout(500); // let init()'s render pass (incl. renderFlaggedFeatures()) finish
      baselineFeatureCount = await countFeatureElements(page);
      baselineCount = await countAllElements(page);
      console.log(`  element count: ${baselineCount}, [data-feature] count: ${baselineFeatureCount}`);
      assert("no [data-feature] element exists with no ?ff= param", baselineFeatureCount === 0);
      await ctx.close();
    }

    // ── Case 2: ?ff=<all five flags>, non-localhost host — MUST be ignored ──
    console.log(`\n?ff=<all flags> load (host=${NONLOCAL_HOST}, must be ignored — not localhost)`);
    {
      const ctx  = await browser.newContext();
      const page = await ctx.newPage();
      const url = `${base}/?ff=markup_meter,wait_or_buy,good_price_v2,event_watch,page_v2`;
      await page.goto(url, { waitUntil: "networkidle" });
      await page.waitForTimeout(500);
      const featureCount = await countFeatureElements(page);
      const allCount = await countAllElements(page);
      console.log(`  element count: ${allCount}, [data-feature] count: ${featureCount}`);
      assert("no [data-feature] element exists even with ?ff= naming every flag",
        featureCount === 0);
      assert("DOM element count is identical to the baseline load (no hidden elements added either)",
        allCount === baselineCount, `baseline=${baselineCount}, got=${allCount}`);
      await ctx.close();
    }

    // ── Case 3: prove the check can actually fail — serve a variant of flags.js with one
    // flag hardcoded true (a stand-in for a feature merged with its flag left on), and
    // assert the leak IS detected. Uses page.route rather than addInitScript: flags.js
    // declares FEATURE_FLAGS as a page-scope `const`, which an addInitScript running before
    // it would only shadow, not override -- serving a genuinely different flags.js body is
    // the reliable way to simulate "a flag shipped on" here.
    console.log(`\nViolation check: flags.js variant with markup_meter hardcoded true`);
    {
      const flagsSrc = fs.readFileSync(path.join(ROOT, "flags.js"), "utf8");
      const leaked = flagsSrc.replace("markup_meter: false,", "markup_meter: true,");
      if (leaked === flagsSrc) {
        throw new Error("precondition failed: could not construct the flags.js violation variant");
      }

      const ctx  = await browser.newContext();
      const page = await ctx.newPage();
      await page.route("**/flags.js", (route) =>
        route.fulfill({ status: 200, contentType: "application/javascript", body: leaked })
      );
      await page.goto(base, { waitUntil: "networkidle" });
      await page.waitForTimeout(500);
      const featureCount = await countFeatureElements(page);
      const markupMeterPresent = await page.evaluate(
        () => document.querySelector('[data-feature="markup_meter"]') !== null
      );
      console.log(`  [data-feature] count: ${featureCount}, markup_meter element present: ${markupMeterPresent}`);
      assert("a flag hardcoded true DOES render a [data-feature] element (the check can fail)",
        featureCount > 0 && markupMeterPresent);
      await ctx.close();
    }

  } finally {
    await browser.close();
    server.close();
  }

  console.log(`\n${failures === 0 ? PASS : FAIL}  ${failures === 0 ? "All feature-flag checks passed." : `${failures} check(s) failed.`}\n`);
  process.exit(failures === 0 ? 0 : 1);
}

run().catch(err => { console.error(err); process.exit(1); });
