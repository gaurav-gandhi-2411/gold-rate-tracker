// update-and-notify.js
// Reads scraped JSON from stdin and appends it to ../data/prices.json.
//
// NOTE: this script no longer sends any ntfy notification. The legacy
// "22K dropped >= Rs.100" drop-alert that fired here was RETIRED — it fired
// immediately on every scrape with no quiet-hours awareness and double-fired
// with the Python T3 trigger (>= Rs.150) for the same move. Intraday price-move
// alerts are now owned exclusively by ml/notifications.py T3, which is gated,
// quiet-hours-aware, deduped, and rate-capped. This script's sole job is to
// append the reading; downstream `python -m ml.notifications` does the alerting.
//
// (The script name is kept for the check-price.yml pipeline contract.)

import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PRICES_FILE = path.join(__dirname, "..", "data", "prices.json");

async function readStdin() {
  let data = "";
  for await (const chunk of process.stdin) data += chunk;
  return data.trim();
}

async function loadPrices() {
  try {
    const raw = await fs.readFile(PRICES_FILE, "utf8");
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (err) {
    if (err.code === "ENOENT") return [];
    throw err;
  }
}

async function main() {
  const stdin = await readStdin();
  if (!stdin) throw new Error("No scrape data on stdin");
  const reading = JSON.parse(stdin);

  if (!reading["22k"]) {
    throw new Error("Scrape produced no 22k value — refusing to write");
  }

  const history = await loadPrices();
  history.push(reading);
  await fs.writeFile(PRICES_FILE, JSON.stringify(history, null, 2) + "\n");
  console.log(
    `Appended reading: 22k=${reading["22k"]} 24k=${reading["24k"]} 18k=${reading["18k"]}`
  );
}

// Run main() only when invoked as a script (`node update-and-notify.js`).
if (process.argv[1] === __filename) {
  main().catch((err) => {
    console.error("Update failed:", err);
    process.exit(1);
  });
}
