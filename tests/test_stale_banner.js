// tests/test_stale_banner.js — renderStaleBanner state logic (ADR 025: IBJA-primary)
// Run: node --test tests/test_stale_banner.js

import { test } from "node:test";
import assert from "node:assert/strict";

const STALE_THRESHOLD_H = 8; // mirrors app.js + inference.py constant (Tanishq enrichment gate)

function istDayKey(d) {
  return d.toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
}

// Mirrors renderStaleBanner()'s decision logic in app.js (ADR 025).
// Per ADR 025, IBJA-calibrated is the PRIMARY path — trusted via price_source
// alone (inference.py already gated its freshness), with the date qualifier
// derived purely from whether ibja_asof falls on today's IST calendar day.
// Returns: "hidden" | "approximate_today" | "approximate_carry_forward" | "stale"
function bannerStateForForecast(forecast, nowMs) {
  if (!forecast) return "hidden";

  if (forecast.price_source === "ibja_calibrated" && forecast.ibja_asof) {
    const ibjaDate = new Date(forecast.ibja_asof);
    const isToday = istDayKey(ibjaDate) === istDayKey(new Date(nowMs));
    return isToday ? "approximate_today" : "approximate_carry_forward";
  }

  if (!forecast.scraped_at) return "hidden";
  const scrapeAgeH = (nowMs - new Date(forecast.scraped_at).getTime()) / 3_600_000;
  if (scrapeAgeH <= STALE_THRESHOLD_H) return "hidden"; // Tanishq enrichment fresh

  return "stale"; // genuinely stale — neither source available
}

// ── Tanishq-enrichment path ───────────────────────────────────────────────────

test("scraped_at 9h old + price_source=tanishq_scrape → stale", () => {
  const nowMs = Date.now();
  const forecast = {
    scraped_at:   new Date(nowMs - 9 * 3_600_000).toISOString(),
    predicted_at: new Date(nowMs).toISOString(),
    price_source: "tanishq_scrape",
  };
  assert.equal(bannerStateForForecast(forecast, nowMs), "stale");
});

test("scraped_at 7h old → hidden (fresh Tanishq enrichment)", () => {
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

test("no forecast → hidden", () => {
  assert.equal(bannerStateForForecast(null, Date.now()), "hidden");
});

test("no scraped_at field, no price_source → hidden", () => {
  const nowMs = Date.now();
  const forecast = { predicted_at: new Date(nowMs - 9 * 3_600_000).toISOString() };
  assert.equal(bannerStateForForecast(forecast, nowMs), "hidden");
});

// ── IBJA-primary path (ADR 025) ───────────────────────────────────────────────

test("ibja_calibrated, ibja_asof is today → approximate_today (no date qualifier)", () => {
  const nowMs = Date.now();
  const forecast = {
    scraped_at:   new Date(nowMs - 10 * 3_600_000).toISOString(), // Tanishq stale — expected
    price_source: "ibja_calibrated",
    ibja_asof:    new Date(nowMs - 2 * 3_600_000).toISOString(),  // published a few hours ago
    current_22k:  14500,
    est_low:      14450,
    est_high:     14550,
  };
  assert.equal(bannerStateForForecast(forecast, nowMs), "approximate_today");
});

test("ibja_calibrated, ibja_asof is Friday and now is Sunday → approximate_carry_forward, NOT stale", () => {
  // Per ADR 025: IBJA publishes weekday-only, so a Friday close on a Sunday is
  // the EXPECTED steady state, not staleness. Previously (pre-025) this asserted
  // "stale" — that was the exact gap ADR 025 closed.
  const nowMs = Date.now();
  const forecast = {
    scraped_at:   new Date(nowMs - 9 * 3_600_000).toISOString(),
    price_source: "ibja_calibrated",
    ibja_asof:    new Date(nowMs - 50 * 3_600_000).toISOString(), // Friday rate, now is Sunday
    current_22k:  14500,
    est_low:      14450,
    est_high:     14550,
  };
  assert.equal(bannerStateForForecast(forecast, nowMs), "approximate_carry_forward");
});

test("ibja_calibrated, ibja_asof 20 days old → still approximate_carry_forward (backend already gated it)", () => {
  // Per ADR 025, the frontend trusts price_source alone — inference.py's own
  // 14-day backstop is what decides whether ibja_calibrated is emitted at all.
  // If the backend served it, the frontend shows it; it never re-derives an age gate.
  const nowMs = Date.now();
  const forecast = {
    scraped_at:   new Date(nowMs - 9 * 3_600_000).toISOString(),
    price_source: "ibja_calibrated",
    ibja_asof:    new Date(nowMs - 20 * 24 * 3_600_000).toISOString(),
    current_22k:  14500,
  };
  assert.equal(bannerStateForForecast(forecast, nowMs), "approximate_carry_forward");
});

test("ibja_calibrated but ibja_asof missing → falls through to Tanishq staleness check", () => {
  const nowMs = Date.now();
  const forecast = {
    scraped_at:   new Date(nowMs - 9 * 3_600_000).toISOString(),
    price_source: "ibja_calibrated",
    // ibja_asof intentionally absent — defensive fallback
    current_22k:  14500,
    est_low:      14450,
    est_high:     14550,
  };
  assert.equal(bannerStateForForecast(forecast, nowMs), "stale");
});
