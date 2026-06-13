// test_update_and_notify.mjs
// Tests for the drop-alert decision logic in update-and-notify.js.
// Run with: node --test test_update_and_notify.mjs  (from scraper/)
//
// No live network, no disk: decideDropNotification() is pure. These lock in the
// "no double drop-alert" property for the Worker-dispatch vs scheduled-scrape
// race (WS1 failure mode #5): the two paths serialize via the `check-price`
// concurrency group, so the second run compares against the first's appended
// entry. When both land the same value, delta === 0 and no second alert fires.

import assert from "assert/strict";
import { test } from "node:test";

import { decideDropNotification } from "./update-and-notify.js";

const THRESHOLD = 100;

test("first reading (no lastEntry) → no alert", () => {
  const d = decideDropNotification({ "22k": 13710 }, undefined, THRESHOLD);
  assert.equal(d.notify, false);
  assert.equal(d.reason, "first-reading");
});

test("drop >= threshold → alert with ASCII-safe title and correct numbers", () => {
  const d = decideDropNotification(
    { "22k": 13500, timestamp: "2026-06-13T12:30:00.000Z" },
    { "22k": 13710, timestamp: "2026-06-13T09:00:00.000Z" },
    THRESHOLD,
  );
  assert.equal(d.notify, true);
  // Title goes in an HTTP header → must be ASCII (norm #12: Rs. not the glyph).
  assert.ok(!d.payload.title.includes("₹"), "title must not contain the rupee glyph");
  assert.match(d.payload.title, /Gold 22K dropped Rs\./);
  assert.equal(d.payload.priority, 4);
  assert.equal(d.payload.tags, "money_with_wings,chart_with_downwards_trend");
  // Body carries the real drop amount (210 = 13710 - 13500).
  assert.ok(d.payload.message.includes("210"), "message must state the Rs.210 drop");
});

test("drop below threshold → no alert", () => {
  const d = decideDropNotification(
    { "22k": 13660 },
    { "22k": 13710 },
    THRESHOLD,
  );
  assert.equal(d.notify, false);
  assert.match(d.reason, /below-threshold/);
});

test("price rose → no alert (drops only, per spec)", () => {
  const d = decideDropNotification(
    { "22k": 13900 },
    { "22k": 13710 },
    THRESHOLD,
  );
  assert.equal(d.notify, false);
  assert.match(d.reason, /^rose/);
});

test("WS1 race guard: identical value (delta 0) → no double drop-alert", () => {
  // A dispatch run appended 13710; a scheduled run in the same cycle scrapes the
  // same 13710. The scheduled run compares against the dispatch entry → delta 0.
  const d = decideDropNotification(
    { "22k": 13710, timestamp: "2026-06-13T12:31:00.000Z" },
    { "22k": 13710, timestamp: "2026-06-13T12:30:00.000Z" },
    THRESHOLD,
  );
  assert.equal(d.notify, false);
  assert.equal(d.reason, "unchanged");
});

test("WS1 race guard: re-fire suppressed after the drop was already alerted", () => {
  // Run 1: 13710 -> 13500 fires the drop alert (verified above).
  // Run 2 (same cycle): another reading of 13500 vs the appended 13500 → delta 0.
  const d = decideDropNotification(
    { "22k": 13500 },
    { "22k": 13500 },
    THRESHOLD,
  );
  assert.equal(d.notify, false);
  assert.equal(d.reason, "unchanged");
});

test("drop exactly at threshold → alert (>= boundary)", () => {
  const d = decideDropNotification({ "22k": 13610 }, { "22k": 13710 }, THRESHOLD);
  assert.equal(d.notify, true);
});
