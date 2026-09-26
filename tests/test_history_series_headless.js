// tests/test_history_series_headless.js — GG 3d: multi-day history reads the chart's series.
//
// Loads the REAL index.html/app.js in headless Chromium with prices.json, forecast.json and
// data/ibja_derived_prices.json replaced per state (fetch is intercepted; everything else is
// served from the repo). It checks that the hero verdict and sparkline, the comparison cards,
// the history table and the good-price card take their history from the same series as the trend
// chart (app.js historyRows):
//   * "short-prices": prices.json holds only today's two Tanishq readings, as it will after
//     #2075. Every history reader still renders, from the IBJA-based estimate, and says so. The
//     table hides 24K/18K, which the estimate does not have.
//   * "no-derived": the derived file is missing. Everything falls back to the Tanishq rows,
//     as before this change, and says so.
//
// Run: node tests/test_history_series_headless.js   (repo root; needs scraper/node_modules
//      + a Playwright Chromium).

import http from "node:http";
import path from "node:path";
import fs from "node:fs";
import pkg from "../scraper/node_modules/playwright/index.js";
import { LOCAL_ONLY_ARGS } from "./helpers/local_only_browser.js";
const { chromium } = pkg;

const ROOT = path.resolve(process.env.SITE_ROOT || "."); // SITE_ROOT: run against another checkout

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
  if (cond) console.log(`  ${PASS}  ${label}`);
  else { console.log(`  ${FAIL}  ${label}${detail ? " — " + detail : ""}`); failures++; }
}

const NOW = Date.now();
const HOUR = 3_600_000;
const iso = (h) => new Date(NOW - h * HOUR).toISOString();
const fmt = (n) => n.toLocaleString("en-IN");

// The IBJA-based series: one row per day at 17:00 IST for 40 days.
const DERIVED = Array.from({ length: 40 }, (_, i) => {
  const d = new Date(NOW - (40 - i) * 24 * HOUR);
  d.setUTCHours(11, 30, 0, 0);
  return { timestamp: d.toISOString(), "22k": Math.round(14050 + 90 * Math.sin(i / 4) - i * 2) };
});
const LATEST_DERIVED = DERIVED[DERIVED.length - 1]["22k"];

// Full Tanishq history (30 days, one reading a day) and the #2075-shortened version (today only).
const TANISHQ_FULL = Array.from({ length: 30 }, (_, i) => {
  const v = 13700 + i * 11;
  return { timestamp: iso((30 - i) * 24 - 2), "22k": v, "24k": v + 1300, "18k": v - 2400 };
});
const TANISHQ_TODAY = [
  { timestamp: iso(3), "22k": 14010, "24k": 15310, "18k": 11610 },
  { timestamp: iso(1), "22k": 14040, "24k": 15340, "18k": 11640 },
];
const FORECAST = { price_source: "tanishq_scrape", current_22k: 14040, predicted_22k: 14040, scraped_at: iso(1), predicted_at: iso(1) };

const STATES = [
  { slug: "short-prices", prices: TANISHQ_TODAY, derived: DERIVED, estimate: true },
  { slug: "no-derived", prices: TANISHQ_FULL, derived: null, estimate: false },
];

async function injectData(page, st, lang) {
  await page.addInitScript(({ fc, pr, dv, lang }) => {
    try { localStorage.setItem("lang", lang); localStorage.setItem("first-visit-dismissed", "1"); } catch {}
    const orig = window.fetch.bind(window);
    const json = (b) => Promise.resolve(new Response(b, { status: 200, headers: { "Content-Type": "application/json" } }));
    window.fetch = (url, opts) => {
      const u = String(url);
      if (u.includes("forecast.json")) return json(fc);
      if (u.includes("ibja_derived_prices.json")) return dv === null ? Promise.resolve(new Response("", { status: 404 })) : json(dv);
      if (u.includes("prices.json")) return json(pr);
      return orig(url, opts);
    };
  }, { fc: JSON.stringify(FORECAST), pr: JSON.stringify(st.prices), dv: st.derived === null ? null : JSON.stringify(st.derived), lang });
}

