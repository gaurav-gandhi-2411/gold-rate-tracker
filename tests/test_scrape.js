// tests/test_scrape.js
// Pure-function tests for the scraper's validation logic.
// No browser required — these test validate() directly.
//
// Run: node --test tests/test_scrape.js  (from repo root)
// The Playwright fixture test (DOM extraction) is in scraper/test_scrape.js.

import assert from "assert/strict";
import { test } from "node:test";

// Validation thresholds mirrored from scraper/scrape.js
const RANGE_MIN = 2000;
const RANGE_MAX = 25000;
const RATIO_22_24_MIN = 0.905;
const RATIO_22_24_MAX = 0.925;
const RATIO_18_24_MIN = 0.73;
const RATIO_18_24_MAX = 0.77;

function validate(rate22, rate24, rate18) {
  for (const [label, val] of [["22K", rate22], ["24K", rate24], ["18K", rate18]]) {
    if (!Number.isFinite(val) || val < RANGE_MIN || val > RANGE_MAX) {
      throw new Error(`${label}=₹${val} outside range [${RANGE_MIN}, ${RANGE_MAX}]`);
    }
  }
  if (!(rate18 < rate22 && rate22 < rate24)) {
    throw new Error(`ordering violated: ${rate18} < ${rate22} < ${rate24}`);
  }
  const r22_24 = rate22 / rate24;
  const r18_24 = rate18 / rate24;
  if (r22_24 < RATIO_22_24_MIN || r22_24 > RATIO_22_24_MAX) {
    throw new Error(`22K/24K ratio ${r22_24.toFixed(4)} out of range [${RATIO_22_24_MIN}, ${RATIO_22_24_MAX}]`);
  }
  if (r18_24 < RATIO_18_24_MIN || r18_24 > RATIO_18_24_MAX) {
    throw new Error(`18K/24K ratio ${r18_24.toFixed(4)} out of range [${RATIO_18_24_MIN}, ${RATIO_18_24_MAX}]`);
  }
  return { r22_24, r18_24 };
}

// ── Fixture values hand-read from tests/fixtures/tanishq_sample.html ─────────
// 22K=14010, 24K=15284, 18K=11463  (as of 2026-05-09)

test("validation passes for fixture values", () => {
  const result = validate(14010, 15284, 11463);
  assert.ok(result.r22_24 >= RATIO_22_24_MIN && result.r22_24 <= RATIO_22_24_MAX);
  assert.ok(result.r18_24 >= RATIO_18_24_MIN && result.r18_24 <= RATIO_18_24_MAX);
});

test("validation passes for a range of realistic gold prices", () => {
  // 22K at various price levels, with correct karat ratios
  for (const base24 of [7000, 10000, 14000, 18000, 22000]) {
    const r22 = Math.round(base24 * 22 / 24);
    const r18 = Math.round(base24 * 18 / 24);
    assert.doesNotThrow(() => validate(r22, base24, r18));
  }
});

test("validation fails: value below minimum (₹2000)", () => {
  assert.throws(() => validate(1500, 15284, 11463), /outside range/);
});

test("validation fails: value above maximum (₹25000)", () => {
  assert.throws(() => validate(14010, 30000, 11463), /outside range/);
});

test("validation fails: ordering violated — 22K > 24K", () => {
  assert.throws(() => validate(15284, 14010, 11463), /ordering violated/);
});

test("validation fails: ordering violated — 18K > 22K", () => {
  assert.throws(() => validate(11463, 15284, 12000), /ordering violated/);
});

test("validation fails: 22K/24K ratio too low (18K value in 22K slot)", () => {
  // 11463/15284 = 0.75 — looks like 18K purity, not 22K
  assert.throws(() => validate(11463, 15284, 9000), /22K\/24K ratio.*out of range/);
});

test("validation fails: 18K/24K ratio too high (22K value in 18K slot)", () => {
  // 14000/15284 = 0.916 — looks like 22K purity, not 18K
  assert.throws(() => validate(14010, 15284, 14000), /18K\/24K ratio.*out of range/);
});

test("validation fails: NaN values", () => {
  assert.throws(() => validate(NaN, 15284, 11463), /outside range/);
});
