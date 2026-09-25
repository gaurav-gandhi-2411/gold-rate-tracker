// tests/test_hero_display_headless.js — E2 hero states in a real browser (ADR 059 gates).
//
// Loads the REAL index.html/app.js in headless Chromium with prices.json/forecast.json
// replaced per state (fetch is intercepted; everything else is served from the repo), and
// asserts, EN and HI, that the hero's figure, label and Tanishq line say what they are:
// Tanishq named only on a real, fresh-or-dated, plausible Tanishq reading; every estimate
// labelled as our estimate; nothing named Tanishq after a takedown.
//
// Run: node tests/test_hero_display_headless.js   (repo root; needs scraper/node_modules
//      + a Playwright Chromium). SCREENSHOT_DIR=<dir> writes one hero screenshot per
//      state x width (1280, 390) x language. SITE_ROOT=<dir> serves another checkout (used
//      for the "before" screenshots; assertions are skipped there with NO_ASSERT=1).

import http from "node:http";
import path from "node:path";
import fs from "node:fs";
import pkg from "../scraper/node_modules/playwright/index.js";
import { LOCAL_ONLY_ARGS } from "./helpers/local_only_browser.js";
const { chromium } = pkg;

const ROOT = path.resolve(process.env.SITE_ROOT || ".");
const NO_ASSERT = process.env.NO_ASSERT === "1";

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

const PASS = "\x1b[32mPASS\x1b[0m";
const FAIL = "\x1b[31mFAIL\x1b[0m";
let failures = 0;
function assert(label, cond, detail = "") {
  if (NO_ASSERT) return;
  if (cond) console.log(`  ${PASS}  ${label}`);
  else { console.log(`  ${FAIL}  ${label}${detail ? " — " + detail : ""}`); failures++; }
}

const NOW = Date.now();
const HOUR = 3_600_000;
const iso = (h) => new Date(NOW - h * HOUR).toISOString();
function tanishqRows(hoursAgo, value) {
  const rows = [];
  for (let d = 12; d >= 1; d--) {
    rows.push({ timestamp: iso(hoursAgo + d * 24), "22k": value - d * 15, "24k": value - d * 15 + 1300, "18k": value - d * 15 - 2400 });
  }
  rows.push({ timestamp: iso(hoursAgo + 5), "22k": value - 20, "24k": value + 1280, "18k": value - 2420 });
  rows.push({ timestamp: iso(hoursAgo), "22k": value, "24k": value + 1300, "18k": value - 2400 });
  return rows;
}
const derivedRows = (value) => tanishqRows(6, value).map((r) => ({ ...r, source: "ibja_calibrated_derived" }));
const ibja = (value, scrapedH = 40) => ({ price_source: "ibja_calibrated", current_22k: value, ibja_asof: iso(4), scraped_at: iso(scrapedH), est_low: value - 60, est_high: value + 60, predicted_at: iso(1) });
const tier1 = (value, h) => ({ price_source: "tanishq_scrape", current_22k: value, scraped_at: iso(h), predicted_at: iso(h) });

const TANISHQ_RE = /tanishq|तनिष्क/i;
const STATES = [
  { slug: "fresh", prices: tanishqRows(1, 14040), forecast: tier1(14040, 1),
    expect: { approx: false, labelTanishq: true, line: "none" } },
  { slug: "not-fresh", prices: tanishqRows(20, 14040), forecast: ibja(14100),
    expect: { approx: true, labelTanishq: false, line: "figure" } },
  { slug: "blocked", prices: tanishqRows(120, 13900), forecast: ibja(14100, 120),
    expect: { approx: true, labelTanishq: false, line: "dated-no-figure" } },
  { slug: "tanishq-disabled", prices: derivedRows(14100), forecast: ibja(14100),
    expect: { approx: true, labelTanishq: false, line: "none", noTanishqAnywhere: true } },
  { slug: "implausible", prices: tanishqRows(1, 16920), forecast: ibja(14100),
    expect: { approx: true, labelTanishq: false, line: "none" } },
];

