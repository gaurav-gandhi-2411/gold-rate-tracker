// tests/test_dedup.js
// Pure-function tests for the display-only dedup used by app.js.
// No browser required.
//
// Run: node --test tests/test_dedup.js  (from repo root)
//
// IMPORTANT: This dedup is rendering-only. prices.json is NEVER modified.
// ML pipeline reads prices.json directly and never passes through this logic.

import assert from "assert/strict";
import { test } from "node:test";

// ── Inline copy of the canonical implementation from app.js ───────────────────
// Must stay in sync with dedupReadings() in app.js.
// If you change one, change both.
function dedupReadings(readings) {
  if (readings.length === 0) return [];
  const groups = [];
  let g = { reading: readings[0], endTimestamp: readings[0].timestamp, count: 1 };
  for (let i = 1; i < readings.length; i++) {
    if (readings[i]["22k"] === g.reading["22k"]) {
      g.endTimestamp = readings[i].timestamp;
      g.count++;
    } else {
      groups.push(g);
      g = { reading: readings[i], endTimestamp: readings[i].timestamp, count: 1 };
    }
  }
  groups.push(g);
  return groups;
}

// ── Inline copy of the canonical implementation from app.js ───────────────────
// Must stay in sync with dedupForChart() in app.js.
function dedupForChart(readings) {
  if (readings.length === 0) return [];
  const pts = [];
  let runStart = readings[0];
  let runEnd   = readings[0];
  for (let i = 1; i < readings.length; i++) {
    if (readings[i]["22k"] === runStart["22k"]) {
      runEnd = readings[i];
    } else {
      pts.push(runStart);
      if (runEnd !== runStart) pts.push(runEnd);
      runStart = readings[i];
      runEnd   = readings[i];
    }
  }
  pts.push(runStart);
  if (runEnd !== runStart) pts.push(runEnd);
  return pts;
}

// ── Fixtures ──────────────────────────────────────────────────────────────────
function mk(price, ts) {
  return { "22k": price, "24k": price + 1000, "18k": price - 1000, timestamp: ts };
}
const A1 = mk(14320, "2026-05-01T06:30:00Z");
const A2 = mk(14320, "2026-05-02T06:30:00Z");
const A3 = mk(14320, "2026-05-03T06:30:00Z");
const B1 = mk(14275, "2026-05-04T06:30:00Z");
const B2 = mk(14275, "2026-05-05T06:30:00Z");
const C1 = mk(14320, "2026-05-06T06:30:00Z"); // price returns to A's level
const D1 = mk(14250, "2026-05-07T06:30:00Z");

// ════════════════════════════════════════════════════════════════════════════
// dedupReadings — history display
// ════════════════════════════════════════════════════════════════════════════

test("dedupReadings: consecutive identical readings collapse to one group", () => {
  const groups = dedupReadings([A1, A2, A3]);
  assert.equal(groups.length, 1, "three identical readings → one group");
  assert.equal(groups[0].reading["22k"], 14320);
  assert.equal(groups[0].count, 3);
  assert.equal(groups[0].reading.timestamp, A1.timestamp, "group starts at first reading");
  assert.equal(groups[0].endTimestamp, A3.timestamp,     "group ends at last reading");
});

test("dedupReadings: non-consecutive identical readings are NOT collapsed (separate events)", () => {
  // A1(14320) → B1(14275) → C1(14320) — price returns to 14320, must be separate
  const groups = dedupReadings([A1, B1, C1]);
  assert.equal(groups.length, 3, "A, B, A (non-consecutive) → three separate groups");
  assert.equal(groups[0].reading["22k"], 14320);
  assert.equal(groups[1].reading["22k"], 14275);
  assert.equal(groups[2].reading["22k"], 14320, "third group is separate even though same price as first");
  assert.equal(groups[2].reading.timestamp, C1.timestamp);
});

test("dedupReadings: single reading returns one group with count 1", () => {
  const groups = dedupReadings([A1]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].count, 1);
  assert.equal(groups[0].reading.timestamp, A1.timestamp);
  assert.equal(groups[0].endTimestamp, A1.timestamp);
});

test("dedupReadings: all-identical readings collapse to one group", () => {
  const groups = dedupReadings([A1, A2, A3, mk(14320, "2026-05-08T06:30:00Z")]);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].count, 4);
});

test("dedupReadings: all-distinct readings each become their own group", () => {
  const groups = dedupReadings([A1, B1, C1, D1]);
  assert.equal(groups.length, 4, "four distinct prices → four groups");
  assert.equal(groups[0].count, 1);
  assert.equal(groups[1].count, 1);
  assert.equal(groups[2].count, 1);
  assert.equal(groups[3].count, 1);
  assert.equal(groups[3].reading["22k"], 14250);
});

test("dedupReadings: empty array returns empty array", () => {
  assert.deepEqual(dedupReadings([]), []);
});

test("dedupReadings: mixed runs collapse correctly (A,A,A,B,B,A)", () => {
  // A,A,A → one group (count=3), B,B → one group (count=2), A → one group (count=1)
  const groups = dedupReadings([A1, A2, A3, B1, B2, C1]);
  assert.equal(groups.length, 3);
  assert.equal(groups[0].reading["22k"], 14320); assert.equal(groups[0].count, 3);
  assert.equal(groups[1].reading["22k"], 14275); assert.equal(groups[1].count, 2);
  assert.equal(groups[2].reading["22k"], 14320); assert.equal(groups[2].count, 1);
  assert.equal(groups[2].reading.timestamp, C1.timestamp, "last A group starts at C1");
});

// ════════════════════════════════════════════════════════════════════════════
// dedupForChart — chart display points
// ════════════════════════════════════════════════════════════════════════════

test("dedupForChart: single reading returns one point", () => {
  const pts = dedupForChart([A1]);
  assert.equal(pts.length, 1);
  assert.equal(pts[0]["22k"], 14320);
});

test("dedupForChart: flat hold returns start AND end point (to show hold duration)", () => {
  // [A1, A2, A3] at 14320 → two chart points: first and last timestamp
  const pts = dedupForChart([A1, A2, A3]);
  assert.equal(pts.length, 2, "multi-reading flat hold → start + end points");
  assert.equal(pts[0]["22k"], 14320);
  assert.equal(pts[1]["22k"], 14320);
  assert.equal(pts[0].timestamp, A1.timestamp, "first point = start of hold");
  assert.equal(pts[1].timestamp, A3.timestamp, "second point = end of hold");
});

test("dedupForChart: non-last run emits start+end; next run starts fresh", () => {
  // A1,A2 (hold 14320) then B1 (14275)
  const pts = dedupForChart([A1, A2, B1]);
  assert.equal(pts.length, 3);
  assert.equal(pts[0].timestamp, A1.timestamp); // run start
  assert.equal(pts[1].timestamp, A2.timestamp); // run end
  assert.equal(pts[2].timestamp, B1.timestamp); // new run start
  assert.equal(pts[2]["22k"], 14275);
});

test("dedupForChart: all distinct readings return same count as input", () => {
  const pts = dedupForChart([A1, B1, C1, D1]);
  assert.equal(pts.length, 4, "all-distinct → same number of chart points");
});
