// tests/test_tier_degradation_visible.js
// R3 (audit 2026-09-04): the seventh silent fallback -- when price_source
// stays "ibja_calibrated" (ADR 025's normal steady state), the page
// previously gave zero indication whether Tanishq confirmed 2h ago or
// 3 weeks ago. Inlined from app.js's renderStaleBanner (app.js has no
// module system -- same pattern as test_good_price.js/test_vol_regime.js).
//
// Run: node --test tests/test_tier_degradation_visible.js  (from repo root)

import { test } from "node:test";
import assert from "node:assert/strict";

const TIER_DEGRADED_THRESHOLD_H = 48;

// Inline copy of the renderStaleBanner logic under test: given a forecast in
// the ibja_calibrated tier, decide whether the long-silence note is appended.
// Returns the appended-note key or null (no append -- routine steady state).
function tierDegradationNoteKey(forecast, nowMs) {
  if (forecast.price_source !== "ibja_calibrated" || !forecast.ibja_asof) return undefined; // n/a, not this tier
  if (!forecast.scraped_at) return null;
  const scrapedAgeH = (nowMs - new Date(forecast.scraped_at).getTime()) / 3_600_000;
  return scrapedAgeH > TIER_DEGRADED_THRESHOLD_H ? "bannerTanishqLongSilent" : null;
}

const NOW = Date.parse("2026-09-04T12:00:00Z");
const HOUR = 3_600_000;

function isoHoursAgo(hours) {
  return new Date(NOW - hours * HOUR).toISOString();
}

test("tierDegradationNoteKey: routine ibja_calibrated (Tanishq confirmed recently) stays silent", () => {
  const forecast = {
    price_source: "ibja_calibrated",
    ibja_asof: isoHoursAgo(1),
    scraped_at: isoHoursAgo(9), // just past the 8h enrichment gate, routine per ADR 025
  };
  assert.equal(tierDegradationNoteKey(forecast, NOW), null);
});

test("tierDegradationNoteKey: exactly at the degradation threshold stays silent (strictly greater-than)", () => {
  const forecast = {
    price_source: "ibja_calibrated",
    ibja_asof: isoHoursAgo(1),
    scraped_at: isoHoursAgo(TIER_DEGRADED_THRESHOLD_H),
  };
  assert.equal(tierDegradationNoteKey(forecast, NOW), null);
});

test("tierDegradationNoteKey: past the degradation threshold appends the note", () => {
  const forecast = {
    price_source: "ibja_calibrated",
    ibja_asof: isoHoursAgo(1),
    scraped_at: isoHoursAgo(TIER_DEGRADED_THRESHOLD_H + 1),
  };
  assert.equal(tierDegradationNoteKey(forecast, NOW), "bannerTanishqLongSilent");
});

test("tierDegradationNoteKey: a permanently dead runner (weeks of silence) is visible, not silent forever", () => {
  const forecast = {
    price_source: "ibja_calibrated",
    ibja_asof: isoHoursAgo(1),
    scraped_at: isoHoursAgo(24 * 21), // 3 weeks
  };
  assert.equal(tierDegradationNoteKey(forecast, NOW), "bannerTanishqLongSilent");
});

test("tierDegradationNoteKey: missing scraped_at (very old cached forecast.json) does not crash or claim degradation", () => {
  const forecast = { price_source: "ibja_calibrated", ibja_asof: isoHoursAgo(1) };
  assert.equal(tierDegradationNoteKey(forecast, NOW), null);
});

test("tierDegradationNoteKey: not the ibja_calibrated tier -- not applicable, no false claim either way", () => {
  const forecast = { price_source: "tanishq_scrape", scraped_at: isoHoursAgo(100) };
  assert.equal(tierDegradationNoteKey(forecast, NOW), undefined);
});

test("tierDegradationNoteKey: driven by forecast.scraped_at, not any hand-typed duration", () => {
  // Same tier/ibja_asof, only scraped_at differs -- output must track it exactly.
  const base = { price_source: "ibja_calibrated", ibja_asof: isoHoursAgo(1) };
  assert.equal(tierDegradationNoteKey({ ...base, scraped_at: isoHoursAgo(47) }, NOW), null);
  assert.equal(tierDegradationNoteKey({ ...base, scraped_at: isoHoursAgo(49) }, NOW), "bannerTanishqLongSilent");
});
