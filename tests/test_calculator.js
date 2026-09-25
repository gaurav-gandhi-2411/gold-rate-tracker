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

// --- computePurchaseCostRange (jewellery-type presets + custom) ---
// Worked example throughout: rate ₹7,000/g, 10 g -> gold value ₹70,000.

const computePurchaseCostRange = app.pure("computePurchaseCostRange");
const RATE = 7000;
const GRAMS = 10;

test("preset 'coins' (3-8%, typical 5%): typical/low/high totals", () => {
  const r = computePurchaseCostRange({ ratePerGram: RATE, grams: GRAMS, presetId: "coins" });
  // typical 5%: making 3500, gst (70000+3500)×0.03 = 2205, total 75705
  assert.deepEqual(r.typical, { goldValue: 70000, making: 3500, gst: 2205, total: 75705 });
  // low 3%: making 2100, gst 72100×0.03 = 2163, total 74263
  assert.deepEqual(r.low, { goldValue: 70000, making: 2100, gst: 2163, total: 74263 });
  // high 8%: making 5600, gst 75600×0.03 = 2268, total 77868
  assert.deepEqual(r.high, { goldValue: 70000, making: 5600, gst: 2268, total: 77868 });
});

test("preset 'plain' (8-12%, typical 10%): typical/low/high totals", () => {
  const r = computePurchaseCostRange({ ratePerGram: RATE, grams: GRAMS, presetId: "plain" });
  assert.deepEqual(r.typical, { goldValue: 70000, making: 7000, gst: 2310, total: 79310 });
  assert.deepEqual(r.low, { goldValue: 70000, making: 5600, gst: 2268, total: 77868 });
  assert.deepEqual(r.high, { goldValue: 70000, making: 8400, gst: 2352, total: 80752 });
});

test("preset 'intricate' (15-25%, typical 20%): typical/low/high totals", () => {
  const r = computePurchaseCostRange({ ratePerGram: RATE, grams: GRAMS, presetId: "intricate" });
  assert.deepEqual(r.typical, { goldValue: 70000, making: 14000, gst: 2520, total: 86520 });
  assert.deepEqual(r.low, { goldValue: 70000, making: 10500, gst: 2415, total: 82915 });
  assert.deepEqual(r.high, { goldValue: 70000, making: 17500, gst: 2625, total: 90125 });
});

test("custom % of gold value: typical === low === high (no invented range)", () => {
  const r = computePurchaseCostRange({
    ratePerGram: RATE, grams: GRAMS, presetId: "custom", customValue: 10, customUnit: "pct",
  });
  const expected = { goldValue: 70000, making: 7000, gst: 2310, total: 79310 };
  assert.deepEqual(r.typical, expected);
  assert.deepEqual(r.low, expected);
  assert.deepEqual(r.high, expected);
});

test("custom ₹/gram: flat making charge, typical === low === high", () => {
  const r = computePurchaseCostRange({
    ratePerGram: RATE, grams: GRAMS, presetId: "custom", customValue: 400, customUnit: "perGram",
  });
  // making 10×400 = 4000; gst (74000)×0.03 = 2220; total 76220
  const expected = { goldValue: 70000, making: 4000, gst: 2220, total: 76220 };
  assert.deepEqual(r.typical, expected);
  assert.deepEqual(r.low, expected);
  assert.deepEqual(r.high, expected);
});

test("returns null on an unknown preset id", () => {
  assert.equal(computePurchaseCostRange({ ratePerGram: RATE, grams: GRAMS, presetId: "bridal" }), null);
});

test("custom returns null on a blank, negative, or non-finite value", () => {
  const base = { ratePerGram: RATE, grams: GRAMS, presetId: "custom", customUnit: "pct" };
  assert.equal(computePurchaseCostRange({ ...base, customValue: NaN }), null);
  assert.equal(computePurchaseCostRange({ ...base, customValue: -1 }), null);
  assert.equal(computePurchaseCostRange({ ...base, customValue: undefined }), null);
});

test("custom returns null on an unknown unit", () => {
  assert.equal(computePurchaseCostRange({
    ratePerGram: RATE, grams: GRAMS, presetId: "custom", customValue: 10, customUnit: "flat",
  }), null);
});

