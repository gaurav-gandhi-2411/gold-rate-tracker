// tests/test_stale_banner_headless.js — Φ20 headless norm-14 verify
// Loads the live app in Chromium, intercepts data fetches, checks banner
// computed visibility (not just the hidden attribute).
//
// Run: node tests/test_stale_banner_headless.js  (from repo root)
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

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeForecast(scrapedAgeH, predictedAgeH = 0.1) {
  const now     = Date.now();
  const scraped  = new Date(now - scrapedAgeH  * 3_600_000).toISOString();
  const predicted = new Date(now - predictedAgeH * 3_600_000).toISOString();
  return JSON.stringify({
    predicted_at: predicted,
    scraped_at:   scraped,
    target_window: "5d",
    headline: {
      method: "naive_flat_hold",
      predicted_22k: 14045,
      lower: 13200,
      upper: 14900,
      conformal_pi_half: 800,
      naive_mae_recent_30: 550,
      vol_context: {
        half_width: 800, half_width_raw: 400, method: "realized_20d",
        window_days: 20, contiguous_days: 30, is_floored: false, is_degraded: false,
        floor_fraction: 0.5, static_pi_half: 800, baseline_half_width: 350,
        regime: "calm",
      },
    },
    chronos_companion: {
      status: "success", lean_direction: "flat", lean_strength_pct: 0.5,
      direction_acc_30f: 0.55, direction_prob_basis: "base_rate_fallback",
      horizon_p10: [13900, 13850, 13800, 13750, 13700],
      horizon_p50: [14045, 14050, 14055, 14060, 14065],
      horizon_p90: [14200, 14250, 14300, 14350, 14400],
      model_version: "amazon/chronos-bolt-tiny@test",
      calibration_applied: false, calibration_just_unlocked: false,
      majority_direction: "flat", direction_consensus: 0.5,
    },
    driver_context: {
      computed_at: predicted, macro_staleness_days: 0, macro_fresh: true,
      premium_threshold_pct: 15,
      windows: {
        "7d": { n_obs: 5, t0_date: "2026-06-01", t1_date: "2026-06-07",
          delta_pct_ibja: 0.5, delta_pct_gold_usd: 0.2, delta_pct_usdinr: 0.1,
          delta_pct_premium: 0.2, total_move_rs_per_g: 50,
          gold_usd_contrib_rs_per_g: 30, usdinr_contrib_rs_per_g: 10,
          premium_contrib_rs_per_g: 10, premium_share_pct: 5,
          attribution_valid: true, attribution_valid_reason: "ok" },
        "30d": { n_obs: 20, t0_date: "2026-05-08", t1_date: "2026-06-07",
          delta_pct_ibja: 2.0, delta_pct_gold_usd: 1.0, delta_pct_usdinr: 0.5,
          delta_pct_premium: 0.5, total_move_rs_per_g: 200,
          gold_usd_contrib_rs_per_g: 100, usdinr_contrib_rs_per_g: 50,
          premium_contrib_rs_per_g: 50, premium_share_pct: 5,
          attribution_valid: true, attribution_valid_reason: "ok" },
      },
      driver_state: { usd_inr_now: 84.5, gold_usd_now: 2350, usd_inr_30d_pct_change: 0.5, gold_usd_30d_pct_change: 1.0 },
    },
    real_readings_count: 10,
    current_22k: 14045,
    macro_cache_age_days: 0.5,
    calibration_applied: false,
    calibration_just_unlocked: false,
  });
}

function makePrices(scrapedAgeH) {
  const now  = Date.now();
  // Build 30 readings across 30 days, most fresh, last one at scrapedAgeH
  const readings = [];
  for (let i = 29; i > 0; i--) {
    const ts = new Date(now - i * 86_400_000).toISOString();
    readings.push({ timestamp: ts, "22k": 14000 + i * 10, "24k": 15200 + i * 10,
      "18k": 11400 + i * 5, source: "https://www.tanishq.co.in/gold-rate.html?lang=en_IN" });
  }
  // Last reading matches scraped_at
  const last = new Date(now - scrapedAgeH * 3_600_000).toISOString();
  readings.push({ timestamp: last, "22k": 14045, "24k": 15322, "18k": 11491,
    source: "https://www.tanishq.co.in/gold-rate.html?lang=en_IN" });
  return JSON.stringify(readings);
}

