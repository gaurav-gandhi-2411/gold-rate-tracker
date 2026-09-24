// tests/test_page_v2.js
// Tests for page_v2 (item 6, flagged OFF): the pure data/build functions behind the
// five-jobs view. DOM-mounting (renderFlaggedFeatures itself) is deliberately NOT
// exercised here -- tests/helpers/load_app.js's document stub can't meaningfully verify
// real DOM structure (see that file's own comment), and doesn't load flags.js at all, so
// every function under test here is flag-agnostic on purpose (see app.js's own comment on
// why isFeatureOn() checks live only in renderFlaggedFeatures, not in these functions).
//
// Run: node --test tests/test_page_v2.js  (from repo root)

import { test } from "node:test";
import assert from "node:assert/strict";

import { loadApp } from "./helpers/load_app.js";

const app = loadApp();

const computeWeeklyPriceComparison = app.pure("computeWeeklyPriceComparison");
const weekKeyIST                   = app.pure("weekKeyIST");
const pickRangeShadowEntry         = app.pure("pickRangeShadowEntry");
const computeMoveRangeJob          = app.pure("computeMoveRangeJob");
const computePriceNowJob           = app.pure("computePriceNowJob");
const computeConfidenceNote        = app.pure("computeConfidenceNote");
const readMarkupToday              = app.pure("readMarkupToday");
const pv2ExtractSentences          = app.pure("pv2ExtractSentences");
const pv2BuildGoodPriceCard        = app.pure("pv2BuildGoodPriceCard");
const pv2BuildMarkupCard           = app.pure("pv2BuildMarkupCard");
const pv2BuildWaitOrBuyCard        = app.pure("pv2BuildWaitOrBuyCard");
const pv2BuildEventWatchCard       = app.pure("pv2BuildEventWatchCard");
const pv2BuildCoreHtml             = app.pure("pv2BuildCoreHtml");

const MIN_WEEKS_COMPARISON = app.run("MIN_WEEKS_COMPARISON");
const RANGE_SHADOW_MAX_AGE_DAYS = app.run("RANGE_SHADOW_MAX_AGE_DAYS");

// ── Test helpers ──────────────────────────────────────────────────────────────

// One reading per distinct week, `weeksAgo` full weeks before "now", anchored at noon IST
// so week-boundary artefacts never bite (same anchoring idea as test_good_price.js's own
// makeReading).
function makeWeeklyReading(price, weeksAgo) {
  const now = new Date();
  const t = new Date(now.getTime() - weeksAgo * 7 * 86400e3);
  return { timestamp: t.toISOString(), "22k": price };
}

function makeWeeklyReadings(prices) {
  // prices[0] is the OLDEST (most weeksAgo), prices[last] is today (weeksAgo=0).
  return prices.map((price, i) => makeWeeklyReading(price, prices.length - 1 - i));
}

// ── computeWeeklyPriceComparison (F3, good_price_v2) ────────────────────────────

test("computeWeeklyPriceComparison returns null with fewer than 2 readings", () => {
  assert.equal(computeWeeklyPriceComparison([], 4), null);
  assert.equal(computeWeeklyPriceComparison([makeWeeklyReading(14000, 0)], 4), null);
});

test("computeWeeklyPriceComparison: too-little-data path below MIN_WEEKS_COMPARISON", () => {
  // Today + 1 prior week = only 1 distinct prior week, below the 3-week minimum.
  const readings = makeWeeklyReadings([14000, 14200]);
  const result = computeWeeklyPriceComparison(readings, 4);
  assert.ok(result !== null);
  assert.equal(result.n, 1);
  assert.equal(result.lower, null);
  assert.equal(result.higher, null);
  assert.equal(result.note, "We don't have enough weeks of price history yet to say.");
});

test("computeWeeklyPriceComparison: 'lower than' when today is cheaper than most weeks", () => {
  // Today (14000) cheaper than all 4 prior weeks (15000 each).
  const readings = makeWeeklyReadings([15000, 15000, 15000, 15000, 14000]);
  const result = computeWeeklyPriceComparison(readings, 4);
  assert.ok(result !== null);
  assert.equal(result.n, 4);
  assert.equal(result.lower, 4);
  assert.equal(result.higher, 0);
  assert.equal(result.note, "Lower than on 4 of the last 4 weeks.");
});

