// tests/test_calculator.js
// Tests for computePurchaseCost (itemised gold purchase cost estimate).
// The function is inlined from app.js since app.js is not a module — same
// convention as tests/test_comparisons.js. Keep in sync with app.js.
//
// Run: node --test tests/test_calculator.js  (from repo root)

import assert from "assert/strict";
import { test } from "node:test";

// --- Inline the function under test (must match app.js) ---

function computePurchaseCost({ ratePerGram, grams, makingPct = 0, gstPct = 3 }) {
  const vals = [ratePerGram, grams, makingPct, gstPct];
  if (!vals.every(Number.isFinite)) return null;
  if (ratePerGram < 0 || grams < 0 || makingPct < 0 || gstPct < 0) return null;

  const goldValue = ratePerGram * grams;
  const making = goldValue * (makingPct / 100);
  const gst = (goldValue + making) * (gstPct / 100);
  const total = goldValue + making + gst;

  return {
    goldValue: Math.round(goldValue),
    making: Math.round(making),
    gst: Math.round(gst),
    total: Math.round(total),
  };
}

// --- Tests ---

test("happy path: rate × grams + making% + 3% GST on subtotal", () => {
  // gold = 13710×10 = 137100; making = 13710; gst = (137100+13710)×0.03 = 4524.3→4524;
  // total = 137100+13710+4524 = 155334
  const r = computePurchaseCost({ ratePerGram: 13710, grams: 10, makingPct: 10, gstPct: 3 });
  assert.deepEqual(r, { goldValue: 137100, making: 13710, gst: 4524, total: 155334 });
});

test("making charges default to 0 (bare metal + GST floor)", () => {
  // gold = 137100; making = 0; gst = 137100×0.03 = 4113; total = 141213
  const r = computePurchaseCost({ ratePerGram: 13710, grams: 10 });
  assert.deepEqual(r, { goldValue: 137100, making: 0, gst: 4113, total: 141213 });
});

test("GST can be zeroed (gold + making only)", () => {
  // gold = 50000; making = 6000; gst = 0; total = 56000
  const r = computePurchaseCost({ ratePerGram: 10000, grams: 5, makingPct: 12, gstPct: 0 });
  assert.deepEqual(r, { goldValue: 50000, making: 6000, gst: 0, total: 56000 });
});

test("GST applies on (gold value + making), not gold value alone", () => {
  // Distinguishes the correct formula from gst-on-gold-only.
  // gold = 100000; making = 20000; gst = (120000)×0.03 = 3600 (not 3000); total = 123600
  const r = computePurchaseCost({ ratePerGram: 10000, grams: 10, makingPct: 20, gstPct: 3 });
  assert.equal(r.gst, 3600);
  assert.equal(r.total, 123600);
});

test("zero grams yields all-zero breakdown", () => {
  const r = computePurchaseCost({ ratePerGram: 13710, grams: 0, makingPct: 10 });
  assert.deepEqual(r, { goldValue: 0, making: 0, gst: 0, total: 0 });
});

test("fractional grams supported", () => {
  // gold = 6855; making = 6855×0.08 = 548.4; gst = (6855+548.4)×0.03 = 222.102;
  // rounded fields: making 548, gst 222; total = round(7625.502) = 7626
  const r = computePurchaseCost({ ratePerGram: 13710, grams: 0.5, makingPct: 8, gstPct: 3 });
  assert.deepEqual(r, { goldValue: 6855, making: 548, gst: 222, total: 7626 });
});

test("rounds each field to the nearest rupee", () => {
  // gold = 13705; gst = 13705×0.03 = 411.15 → 411; total = 14116
  const r = computePurchaseCost({ ratePerGram: 13705, grams: 1, makingPct: 0, gstPct: 3 });
  assert.deepEqual(r, { goldValue: 13705, making: 0, gst: 411, total: 14116 });
});

test("returns null on negative inputs", () => {
  assert.equal(computePurchaseCost({ ratePerGram: 13710, grams: -1 }), null);
  assert.equal(computePurchaseCost({ ratePerGram: -1, grams: 10 }), null);
  assert.equal(computePurchaseCost({ ratePerGram: 13710, grams: 10, makingPct: -5 }), null);
  assert.equal(computePurchaseCost({ ratePerGram: 13710, grams: 10, gstPct: -3 }), null);
});

test("returns null on non-finite inputs", () => {
  assert.equal(computePurchaseCost({ ratePerGram: NaN, grams: 10 }), null);
  assert.equal(computePurchaseCost({ ratePerGram: 13710, grams: Infinity }), null);
  assert.equal(computePurchaseCost({ ratePerGram: 13710, grams: "10" }), null);
});
