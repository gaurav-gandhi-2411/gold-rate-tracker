// backfill-history.js
// One-shot script: scrapes the Tanishq gold-rate history table (~30 days) and
// merges new entries into data/prices.json. Idempotent — running twice is safe.
//
// Usage (from repo root or scraper/):
//   node scraper/backfill-history.js
//   node scraper/backfill-history.js --dry-run   # prints what would be written, no file changes
//
// Timestamps: each historical date is recorded at noon IST = 06:30 UTC.
// This is a defensible canonical point that does not collide with any live-scrape
// timestamps (cron fires at 00/06/12/18 UTC, actual runtime is a few minutes after).
// Backfilled entries are identified in the source field for future auditability.

import { chromium } from "playwright";
import { readFileSync, writeFileSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PRICES_PATH = resolve(__dirname, "../data/prices.json");
const PAGE_URL = "https://www.tanishq.co.in/gold-rate.html?lang=en_IN";
const SOURCE_TAG = `${PAGE_URL} (history backfill)`;
const DRY_RUN = process.argv.includes("--dry-run");

// Same launch args and user-agent as scrape.js
const BROWSER_ARGS = ["--no-sandbox", "--disable-blink-features=AutomationControlled"];
const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

/**
 * Parse "DD-MM-YYYY" → ISO timestamp at noon IST (06:30 UTC).
 * Noon IST = 12:00 IST = 06:30 UTC (IST is UTC+5:30).
 */
function dateToTimestamp(ddmmyyyy) {
  const [dd, mm, yyyy] = ddmmyyyy.split("-");
  return `${yyyy}-${mm}-${dd}T06:30:00.000Z`;
}

/**
 * Extract date string "DD-MM-YYYY" from a timestamp "YYYY-MM-DDT..." so we can
 * look up existing entries by date.
 */
function timestampToDate(ts) {
  // ts is "YYYY-MM-DDThh:mm:ss.sssZ"
  return ts.slice(0, 10); // "YYYY-MM-DD"
}

/**
 * Scrape all history rows from the Tanishq page.
 * Returns array of { date: "DD-MM-YYYY", rate22: n, rate24: n, rate18: n }.
 */
async function scrapeHistory() {
  const browser = await chromium.launch({
    headless: true,
    args: BROWSER_ARGS,
  });
  const context = await browser.newContext({
    userAgent: USER_AGENT,
    viewport: { width: 1280, height: 800 },
    locale: "en-IN",
  });
  const page = await context.newPage();

  try {
    await page.goto(PAGE_URL, { waitUntil: "networkidle", timeout: 60000 });

    // Wait for the history table to be populated
    await page.waitForSelector("table.goldrate-history-table tr td", { timeout: 30000 });
    await page.waitForTimeout(1000);

    const rows = await page.evaluate(() => {
      const spans = document.querySelectorAll("span.goldpurity-rate[data-goldrate22kt]");
      const tableRows = document.querySelectorAll("table.goldrate-history-table tr");

      // Extract dates from table rows (skip header)
      const dates = [];
      tableRows.forEach(tr => {
        const cells = tr.querySelectorAll("td");
        if (cells.length >= 1) {
          const d = cells[0].innerText?.trim();
          if (/^\d{2}-\d{2}-\d{4}$/.test(d)) dates.push(d);
        }
      });

      // Extract rate data from spans — one span per history row, in same order
      const rates = [];
      spans.forEach(el => {
        rates.push({
          rate22: parseInt(el.dataset.goldrate22kt, 10),
          rate24: parseInt(el.dataset.goldrate24kt, 10),
          rate18: parseInt(el.dataset.goldrate18kt, 10),
        });
      });

      // Zip dates + rates
      const result = [];
      const len = Math.min(dates.length, rates.length);
      for (let i = 0; i < len; i++) {
        result.push({ date: dates[i], ...rates[i] });
      }
      return result;
    });

    return rows;
  } finally {
    await browser.close();
  }
}

async function main() {
  process.stderr.write(`Reading existing prices: ${PRICES_PATH}\n`);
  const existing = JSON.parse(readFileSync(PRICES_PATH, "utf8"));

  // Build set of dates already covered (keyed as "YYYY-MM-DD")
  const coveredDates = new Set(existing.map(r => timestampToDate(r.timestamp)));
  process.stderr.write(`Existing entries: ${existing.length} (${coveredDates.size} unique dates)\n`);

  process.stderr.write("Loading Tanishq history page...\n");
  const historyRows = await scrapeHistory();
  process.stderr.write(`History rows scraped: ${historyRows.length}\n`);

  // Build new entries for dates not yet in prices.json
  const newEntries = [];
  const skipped = [];

  for (const row of historyRows) {
    const timestamp = dateToTimestamp(row.date);
    const dateKey = timestampToDate(timestamp);

    if (coveredDates.has(dateKey)) {
      skipped.push(row.date);
      continue;
    }

    // Validate rates are plausible before inserting
    if (
      !Number.isFinite(row.rate22) || row.rate22 < 2000 || row.rate22 > 25000 ||
      !Number.isFinite(row.rate24) || row.rate24 < 2000 || row.rate24 > 25000 ||
      !Number.isFinite(row.rate18) || row.rate18 < 2000 || row.rate18 > 25000 ||
      !(row.rate18 < row.rate22 && row.rate22 < row.rate24)
    ) {
      process.stderr.write(`SKIPPED (invalid rates): ${row.date} 22K=${row.rate22} 24K=${row.rate24} 18K=${row.rate18}\n`);
      continue;
    }

    newEntries.push({
      timestamp,
      "22k": row.rate22,
      "24k": row.rate24,
      "18k": row.rate18,
      source: SOURCE_TAG,
    });
  }

  process.stderr.write(`New entries to insert: ${newEntries.length}\n`);
  process.stderr.write(`Skipped (already covered): ${skipped.length} — ${skipped.join(", ")}\n`);

  if (newEntries.length === 0) {
    process.stderr.write("Nothing to merge — prices.json is already up to date.\n");
    return;
  }

  // Merge and sort by timestamp ascending
  const merged = [...existing, ...newEntries].sort(
    (a, b) => new Date(a.timestamp) - new Date(b.timestamp)
  );

  if (DRY_RUN) {
    process.stderr.write("\n--- DRY RUN: would write the following new entries ---\n");
    newEntries.forEach(e => process.stderr.write(JSON.stringify(e) + "\n"));
    process.stderr.write(`--- Final row count would be: ${merged.length} ---\n`);
    return;
  }

  writeFileSync(PRICES_PATH, JSON.stringify(merged, null, 2) + "\n");

  process.stderr.write(`\nDone. prices.json updated:\n`);
  process.stderr.write(`  Before: ${existing.length} rows, ${coveredDates.size} unique dates\n`);
  process.stderr.write(`  After:  ${merged.length} rows, ${coveredDates.size + newEntries.length} unique dates\n`);

  // Print new entries to stdout as JSON for auditing
  console.log(JSON.stringify(newEntries, null, 2));
}

main().catch(err => {
  console.error("Backfill failed:", err.message);
  process.exit(1);
});
