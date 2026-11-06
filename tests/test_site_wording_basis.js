// tests/test_site_wording_basis.js — GG 2026-11-06: no misleading line on the site.
//
// * The comparison cards and the week/month verdict line are measured on the IBJA-based estimate,
//   which differs from the shop price in the headline: they must say so, with the estimate's own
//   price and day (computed, never typed).
// * "Pricier than N of the last M days" counted official-rate days, not calendar days.
// * The range is the shop-price equivalent of the next official rate, not "the next price update".
//
// Run: node --test tests/test_site_wording_basis.js  (from repo root)

import assert from "assert/strict";
import { test } from "node:test";
import { loadApp } from "./helpers/load_app.js";

const app = loadApp();
const note = app.pure("comparisonBasisNote");
const t = (key, args) => app.run(`t(${JSON.stringify(key)}, ${JSON.stringify(args ?? {})})`);
const setLang = (lang) => app.run(`currentLang = ${JSON.stringify(lang)}`);

const EST = { estimate: true, rows: [{ timestamp: "2026-11-04T11:30:00.000Z", "22k": 13167 },
                                     { timestamp: "2026-11-05T11:30:00.000Z", "22k": 13400 }] };

test("basis line: names the estimate with its own newest price and day (EN, HI)", () => {
  setLang("en");
  const en = note(EST, true);
  assert.match(en, /daily estimate from India's official gold rate \(₹13,400 on 5 Nov\)/);
  assert.match(en, /not the shop price at the top/);
  // when the headline is itself the estimate, "not the shop price at the top" would be false
  assert.doesNotMatch(note(EST, false), /shop price/);
  assert.match(note(EST, false), /₹13,400 on 5 Nov/);
  setLang("hi");
  const hi = note(EST, true);
  assert.match(hi, /आधिकारिक सोने के भाव से बने हमारे रोज़ के अनुमान/);
  assert.match(hi, /₹13,400/);
  setLang("en");
});

test("basis line: absent when the cards are on the shop-price series itself", () => {
  assert.equal(note({ estimate: false, rows: EST.rows }), "");
  assert.equal(note({ estimate: true, rows: [] }), "");
  assert.equal(note(null), "");
});

test("proof line counts official-rate days in the past month (EN, HI)", () => {
  setLang("en");
  assert.equal(t("proofLinePricier", { days: 11, total: 21 }),
    "Pricier than on 11 of the 21 official-rate days in the past month.");
  assert.doesNotMatch(t("proofLineCheaper", { days: 3, total: 21 }), /of the last 21 days/);
  setLang("hi");
  assert.match(t("proofLinePricier", { days: 11, total: 21 }), /21 आधिकारिक भाव वाले दिनों में से 11/);
  setLang("en");
});

test("the range is labelled as the shop-price equivalent of the next official rate (EN, HI)", () => {
  setLang("en");
  assert.match(t("goodPriceTomorrow", { low: "13,254", high: "13,616" }),
    /^Shop-price equivalent of the next official rate: likely/);
  setLang("hi");
  assert.match(t("goodPriceTomorrow", { low: "13,254", high: "13,616" }), /^अगले आधिकारिक भाव के बराबर दुकान का भाव/);
  setLang("en");
});