test("computeWeeklyPriceComparison: 'higher than' when today is pricier than most weeks", () => {
  const readings = makeWeeklyReadings([13000, 13000, 13000, 13000, 14000]);
  const result = computeWeeklyPriceComparison(readings, 4);
  assert.ok(result !== null);
  assert.equal(result.higher, 4);
  assert.equal(result.lower, 0);
  assert.equal(result.note, "Higher than on 4 of the last 4 weeks.");
});

test("computeWeeklyPriceComparison: 'about the same' when lower and higher counts tie", () => {
  // 2 weeks cheaper than today, 2 weeks pricier than today (n=4, exact tie).
  const readings = makeWeeklyReadings([13000, 13000, 15000, 15000, 14000]);
  const result = computeWeeklyPriceComparison(readings, 4);
  assert.ok(result !== null);
  assert.equal(result.lower, 2);
  assert.equal(result.higher, 2);
  assert.equal(result.note, "About the same as most of the last 4 weeks.");
});

test("computeWeeklyPriceComparison excludes today's own week (never compares today to itself)", () => {
  // A second reading in the SAME week as today, at a very different price -- must not
  // count as one of the "past weeks" being compared against.
  const now = new Date();
  const sameWeekEarlier = new Date(now.getTime() - 2 * 86400e3); // 2 days ago, same week as today
  const readings = [
    makeWeeklyReading(15000, 3),
    makeWeeklyReading(15000, 2),
    makeWeeklyReading(15000, 1),
    { timestamp: sameWeekEarlier.toISOString(), "22k": 9000 }, // same week as today, would skew badly if counted
    makeWeeklyReading(14000, 0),
  ];
  const result = computeWeeklyPriceComparison(readings, 4);
  assert.ok(result !== null);
  assert.equal(result.n, 3, "the same-week reading must not be counted as a distinct past week");
});

test("weekKeyIST groups two readings in the same calendar week under one key", () => {
  const monday = new Date("2026-09-21T06:00:00.000Z"); // a Monday, IST
  const friday = new Date("2026-09-25T06:00:00.000Z"); // same week, Friday
  assert.equal(weekKeyIST(monday), weekKeyIST(friday));
  const nextMonday = new Date("2026-09-28T06:00:00.000Z");
  assert.notEqual(weekKeyIST(monday), weekKeyIST(nextMonday));
});

test("weekKeyIST gives one Monday key per IST week whatever the machine timezone", async () => {
  // Regression: the first version re-parsed a locale string as the machine's local time and
  // read it back with toISOString(), so on a non-UTC machine readings near IST midnight split
  // one week into two keys ("13 of 13 weeks" then covered ~6.5 real weeks). Run the real
  // function in child processes under several TZ values.
  const { execFileSync } = await import("node:child_process");
  const { fileURLToPath } = await import("node:url");
  const path = await import("node:path");
  const appPath = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "app.js");
  const script = `
    const fs = require("fs"); const src = fs.readFileSync(${JSON.stringify(appPath)}, "utf8");
    const s = src.indexOf("function weekKeyIST(date)"); const e = src.indexOf("}\\n", s);
    const e2 = src.indexOf("}\\r\\n", s); const end = (e2 > 0 && (e < 0 || e2 < e)) ? e2 : e;
    const weekKeyIST = new Function(src.slice(s, end + 1) + "; return weekKeyIST;")();
    // every hour from IST Monday 2026-09-21 00:00 to IST Sunday 2026-09-27 23:00
    const keys = new Set();
    for (let h = 0; h < 7 * 24; h++) keys.add(weekKeyIST(new Date(Date.UTC(2026, 8, 20, 18, 30) + h * 3600e3)));
    process.stdout.write(JSON.stringify([...keys]));`;
  for (const tz of ["UTC", "Asia/Kolkata", "America/New_York", "Pacific/Kiritimati"]) {
    const out = execFileSync(process.execPath, ["-e", script], { env: { ...process.env, TZ: tz } });
    assert.deepEqual(JSON.parse(out.toString()), ["2026-09-21"], `TZ=${tz}`);
  }
});

// ── pickRangeShadowEntry / computeMoveRangeJob (job 3) ──────────────────────────

test("pickRangeShadowEntry returns null when entries array is missing/empty", () => {
  assert.equal(pickRangeShadowEntry(null, "1d"), null);
  assert.equal(pickRangeShadowEntry({ entries: [] }, "1d"), null);
});

