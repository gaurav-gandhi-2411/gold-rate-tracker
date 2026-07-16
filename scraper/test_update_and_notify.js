// scraper/test_update_and_notify.js
// Unit tests for the >5% jump staleness guard in update-and-notify.js.
// Run: npm test (from scraper/ directory)

import assert from "assert/strict";
import { test } from "node:test";
import { checkJump } from "./update-and-notify.js";

test("checkJump: no prior reading — passes (first-ever data point)", () => {
  assert.doesNotThrow(() => checkJump({ "22k": 14000, "24k": 15280, "18k": 11460 }, undefined));
});

test("checkJump: flat market (0% change) — passes", () => {
  const prev = { "22k": 14000, "24k": 15280, "18k": 11460 };
  assert.doesNotThrow(() => checkJump({ ...prev }, prev));
});

test("checkJump: small realistic move under threshold — passes", () => {
  const prev = { "22k": 14000, "24k": 15280, "18k": 11460 };
  const reading = { "22k": 14200, "24k": 15490, "18k": 11620 }; // ~1.4% up
  assert.doesNotThrow(() => checkJump(reading, prev));
});

test("checkJump: >5% jump on 22k — throws", () => {
  const prev = { "22k": 14000, "24k": 15280, "18k": 11460 };
  const reading = { "22k": 15000, "24k": 15280, "18k": 11460 }; // +7.1%
  assert.throws(() => checkJump(reading, prev), /Implausible 22k jump/);
});

test("checkJump: >5% drop on 24k — throws", () => {
  const prev = { "22k": 14000, "24k": 15280, "18k": 11460 };
  const reading = { "22k": 14000, "24k": 14000, "18k": 11460 }; // -8.4%
  assert.throws(() => checkJump(reading, prev), /Implausible 24k jump/);
});

test("checkJump: prior reading missing a karat value — skips that karat, not a crash", () => {
  const prev = { "22k": 14000 }; // malformed/partial old entry
  assert.doesNotThrow(() => checkJump({ "22k": 14200, "24k": 15490, "18k": 11620 }, prev));
});
