#!/usr/bin/env node
// scripts/carry_forward_readings.js -- keep every Tanishq reading when a bot branch is rebuilt.
//
// Why: during the 2026-10-14/15 sync outage each self-hosted scrape force-pushed its own single reading
// over the previous, still unmerged, reading on bot/tanishq-selfhosted-sync, so three readings were
// destroyed. scripts/bot_branch_push.sh now rebuilds the branch as
//     origin/master  +  every reading found in this run's commit  +  every reading found on the existing
//                       bot branch that master does not have yet
// and this script does the data part of that. It runs on the Windows self-hosted host, where Python is
// not assumed, so it is plain Node (Node is set up by the workflow for the scraper anyway).
//
// Usage (inside a git work tree whose HEAD is detached at --base, files clean):
//   node scripts/carry_forward_readings.js --base origin/master --source <ref> [--source <ref> ...]
// The FIRST --source is this run's commit: its health file wins (it is the newest job-health record).
//
// Rules:
//   * data/prices.json (JSON array): add every source entry whose timestamp master lacks, in timestamp
//     order, never twice;
//   * data/tanishq_scrape_outcomes.jsonl: add every source line master lacks, in timestamp order, never
//     twice; the file's existing bytes (it has mixed LF/CRLF line ends) are not rewritten;
//   * data/tanishq_selfhosted_health.json: taken from the first source;
//   * a source that changed any OTHER file, or a file that does not parse, makes this exit non-zero (2 or
//     3) and write nothing, so the caller keeps the branch as it is and pages instead of dropping data.
// Exit codes: 0 ok, 2 unreadable data, 3 unexpected files in a source, 64 bad usage.
"use strict";

const { execFileSync } = require("node:child_process");
const fs = require("node:fs");

const PRICES = "data/prices.json";
const OUTCOMES = "data/tanishq_scrape_outcomes.jsonl";
const HEALTH = "data/tanishq_selfhosted_health.json";
const HANDLED = new Set([PRICES, OUTCOMES, HEALTH]);

function die(code, msg) {
  process.stderr.write(`carry_forward_readings: ${msg}\n`);
  process.exit(code);
}

function git(args) {
  return execFileSync("git", args, { encoding: "buffer", maxBuffer: 1 << 28, stdio: ["ignore", "pipe", "pipe"] });
}

function show(ref, path) {
  try {
    return git(["show", `${ref}:${path}`]).toString("utf8");
  } catch (e) {
    return null;
  }
}

function parseArgs(argv) {
  const out = { base: null, sources: [] };
  for (let i = 0; i < argv.length; i += 2) {
    if (argv[i] === "--base") out.base = argv[i + 1];
    else if (argv[i] === "--source") out.sources.push(argv[i + 1]);
    else die(64, `unknown argument ${argv[i]}`);
    if (argv[i + 1] === undefined) die(64, `${argv[i]} needs a value`);
  }
  if (!out.base || out.sources.length === 0) die(64, "need --base and at least one --source");
  return out;
}

function eolOf(text) {
  const crlf = (text.match(/\r\n/g) || []).length;
  const lf = (text.match(/\n/g) || []).length - crlf;
  return crlf > lf ? "\r\n" : "\n";
}

function parsePrices(text, label) {
  let arr;
  try {
    arr = JSON.parse(text);
  } catch (e) {
    die(2, `${label} ${PRICES} is not valid JSON`);
  }
  if (!Array.isArray(arr) || arr.some((r) => !r || typeof r.timestamp !== "string")) {
    die(2, `${label} ${PRICES} is not an array of timestamped readings`);
  }
  return arr;
}

function lineParts(raw) {
  return raw.match(/[^\n]*\n|[^\n]+$/g) || [];
}

function lineTs(line) {
  try {
    const t = JSON.parse(line).timestamp;
    return typeof t === "string" ? t : null;
  } catch (e) {
    return null;
  }
}

