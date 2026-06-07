// tests/test_stale_banner.js — renderStaleBanner staleness logic (Φ20)
// Run: node --test tests/test_stale_banner.js

import { test } from "node:test";
import assert from "node:assert/strict";

// Mirrors the decision logic of renderStaleBanner() in app.js.
// Returns true if the banner should be shown, false otherwise.
function shouldShowStaleBanner(forecast, nowMs) {
  if (!forecast || !forecast.scraped_at) return false;
  const ageH = (nowMs - new Date(forecast.scraped_at).getTime()) / 3_600_000;
  return ageH > 8;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

test("scraped_at 9h old + predicted_at fresh → banner shows", () => {
  // Split-state scenario: scrape failed but inference ran (CF block).
  const nowMs = Date.now();
  const forecast = {
    scraped_at: new Date(nowMs - 9 * 3_600_000).toISOString(),
    predicted_at: new Date(nowMs).toISOString(),
  };
  assert.equal(shouldShowStaleBanner(forecast, nowMs), true);
});

test("scraped_at 7h old → banner hidden", () => {
  const nowMs = Date.now();
  const forecast = {
    scraped_at: new Date(nowMs - 7 * 3_600_000).toISOString(),
  };
  assert.equal(shouldShowStaleBanner(forecast, nowMs), false);
});

test("scraped_at exactly 8h old → banner hidden", () => {
  // Boundary: condition is > 8, not >= 8.
  const nowMs = Date.now();
  const forecast = {
    scraped_at: new Date(nowMs - 8 * 3_600_000).toISOString(),
  };
  assert.equal(shouldShowStaleBanner(forecast, nowMs), false);
});

test("no scraped_at field → banner hidden", () => {
  const nowMs = Date.now();
  const forecast = { predicted_at: new Date(nowMs - 9 * 3_600_000).toISOString() };
  assert.equal(shouldShowStaleBanner(forecast, nowMs), false);
});
