// scrape.js
// Loads the Tanishq gold rate page in a real headless browser and extracts
// 22K, 24K, and 18K rates per gram using DOM data attributes.
//
// Output: prints a single JSON line to stdout, e.g.
//   {"timestamp":"2026-05-09T08:00:00.000Z","22k":14010,"24k":15284,"18k":11463}
//
// On failure it exits non-zero so the GitHub Action fails loudly instead of
// silently writing bad data.

import { chromium } from "playwright";

const URL = "https://www.tanishq.co.in/gold-rate.html?lang=en_IN";

// Validation thresholds (per gram, INR)
const RANGE_MIN = 2000;
const RANGE_MAX = 25000;
const RATIO_22_24_MIN = 0.905;  // theoretical 91.67%, allow ±1%
const RATIO_22_24_MAX = 0.925;
const RATIO_18_24_MIN = 0.73;   // theoretical 75%, allow ±2%
const RATIO_18_24_MAX = 0.77;

/**
 * Extract gold rates from the Tanishq page using the `data-goldrate*`
 * attributes on the `span#goldpurity-rate` element in the history table.
 * These attributes carry all three karats independently — no regex or
 * arithmetic needed.
 *
 * @returns {{ rate22: number, rate24: number, rate18: number }}
 */
async function extractRates(page) {
  // Wait for the rate widget to appear (JS-rendered after page load)
  await page.waitForSelector("span.goldpurity-rate[data-goldrate22kt]", {
    timeout: 30000,
  });

  const rates = await page.evaluate(() => {
    const el = document.querySelector("span.goldpurity-rate[data-goldrate22kt]");
    if (!el) return null;
    return {
      rate22: parseInt(el.dataset.goldrate22kt, 10),
      rate24: parseInt(el.dataset.goldrate24kt, 10),
      rate18: parseInt(el.dataset.goldrate18kt, 10),
    };
  });

  if (!rates) throw new Error("goldpurity-rate element not found after waiting");
  return rates;
}

/**
 * Validate extracted rates. Throws with a descriptive message if any check
 * fails so the workflow fails visibly rather than silently writing bad data.
 */
function validate(rate22, rate24, rate18) {
  const fail = (msg) => {
    throw new Error(
      `Rate validation failed: ${msg}\n` +
        `  Extracted: 22K=₹${rate22}, 24K=₹${rate24}, 18K=₹${rate18}`
    );
  };

  // Range check
  for (const [label, val] of [["22K", rate22], ["24K", rate24], ["18K", rate18]]) {
    if (!Number.isFinite(val) || val < RANGE_MIN || val > RANGE_MAX) {
      fail(`${label}=₹${val} is outside ₹${RANGE_MIN}–₹${RANGE_MAX}`);
    }
  }

  // Strict ordering
  if (!(rate18 < rate22 && rate22 < rate24)) {
    fail(`expected 18K < 22K < 24K but got ${rate18} < ${rate22} < ${rate24}`);
  }

  // Karat ratio checks
  const r22_24 = rate22 / rate24;
  const r18_24 = rate18 / rate24;

  if (r22_24 < RATIO_22_24_MIN || r22_24 > RATIO_22_24_MAX) {
    fail(
      `22K/24K ratio ${r22_24.toFixed(4)} outside [${RATIO_22_24_MIN}, ${RATIO_22_24_MAX}]`
    );
  }
  if (r18_24 < RATIO_18_24_MIN || r18_24 > RATIO_18_24_MAX) {
    fail(
      `18K/24K ratio ${r18_24.toFixed(4)} outside [${RATIO_18_24_MIN}, ${RATIO_18_24_MAX}]`
    );
  }

  return { r22_24, r18_24 };
}

async function scrape() {
  const browser = await chromium.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
  });

  const context = await browser.newContext({
    userAgent:
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    viewport: { width: 1280, height: 800 },
    locale: "en-IN",
  });

  const page = await context.newPage();

  try {
    await page.goto(URL, { waitUntil: "domcontentloaded", timeout: 60000 });

    const { rate22, rate24, rate18 } = await extractRates(page);
    const { r22_24, r18_24 } = validate(rate22, rate24, rate18);

    const result = {
      timestamp: new Date().toISOString(),
      "22k": rate22,
      "24k": rate24,
      "18k": rate18,
      source: URL,
    };

    process.stderr.write(
      `22K: ₹${rate22.toLocaleString("en-IN")}\n` +
      `24K: ₹${rate24.toLocaleString("en-IN")}\n` +
      `18K: ₹${rate18.toLocaleString("en-IN")}\n` +
      `ratios: 22/24=${r22_24.toFixed(3)} ✓, 18/24=${r18_24.toFixed(3)} ✓\n`
    );

    console.log(JSON.stringify(result));
  } catch (err) {
    // Dump page text to help diagnose future breakages
    try {
      const bodyText = await page.evaluate(() => document.body.innerText);
      process.stderr.write("\n=== PAGE TEXT (first 3000 chars) ===\n");
      process.stderr.write(bodyText.slice(0, 3000));
      process.stderr.write("\n=== END PAGE TEXT ===\n");
    } catch (_) {}
    throw err;
  } finally {
    await browser.close();
  }
}

scrape().catch((err) => {
  console.error("Scrape failed:", err.message);
  process.exit(1);
});