async function run() {
  const { server, port } = await startServer(ROOT);
  const browser = await chromium.launch({ headless: true, args: LOCAL_ONLY_ARGS });
  try {
    for (const st of STATES) {
      for (const lang of ["en", "hi"]) {
        for (const width of [1280, 390]) {
          console.log(`\nState: ${st.slug} — ${lang} @ ${width}px`);
          const ctx = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 900 }, isMobile: width === 390, deviceScaleFactor: 1, reducedMotion: "reduce" });
          const page = await ctx.newPage();
          const errors = [];
          page.on("pageerror", (e) => errors.push(String(e)));
          await injectData(page, st, lang);
          await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: "networkidle" });
          await page.waitForTimeout(700);
          const s = await page.evaluate(() => {
            const vis = (el) => !!el && !el.hidden && getComputedStyle(el).display !== "none";
            const byId = (id) => document.getElementById(id);
            const txt = (id) => (vis(byId(id)) ? byId(id).textContent.trim() : null);
            const rows = [...document.querySelectorAll("#history-body tr")];
            const firstCells = rows.length ? [...rows[0].querySelectorAll("td")] : [];
            const th24 = document.querySelector(".history-table thead th:nth-child(3)");
            return {
              verdict: txt("verdict-headline"),
              sparkline: vis(byId("sparkline-wrap")),
              sparkRange: txt("sparkline-range"),
              comparisons: vis(byId("comparison-section")),
              cmp30: txt("cmp-30d-value"),
              cmpNote: txt("comparison-source-note"),
              historyRows: rows.length,
              historyFirst22k: firstCells.length > 1 ? firstCells[1].textContent.trim() : null,
              historyNote: txt("history-source-note"),
              th24Visible: vis(th24),
              goodPrice: vis(byId("model-signal-section")),
            };
          });
          console.log(`  ${JSON.stringify(s)}`);
          assert("verdict renders", !!s.verdict, String(s.verdict));
          assert("sparkline renders", s.sparkline === true);
          assert("comparison cards render with a value", s.comparisons === true && !!s.cmp30 && s.cmp30 !== "—", String(s.cmp30));
          assert("history table has day rows", s.historyRows >= 15, String(s.historyRows));
          assert("good-price card renders", s.goodPrice === true);
          if (st.estimate) {
            assert("sparkline range says it is our IBJA-based estimate", /IBJA/.test(s.sparkRange || ""), s.sparkRange);
            assert("comparison note says estimate", /IBJA/.test(s.cmpNote || "") && !/tanishq|तनिष्क/i.test(s.cmpNote || ""), s.cmpNote);
            assert("history note says estimate", /IBJA/.test(s.historyNote || ""), s.historyNote);
            assert("history's newest row is the latest estimate", (s.historyFirst22k || "").includes(fmt(LATEST_DERIVED)), s.historyFirst22k);
            assert("24K/18K columns hidden for the 22K-only estimate", s.th24Visible === false);
          } else {
            assert("sparkline range has no estimate wording", !/IBJA/.test(s.sparkRange || ""), s.sparkRange);
            assert("comparison note names Tanishq", /tanishq|तनिष्क/i.test(s.cmpNote || ""), s.cmpNote);
            assert("history note names Tanishq", /tanishq|तनिष्क/i.test(s.historyNote || ""), s.historyNote);
            assert("history's newest row is the latest Tanishq reading", (s.historyFirst22k || "").includes(fmt(TANISHQ_FULL[TANISHQ_FULL.length - 1]["22k"])), s.historyFirst22k);
            assert("24K/18K columns shown for Tanishq rows", s.th24Visible === true);
          }
          assert("no page errors", errors.length === 0, errors.join(" | "));
          await ctx.close();
        }
      }
    }
  } finally {
    await browser.close();
    server.close();
  }
  console.log(failures === 0 ? "\nAll history-series checks passed." : `\n${failures} check(s) FAILED.`);
  process.exit(failures === 0 ? 0 : 1);
}

run().catch((err) => { console.error(err); process.exit(1); });
