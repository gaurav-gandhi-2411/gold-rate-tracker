// tests/test_tier_degradation_visible.js
// R3 (audit 2026-09-04): the seventh silent fallback -- when price_source
// stays "ibja_calibrated" (ADR 025's normal steady state), the page
// previously gave zero indication whether Tanishq confirmed 2h ago or
// 3 weeks ago. Drives the REAL renderStaleBanner from app.js
// (tests/helpers/load_app.js); it used to test an inlined copy of the branch.
//
// Run: node --test tests/test_tier_degradation_visible.js  (from repo root)

import { test } from "node:test";
import assert from "node:assert/strict";

import { loadApp } from "./helpers/load_app.js";

const SENTINEL = "\u0000";
const TIER_DEGRADED_THRESHOLD_H = loadApp().run("TIER_DEGRADED_THRESHOLD_H");

// Runs the real renderStaleBanner under a fixed clock and reports whether it appended the
// long-silence note: the note key, null (banner shown for the ibja_calibrated tier, no note), or
// undefined (not this tier -- the note is not applicable).
function tierDegradationNoteKey(forecast, nowMs) {
  if (forecast.price_source !== "ibja_calibrated" || !forecast.ibja_asof) return undefined;
  const app = loadApp({ nowMs });
  try {
    app.renderStaleBanner(forecast);
    const banner = app.element("stale-banner");
    if (banner.hidden) throw new Error("real renderStaleBanner hid the banner for an ibja_calibrated forecast");
    const notePrefix = app.t("bannerTanishqLongSilent", { rel: SENTINEL }).split(SENTINEL)[0];
    return banner.textContent.includes(notePrefix) ? "bannerTanishqLongSilent" : null;
  } finally {
    app.dispose();
  }
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
