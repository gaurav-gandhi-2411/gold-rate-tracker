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

// ── dedupForChart tests ───────────────────────────────────────────────────────

test("dedupForChart: empty", () => assert.deepEqual(dedupForChart([]), []));

test("dedupForChart: single point", () => {
  const res = dedupForChart([r(100, "t1")]);
  assert.equal(res.length, 1);
});

test("dedupForChart: two identical → two points (start+end)", () => {
  const res = dedupForChart([r(100, "t1"), r(100, "t2")]);
  assert.equal(res.length, 2);
  assert.equal(res[0].timestamp, "t1");
  assert.equal(res[1].timestamp, "t2");
});

test("dedupForChart: two distinct → two points", () => {
  const res = dedupForChart([r(100, "t1"), r(200, "t2")]);
  assert.equal(res.length, 2);
});

test("dedupForChart: run-then-change emits start+end+change", () => {
  // [A, A, A, B] → [A_start, A_end, B]
  const res = dedupForChart([r(100, "t1"), r(100, "t2"), r(100, "t3"), r(200, "t4")]);
  assert.equal(res.length, 3);
  assert.equal(res[0].timestamp, "t1");
  assert.equal(res[1].timestamp, "t3");
  assert.equal(res[2].timestamp, "t4");
});

// ── Chart series dedup (renderChart uses dedupForChart) ───────────────────────
// renderChart calls dedupForChart(filtered) before building labels/data arrays,
// so these tests verify the data the trend chart actually receives.

test("chart series: 40 flat readings (one 3h price hold) collapse to 2 points", () => {
  // Real-world case: scraper fires every 3h, price unchanged for ~5 days = ~40 readings.
  // renderChart must hand Chart.js only 2 points (start + end), not 40.
  const flat = Array.from({ length: 40 }, (_, i) => ({
    "22k": 14365, "24k": 15678, "18k": 11751,
    timestamp: new Date(Date.UTC(2026, 5, 1, i * 3)).toISOString(),
  }));
  const chartPts = dedupForChart(flat);
  assert.equal(chartPts.length, 2, "40 flat readings must collapse to 2 chart points");
  assert.equal(chartPts[0].timestamp, flat[0].timestamp, "first point is the run start");
  assert.equal(chartPts[chartPts.length - 1].timestamp, flat[flat.length - 1].timestamp, "last point is the run end");
});

test("chart series: three price levels produce correct stepped segments", () => {
  // [A×3, B×2, C×1] → [A_start, A_end, B_start, B_end, C]
  // Each segment has its own start+end boundary; single-reading runs appear once.
  const pts = dedupForChart([
    r(14000, "t1"), r(14000, "t2"), r(14000, "t3"),
    r(14200, "t4"), r(14200, "t5"),
    r(14300, "t6"),
  ]);
  assert.equal(pts.length, 5, "three runs → 5 chart points (2+2+1)");
  assert.equal(pts[0]["22k"], 14000); // A start
  assert.equal(pts[1]["22k"], 14000); // A end (t3)
  assert.equal(pts[2]["22k"], 14200); // B start (t4)
  assert.equal(pts[3]["22k"], 14200); // B end (t5)
  assert.equal(pts[4]["22k"], 14300); // C (single point)
  assert.equal(pts[1].timestamp, "t3");
  assert.equal(pts[2].timestamp, "t4");
});