function makeForecastIBJA(scrapeAgeH, ibjaAgeH) {
  const now     = Date.now();
  const scraped  = new Date(now - scrapeAgeH  * 3_600_000).toISOString();
  const predicted = new Date(now - 0.1 * 3_600_000).toISOString();
  const ibjaAsof  = new Date(now - ibjaAgeH   * 3_600_000).toISOString();
  return JSON.stringify({
    predicted_at: predicted,
    scraped_at:   scraped,
    target_window: "5d",
    price_source: "ibja_calibrated",
    current_22k:  14500,
    est_low:      14450,
    est_high:     14550,
    ibja_asof:    ibjaAsof,
    headline: {
      method: "naive_flat_hold",
      predicted_22k: 14500,
      lower: 13565,
      upper: 15435,
      conformal_pi_half: 935,
      naive_mae_recent_30: 616,
      vol_context: {
        half_width: 468, half_width_raw: 231, method: "realized_20d",
        window_days: 20, contiguous_days: 54, is_floored: true, is_degraded: false,
        floor_fraction: 0.5, static_pi_half: 935, baseline_half_width: 346,
        regime: "calm",
      },
    },
    chronos_companion: {
      status: "success", lean_direction: "flat", lean_strength_pct: 0.5,
      direction_acc_30f: 0.55, direction_prob_basis: "base_rate_fallback",
      horizon_p10: [13900, 13850, 13800, 13750, 13700],
      horizon_p50: [14500, 14505, 14510, 14515, 14520],
      horizon_p90: [15100, 15150, 15200, 15250, 15300],
      model_version: "amazon/chronos-bolt-tiny@test",
      calibration_applied: true, calibration_just_unlocked: false,
      majority_direction: "flat", direction_consensus: 0.5,
    },
    driver_context: null,
    real_readings_count: 30,
    model_fallback: false,
    predicted_22k: 14500,
    lower: 13565,
    upper: 15435,
    target_time: predicted,
    model_status: "naive_headline",
    model_version: "naive_flat_hold",
    warmup: false,
  });
}

// ─── Banner visibility check (getComputedStyle, not just .hidden) ─────────────

