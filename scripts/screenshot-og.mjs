// Captures og.html at 1200×630 → og.png.
// Run: node scripts/screenshot-og.mjs [base_url]
// Requires: playwright + chromium (npx playwright install chromium)
import { chromium } from "playwright";

const BASE = process.argv[2] ?? "http://localhost:8080";

const browser = await chromium.launch();
const page = await browser.newPage();
await page.setViewportSize({ width: 1200, height: 630 });
await page.goto(`${BASE}/og.html`, { waitUntil: "networkidle" });

// Wait until og.html signals it has rendered all data
await page.waitForFunction(
  () => document.body.getAttribute("data-loaded") === "true",
  { timeout: 15_000 }
);

// A card that failed to render must not replace the last good og.png (2026-11-01: a broken card
// was published for 5.5 months). Exit non-zero and leave og.png as it is.
const renderError = await page.evaluate(() => document.body.getAttribute("data-error"));
if (renderError !== null) {
  await browser.close();
  console.error(`og.html failed to render, og.png NOT written: ${renderError}`);
  process.exit(1);
}

await page.screenshot({ path: "og.png" });
await browser.close();
console.log("og.png written");