test("pickRangeShadowEntry picks the most recent matching-horizon entry", () => {
  const nowMs = Date.parse("2026-09-24T12:00:00Z");
  const shadowLog = {
    entries: [
      { as_of: "2026-09-20", horizon: "1d", lo: 13900, hi: 14100 },
      { as_of: "2026-09-22", horizon: "1d", lo: 13950, hi: 14150 },
      { as_of: "2026-09-22", horizon: "week", lo: 13500, hi: 14500 },
    ],
  };
  const oneDay = pickRangeShadowEntry(shadowLog, "1d", nowMs);
  assert.deepEqual(oneDay, { low: 13950, high: 14150 });
  const week = pickRangeShadowEntry(shadowLog, "week", nowMs);
  assert.deepEqual(week, { low: 13500, high: 14500 });
});

test("pickRangeShadowEntry fails closed on an entry older than RANGE_SHADOW_MAX_AGE_DAYS", () => {
  const nowMs = Date.parse("2026-09-24T12:00:00Z");
  const staleDays = RANGE_SHADOW_MAX_AGE_DAYS + 5;
  const staleDate = new Date(nowMs - staleDays * 86400e3).toISOString().slice(0, 10);
  const shadowLog = { entries: [{ as_of: staleDate, horizon: "1d", lo: 100, hi: 200 }] };
  assert.equal(pickRangeShadowEntry(shadowLog, "1d", nowMs), null);
});

test("computeMoveRangeJob: next_day_range_shadow.json takes priority for the 1-day statement", () => {
  const job = computeMoveRangeJob(null, { lo: 13800, hi: 14200 }, null, null);
  assert.ok(job.oneDayNote.includes("13,800") && job.oneDayNote.includes("14,200"));
  assert.equal(job.sevenDayNote, null); // no weekly_range_shadow_log.json data at all
});

test("computeMoveRangeJob: falls back to weekly_range_shadow_log.json's own '1d' entries", () => {
  const nowMs = Date.parse("2026-09-24T12:00:00Z");
  const shadowLog = { entries: [{ as_of: "2026-09-23", horizon: "1d", lo: 13700, hi: 14300 }] };
  const job = computeMoveRangeJob(null, null, shadowLog, null, nowMs);
  assert.ok(job.oneDayNote.includes("13,700") && job.oneDayNote.includes("14,300"));
});

test("computeMoveRangeJob: falls back to the live next-trading-day band when no shadow data exists", () => {
  const fc = { headline: { lower: 13900, upper: 14100 } };
  const job = computeMoveRangeJob(fc, null, null, null);
  assert.ok(job.oneDayNote.includes("13,900") && job.oneDayNote.includes("14,100"));
  assert.equal(job.sevenDayNote, null);
});

test("computeMoveRangeJob: no data anywhere -> both notes null", () => {
  const job = computeMoveRangeJob(null, null, null, null);
  assert.equal(job.oneDayNote, null);
  assert.equal(job.sevenDayNote, null);
});

test("computeMoveRangeJob: 7-day statement only ever comes from weekly_range_shadow_log.json's 'week' entries", () => {
  const nowMs = Date.parse("2026-09-24T12:00:00Z");
  const shadowLog = { entries: [{ as_of: "2026-09-23", horizon: "week", lo: 13500, hi: 14700 }] };
  const fc = { headline: { lower: 13900, upper: 14100 } };
  const job = computeMoveRangeJob(fc, null, shadowLog, null, nowMs);
  assert.ok(job.sevenDayNote.includes("13,500") && job.sevenDayNote.includes("14,700"));
});

test("computeMoveRangeJob: odds clause appended to both notes when band coverage is fresh", () => {
  const nowMs = Date.parse("2026-09-24T12:00:00Z");
  const fc = { headline: { lower: 13900, upper: 14100 } };
  const shadowLog = { entries: [{ as_of: "2026-09-23", horizon: "week", lo: 13500, hi: 14700 }] };
  const bandCoverage = { coverage: 0.75, n: 60, generated_at_utc: new Date(nowMs - 2 * 86400e3).toISOString() };
  const job = computeMoveRangeJob(fc, null, shadowLog, bandCoverage, nowMs);
  assert.ok(job.oneDayNote.includes("times out of 10"), job.oneDayNote);
  assert.ok(job.sevenDayNote.includes("times out of 10"), job.sevenDayNote);
});