async function injectData(page, { forecast, prices }, lang) {
  await page.addInitScript(({ fc, pr, lang }) => {
    try { localStorage.setItem("lang", lang); localStorage.setItem("first-visit-dismissed", "1"); } catch {}
    const orig = window.fetch.bind(window);
    window.fetch = (url, opts) => {
      const u = String(url);
      const body = u.includes("forecast.json") ? fc : u.includes("prices.json") ? pr : null;
      if (body !== null) return Promise.resolve(new Response(body, { status: 200, headers: { "Content-Type": "application/json" } }));
      return orig(url, opts);
    };
  }, { fc: JSON.stringify(forecast), pr: JSON.stringify(prices), lang });
}

async function run() {
  const { server, port } = await startServer(ROOT);
  const browser = await chromium.launch({ headless: true, args: LOCAL_ONLY_ARGS });
  const shotDir = process.env.SCREENSHOT_DIR;
  try {
    for (const st of STATES) {
      for (const lang of ["en", "hi"]) {
        for (const width of [1280, 390]) {
          console.log(`\nState: ${st.slug} — ${lang} @ ${width}px`);
          const ctx = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 900 }, isMobile: width === 390, deviceScaleFactor: 1 });
          const page = await ctx.newPage();
          const errors = [];
          page.on("pageerror", (e) => errors.push(String(e)));
          await injectData(page, st, lang);
          await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: "networkidle" });
          await page.waitForTimeout(700);
          const s = await page.evaluate(() => {
            const vis = (id) => { const el = document.getElementById(id); return !!el && !el.hidden && getComputedStyle(el).display !== "none"; };
            const txt = (id) => (vis(id) ? document.getElementById(id).textContent.trim() : null);
            const calc = document.querySelector(".calc-rate-used");
            return { price: txt("hero-price"), label: txt("hero-location"), line: txt("hero-last-confirmed"),
              banner: txt("stale-banner") || "", calc: calc ? calc.textContent.trim() : "" };
          });
          console.log(`  ${JSON.stringify(s)}`);
          const e = st.expect;
          assert("hero shows a figure", /\d/.test(s.price || ""), s.price);
          assert("figure has a label", !!s.label, s.label);
          assert(e.approx ? "figure is marked ≈ (estimate)" : "figure is not marked ≈", (s.price || "").includes("≈") === e.approx, s.price);
          assert(e.labelTanishq ? "label names Tanishq" : "label does not name Tanishq", TANISHQ_RE.test(s.label || "") === e.labelTanishq, s.label);
          if (e.approx) assert("an estimate is never labelled as Tanishq's", !TANISHQ_RE.test(s.label || ""), s.label);
          if (e.line === "none") assert("no Tanishq line", s.line === null, s.line);
          if (e.line === "figure") assert("Tanishq line carries the dated Tanishq figure", TANISHQ_RE.test(s.line || "") && /14,040/.test(s.line || ""), s.line);
          if (e.line === "dated-no-figure") assert("Tanishq line is dated, no figure", TANISHQ_RE.test(s.line || "") && !/₹/.test(s.line || ""), s.line);
          if (e.noTanishqAnywhere) assert("nothing names Tanishq", !TANISHQ_RE.test(`${s.label} ${s.line} ${s.banner} ${s.calc}`));
          assert("no page errors", errors.length === 0, errors.join(" | "));
          if (shotDir) {
            fs.mkdirSync(shotDir, { recursive: true });
            const file = path.join(shotDir, `${st.slug}-${lang}-${width}.png`);
            await page.locator("#section-home").screenshot({ path: file });
            console.log(`  screenshot -> ${file}`);
          }
          await ctx.close();
        }
      }
    }
  } finally {
    await browser.close();
    server.close();
  }
  if (NO_ASSERT) { console.log("\nScreenshots only (NO_ASSERT=1)."); process.exit(0); }
  console.log(failures === 0 ? "\nAll hero display-state checks passed." : `\n${failures} check(s) FAILED.`);
  process.exit(failures === 0 ? 0 : 1);
}

run().catch((err) => { console.error(err); process.exit(1); });
