// Captures og.html at 1200×630 → og.png, and writes the sidecar data/og-card.json: the values the card
// drew (price, its text, label, kind, as-of time, verdict, the card's whole text) plus when it was built.
// scripts/live_share_card.py checks the published sidecar every hour against the published data.
// Run: node scripts/screenshot-og.mjs [base_url]
// Requires: playwright + chromium (npx playwright install chromium)
import { writeFileSync } from "node:fs";
import { chromium } from "playwright";

const SIDECAR = "data/og-card.json";

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

// og.png and its sidecar are written together or not at all, so the sidecar always describes the
// published card.
const drawn = await page.evaluate(() => window.__ogCard ?? null);
if (drawn === null || typeof drawn !== "object") {
  await browser.close();
  console.error("og.html rendered but exposed no window.__ogCard, og.png NOT written");
  process.exit(1);
}

await page.screenshot({ path: "og.png" });
await browser.close();
const sidecar = { ...drawn, built_at: new Date().toISOString() };
writeFileSync(SIDECAR, JSON.stringify(sidecar, null, 2) + "\n", "utf8");
console.log("og.png written");
console.log(`${SIDECAR} written: ${JSON.stringify(sidecar)}`);
