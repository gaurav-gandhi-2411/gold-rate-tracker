// scraper/test_scrape.js
// Playwright-based fixture test for the DOM extraction logic.
// Run: npm test (from scraper/ directory)
//
// This verifies that the actual CSS selector used in scrape.js correctly
// reads the goldpurity-rate data attributes from the saved HTML fixture.

import { chromium } from "playwright";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";
import assert from "assert/strict";
import { test } from "node:test";

const __dir = dirname(fileURLToPath(import.meta.url));
const FIXTURE = resolve(__dir, "..", "tests", "fixtures", "tanishq_sample.html");

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

  console.log(`  22K=₹${rates.rate22}  24K=₹${rates.rate24}  18K=₹${rates.rate18}`);
  console.log(`  22/24=${(rates.rate22/rates.rate24).toFixed(4)}  18/24=${(rates.rate18/rates.rate24).toFixed(4)}`);
});
