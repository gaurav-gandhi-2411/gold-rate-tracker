// update-and-notify.js
// Reads scraped JSON from stdin, appends to ../data/prices.json,
// and (only if 22K dropped by ≥ ₹100 vs the last reading) sends a
// notification via ntfy.sh.
//
// Env vars required:
//   NTFY_TOPIC     – ntfy.sh topic name (set as a GitHub Actions secret)
// Optional:
//   NTFY_SERVER    – defaults to https://ntfy.sh
//   DROP_THRESHOLD – defaults to 100 (rupees)

import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const PRICES_FILE = path.join(__dirname, "..", "data", "prices.json");

const NTFY_SERVER = process.env.NTFY_SERVER || "https://ntfy.sh";
const NTFY_TOPIC = process.env.NTFY_TOPIC;
const DROP_THRESHOLD = parseInt(process.env.DROP_THRESHOLD || "100", 10);

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

async function sendNtfy({ title, message, tags, priority }) {
  if (!NTFY_TOPIC) {
    console.warn("NTFY_TOPIC not set — skipping notification");
    return;
  }
  const url = `${NTFY_SERVER}/${NTFY_TOPIC}`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Title: title,
      Tags: tags || "money_with_wings",
      Priority: String(priority || 4), // 1=min, 5=max
    },
    body: message,
  });
  if (!res.ok) {
    console.error(`ntfy push failed: ${res.status} ${await res.text()}`);
  } else {
    console.log(`Notification sent to ${NTFY_SERVER}/${NTFY_TOPIC}`);
  }
}

function fmt(n) {
  return "₹" + n.toLocaleString("en-IN");
}

async function main() {
  const stdin = await readStdin();
  if (!stdin) throw new Error("No scrape data on stdin");
  const reading = JSON.parse(stdin);

  if (!reading["22k"]) {
    throw new Error("Scrape produced no 22k value — refusing to write");
  }

  const history = await loadPrices();

  // Find the most recent entry that has a 22k reading (in case any were null).
  const lastEntry = [...history]
    .reverse()
    .find((e) => e && typeof e["22k"] === "number");

  // Append the new reading.
  history.push(reading);
  await fs.writeFile(PRICES_FILE, JSON.stringify(history, null, 2) + "\n");
  console.log(
    `Appended reading: 22k=${reading["22k"]} 24k=${reading["24k"]} 18k=${reading["18k"]}`
  );

  // Decide whether to notify.
  if (!lastEntry) {
    console.log("First reading — no previous price to compare. Skipping notification.");
    return;
  }

  const delta = reading["22k"] - lastEntry["22k"];

  if (delta < 0 && Math.abs(delta) >= DROP_THRESHOLD) {
    const drop = Math.abs(delta);
    await sendNtfy({
      title: `Gold 22K dropped ${fmt(drop)}`,
      message:
        `22K is now ${fmt(reading["22k"])} per gram\n` +
        `Previous: ${fmt(lastEntry["22k"])} (${new Date(lastEntry.timestamp).toLocaleString("en-IN")})\n` +
        `Change: -${fmt(drop)}`,
      tags: "money_with_wings,chart_with_downwards_trend",
      priority: 4,
    });
  } else if (delta < 0) {
    console.log(`22K dropped ${Math.abs(delta)} — below threshold of ${DROP_THRESHOLD}, no notification.`);
  } else if (delta > 0) {
    console.log(`22K rose by ${delta} — silent (per spec).`);
  } else {
    console.log("22K unchanged — silent.");
  }
}

main().catch((err) => {
  console.error("Update/notify failed:", err);
  process.exit(1);
});