async function checkBannerState(page) {
  return page.evaluate(() => {
    const el = document.getElementById("stale-banner");
    if (!el) return { found: false };
    const cs = window.getComputedStyle(el);
    return {
      found:       true,
      hidden:      el.hidden,                           // IDL hidden attribute
      display:     cs.display,                          // computed display
      visibility:  cs.visibility,
      text:        el.textContent.trim(),
    };
  });
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
  const browser = await chromium.launch({ headless: true });
  const base    = `http://127.0.0.1:${port}`;

  // ── Inject mock fetch (addInitScript runs before page scripts) ───────────────
  // Override window.fetch so any call to data/forecast.json or data/prices.json
  // returns the mock payload, bypassing both the real HTTP server and the SW.
  async function injectMockFetch(page, forecastJson, pricesJson) {
    await page.addInitScript(({ fc, pr }) => {
      const orig = window.fetch.bind(window);
      window.fetch = (url, opts) => {
        const u = String(url);
        if (u.endsWith("forecast.json") || u.includes("forecast.json")) {
          return Promise.resolve(new Response(fc, {
            status: 200, headers: { "Content-Type": "application/json" }
          }));
        }
        if (u.endsWith("prices.json") || u.includes("prices.json")) {
          return Promise.resolve(new Response(pr, {
            status: 200, headers: { "Content-Type": "application/json" }
          }));
        }
        return orig(url, opts);
      };
    }, { fc: forecastJson, pr: pricesJson });
  }

  try {
    // ── Scenario A: scraped_at 9h old, predicted_at 6 min old → banner SHOWS ──
    console.log("\nScenario A: scraped_at=9h, predicted_at=0.1h → banner must show");
    {
      const ctx  = await browser.newContext();
      const page = await ctx.newPage();
      await injectMockFetch(page, makeForecast(9, 0.1), makePrices(9));

      await page.goto(base, { waitUntil: "networkidle" });
      // Give renderStaleBanner() time to run (it runs synchronously after fetch resolves)
      await page.waitForTimeout(500);

      const state = await checkBannerState(page);
      console.log(`  Banner state: hidden=${state.hidden}, display=${state.display}, text="${state.text}"`);

      assert("banner.hidden === false",       state.hidden === false);
      assert("computed display !== 'none'",   state.display !== "none",   `got "${state.display}"`);
      assert("computed visibility !== hidden", state.visibility !== "hidden", `got "${state.visibility}"`);
      assert('text includes "last confirmed price"',
        state.text.includes("last confirmed price"), `got "${state.text}"`);

      await ctx.close();
    }

    // ── Scenario B: scraped_at 1h old → banner HIDDEN (reset path) ────────────
    console.log("\nScenario B: scraped_at=1h, predicted_at=0.1h → banner must be hidden");
    {
      const ctx  = await browser.newContext();
      const page = await ctx.newPage();
      await injectMockFetch(page, makeForecast(1, 0.1), makePrices(1));

      await page.goto(base, { waitUntil: "networkidle" });
      await page.waitForTimeout(500);

      const state = await checkBannerState(page);
      console.log(`  Banner state: hidden=${state.hidden}, display=${state.display}, text="${state.text}"`);

      assert("banner.hidden === true",        state.hidden === true);
      assert("computed display === 'none'",   state.display === "none",   `got "${state.display}"`);

      await ctx.close();
    }

    // ── Scenario C: IBJA-primary, published today → "Estimated" banner (ADR 025) ──
    console.log("\nScenario C: price_source=ibja_calibrated, IBJA 2h old → 'Estimated' banner");
    {
      const ctx  = await browser.newContext();
      const page = await ctx.newPage();
      // scrape 9h old (stale — expected), IBJA 2h old (fresh, today) → IBJA-primary
      await injectMockFetch(page, makeForecastIBJA(9, 2), makePrices(9));

      await page.goto(base, { waitUntil: "networkidle" });
      await page.waitForTimeout(500);

      const state = await checkBannerState(page);
      console.log(`  Banner state: hidden=${state.hidden}, display=${state.display}, text="${state.text}"`);

      assert("banner.hidden === false",         state.hidden === false);
      assert("computed display !== 'none'",     state.display !== "none",   `got "${state.display}"`);
      assert('text includes "Estimated retail price"',
        state.text.includes("Estimated retail price"), `got "${state.text}"`);
      assert('text includes "IBJA"',
        state.text.includes("IBJA"),            `got "${state.text}"`);
      assert('text includes "today"',
        state.text.includes("today"),           `got "${state.text}"`);
      assert('text does NOT include "last confirmed price"',
        !state.text.includes("last confirmed price"), `got "${state.text}"`);

      await ctx.close();
    }

    // ── Scenario C2: IBJA-primary, weekend/holiday carry-forward (ADR 025) ────
    console.log("\nScenario C2: price_source=ibja_calibrated, IBJA 50h old → dated carry-forward banner");
    {
      const ctx  = await browser.newContext();
      const page = await ctx.newPage();
      // scrape 9h old (stale — expected), IBJA 50h old (Friday, e.g. now is Sunday)
      await injectMockFetch(page, makeForecastIBJA(9, 50), makePrices(9));

      await page.goto(base, { waitUntil: "networkidle" });
      await page.waitForTimeout(500);

      const state = await checkBannerState(page);
      console.log(`  Banner state: hidden=${state.hidden}, display=${state.display}, text="${state.text}"`);

      assert("banner.hidden === false",         state.hidden === false);
      assert('text includes "Estimated retail price"',
        state.text.includes("Estimated retail price"), `got "${state.text}"`);
      assert('text includes "close" (dated carry-forward qualifier)',
        state.text.includes("close"),           `got "${state.text}"`);
      assert('text does NOT include "today\'s"  (must not overclaim freshness)',
        !state.text.includes("today's"),        `got "${state.text}"`);
      assert('text does NOT include "last confirmed price"',
        !state.text.includes("last confirmed price"), `got "${state.text}"`);

      await ctx.close();
    }

    // ── Scenario D: hero shows point estimate; range is a separate secondary
    // line, not jammed into the headline (decluttered per the presentation fix —
    // the point estimate AND range crammed into one giant number read as
    // garbled/stale at a glance). Hero: "≈ ₹14,500". Subline: "estimated range
    // ₹14,450–₹14,550".
    console.log("\nScenario D: price_source=ibja_calibrated → hero '≈ ₹14,500', range as a separate secondary line");
    {
      const ctx  = await browser.newContext();
      const page = await ctx.newPage();
      await injectMockFetch(page, makeForecastIBJA(9, 2), makePrices(9));

      await page.goto(base, { waitUntil: "networkidle" });
      await page.waitForTimeout(500);

      const { heroText, rangeText, rangeHidden, lastConfText, lastConfHidden } = await page.evaluate(() => {
        const hero      = document.getElementById("hero-price");
        const range     = document.getElementById("hero-estimate-range");
        const lastConf  = document.getElementById("hero-last-confirmed");
        return {
          heroText:      hero     ? hero.textContent.trim()     : null,
          rangeText:     range    ? range.textContent.trim()    : null,
          rangeHidden:   range    ? range.hidden                : null,
          lastConfText:  lastConf ? lastConf.textContent.trim() : null,
          lastConfHidden: lastConf ? lastConf.hidden            : null,
        };
      });
      console.log(`  Hero text: "${heroText}"`);
      console.log(`  Range text: "${rangeText}" (hidden=${rangeHidden})`);
      console.log(`  Last-confirmed text: "${lastConfText}" (hidden=${lastConfHidden})`);

      assert("hero-price element found",       heroText !== null);
      assert('hero text includes "≈"',         heroText.includes("≈"),      `got "${heroText}"`);
      assert('hero text includes "14,500"',    heroText.includes("14,500"), `got "${heroText}"`);
      assert('hero text does NOT include the range (no "14,450")',
        !heroText.includes("14,450"), `got "${heroText}" — range leaked into the headline`);

      assert("hero-estimate-range element found", rangeText !== null);
      assert("hero-estimate-range is visible",    rangeHidden === false);
      assert('range text includes "14,450"',      rangeText.includes("14,450"), `got "${rangeText}"`);
      assert('range text includes "14,550"',      rangeText.includes("14,550"), `got "${rangeText}"`);

      // Honest last-confirmed-Tanishq line (real prices.json last reading,
      // "14,045" per makePrices(9) — NOT the "14,500" IBJA estimate above).
      assert("hero-last-confirmed element found", lastConfText !== null);
      assert("hero-last-confirmed is visible",    lastConfHidden === false);
      assert('last-confirmed text mentions "Tanishq"',
        lastConfText.includes("Tanishq"), `got "${lastConfText}"`);
      assert('last-confirmed text includes the real reading "14,045"',
        lastConfText.includes("14,045"), `got "${lastConfText}"`);
      assert('last-confirmed text does NOT include the estimate "14,500"',
        !lastConfText.includes("14,500"), `got "${lastConfText}" — estimate leaked into the last-confirmed line`);

      await ctx.close();
    }

    // ── Scenario E: fresh Tanishq scrape — hero-last-confirmed must be
    // hidden. The hero itself already IS the last-confirmed reading here;
    // showing the secondary line too would just repeat the same number.
    console.log("\nScenario E: price_source=tanishq_scrape (fresh) → hero-last-confirmed hidden (would be redundant)");
    {
      const ctx  = await browser.newContext();
      const page = await ctx.newPage();
      await injectMockFetch(page, makeForecast(1, 0.1), makePrices(1));

      await page.goto(base, { waitUntil: "networkidle" });
      await page.waitForTimeout(500);

      const lastConfHidden = await page.evaluate(() => {
        const el = document.getElementById("hero-last-confirmed");
        return el ? el.hidden : null;
      });
      console.log(`  hero-last-confirmed hidden: ${lastConfHidden}`);

      assert("hero-last-confirmed is hidden on a fresh Tanishq scrape",
        lastConfHidden === true, `got hidden=${lastConfHidden}`);

      await ctx.close();
    }

  } finally {
    await browser.close();
    server.close();
  }

  console.log(`\n${failures === 0 ? PASS : FAIL}  ${failures === 0 ? "All banner checks passed." : `${failures} check(s) failed.`}\n`);
  process.exit(failures === 0 ? 0 : 1);
}

run().catch(err => { console.error(err); process.exit(1); });