// ── computePriceNowJob (job 1) ───────────────────────────────────────────────────

test("computePriceNowJob returns null with no readings", () => {
  assert.equal(computePriceNowJob([], null), null);
});

test("computePriceNowJob: ibja_calibrated tier uses forecast.current_22k + the estimate label", () => {
  const readings = [{ timestamp: new Date().toISOString(), "22k": 14000 }];
  const forecast = { price_source: "ibja_calibrated", current_22k: 14050 };
  const job = computePriceNowJob(readings, forecast);
  assert.equal(job.price, 14050);
  assert.equal(job.sourceLabel, "An estimate, matched to IBJA and Tanishq's own numbers.");
});

test("computePriceNowJob: fusion_consensus tier uses the consensus label", () => {
  const readings = [{ timestamp: new Date().toISOString(), "22k": 14000 }];
  const forecast = { price_source: "fusion_consensus", current_22k: 13950 };
  const job = computePriceNowJob(readings, forecast);
  assert.equal(job.price, 13950);
  assert.ok(job.sourceLabel.includes("unreachable"));
});

test("computePriceNowJob: plain Tanishq tier uses the latest reading + confirmed label", () => {
  const readings = [{ timestamp: new Date().toISOString(), "22k": 14200 }];
  const job = computePriceNowJob(readings, null);
  assert.equal(job.price, 14200);
  assert.equal(job.sourceLabel, "Confirmed live at Tanishq.");
});

// ── computeConfidenceNote (job 2) ─────────────────────────────────────────────────

test("computeConfidenceNote returns null when band coverage is missing", () => {
  assert.equal(computeConfidenceNote(null), null);
});

test("computeConfidenceNote returns the floored fraction phrase, never a raw percentage", () => {
  const bandCoverage = { coverage: 0.709, n: 86, generated_at_utc: new Date().toISOString() };
  const note = computeConfidenceNote(bandCoverage);
  assert.ok(note.includes("about 7 times out of 10"), note);
  assert.ok(!note.includes("70.9"), "must never type the raw percentage");
});

// ── readMarkupToday (F1, markup_meter) ────────────────────────────────────────────

test("readMarkupToday returns null when markup_pct is missing", () => {
  assert.equal(readMarkupToday(null), null);
  assert.equal(readMarkupToday({}), null);
});

test("readMarkupToday uses an explicit category field when present", () => {
  const result = readMarkupToday({ markup_pct: 12, category: "higher" });
  assert.deepEqual(result, { pct: 12, categoryKey: "higher" });
});

test("readMarkupToday derives category from usual_low_pct/usual_high_pct bounds", () => {
  assert.equal(readMarkupToday({ markup_pct: 5, usual_low_pct: 8, usual_high_pct: 14 }).categoryKey, "lower");
  assert.equal(readMarkupToday({ markup_pct: 20, usual_low_pct: 8, usual_high_pct: 14 }).categoryKey, "higher");
  assert.equal(readMarkupToday({ markup_pct: 10, usual_low_pct: 8, usual_high_pct: 14 }).categoryKey, "usual");
});

test("readMarkupToday derives category from a percentile field as a last resort", () => {
  assert.equal(readMarkupToday({ markup_pct: 10, percentile: 10 }).categoryKey, "lower");
  assert.equal(readMarkupToday({ markup_pct: 10, percentile: 90 }).categoryKey, "higher");
  assert.equal(readMarkupToday({ markup_pct: 10, percentile: 50 }).categoryKey, "usual");
});

test("readMarkupToday returns null when no field can honestly classify it", () => {
  assert.equal(readMarkupToday({ markup_pct: 10, some_unknown_field: 1 }), null);
});

// ── pv2ExtractSentences (F2/F4 verbatim-sentence contract) ────────────────────────

test("pv2ExtractSentences reads a sentences array", () => {
  assert.deepEqual(pv2ExtractSentences({ sentences: ["a", "b"] }), ["a", "b"]);
});

test("pv2ExtractSentences falls back to a single sentence field", () => {
  assert.deepEqual(pv2ExtractSentences({ sentence: "only one" }), ["only one"]);
});

test("pv2ExtractSentences returns null for absent/malformed payloads", () => {
  assert.equal(pv2ExtractSentences(null), null);
  assert.equal(pv2ExtractSentences({}), null);
  assert.equal(pv2ExtractSentences({ sentences: [] }), null);
  assert.equal(pv2ExtractSentences({ sentences: [1, 2] }), null);
  assert.equal(pv2ExtractSentences({ sentence: "" }), null);
});

