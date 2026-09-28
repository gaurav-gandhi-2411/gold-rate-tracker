// tests/test_retailer_takedown_headless.js — ADR 059 (G1d) takedown fallback proof.
//
// Drives the REAL app.js/index.html in headless Chromium with the output of the REAL
// pipeline run with Tanishq (and Kalyan) switched off in config/retailers.json:
// tests/fixtures/retailer_takedown/{forecast.json,prices.json}, produced and
// drift-checked by tests/test_retailer_takedown.py. Timestamps are shifted by
// (now - pipeline_now) so the page sees them as current; nothing else is edited.
//
// Asserts the page falls back cleanly to IBJA x markup: an "≈" estimate equal to
// forecast.current_22k, and no Tanishq-attributed figure or label anywhere in the
// price surfaces (hero location, last-confirmed line, banner, calculator), EN and HI.
//
// Run: node tests/test_retailer_takedown_headless.js   (from repo root; needs
//      scraper/node_modules + a Playwright Chromium). SCREENSHOT_DIR=<dir> also
//      writes desktop + phone screenshots (used for reports/screenshots/retailer-takedown/).

import http from "node:http";
import path from "node:path";
import fs from "node:fs";
import pkg from "../scraper/node_modules/playwright/index.js";
import { LOCAL_ONLY_ARGS } from "./helpers/local_only_browser.js";
const { chromium } = pkg;

const ROOT = path.resolve(".");
const FIX = path.join(ROOT, "tests", "fixtures", "retailer_takedown");

function startServer(root) {
  const MIME = {
    ".html": "text/html", ".js": "application/javascript", ".css": "text/css",
    ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml",
    ".webmanifest": "application/manifest+json", ".woff2": "font/woff2",
  };
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
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ server, port: server.address().port })));
}

// Shift every ISO timestamp in the fixture by offsetMs (keys ending _at/_asof/_time,
// "timestamp", and prices rows) -- the pipeline output itself is otherwise untouched.
const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/;
function shiftTimestamps(value, offsetMs) {
  if (Array.isArray(value)) return value.map((v) => shiftTimestamps(v, offsetMs));
  if (value && typeof value === "object") {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = shiftTimestamps(v, offsetMs);
    return out;
  }
  if (typeof value === "string" && ISO_RE.test(value) && !Number.isNaN(Date.parse(value))) {
    return new Date(Date.parse(value) + offsetMs).toISOString();
  }
  return value;
}

const PASS = "\x1b[32mPASS\x1b[0m";
const FAIL = "\x1b[31mFAIL\x1b[0m";
let failures = 0;
function assert(label, cond, detail = "") {
  if (cond) console.log(`  ${PASS}  ${label}`);
  else { console.log(`  ${FAIL}  ${label}${detail ? " — " + detail : ""}`); failures++; }
}

const forecastRaw = JSON.parse(fs.readFileSync(path.join(FIX, "forecast.json"), "utf8"));
const pricesRaw = JSON.parse(fs.readFileSync(path.join(FIX, "prices.json"), "utf8"));
const { pipeline_now_utc } = JSON.parse(fs.readFileSync(path.join(FIX, "generated_at.json"), "utf8"));
const offsetMs = Date.now() - Date.parse(pipeline_now_utc);
const forecast = shiftTimestamps(forecastRaw, offsetMs);
const prices = shiftTimestamps(pricesRaw, offsetMs);

async function injectData(page, lang) {
  await page.addInitScript(({ fc, pr, lang }) => {
    try { if (lang) localStorage.setItem("lang", lang); } catch {}
    const orig = window.fetch.bind(window);
    window.fetch = (url, opts) => {
      const u = String(url);
      const body = u.includes("forecast.json") ? fc : u.includes("prices.json") ? pr : null;
      if (body !== null) {
        return Promise.resolve(new Response(body, { status: 200, headers: { "Content-Type": "application/json" } }));
      }
      return orig(url, opts);
    };
  }, { fc: JSON.stringify(forecast), pr: JSON.stringify(prices), lang });
}

