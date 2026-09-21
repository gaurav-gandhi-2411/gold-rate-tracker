import { test } from "node:test";
import assert from "node:assert/strict";
import { loadApp } from "./helpers/load_app.js";

// Tests the REAL computeTrendDescription from app.js under a fixed clock (tests/helpers/load_app.js).
// This file used to test a wrapper called classifyTrend that exists nowhere in app.js, next to a
// copy of computeTrendDescription that was a stub returning null -- so the real function had no
// coverage at all (audit 2026-09-21, instance #21).
const NOW = Date.parse("2026-09-15T12:00:00Z");
const DAY = 86_400_000;
const app = loadApp({ nowMs: NOW });
const computeTrendDescription = app.pure("computeTrendDescription");

// Two readings inside the window: `first` daysAgo the earliest, `last` now.
const readings = (first, last, firstDaysAgo = 6) => [
  { timestamp: new Date(NOW - firstDaysAgo * DAY).toISOString(), "22k": first },
  { timestamp: new Date(NOW).toISOString(), "22k": last },
];

test("trend: flat when |delta| < 100", () => {
  assert.equal(computeTrendDescription(readings(14000, 14050), 7), "Roughly flat over the past 7 days");
  assert.equal(computeTrendDescription(readings(14000, 13950), 7), "Roughly flat over the past 7 days");
  assert.equal(computeTrendDescription(readings(14000, 14000), 7), "Roughly flat over the past 7 days");
});

test("trend: up when delta >= 100, with the signed amount", () => {
  const res = computeTrendDescription(readings(14000, 14200), 7);
  assert.equal(res, "Trending up — +₹200 over the past 7 days");
});

test("trend: down when delta <= -100, with the signed amount", () => {
  const res = computeTrendDescription(readings(14200, 14000), 7);
  assert.equal(res, "Trending down — −₹200 over the past 7 days");
});

test("trend: boundary at exactly +100 is up, exactly -100 is down", () => {
  assert.ok(computeTrendDescription(readings(14000, 14100), 7).startsWith("Trending up"));
  assert.ok(computeTrendDescription(readings(14100, 14000), 7).startsWith("Trending down"));
});

test("trend: readings older than the window are ignored (delta measured inside the window only)", () => {
  // A huge move 30 days ago must not leak into a 7-day description.
  const all = [
    { timestamp: new Date(NOW - 30 * DAY).toISOString(), "22k": 10000 },
    ...readings(14000, 14020, 5),
  ];
  assert.equal(computeTrendDescription(all, 7), "Roughly flat over the past 7 days");
});

test("trend: fewer than two readings inside the window -> null, not a fabricated description", () => {
  assert.equal(computeTrendDescription(null, 7), null);
  assert.equal(computeTrendDescription([], 7), null);
  const oneRecent = [
    { timestamp: new Date(NOW - 30 * DAY).toISOString(), "22k": 10000 },
    { timestamp: new Date(NOW - 1 * DAY).toISOString(), "22k": 14000 },
  ];
  assert.equal(computeTrendDescription(oneRecent, 7), null);
});
