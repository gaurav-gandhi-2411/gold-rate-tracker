// tests/test_page_v2_headless.js — page_v2 (item 6, flagged OFF): proves the REAL app.js,
// on localhost with ?ff=page_v2,..., renders each of the five jobs exactly once, and that
// with no ?ff= at all (production shape) nothing page_v2-related exists in the DOM. Modelled
// on tests/test_stale_banner_headless.js / tests/test_feature_flags_headless.js's own
// serve-the-repo-root + real-Chromium pattern.
//
// Run: node tests/test_page_v2_headless.js  (from repo root)
// Requires: scraper/node_modules (npm ci in scraper/ first)

import http from "node:http";
import path from "node:path";
import fs from "node:fs";
import pkg from "../scraper/node_modules/playwright/index.js";
const { chromium } = pkg;

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
    server.listen(0, "127.0.0.1", () => resolve({ server, port: server.address().port }));
  });
}

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

const PV2_JOBS = ["price_now", "how_sure", "how_much_move", "good_price", "what_you_pay"];

async function run() {
  const { server, port } = await startServer(ROOT);
  const browser = await chromium.launch({ headless: true });
  // 127.0.0.1, not "localhost" by name -- isFeatureOn()'s local-only check accepts both, and
  // this avoids any DNS resolution step at all (same reasoning as test_stale_banner_headless.js).
  const base = `http://127.0.0.1:${port}`;

  try {
    console.log("\nBaseline load (no ?ff=) — nothing page_v2-related should exist");
    {
      const ctx  = await browser.newContext();
      const page = await ctx.newPage();
      await page.goto(base, { waitUntil: "networkidle" });
      await page.waitForTimeout(500);
      const pageV2Exists = await page.evaluate(() => document.getElementById("page-v2") !== null);
      const layoutGridHidden = await page.evaluate(() => document.querySelector(".layout-grid")?.hidden === true);
      assert("no #page-v2 element exists with no ?ff=", !pageV2Exists);
      assert(".layout-grid is NOT hidden (old sections stay visible)", !layoutGridHidden);
      await ctx.close();
    }

    console.log("\n?ff=page_v2,markup_meter,wait_or_buy,good_price_v2,event_watch (localhost) — five jobs render once each");
    {
      const ctx  = await browser.newContext();
      const page = await ctx.newPage();
      const url = `${base}/?ff=page_v2,markup_meter,wait_or_buy,good_price_v2,event_watch`;
      await page.goto(url, { waitUntil: "networkidle" });
      await page.waitForTimeout(500);

      const pageV2Exists = await page.evaluate(() => document.getElementById("page-v2") !== null);
      assert("#page-v2 container exists", pageV2Exists);

      const layoutGridHidden = await page.evaluate(() => document.querySelector(".layout-grid")?.hidden === true);
      assert("the single .layout-grid toggle hid every old section", layoutGridHidden);

      for (const job of PV2_JOBS) {
        // eslint-disable-next-line no-loop-func
        const count = await page.evaluate((j) => document.querySelectorAll(`[data-pv2-job="${j}"]`).length, job);
        assert(`job "${job}" renders exactly once`, count === 1, `found ${count}`);
      }

      // good_price_v2 was included in ?ff= above and data/prices.json is always present in
      // this repo, so job 4's slot must actually have content, not just exist empty.
      const goodPriceHasContent = await page.evaluate(
        () => document.querySelector('[data-pv2-job="good_price"]')?.children.length > 0
      );
      assert("job 4 (good_price_v2) slot has real content, not just an empty slot", goodPriceHasContent);

      // The calculator (job 5) is the SAME live node, relocated — its own id must still be
      // present exactly once, now inside #page-v2.
      const calcInsidePageV2 = await page.evaluate(
        () => document.querySelector("#page-v2 #calculator-section") !== null
      );
      assert("job 5's calculator was relocated into #page-v2, not duplicated", calcInsidePageV2);

      // markup_meter/wait_or_buy/event_watch: their data files don't exist on this branch, so
      // per the brief's own contract ("handle absence by rendering nothing") none of these
      // three should have rendered anything, even though their flags were included above.
      const optionalCount = await page.evaluate(
        () => document.querySelectorAll('[data-feature="markup_meter"],[data-feature="wait_or_buy"],[data-feature="event_watch"]').length
      );
      assert("F1/F2/F4 render nothing when their data files are absent (even with their flags on)", optionalCount === 0,
        `found ${optionalCount}`);

      await ctx.close();
    }

  } finally {
    await browser.close();
    server.close();
  }

  console.log(`\n${failures === 0 ? PASS : FAIL}  ${failures === 0 ? "All page_v2 headless checks passed." : `${failures} check(s) failed.`}\n`);
  process.exit(failures === 0 ? 0 : 1);
}

run().catch(err => { console.error(err); process.exit(1); });
