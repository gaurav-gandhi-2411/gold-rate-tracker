// tests/test_comparisons.js
// Tests for computeComparisons logic (vsLow uses 30d window, not all-time).
// Functions are inlined from app.js since app.js is not a module.
//
// Run: node --test tests/test_comparisons.js  (from repo root)

import assert from "assert/strict";
import { test } from "node:test";

// --- Inline the function under test (must match app.js) ---

function computeComparisons(readings) {
  if (readings.length === 0) return null;
  const now     = Date.now();
  const current = readings[readings.length - 1]["22k"];
  const avg     = (arr) => Math.round(arr.reduce((s, v) => s + v, 0) / arr.length);
  const p22     = (r) => r["22k"];

  const prices7d  = readings.filter(r => now - new Date(r.timestamp).getTime() <= 7 * 86400e3).map(p22);
  const prices30d = readings.filter(r => now - new Date(r.timestamp).getTime() <= 30 * 86400e3).map(p22);
  const spanDays  = Math.round((now - new Date(readings[0].timestamp).getTime()) / 86400e3);

  return {
    vs7d:     prices7d.length  > 1 ? current - avg(prices7d)       : null,
    vs30d:    prices30d.length > 1 ? current - avg(prices30d)      : null,
    vsLow:    prices30d.length > 0 ? current - Math.min(...prices30d) : null,
    spanDays,
  };
}

// --- Helpers ---

function makeReading(price, daysAgo) {
  const ts = new Date(Date.now() - daysAgo * 86400 * 1000).toISOString();
  return { timestamp: ts, "22k": price };
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
