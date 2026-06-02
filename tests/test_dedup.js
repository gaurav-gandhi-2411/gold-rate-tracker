// tests/test_dedup.js
// Tests for dedupeByISTDay — one reading per IST calendar day (latest wins).
// Function is inlined from app.js since app.js is not a module.
//
// Run: node --test tests/test_dedup.js  (from repo root)

import assert from "assert/strict";
import { test } from "node:test";

// --- Inline the function under test (must match app.js) ---

function dedupeByISTDay(readings) {
  const byDay = new Map();
  for (const r of readings) {
    const key = new Date(r.timestamp).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
    byDay.set(key, r);
  }
  return [...byDay.values()];
}

// --- Tests ---

test("single reading per day is unchanged", () => {
  const r1 = { timestamp: "2026-05-30T08:00:00.000Z", "22k": 14440 };
  const r2 = { timestamp: "2026-05-31T08:00:00.000Z", "22k": 14450 };
  const result = dedupeByISTDay([r1, r2]);
  assert.equal(result.length, 2);
  assert.equal(result[0]["22k"], 14440);
  assert.equal(result[1]["22k"], 14450);
});

test("multiple same-day readings collapse to latest (same price)", () => {
  // Three readings on 2026-05-31 IST — all same price
  const r1 = { timestamp: "2026-05-31T00:00:00.000Z", "22k": 14440 };
  const r2 = { timestamp: "2026-05-31T04:00:00.000Z", "22k": 14440 };
  const r3 = { timestamp: "2026-05-31T08:00:00.000Z", "22k": 14440 };
  const result = dedupeByISTDay([r1, r2, r3]);
  assert.equal(result.length, 1, "three same-day readings → one entry");
  assert.equal(result[0].timestamp, r3.timestamp, "latest reading is kept");
});

test("multiple same-day readings collapse to latest (different prices)", () => {
  // Intraday price update: later reading shows different price
  const r1 = { timestamp: "2026-05-31T02:00:00.000Z", "22k": 14430 };
  const r2 = { timestamp: "2026-05-31T06:00:00.000Z", "22k": 14440 };
  const result = dedupeByISTDay([r1, r2]);
  assert.equal(result.length, 1);
  assert.equal(result[0]["22k"], 14440, "latest price is kept");
});

test("readings spanning multiple days each get one entry", () => {
  const readings = [
    { timestamp: "2026-05-29T04:00:00.000Z", "22k": 14495 },
    { timestamp: "2026-05-29T14:00:00.000Z", "22k": 14495 },
    { timestamp: "2026-05-30T04:00:00.000Z", "22k": 14440 },
    { timestamp: "2026-05-30T14:00:00.000Z", "22k": 14440 },
    { timestamp: "2026-05-31T04:00:00.000Z", "22k": 14440 },
  ];
  const result = dedupeByISTDay(readings);
  assert.equal(result.length, 3, "three distinct IST days → three entries");
});

test("IST day boundary: UTC midnight is IST 05:30 — same IST day", () => {
  // 2026-05-30T18:30Z = 2026-05-31T00:00 IST (start of May 31 IST)
  // 2026-05-30T20:00Z = 2026-05-31T01:30 IST — same IST day as above
  const r1 = { timestamp: "2026-05-30T18:30:00.000Z", "22k": 14440 };
  const r2 = { timestamp: "2026-05-30T20:00:00.000Z", "22k": 14440 };
  const result = dedupeByISTDay([r1, r2]);
  assert.equal(result.length, 1, "both readings fall on same IST day");
});

test("empty input returns empty output", () => {
  const result = dedupeByISTDay([]);
  assert.equal(result.length, 0);
});

test("output preserves chronological order", () => {
  const readings = [
    { timestamp: "2026-05-28T08:00:00.000Z", "22k": 14400 },
    { timestamp: "2026-05-29T08:00:00.000Z", "22k": 14440 },
    { timestamp: "2026-05-30T08:00:00.000Z", "22k": 14480 },
  ];
  const result = dedupeByISTDay(readings);
  assert.equal(result[0]["22k"], 14400);
  assert.equal(result[1]["22k"], 14440);
  assert.equal(result[2]["22k"], 14480);
});
