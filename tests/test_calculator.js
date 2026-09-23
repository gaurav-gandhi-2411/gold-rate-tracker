// tests/test_calculator.js
// Tests for computePurchaseCost (itemised gold purchase cost estimate).
// The function is inlined from app.js since app.js is not a module — same
// convention as tests/test_comparisons.js. Keep in sync with app.js.
//
// Run: node --test tests/test_calculator.js  (from repo root)

import assert from "assert/strict";
import { test } from "node:test";

// --- Inline the function under test (must match app.js) ---

import { loadApp } from "./helpers/load_app.js";

// Real app.js, not a copy: see tests/helpers/load_app.js.
const app = loadApp();
const computePurchaseCost = app.pure("computePurchaseCost");

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

// --- makingPerGram (flat ₹/gram making charge) ---

test("flat ₹/gram making charge: grams × makingPerGram, GST on gold + making", () => {
  // gold = 13710×10 = 137100; making = 10×400 = 4000; gst = 141100×0.03 = 4233; total = 145333
  const r = computePurchaseCost({ ratePerGram: 13710, grams: 10, makingPerGram: 400, gstPct: 3 });
  assert.deepEqual(r, { goldValue: 137100, making: 4000, gst: 4233, total: 145333 });
});

test("GST math to the paisa: rupee fields round the exact paise result", () => {
  // gold = 13711.37×7.35 = 100778.5695; making (pct 11.5) = 11589.535493 → 11590
  // gst = (100778.5695 + 11589.535493)×0.03 = 3371.043180 → 3371
  // total = 115739.148173 → 115739
  const r = computePurchaseCost({ ratePerGram: 13711.37, grams: 7.35, makingPct: 11.5, gstPct: 3 });
  assert.deepEqual(r, { goldValue: 100779, making: 11590, gst: 3371, total: 115739 });
});

test("negative or non-finite makingPerGram returns null", () => {
  assert.equal(computePurchaseCost({ ratePerGram: 13710, grams: 10, makingPerGram: -1 }), null);
  assert.equal(computePurchaseCost({ ratePerGram: 13710, grams: 10, makingPerGram: NaN }), null);
});

// --- computePurchaseCostRange ---

const computePurchaseCostRange = app.pure("computePurchaseCostRange");

test("range in % mode: low and high are the two bound estimates", () => {
  const r = computePurchaseCostRange({
    ratePerGram: 13710, grams: 10, makingMode: "pct", makingLow: 6, makingHigh: 25,
  });
  // low: making 8226, gst (137100+8226)×0.03 = 4359.78 → 4360, total 149686
  assert.deepEqual(r.low, { goldValue: 137100, making: 8226, gst: 4360, total: 149686 });
  // high: making 34275, gst 171375×0.03 = 5141.25 → 5141, total 176516
  assert.deepEqual(r.high, { goldValue: 137100, making: 34275, gst: 5141, total: 176516 });
});

test("range in ₹/gram mode uses flat making charges", () => {
  const r = computePurchaseCostRange({
    ratePerGram: 13710, grams: 10, makingMode: "perGram", makingLow: 200, makingHigh: 600,
  });
  assert.equal(r.low.making, 2000);
  assert.equal(r.high.making, 6000);
  assert.equal(r.low.total, Math.round((137100 + 2000) * 1.03));
  assert.equal(r.high.total, Math.round((137100 + 6000) * 1.03));
});

test("reversed bounds are sorted, not rejected", () => {
  const a = computePurchaseCostRange({ ratePerGram: 10000, grams: 5, makingMode: "pct", makingLow: 20, makingHigh: 8 });
  const b = computePurchaseCostRange({ ratePerGram: 10000, grams: 5, makingMode: "pct", makingLow: 8, makingHigh: 20 });
  assert.deepEqual(a, b);
});

test("equal bounds collapse to a single figure (low === high)", () => {
  const r = computePurchaseCostRange({ ratePerGram: 10000, grams: 5, makingMode: "pct", makingLow: 0, makingHigh: 0 });
  assert.deepEqual(r.low, r.high);
  assert.deepEqual(r.low, { goldValue: 50000, making: 0, gst: 1500, total: 51500 });
});

test("range returns null on an unknown mode, blank bound, or negative bound", () => {
  const base = { ratePerGram: 13710, grams: 10, makingLow: 6, makingHigh: 25 };
  assert.equal(computePurchaseCostRange({ ...base, makingMode: "flat" }), null);
  assert.equal(computePurchaseCostRange({ ...base, makingMode: "pct", makingLow: NaN }), null);
  assert.equal(computePurchaseCostRange({ ...base, makingMode: "pct", makingHigh: -1 }), null);
  assert.equal(computePurchaseCostRange({ ...base, makingMode: "perGram", grams: -2 }), null);
});

test("default making range matches its cited source (6–25% or ₹200–600/g)", () => {
  const d = app.run("MAKING_CHARGE_DEFAULTS");
  assert.deepEqual(JSON.parse(JSON.stringify(d)), { pct: { low: 6, high: 25 }, perGram: { low: 200, high: 600 } });
});