// ── pv2Build*Card descriptors (card rendering given sample data) ──────────────────

test("pv2BuildGoodPriceCard returns null when there isn't enough reading history", () => {
  assert.equal(pv2BuildGoodPriceCard([]), null);
});

test("pv2BuildGoodPriceCard returns a descriptor with both 30d and 90d comparison lines", () => {
  const readings = makeWeeklyReadings(Array.from({ length: 14 }, (_, i) => 14000 + i * 10));
  const card = pv2BuildGoodPriceCard(readings);
  assert.ok(card !== null);
  assert.equal(card.className, "pv2-good-price");
  assert.equal(card.dataFeature, "good_price_v2");
  assert.equal(card.paragraphs.length, 2);
  assert.ok(card.paragraphs[0].startsWith("Compared with the last month:"));
  assert.ok(card.paragraphs[1].startsWith("Compared with the last three months:"));
});

test("pv2BuildMarkupCard returns null when markup_today data is absent (brief's own contract)", () => {
  assert.equal(pv2BuildMarkupCard(null), null);
});

test("pv2BuildMarkupCard returns a descriptor with the exact required phrase shape", () => {
  const card = pv2BuildMarkupCard({ markup_pct: 12.4, category: "higher" });
  assert.ok(card !== null);
  assert.equal(card.className, "pv2-markup");
  assert.equal(card.dataFeature, "markup_meter");
  assert.equal(card.paragraphs.length, 1);
  assert.equal(card.paragraphs[0], "Tanishq is charging about 12% above the market rate today — higher than usual.");
});

test("pv2BuildWaitOrBuyCard returns null when data is absent", () => {
  assert.equal(pv2BuildWaitOrBuyCard(null), null);
});

test("pv2BuildWaitOrBuyCard renders the pipeline's own sentence(s) verbatim", () => {
  const card = pv2BuildWaitOrBuyCard({ sentences: ["Prices have been flat -- no strong reason to wait."] });
  assert.ok(card !== null);
  assert.equal(card.dataFeature, "wait_or_buy");
  assert.deepEqual(card.paragraphs, ["Prices have been flat -- no strong reason to wait."]);
});

test("pv2BuildEventWatchCard returns null when data is absent", () => {
  assert.equal(pv2BuildEventWatchCard(null), null);
});

test("pv2BuildEventWatchCard renders the pipeline's own sentence(s) verbatim", () => {
  const card = pv2BuildEventWatchCard({ sentences: ["No major events in the next 7 days."] });
  assert.ok(card !== null);
  assert.equal(card.dataFeature, "event_watch");
  assert.deepEqual(card.paragraphs, ["No major events in the next 7 days."]);
});

// ── pv2BuildCoreHtml (jobs 1/2/3/5 skeleton, given sample data) ────────────────────

test("pv2BuildCoreHtml renders price/confidence/move-range content when data is present", () => {
  const readings = [{ timestamp: new Date().toISOString(), "22k": 14000 }];
  const fc = { headline: { lower: 13800, upper: 14200 } };
  const bandCoverage = { coverage: 0.8, n: 50, generated_at_utc: new Date().toISOString() };
  const html = pv2BuildCoreHtml(fc, readings, bandCoverage, null, null);
  assert.ok(html.includes("14,000"), "price now job should show today's price");
  assert.ok(html.includes("13,800") && html.includes("14,200"), "move-range job should show the live band");
  assert.ok(html.includes("times out of 10"), "how-sure job should show the measured coverage phrase");
  assert.ok(html.includes('id="pv2-calculator-slot"'), "job 5's calculator slot must exist");
  assert.ok(html.includes('id="pv2-good-price-slot"'), "job 4's slot must exist");
  assert.ok(html.includes('id="pv2-extras-slot"'), "the F2/F4 extras slot must exist");
});

test("pv2BuildCoreHtml degrades to the unavailable copy when there is no data at all", () => {
  const html = pv2BuildCoreHtml(null, [], null, null, null);
  assert.ok(html.includes("We don't have a price to show right now."));
  assert.ok(html.includes("We don't have a recent enough track record"));
  assert.ok(html.includes("We don't have a short-term range to show today."));
});
