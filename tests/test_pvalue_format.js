// tests/test_pvalue_format.js
// Tests for formatPValue() (i18n.js) -- the shared p-value display helper.
//
// Why this exists: how-we-know.js used to render `bt.wilcoxon_signed_rank_p.toFixed(4)`
// directly, which prints "0.0000" for any p-value below 0.00005 -- reading as "exactly
// zero" (a misreading; a p-value is never literally 0). formatPValue() is the one shared
// place this "rounds to zero at the shown precision" case is handled, for every p-value
// display site (currently just how-we-know.js's Wilcoxon p; scripts/inject_metrics.py's
// pval2/pval4 marker formats mirror the same rule on the Python side for README/site
// generation, kept in sync by policy the same way frac10/fractionOutOf10Phrase are).
//
// Run: node --test tests/test_pvalue_format.js  (from repo root)

import { test } from "node:test";
import assert from "node:assert/strict";

import { loadApp } from "./helpers/load_app.js";

const app = loadApp();
const formatPValue = app.pure("formatPValue");

test("ordinary p-value formats as '=' at the requested precision", () => {
  assert.deepEqual(formatPValue(0.0043, 4), { op: "=", text: "0.0043" });
});

test("p-value that rounds to zero at 4 decimals renders 'p < 0.0001', never '0.0000'", () => {
  // Exactly the bug that shipped: bt.wilcoxon_signed_rank_p.toFixed(4) on a very
  // small p-value used to print "0.0000".
  const result = formatPValue(0.00002, 4);
  assert.deepEqual(result, { op: "&lt;", text: "0.0001" });
  assert.doesNotMatch(result.text, /^0\.0000$/);
});

test("value just below the rounding threshold (< 0.00005) is '<'; just above is '='", () => {
  assert.equal(formatPValue(0.00004, 4).op, "&lt;");
  assert.equal(formatPValue(0.00006, 4).op, "=");
  assert.equal(formatPValue(0.00006, 4).text, "0.0001");
});

test("respects a custom decimals argument (README's pval2-equivalent 2-decimal case)", () => {
  assert.deepEqual(formatPValue(0.42, 2), { op: "=", text: "0.42" });
  assert.deepEqual(formatPValue(0.003, 2), { op: "&lt;", text: "0.01" });
});

test("defaults to 4 decimal places when no decimals argument is given", () => {
  assert.deepEqual(formatPValue(0.1), { op: "=", text: "0.1000" });
});

test("non-finite or non-numeric input returns null, matching the null p_value fallback", () => {
  assert.equal(formatPValue(null, 4), null);
  assert.equal(formatPValue(undefined, 4), null);
  assert.equal(formatPValue(NaN, 4), null);
  assert.equal(formatPValue("0.01", 4), null);
});

test("a p-value of exactly zero (degenerate but possible input) renders '< 0.0001', not '= 0.0000'", () => {
  assert.deepEqual(formatPValue(0, 4), { op: "&lt;", text: "0.0001" });
});
