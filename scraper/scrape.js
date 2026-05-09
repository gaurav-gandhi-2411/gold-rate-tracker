// scrape.js
// Loads the Tanishq gold rate page in a real headless browser (gets past the 403
// that simple HTTP fetches hit), then extracts 22K, 24K, and 18K rates per gram.
//
// Output: prints a single JSON line to stdout, e.g.
//   {"timestamp":"2026-05-09T08:00:00.000Z","22k":6450,"24k":7036,"18k":5278}
//
// On failure it exits non-zero so the GitHub Action fails loudly instead of
// silently writing bad data.

import { chromium } from "playwright";

const URL = "https://www.tanishq.co.in/gold-rate.html?lang=en_IN";

// Helper: pull the first integer (with optional commas) out of a string.
function extractRupees(text) {
  if (!text) return null;
  // Match Indian number formats: 6,450 or 6450 or ₹ 6,450.00
  const match = text.replace(/,/g, "").match(/(\d{4,6})(?:\.\d+)?/);
  if (!match) return null;
  const n = parseInt(match[1], 10);
  // Sanity check: gold per gram is realistically between ₹2000 and ₹20000
  if (n < 2000 || n > 20000) return null;
  return n;
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
    // Tanishq's rate widget loads via JS. Give it generous time.
    await page.waitForTimeout(5000);

    // Strategy: extract everything, then parse with regex.
    // The page lays out rates as labeled chunks like "22 KT" / "₹ 6,450"
    // somewhere near each other. We grab the full rendered text and parse.
    const bodyText = await page.evaluate(() => document.body.innerText);

    // Look for patterns like "22 KT" or "22KT" or "22 Karat" followed by a price.
    // We allow up to 200 characters of slop between the label and the price
    // because the page might render them in adjacent cards.
    const findRate = (karat) => {
      const pattern = new RegExp(
        `${karat}\\s*(?:KT|K|Karat|Carat|कैरट)[\\s\\S]{0,200}?(?:₹|Rs\\.?|INR)?\\s*(\\d{1,2}[,]?\\d{3})`,
        "i"
      );
      const m = bodyText.match(pattern);
      return m ? extractRupees(m[1]) : null;
    };

    const rate22 = findRate(22);
    const rate24 = findRate(24);
    const rate18 = findRate(18);

    if (!rate22) {
      // Dump page text to stderr so the workflow log shows what we got.
      console.error("=== PAGE TEXT (first 2000 chars) ===");
      console.error(bodyText.slice(0, 2000));
      console.error("=== END PAGE TEXT ===");
      throw new Error("Could not parse 22K rate from Tanishq page");
    }

    const result = {
      timestamp: new Date().toISOString(),
      "22k": rate22,
      "24k": rate24,
      "18k": rate18,
      source: URL,
    };

    // Single JSON line for easy capture in shell.
    console.log(JSON.stringify(result));
  } finally {
    await browser.close();
  }
}

scrape().catch((err) => {
  console.error("Scrape failed:", err.message);
  process.exit(1);
});
