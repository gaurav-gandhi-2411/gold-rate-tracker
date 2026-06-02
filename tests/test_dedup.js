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
