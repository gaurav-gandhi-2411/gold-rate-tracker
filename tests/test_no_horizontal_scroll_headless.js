// tests/test_no_horizontal_scroll_headless.js -- the live page must never scroll sideways.
//
// Bug (found 2026-09-25): .hero-card::before's decorative glow reaches 80px past the hero
// card's right edge, which made the whole page 60px wider than a 390px phone screen
// (documentElement.scrollWidth 450) so it could be dragged sideways. Fixed with
// `main { overflow-x: clip; }` in style.css. This test loads the REAL index.html/app.js/
// style.css from the repo root with the committed data files, at phone and desktop widths,
// in EN and HI, and asserts the page is exactly as wide as the viewport and that a
// programmatic sideways scroll does not move it.
//
// Run: node tests/test_no_horizontal_scroll_headless.js  (from repo root)
// Requires: scraper/node_modules (npm ci in scraper/ first)

import http from "node:http";
import path from "node:path";
import fs from "node:fs";
import assert from "node:assert/strict";
import pkg from "../scraper/node_modules/playwright/index.js";
import { LOCAL_ONLY_ARGS } from "./helpers/local_only_browser.js";
const { chromium } = pkg;

const MIME = {
  ".html": "text/html",
  ".js": "application/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".webmanifest": "application/manifest+json",
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

const VIEWPORTS = [
  { width: 360, height: 780 },
  { width: 390, height: 844 },
  { width: 768, height: 1024 },
  { width: 1280, height: 900 },
];

const { server, port } = await startServer(process.cwd());
const browser = await chromium.launch({ headless: true, args: LOCAL_ONLY_ARGS });
let failures = 0;
try {
  for (const lang of ["en", "hi"]) {
    for (const vp of VIEWPORTS) {
      const ctx = await browser.newContext({ viewport: vp, serviceWorkers: "block" });
      await ctx.addInitScript((l) => {
        try { localStorage.setItem("lang", l); } catch { /* storage blocked: default language */ }
      }, lang);
      const page = await ctx.newPage();
      await page.route(/^https?:\/\/(?!127\.0\.0\.1)/, (r) => r.abort());
      await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: "load" });
      await page.waitForFunction(() => {
        const p = document.getElementById("hero-price");
        return p && p.textContent.trim() !== "" && p.textContent.trim() !== "—";
      }, null, { timeout: 15000 });
      const m = await page.evaluate(() => {
        window.scrollTo(500, 0);
        return {
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: document.documentElement.clientWidth,
          scrollX: window.scrollX,
        };
      });
      const label = `${lang} ${vp.width}px`;
      try {
        assert.equal(m.scrollWidth, m.clientWidth, `${label}: page is ${m.scrollWidth}px wide in a ${m.clientWidth}px viewport`);
        assert.equal(m.scrollX, 0, `${label}: page scrolled sideways by ${m.scrollX}px`);
        console.log(`PASS ${label}: scrollWidth ${m.scrollWidth} = viewport, scrollX 0`);
      } catch (e) {
        failures += 1;
        console.error(`FAIL ${e.message}`);
      }
      await ctx.close();
    }
  }
} finally {
  await browser.close();
  server.close();
}
if (failures) {
  console.error(`${failures} viewport(s) scroll sideways`);
  process.exit(1);
}
console.log("All viewports: no sideways scroll.");