async function readSurfaces(page) {
  return page.evaluate(() => {
    const txt = (id) => { const el = document.getElementById(id); return el ? el.textContent.trim() : null; };
    const vis = (id) => { const el = document.getElementById(id); return !!el && !el.hidden && getComputedStyle(el).display !== "none"; };
    const calc = document.querySelector(".calc-rate-used");
    return {
      heroPrice: txt("hero-price"),
      heroLocation: txt("hero-location"),
      lastConfirmedVisible: vis("hero-last-confirmed"),
      banner: vis("stale-banner") ? txt("stale-banner") : "",
      calcRate: calc ? calc.textContent.trim() : "",
      rate24: txt("rate-24"),
    };
  });
}

const TANISHQ_RE = /tanishq|तनिष्क/i;

async function run() {
  const { server, port } = await startServer(ROOT);
  const browser = await chromium.launch({ headless: true, args: LOCAL_ONLY_ARGS });
  const base = `http://127.0.0.1:${port}`;
  const shotDir = process.env.SCREENSHOT_DIR;
  const expected = forecast.current_22k.toLocaleString("en-IN");

  const scenarios = [
    { name: "desktop EN", viewport: { width: 1280, height: 1000 }, lang: "en", shot: "desktop-en.png" },
    { name: "phone EN", viewport: { width: 390, height: 844 }, lang: "en", shot: "phone-en.png", mobile: true },
    { name: "phone HI", viewport: { width: 390, height: 844 }, lang: "hi", shot: "phone-hi.png", mobile: true },
  ];

  try {
    assert("fixture is the IBJA fallback tier", forecast.price_source === "ibja_calibrated", forecast.price_source);
    assert("fixture history is IBJA-derived only", prices.every((r) => r.source === "ibja_calibrated_derived"));

    for (const sc of scenarios) {
      console.log(`\nScenario: Tanishq taken down — ${sc.name}`);
      const ctx = await browser.newContext({ viewport: sc.viewport, isMobile: !!sc.mobile, deviceScaleFactor: 1 });
      const page = await ctx.newPage();
      const errors = [];
      page.on("pageerror", (e) => errors.push(String(e)));
      await injectData(page, sc.lang);
      await page.goto(base, { waitUntil: "networkidle" });
      await page.waitForTimeout(600);
      const s = await readSurfaces(page);
      console.log(`  ${JSON.stringify(s)}`);

      assert("hero shows the ≈ IBJA estimate", s.heroPrice && s.heroPrice.includes("≈") && s.heroPrice.includes(expected), s.heroPrice);
      assert("hero location does not name Tanishq", s.heroLocation && !TANISHQ_RE.test(s.heroLocation), s.heroLocation);
      assert("hero location says IBJA", /IBJA/.test(s.heroLocation || ""), s.heroLocation);
      assert("no 'Tanishq last confirmed' line", s.lastConfirmedVisible === false);
      assert("banner does not name Tanishq", !TANISHQ_RE.test(s.banner), s.banner);
      assert("calculator rate line does not name Tanishq", !TANISHQ_RE.test(s.calcRate), s.calcRate);
      assert("24K card renders a number", /\d/.test(s.rate24 || ""), s.rate24);
      assert("no page errors", errors.length === 0, errors.join(" | "));

      if (shotDir) {
        fs.mkdirSync(shotDir, { recursive: true });
        await page.screenshot({ path: path.join(shotDir, sc.shot), fullPage: false });
        console.log(`  screenshot -> ${path.join(shotDir, sc.shot)}`);
      }
      await ctx.close();
    }
  } finally {
    await browser.close();
    server.close();
  }

  console.log(failures === 0 ? "\nAll retailer-takedown render checks passed." : `\n${failures} check(s) FAILED.`);
  process.exit(failures === 0 ? 0 : 1);
}

run().catch((err) => { console.error(err); process.exit(1); });
