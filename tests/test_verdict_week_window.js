// tests/test_verdict_week_window.js — "Up Rs.X this week" must be a calendar week's move.
//
// Site audit 2026-11-05: on the daily estimate series (one row per IBJA day at 11:30Z = 17:00 IST)
// computeVerdict took the oldest row inside a rolling 168 h window from NOW. After 17:00 IST that
// window had already dropped last week's same-weekday row, so "this week" was 6 days: the live
// page said "Up Rs.379 this week" when the 7-day move was +Rs.175.
//
// Run: node --test tests/test_verdict_week_window.js  (from repo root)

import assert from "assert/strict";
import { test } from "node:test";
import { loadApp } from "./helpers/load_app.js";

const app = loadApp();
const weekAgoReading = app.pure("weekAgoReading");
const computeVerdict = app.pure("computeVerdict");

// One row per IST day at 11:30Z, ending on the IST date `days` before today (0 = today).
function dailyRows(prices) {
  const todayIst = new Date(Date.now() + 5.5 * 3600e3).toISOString().slice(0, 10);
  const base = Date.parse(todayIst + "T11:30:00.000Z");
  return prices.map((v, i) => ({
    timestamp: new Date(base - (prices.length - 1 - i) * 86_400_000).toISOString(),
    "22k": v,
  }));
}

test("weekAgoReading: the row 7 IST days before the newest, whatever the time of day", () => {
  const rows = dailyRows([13000, 12990, 13050, 13100, 13150, 13200, 13300, 13400]); // 8 days
  assert.equal(weekAgoReading(rows)["22k"], 13000);
  // a gap (weekend/holiday) on the exact day: the last row before it
  const gappy = [rows[0], ...rows.slice(2)];
  assert.equal(weekAgoReading(gappy)["22k"], 13000);
  const noSevenDayOld = rows.slice(1);
  assert.equal(weekAgoReading(noSevenDayOld), null);
  assert.equal(weekAgoReading([]), null);
});

test("verdict 'this week' delta is the 7-day move, not a 6-day one (live 5 Nov 2026 shape)", () => {
  // 29 Oct 13,225 -> 30 Oct 13,021 -> ... -> 5 Nov 13,400: real week +175, six days +379
  const rows = dailyRows([13225, 13021, 13100, 12900, 12787, 13050, 13167, 13400]);
  const v = computeVerdict(rows, null);
  assert.equal(v.type, "up");
  assert.match(v.reason, /175/);
  assert.doesNotMatch(v.reason, /379/);
});
