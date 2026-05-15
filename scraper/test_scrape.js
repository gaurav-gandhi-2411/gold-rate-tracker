// scraper/test_scrape.js
// Playwright-based fixture test for the DOM extraction logic.
// Run: npm test (from scraper/ directory)
//
// This verifies that the actual CSS selector used in scrape.js correctly
// reads the goldpurity-rate data attributes from the saved HTML fixture,
// and that all range/ratio invariants hold (DOM canary guards).

import { chromium } from "playwright";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";
import assert from "assert/strict";
import { test } from "node:test";

const __dir = dirname(fileURLToPath(import.meta.url));
const FIXTURE = resolve(__dir, "..", "tests", "fixtures", "tanishq_sample.html");

// Mirrors the validation thresholds in scrape.js — must stay in sync.
const RANGE_MIN = 2000;
const RANGE_MAX = 25000;
const RATIO_22_24_MIN = 0.905;
const RATIO_22_24_MAX = 0.925;
const RATIO_18_24_MIN = 0.73;
const RATIO_18_24_MAX = 0.77;

test("fixture: extracts 22K/24K/18K via goldpurity-rate data-* attributes", async () => {
  const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
  const page = await browser.newPage();

  await page.goto(`file:///${FIXTURE.replace(/\\/g, "/")}`, {
    waitUntil: "domcontentloaded",
  });

  // Mirror of the selector in scrape.js
  const rates = await page.evaluate(() => {
    const el = document.querySelector("span.goldpurity-rate[data-goldrate22kt]");
    if (!el) return null;
    return {
      rate22: parseInt(el.dataset.goldrate22kt, 10),
      rate24: parseInt(el.dataset.goldrate24kt, 10),
      rate18: parseInt(el.dataset.goldrate18kt, 10),
    };
  });

  await browser.close();

  assert.ok(rates !== null, "goldpurity-rate span must exist in fixture");

  // Hand-verified from tests/fixtures/tanishq_sample.html
  assert.equal(rates.rate22, 14010, "22K");
  assert.equal(rates.rate24, 15284, "24K");
  assert.equal(rates.rate18, 11463, "18K");

  console.log(`  22K=Rs.${rates.rate22}  24K=Rs.${rates.rate24}  18K=Rs.${rates.rate18}`);
  console.log(`  22/24=${(rates.rate22/rates.rate24).toFixed(4)}  18/24=${(rates.rate18/rates.rate24).toFixed(4)}`);
});

test("canary: 22K price is within expected INR range", async () => {
  const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
  const page = await browser.newPage();
  await page.goto(`file:///${FIXTURE.replace(/\\/g, "/")}`, { waitUntil: "domcontentloaded" });
  const rate22 = await page.evaluate(() => {
    const el = document.querySelector("span.goldpurity-rate[data-goldrate22kt]");
    return el ? parseInt(el.dataset.goldrate22kt, 10) : null;
  });
  await browser.close();
  assert.ok(rate22 !== null, "selector must find element");
  assert.ok(rate22 >= RANGE_MIN && rate22 <= RANGE_MAX,
    `22K rate ${rate22} outside expected range [${RANGE_MIN}, ${RANGE_MAX}]`);
});

test("canary: 22K/24K ratio is within expected bounds", async () => {
  const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
  const page = await browser.newPage();
  await page.goto(`file:///${FIXTURE.replace(/\\/g, "/")}`, { waitUntil: "domcontentloaded" });
  const rates = await page.evaluate(() => {
    const el = document.querySelector("span.goldpurity-rate[data-goldrate22kt]");
    if (!el) return null;
    return { r22: parseInt(el.dataset.goldrate22kt, 10), r24: parseInt(el.dataset.goldrate24kt, 10) };
  });
  await browser.close();
  assert.ok(rates !== null, "selector must find element");
  const ratio = rates.r22 / rates.r24;
  assert.ok(ratio >= RATIO_22_24_MIN && ratio <= RATIO_22_24_MAX,
    `22K/24K ratio ${ratio.toFixed(4)} outside expected range [${RATIO_22_24_MIN}, ${RATIO_22_24_MAX}]`);
});

test("canary: 18K/24K ratio is within expected bounds", async () => {
  const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
  const page = await browser.newPage();
  await page.goto(`file:///${FIXTURE.replace(/\\/g, "/")}`, { waitUntil: "domcontentloaded" });
  const rates = await page.evaluate(() => {
    const el = document.querySelector("span.goldpurity-rate[data-goldrate22kt]");
    if (!el) return null;
    return { r18: parseInt(el.dataset.goldrate18kt, 10), r24: parseInt(el.dataset.goldrate24kt, 10) };
  });
  await browser.close();
  assert.ok(rates !== null, "selector must find element");
  const ratio = rates.r18 / rates.r24;
  assert.ok(ratio >= RATIO_18_24_MIN && ratio <= RATIO_18_24_MAX,
    `18K/24K ratio ${ratio.toFixed(4)} outside expected range [${RATIO_18_24_MIN}, ${RATIO_18_24_MAX}]`);
});
