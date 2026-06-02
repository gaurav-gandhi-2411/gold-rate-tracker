import { test } from "node:test";
import assert from "node:assert/strict";

// ── Inline the functions under test (no module system in app.js) ──────────────

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

// dedupeByISTDay: one reading per IST calendar day, latest timestamp wins.
// renderChart calls this to produce one chart point per day.
function dedupeByISTDay(readings) {
  const byDay = new Map();
  for (const r of readings) {
    const key = new Date(r.timestamp).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
    byDay.set(key, r);
  }
  return [...byDay.values()];
}

// ── dedupReadings tests ───────────────────────────────────────────────────────

const r = (price, ts) => ({ "22k": price, "24k": price * 1.1, "18k": price * 0.8, timestamp: ts });

test("dedupReadings: empty", () => assert.deepEqual(dedupReadings([]), []));

test("dedupReadings: single reading", () => {
  const res = dedupReadings([r(100, "t1")]);
  assert.equal(res.length, 1);
  assert.equal(res[0].count, 1);
  assert.equal(res[0].reading["22k"], 100);
});

test("dedupReadings: two identical readings collapsed", () => {
  const res = dedupReadings([r(100, "t1"), r(100, "t2")]);
  assert.equal(res.length, 1);
  assert.equal(res[0].count, 2);
  assert.equal(res[0].reading.timestamp, "t1");
  assert.equal(res[0].endTimestamp, "t2");
});

test("dedupReadings: two distinct readings not collapsed", () => {
  const res = dedupReadings([r(100, "t1"), r(200, "t2")]);
  assert.equal(res.length, 2);
  assert.equal(res[0].count, 1);
  assert.equal(res[1].count, 1);
});

test("dedupReadings: non-consecutive identical NOT collapsed", () => {
  const res = dedupReadings([r(100, "t1"), r(200, "t2"), r(100, "t3")]);
  assert.equal(res.length, 3);
});

test("dedupReadings: all identical", () => {
  const res = dedupReadings([r(100, "t1"), r(100, "t2"), r(100, "t3")]);
  assert.equal(res.length, 1);
  assert.equal(res[0].count, 3);
});

test("dedupReadings: mixed run at start", () => {
  const res = dedupReadings([r(100, "t1"), r(100, "t2"), r(200, "t3")]);
  assert.equal(res.length, 2);
  assert.equal(res[0].count, 2);
  assert.equal(res[1].count, 1);
});

test("dedupReadings: mixed run at end", () => {
  const res = dedupReadings([r(100, "t1"), r(200, "t2"), r(200, "t3")]);
  assert.equal(res.length, 2);
  assert.equal(res[0].count, 1);
  assert.equal(res[1].count, 2);
});

// ── Chart series dedup (renderChart uses dedupeByISTDay) ─────────────────────
// renderChart calls dedupeByISTDay(filtered) — one point per IST calendar day,
// latest reading of that day wins. These tests verify the data the chart receives.
// Helper: anchor to 06:30 UTC = 12:00 IST to avoid midnight-boundary artefacts.

function makeIST(istDaysAgo, hourOffset = 0) {
  const nowIST  = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
  const [y, m, d] = nowIST.split("-").map(Number);
  const baseDay = new Date(Date.UTC(y, m - 1, d - istDaysAgo));
  const noon    = new Date(baseDay.getTime() + 6 * 3600e3 + 30 * 60e3 + hourOffset * 3600e3);
  return noon.toISOString();
}

test("chart series: 6 readings on one IST day collapse to 1 chart point", () => {
  // Real-world: scraper fires every 3h, price held flat all day = 6-8 readings/day.
  // renderChart must emit exactly 1 point for that day (latest reading).
  const sameDay = Array.from({ length: 6 }, (_, i) => ({
    "22k": 14365, timestamp: makeIST(0, i - 3),
  }));
  const chartPts = dedupeByISTDay(sameDay);
  assert.equal(chartPts.length, 1, "6 readings on one IST day must collapse to 1 chart point");
});

test("chart series: latest reading of the day wins, not first", () => {
  // Two readings on the same IST day at different prices (price changed intra-day).
  // renderChart should show the LATEST price (map overwrites on each iteration).
  const dayReadings = [
    { "22k": 14300, timestamp: makeIST(0, -2) },  // earlier
    { "22k": 14365, timestamp: makeIST(0,  2) },  // later — this should win
  ];
  const chartPts = dedupeByISTDay(dayReadings);
  assert.equal(chartPts.length, 1);
  assert.equal(chartPts[0]["22k"], 14365, "latest reading of the day must win");
});

test("chart series: readings on distinct IST days each produce one point", () => {
  // 5 readings, each on a different IST day — chart should have 5 points.
  const readings = [4, 3, 2, 1, 0].map(daysAgo => ({
    "22k": 14000 + daysAgo * 50,
    timestamp: makeIST(daysAgo),
  }));
  const chartPts = dedupeByISTDay(readings);
  assert.equal(chartPts.length, 5, "5 distinct IST days must produce 5 chart points");
});
