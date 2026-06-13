// tests/test_stale_banner.js — renderStaleBanner 3-state logic (Phi20 + Phi22 H5)
// Run: node --test tests/test_stale_banner.js

import { test } from "node:test";
import assert from "node:assert/strict";

const STALE_THRESHOLD_H = 8; // mirrors app.js + inference.py constant
const IBJA_FALLBACK_MAX_AGE_H = 30; // mirrors app.js + inference.py _IBJA_MAX_AGE_H

// Mirrors the 3-state decision logic of renderStaleBanner() in app.js.
// Returns: "hidden" | "approximate" | "stale"
function bannerStateForForecast(forecast, nowMs) {
  if (!forecast || !forecast.scraped_at) return "hidden";
  const scrapeAgeH = (nowMs - new Date(forecast.scraped_at).getTime()) / 3_600_000;
  if (scrapeAgeH <= STALE_THRESHOLD_H) return "hidden"; // scraped-fresh

  // Scrape is stale — check IBJA fallback. IBJA publishes once daily, so the
  // estimate may be up to ~30h old and still be the latest official benchmark.
  if (forecast.price_source === "ibja_calibrated" && forecast.ibja_asof) {
    const ibjaAgeH = (nowMs - new Date(forecast.ibja_asof).getTime()) / 3_600_000;
    if (ibjaAgeH < IBJA_FALLBACK_MAX_AGE_H) return "approximate"; // State 2
  }

  return "stale"; // State 3 — genuinely stale
}

// ── Existing tests (Phi20) ────────────────────────────────────────────────────

test("scraped_at 9h old + price_source=tanishq_scrape → stale", () => {
  const nowMs = Date.now();
  const forecast = {
    scraped_at:   new Date(nowMs - 9 * 3_600_000).toISOString(),
    predicted_at: new Date(nowMs).toISOString(),
    price_source: "tanishq_scrape",
  };
  assert.equal(bannerStateForForecast(forecast, nowMs), "stale");
});

test("scraped_at 7h old → hidden", () => {
  const nowMs = Date.now();
  const forecast = {
    scraped_at:   new Date(nowMs - 7 * 3_600_000).toISOString(),
    price_source: "tanishq_scrape",
  };
  assert.equal(bannerStateForForecast(forecast, nowMs), "hidden");
});

test("scraped_at exactly 8h old → hidden (boundary: condition is > 8, not >= 8)", () => {
  const nowMs = Date.now();
  const forecast = {
    scraped_at:   new Date(nowMs - 8 * 3_600_000).toISOString(),
    price_source: "tanishq_scrape",
  };
  assert.equal(bannerStateForForecast(forecast, nowMs), "hidden");
});

test("no scraped_at field → hidden", () => {
  const nowMs = Date.now();
  const forecast = { predicted_at: new Date(nowMs - 9 * 3_600_000).toISOString() };
  assert.equal(bannerStateForForecast(forecast, nowMs), "hidden");
});

// ── New H5 tests (Phi22) ──────────────────────────────────────────────────────

test("Phi22 state 2: scrape 9h old, ibja_calibrated, IBJA 2h old → approximate", () => {
  const nowMs = Date.now();
  const forecast = {
    scraped_at:   new Date(nowMs - 9 * 3_600_000).toISOString(),
    price_source: "ibja_calibrated",
    ibja_asof:    new Date(nowMs - 2 * 3_600_000).toISOString(),
    current_22k:  14500,
    est_low:      14450,
    est_high:     14550,
  };
  assert.equal(bannerStateForForecast(forecast, nowMs), "approximate");
});

test("H5-widen: scrape 10h old, ibja_calibrated, IBJA 24h old → approximate (within 30h)", () => {
  // Overnight gap: IBJA from yesterday's publication is ~24h old. Previously (8h
  // gate) this fell to "stale"; now it correctly shows the IBJA estimate banner.
  const nowMs = Date.now();
  const forecast = {
    scraped_at:   new Date(nowMs - 10 * 3_600_000).toISOString(),
    price_source: "ibja_calibrated",
    ibja_asof:    new Date(nowMs - 24 * 3_600_000).toISOString(),
    current_22k:  14500,
  };
  assert.equal(bannerStateForForecast(forecast, nowMs), "approximate");
});

test("H5-widen boundary: IBJA exactly 30h old → stale (condition is < 30)", () => {
  const nowMs = Date.now();
  const forecast = {
    scraped_at:   new Date(nowMs - 10 * 3_600_000).toISOString(),
    price_source: "ibja_calibrated",
    ibja_asof:    new Date(nowMs - 30 * 3_600_000).toISOString(),
    current_22k:  14500,
  };
  assert.equal(bannerStateForForecast(forecast, nowMs), "stale");
});

test("Phi22 weekend: scrape 9h old, ibja_calibrated, IBJA 50h old → stale", () => {
  // IBJA publishes weekday-only. On Sunday, latest row is from Friday (48-72h old).
  const nowMs = Date.now();
  const forecast = {
    scraped_at:   new Date(nowMs - 9 * 3_600_000).toISOString(),
    price_source: "ibja_calibrated",
    ibja_asof:    new Date(nowMs - 50 * 3_600_000).toISOString(), // Friday rate, now is Sunday
    current_22k:  14500,
    est_low:      14450,
    est_high:     14550,
  };
  assert.equal(bannerStateForForecast(forecast, nowMs), "stale");
});

test("Phi22 defensive: ibja_calibrated but ibja_asof missing → stale", () => {
  const nowMs = Date.now();
  const forecast = {
    scraped_at:   new Date(nowMs - 9 * 3_600_000).toISOString(),
    price_source: "ibja_calibrated",
    // ibja_asof intentionally absent
    current_22k:  14500,
    est_low:      14450,
    est_high:     14550,
  };
  assert.equal(bannerStateForForecast(forecast, nowMs), "stale");
});
