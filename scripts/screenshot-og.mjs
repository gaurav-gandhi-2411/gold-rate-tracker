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

await page.screenshot({ path: "og.png" });
await browser.close();
console.log("og.png written");
