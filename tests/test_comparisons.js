// tests/test_comparisons.js
// Tests for computeComparisons logic (vsLow uses 30d raw window; vs7d/vs30d use IST-day-deduped daily series).
// Functions are inlined from app.js since app.js is not a module.
//
// Run: node --test tests/test_comparisons.js  (from repo root)

import assert from "assert/strict";
import { test } from "node:test";

// --- Inline the functions under test (must match app.js) ---

function dedupeByISTDay(readings) {
  const byDay = new Map();
  for (const r of readings) {
    const key = new Date(r.timestamp).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
    byDay.set(key, r);
  }
  return [...byDay.values()];
}

function computeComparisons(readings) {
  if (readings.length === 0) return null;
  const now     = Date.now();
  const current = readings[readings.length - 1]["22k"];
  const avg     = (arr) => Math.round(arr.reduce((s, v) => s + v, 0) / arr.length);
  const p22     = (r) => r["22k"];

  const raw7d    = readings.filter(r => now - new Date(r.timestamp).getTime() <= 7 * 86400e3);
  const raw30d   = readings.filter(r => now - new Date(r.timestamp).getTime() <= 30 * 86400e3);
  const prices7d  = dedupeByISTDay(raw7d).map(p22);
  const prices30d = dedupeByISTDay(raw30d).map(p22);
  const spanDays  = Math.round((now - new Date(readings[0].timestamp).getTime()) / 86400e3);

  return {
    vs7d:     prices7d.length  > 1 ? current - avg(prices7d)          : null,
    vs30d:    prices30d.length > 1 ? current - avg(prices30d)         : null,
    vsLow:    raw30d.length    > 0 ? current - Math.min(...raw30d.map(p22)) : null,
    spanDays,
  };
}

// --- Helpers ---

function makeReading(price, daysAgo) {
  const ts = new Date(Date.now() - daysAgo * 86400 * 1000).toISOString();
  return { timestamp: ts, "22k": price };
}

// Build multiple readings all on the same IST calendar day.
// Anchors to 12:00 IST (06:30 UTC) on that day to avoid crossing midnight regardless of
// the test runner's UTC clock — makeReading()'s relative-offset approach is unsafe here
// because IST midnight = 18:30 UTC, so a 5-hour span could straddle two IST days.
function makeReadingsSameISTDay(prices, istDaysAgo) {
  const nowIST  = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" }); // "YYYY-MM-DD"
  const [y, m, d] = nowIST.split("-").map(Number);
  const baseDay = new Date(Date.UTC(y, m - 1, d - istDaysAgo));
  const noon    = new Date(baseDay.getTime() + 6 * 3600e3 + 30 * 60e3); // 06:30 UTC = 12:00 IST
  return prices.map((price, i) => ({
    timestamp: new Date(noon.getTime() + i * 1800e3).toISOString(),
    "22k": price,
  }));
}

// --- Tests ---

test("vsLow uses 30d window — all-time low older than 30 days is excluded", () => {
  // All-time low at 60 days ago (outside 30d window), 30d low at 28 days ago.
  const readings = [
    makeReading(10000, 60),  // all-time low — outside 30d window
    makeReading(14000, 28),  // 30d low — inside window
    makeReading(14200, 14),
    makeReading(14440, 0),   // current
  ];
  const cmp = computeComparisons(readings);
  // vsLow should use the 30d low (14000), not the all-time low (10000)
  assert.equal(cmp.vsLow, 14440 - 14000, "vsLow must be vs 30d low, not all-time low");
  assert.notEqual(cmp.vsLow, 14440 - 10000, "vsLow must NOT include readings older than 30 days");
});

test("vsLow is zero when current equals the 30d low", () => {
  const readings = [
    makeReading(14440, 20),
    makeReading(14440, 10),
    makeReading(14440, 0),
  ];
  const cmp = computeComparisons(readings);
  assert.equal(cmp.vsLow, 0);
});

test("vsLow is positive when current is above the 30d low", () => {
  const readings = [
    makeReading(13800, 25),
    makeReading(14200, 10),
    makeReading(14440, 0),
  ];
  const cmp = computeComparisons(readings);
  assert.equal(cmp.vsLow, 14440 - 13800);
});

test("vs30d is null when fewer than 2 readings in last 30 days", () => {
  const readings = [
    makeReading(14000, 35),  // outside 30d
    makeReading(14440, 0),   // only 1 reading in 30d
  ];
  const cmp = computeComparisons(readings);
  assert.equal(cmp.vs30d, null);
});

test("vsLow uses 30d window when all readings are recent", () => {
  const readings = [
    makeReading(14000, 5),
    makeReading(14200, 3),
    makeReading(14440, 0),
  ];
  const cmp = computeComparisons(readings);
  assert.equal(cmp.vsLow, 14440 - 14000);
});

test("vs7d uses daily-average basis — multiple readings/day count as one", () => {
  // Day 6 ago: 6 readings all at 14000 (flat-held price repeats 6×, same IST calendar day).
  // Day 3 ago: 1 reading at 14200.
  // Day 0 (today): 1 reading at 14440 (current).
  //
  // Raw average of [14000×6, 14200, 14440] = Math.round(112640/8) = 14080 → vs7d = 360
  // Daily average of [14000 (day-6), 14200 (day-3), 14440 (today)] = 14213 → vs7d = 227
  //
  // The daily-basis result (227) is the correct "vs 7-day average price" for a user reading it.
  const dayReadings = makeReadingsSameISTDay([14000, 14000, 14000, 14000, 14000, 14000], 6);
  const readings = [
    ...dayReadings,
    makeReading(14200, 3),
    makeReading(14440, 0),
  ];
  const cmp = computeComparisons(readings);
  // Daily dedup: three days → [14000, 14200, 14440], avg = Math.round(42640/3) = 14213
  const expectedDailyAvg = Math.round((14000 + 14200 + 14440) / 3);
  assert.equal(cmp.vs7d, 14440 - expectedDailyAvg,
    `vs7d should be daily-basis ${14440 - expectedDailyAvg}, not raw-basis`);
  // Confirm it does NOT equal the raw average
  const rawAvg = Math.round((14000 * 6 + 14200 + 14440) / 8);
  assert.notEqual(cmp.vs7d, 14440 - rawAvg,
    "vs7d must NOT be computed over raw (time-weighted) readings");
});

test("vsLow still uses raw readings — intra-day extreme is captured", () => {
  // Day 5 ago: two readings on the same IST day — 14100 then 13800 (intra-day dip).
  // vsLow must use raw30d so both values are seen and the minimum (13800) is used.
  const [r1, r2] = makeReadingsSameISTDay([14100, 13800], 5);
  const readings = [r1, r2, makeReading(14440, 0)];
  const cmp = computeComparisons(readings);
  assert.equal(cmp.vsLow, 14440 - 13800, "vsLow must use raw readings to capture intra-day extremes");
});