test("preset catalogue matches the product owner's chosen buyer-guide ranges (2026-09-23)", () => {
  const presets = app.run("MAKING_CHARGE_PRESETS");
  assert.deepEqual(JSON.parse(JSON.stringify(presets)), [
    { id: "coins", labelKey: "calcPresetCoins", low: 3, high: 8, typical: 5 },
    { id: "plain", labelKey: "calcPresetPlain", low: 8, high: 12, typical: 10 },
    { id: "intricate", labelKey: "calcPresetIntricate", low: 15, high: 25, typical: 20 },
    { id: "custom", labelKey: "calcPresetCustom" },
  ]);
});

// --- renderCalculator states (real app.js against the stub DOM) ---
// The stub DOM's querySelector() (tests/helpers/load_app.js) always returns a fresh,
// unchecked element rather than honouring which radio a test "checks" -- calcSelectedPresetId()
// and calcCustomUnit() therefore always resolve to their defaults ("plain"/"pct") here, the
// same pre-existing limitation the old calcMakingMode()-based tests had (only "pct" mode was
// ever exercised through render). Preset switching and the custom-input error state are
// covered by the pure computePurchaseCostRange tests above and verified live via the
// Playwright screenshots (reports/screenshots/p5-estimator/).

const NOW = Date.parse("2026-09-23T06:00:00Z");
function renderWith({ grams = "10", readingAgeH = 1, forecast = null } = {}) {
  const a = loadApp({ nowMs: NOW });
  const doc = a.run("document");
  doc.getElementById("calc-grams").value = grams;
  const ts = new Date(NOW - readingAgeH * 3_600_000).toISOString();
  a.pure("renderCalculator")([{ timestamp: ts, "22k": 13710, "24k": 14957, "18k": 11218 }], forecast);
  return doc.getElementById("calc-results").innerHTML;
}

test("render: default preset (plain bangles & rings) carries the disclaimer, a typical total, and its range", () => {
  const html = renderWith();
  assert.match(html, /Estimate — stores vary/);
  // plain, typical 10%: gold 137100, making 13710, gst (150810)×0.03=4524.3→4524, total 155334
  assert.match(html, /₹1,55,334/);
  // low 8%: making 10968, gst (148068)×0.03=4442.04→4442, total 152510
  // high 12%: making 16452, gst (153552)×0.03=4606.56→4607, total 158159
  assert.match(html, /₹1,52,510 – ₹1,58,159/);
});

test("render: the making-charge range is its own line under the row, not packed into the value cell", () => {
  const html = renderWith();
  // The row itself carries only the typical amount -- no range text inside its own <span>.
  assert.match(html, /<span>Making charge \(10%\)<\/span><span>₹13,710<\/span>/);
  // low 8%: making 10968; high 12%: making 16452 -- as a sibling <p>, not inline in the row.
  assert.match(html, /<\/div><p class="calc-result-range">Range ₹10,968 – ₹16,452<\/p>/);
});

test("render: 'Estimate — stores vary' is its own prominent line, separate from the HUID/stones fine print", () => {
  const html = renderWith();
  assert.match(html, /<p class="calc-estimate-label">Estimate — stores vary\.<\/p>/);
  // The fine-print disclaimer no longer opens with the estimate phrase -- it's a distinct line now.
  assert.match(html, /<p class="calc-disclaimer">Your jeweller's bill will differ/);
});

test("render: zero grams shows the empty state, not a ₹0 total", () => {
  assert.match(renderWith({ grams: "0" }), /Enter a quantity/);
});

test("render: a confirmed price older than STALE_THRESHOLD_H says how old it is", () => {
  assert.match(renderWith({ readingAgeH: 20 }), /last confirmed price/);
  assert.doesNotMatch(renderWith({ readingAgeH: 1 }), /last confirmed price/);
});

test("render: rate-used line names the Tanishq store rate outside the estimate tier", () => {
  // ADR 059 P2: dated with the reading's own IST date (reading is 1h before NOW).
  assert.match(renderWith(), /Rate used: 22K ₹13,710\/g — Tanishq's listed rate on 23 Sept/);
});

test("render: rate-used line names the IBJA-based estimate in the ibja_calibrated tier", () => {
  const forecast = { price_source: "ibja_calibrated", current_22k: 13800 };
  assert.match(renderWith({ forecast }), /Rate used: 22K ₹13,800\/g — IBJA-based estimate/);
});

test("render: rate-used line names the market-consensus estimate in the fusion_consensus tier", () => {
  const forecast = { price_source: "fusion_consensus", current_22k: 13750 };
  assert.match(renderWith({ forecast }), /Rate used: 22K ₹13,750\/g — market-consensus estimate/);
});