function checkSourceFiles(base, ref) {
  let mb;
  try {
    mb = git(["merge-base", base, ref]).toString("utf8").trim();
  } catch (e) {
    die(3, `${ref} shares no history with ${base}`);
  }
  const changed = git(["diff", "--name-only", mb, ref]).toString("utf8").split("\n").filter(Boolean);
  const extra = changed.filter((p) => !HANDLED.has(p));
  if (extra.length) die(3, `${ref} changed files this script does not carry: ${extra.join(", ")}`);
}

const { base, sources } = parseArgs(process.argv.slice(2));

for (const s of sources) checkSourceFiles(base, s);

// ---- prices.json
const basePricesText = show(base, PRICES);
if (basePricesText === null) die(2, `${PRICES} missing on ${base}`);
const basePrices = parsePrices(basePricesText, base);
const have = new Set(basePrices.map((r) => r.timestamp));
const merged = basePrices.slice();
const carriedPrices = [];
for (const s of sources) {
  const text = show(s, PRICES);
  if (text === null) continue;
  for (const r of parsePrices(text, s)) {
    if (have.has(r.timestamp)) continue;
    have.add(r.timestamp);
    let i = merged.length;
    while (i > 0 && merged[i - 1].timestamp > r.timestamp) i--;
    merged.splice(i, 0, r);
    carriedPrices.push(r.timestamp);
  }
}
const pricesEol = eolOf(basePricesText);
const pricesOut = JSON.stringify(merged, null, 2) + "\n";

// The rewrite must be a no-op when there is nothing to carry: if re-serialising master's own file does not
// reproduce it byte for byte, refuse rather than reformat the whole file.
const roundTrip = JSON.stringify(basePrices, null, 2) + "\n";
if (roundTrip.replace(/\n/g, pricesEol) !== basePricesText) {
  die(2, `${PRICES} on ${base} does not round-trip through JSON.stringify(.., null, 2); not rewriting it`);
}

// ---- outcomes log (insert lines, never rewrite the others)
const baseOutcomesRaw = show(base, OUTCOMES) || "";
const parts = lineParts(baseOutcomesRaw);
const seenLines = new Set(parts.map((p) => p.replace(/\r?\n$/, "")));
const outEol = eolOf(baseOutcomesRaw);
const carriedOutcomes = [];
for (const s of sources) {
  const raw = show(s, OUTCOMES);
  if (raw === null) continue;
  for (const p of lineParts(raw)) {
    const line = p.replace(/\r?\n$/, "");
    if (!line.trim() || seenLines.has(line)) continue;
    const ts = lineTs(line);
    if (ts === null) die(2, `${s} ${OUTCOMES} has a line that is not a timestamped record`);
    seenLines.add(line);
    let at = parts.length;
    while (at > 0) {
      const prev = lineTs(parts[at - 1].replace(/\r?\n$/, ""));
      if (prev === null || prev > ts) at--;
      else break;
    }
    if (at === parts.length && parts.length && !/\n$/.test(parts[parts.length - 1])) {
      parts[parts.length - 1] += outEol;
    }
    parts.splice(at, 0, line + outEol);
    carriedOutcomes.push(ts);
  }
}

// ---- health (newest run's record)
const healthText = show(sources[0], HEALTH);

// ---- write (only after everything parsed)
if (carriedPrices.length) fs.writeFileSync(PRICES, Buffer.from(pricesOut.replace(/\n/g, pricesEol), "utf8"));
if (carriedOutcomes.length) fs.writeFileSync(OUTCOMES, Buffer.from(parts.join(""), "utf8"));
if (healthText !== null) fs.writeFileSync(HEALTH, Buffer.from(healthText, "utf8"));

process.stdout.write(
  `carried readings: prices=${carriedPrices.length} [${carriedPrices.join(", ")}] outcomes=${carriedOutcomes.length}\n`,
);
