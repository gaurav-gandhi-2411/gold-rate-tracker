// tests/test_good_price.js
// Tests for computeGoodPriceSignals, computeBandPos90d, computeTrendResidual30d, and
// computeSupportDistance90d (the "Is today a good price?" card signals). Functions
// inlined from app.js since app.js has no module system.
//
// Run: node --test tests/test_good_price.js  (from repo root)

import { test } from "node:test";
import assert from "node:assert/strict";

// ── Inline helpers (must match app.js) ────────────────────────────────────────

import { loadApp } from "./helpers/load_app.js";

// Real app.js, not a copy: see tests/helpers/load_app.js.
const app = loadApp();
const dedupeByISTDay = app.pure("dedupeByISTDay");
const computeGoodPriceSignals = app.pure("computeGoodPriceSignals");
const computeBandPos90d = app.pure("computeBandPos90d");
const theilSenFit = app.pure("theilSenFit");
const computeTrendResidual30d = app.pure("computeTrendResidual30d");
const computeSupportDistance90d = app.pure("computeSupportDistance90d");

const fmtINR = app.pure("fmtINR");

// ── Test helpers ──────────────────────────────────────────────────────────────

// Anchor to 12:00 IST (06:30 UTC) to avoid midnight-boundary artefacts.
function makeReading(price, istDaysAgo) {
  const nowIST  = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
  const [y, m, d] = nowIST.split("-").map(Number);
  const baseDay = new Date(Date.UTC(y, m - 1, d - istDaysAgo));
  const noon    = new Date(baseDay.getTime() + 6 * 3600e3 + 30 * 60e3);
  return { timestamp: noon.toISOString(), "22k": price };
}

// Build N readings, each on a distinct IST day, spaced 1 day apart from today.
function makeReadings(prices) {
  return prices.map((price, i) => makeReading(price, prices.length - 1 - i));
}

// ── Tests: computeGoodPriceSignals ──────────────────────────────────────────────

test("returns null when fewer than 2 readings", () => {
  assert.equal(computeGoodPriceSignals([]), null);
  assert.equal(computeGoodPriceSignals([makeReading(14000, 1)]), null);
});

test("returns null when fewer than 5 distinct IST days in the 30d window", () => {
  // 4 readings on 4 distinct IST days within 30d — below the 5-day minimum
  const readings = makeReadings([14000, 14100, 14200, 14300]);
  assert.equal(computeGoodPriceSignals(readings), null);
});

test("returns signals when 5+ distinct IST days available", () => {
  const readings = makeReadings([14000, 14100, 14200, 14300, 14400]);
  const signals  = computeGoodPriceSignals(readings);
  assert.ok(signals !== null);
  assert.equal(signals.nDays30d, 5);
});

test("verdictType=cheap and correct strings when today is in bottom 20%", () => {
  // 10 readings, today's price is the lowest → percentile = 10%
  const readings = makeReadings([14500, 14520, 14540, 14560, 14580, 14600, 14620, 14640, 14660, 14000]);
  const signals  = computeGoodPriceSignals(readings);
  assert.ok(signals !== null);
  assert.ok(signals.percentile30d <= 20, `expected ≤20 but got ${signals.percentile30d}`);
  assert.equal(signals.verdictType, "cheap");
  assert.equal(signals.verdictLead, "You're paying less than usual this month");
  assert.equal(signals.supportLine1, "Cheaper than most days this month.");
});

test("verdictType=below-mid when percentile is in 21-40", () => {
  // 10 readings, today at 3rd-lowest → percentile = 30%
  const readings = makeReadings([14000, 14050, 14100, 14600, 14650, 14700, 14750, 14800, 14850, 14090]);
  const signals  = computeGoodPriceSignals(readings);
  assert.ok(signals !== null);
  assert.ok(signals.percentile30d > 20 && signals.percentile30d <= 40,
    `expected 21-40 but got ${signals.percentile30d}`);
  assert.equal(signals.verdictType, "below-mid");
  assert.equal(signals.verdictLead, "You're paying a little less than usual this month");
  assert.equal(signals.supportLine1, "A bit below the usual price this month.");
});

test("verdictType=high and correct strings when today is in top 30%", () => {
  // 10 readings, today's price is the highest → percentile = 100%
  const readings = makeReadings([14000, 14100, 14200, 14300, 14400, 14500, 14600, 14700, 14800, 15000]);
  const signals  = computeGoodPriceSignals(readings);
  assert.ok(signals !== null);
  assert.ok(signals.percentile30d >= 70, `expected ≥70 but got ${signals.percentile30d}`);
  assert.equal(signals.verdictType, "high");
  assert.equal(signals.verdictLead, "You're paying a bit more than usual this month");
  assert.equal(signals.supportLine1, "Pricier than most days this month.");
});

test("verdictType=mid when percentile is in the middle 41-70%", () => {
  // 10 readings with today in the middle → percentile ~60%
  const readings = makeReadings([14000, 14100, 14200, 14300, 14400, 14500, 14600, 14700, 14800, 14450]);
  const signals  = computeGoodPriceSignals(readings);
  assert.ok(signals !== null);
  assert.ok(signals.percentile30d > 40 && signals.percentile30d <= 70,
    `expected 41-70 but got ${signals.percentile30d}`);
  assert.equal(signals.verdictType, "mid");
  assert.equal(signals.verdictLead, "You're paying about the usual amount this month");
  assert.equal(signals.supportLine1, "Right around the middle for this month.");
});

test("supportLine2 says 'below' when today is under 30d average", () => {
  // All readings at 14500 except today (lower) → today is below average
  const readings = makeReadings([14500, 14500, 14500, 14500, 14200]);
  const signals  = computeGoodPriceSignals(readings);
  assert.ok(signals !== null);
  assert.ok(signals.vsAvg30d < 0);
  assert.ok(signals.supportLine2.includes("below the usual price for the month"));
});

test("supportLine2 says 'above' when today is over 30d average", () => {
  // All readings at 14000 except today (higher) → today is above average
  const readings = makeReadings([14000, 14000, 14000, 14000, 14500]);
  const signals  = computeGoodPriceSignals(readings);
  assert.ok(signals !== null);
  assert.ok(signals.vsAvg30d > 0);
  assert.ok(signals.supportLine2.includes("above the usual price for the month"));
});

test("supportLine2 says 'At the 30-day average' when price equals avg", () => {
  // All readings at the same price → vsAvg30d = 0
  const readings = makeReadings([14000, 14000, 14000, 14000, 14000]);
  const signals  = computeGoodPriceSignals(readings);
  assert.ok(signals !== null);
  assert.equal(signals.vsAvg30d, 0);
  assert.equal(signals.supportLine2, "Right at the usual price for the month.");
});

test("divergenceNote fires when percentile=high but vsAvg is negative", () => {
  // Right-skewed: a couple of high outliers pull the average up, but today is
  // still above most of the actual days.
  const readings = [
    makeReading(12000, 9),
    makeReading(12000, 8),
    makeReading(12000, 7),
    makeReading(12000, 6),
    makeReading(12000, 5),
    makeReading(12000, 4),
    makeReading(12000, 3),
    makeReading(20000, 2),
    makeReading(20000, 1),
    makeReading(13000, 0),  // today: above most prev days → high percentile, vsAvg < 0
  ];
  const signals = computeGoodPriceSignals(readings);
  assert.ok(signals !== null);
  assert.equal(signals.verdictType, "high", `expected high, got ${signals.verdictType} (percentile=${signals.percentile30d})`);
  assert.ok(signals.vsAvg30d < 0, `expected vsAvg < 0, got ${signals.vsAvg30d}`);
  assert.ok(signals.divergenceNote !== null, "divergenceNote should fire when HIGH + vsAvg negative");
});

test("divergenceNote is null when percentile and vsAvg agree", () => {
  // Low percentile + negative vsAvg → no divergence
  const readings = makeReadings([15000, 15000, 15000, 15000, 15000, 15000, 15000, 14000]);
  const signals  = computeGoodPriceSignals(readings);
  assert.ok(signals !== null);
  // today = 14000 → low percentile + below avg → agree
  assert.ok(signals.percentile30d <= 40);
  assert.ok(signals.vsAvg30d < 0);
  assert.equal(signals.divergenceNote, null);
});

test("readings older than 30 days are excluded from signals", () => {
  // One reading at 32 days ago (outside window), 5 within window
  const old = makeReading(9000, 32);  // very low price, should NOT affect signals
  const recent = makeReadings([14400, 14450, 14500, 14550, 14600]);
  const readings = [old, ...recent];
  const signals  = computeGoodPriceSignals(readings);
  assert.ok(signals !== null);
  assert.equal(signals.nDays30d, 5, "old reading must be excluded from the 30d window");
  // If the old reading were included, it would pull percentile and avg down significantly
  assert.ok(signals.avg30d > 12000, "avg must not be pulled down by the excluded old reading");
});

// ── computeBandPos90d (Φ11-2 revisit trigger, met 2026-07-17 at ~90 distinct days) ────
// A SUPPORTING line only — never changes computeGoodPriceSignals' verdict hierarchy.
// Inlined from app.js (see file header note); kept in sync by construction since
// this is a fresh addition, not a copy of a pre-existing drifted function.

const MIN_DAYS_90D = app.run("MIN_DAYS_90D");
const FULL_DAYS_90D = app.run("FULL_DAYS_90D");

test("computeBandPos90d returns null below MIN_DAYS_90D (60 distinct days)", () => {
  const readings = makeReadings(Array.from({ length: 59 }, (_, i) => 14000 + i));
  assert.equal(computeBandPos90d(readings), null);
});

test("computeBandPos90d returns a result at exactly MIN_DAYS_90D with a caveat", () => {
  const readings = makeReadings(Array.from({ length: 60 }, (_, i) => 14000 + i));
  const result = computeBandPos90d(readings);
  assert.ok(result !== null);
  assert.equal(result.nDays90d, 60);
  assert.ok(result.note.includes("Only 60 distinct days"),
    `expected a data-sufficiency caveat, got: ${result.note}`);
});

test("computeBandPos90d has no caveat once FULL_DAYS_90D (90) is reached", () => {
  const readings = makeReadings(Array.from({ length: 90 }, (_, i) => 14000 + i));
  const result = computeBandPos90d(readings);
  assert.ok(result !== null);
  assert.equal(result.nDays90d, 90);
  assert.ok(!result.note.includes("indicative"), `expected no caveat, got: ${result.note}`);
});

test("computeBandPos90d note says 'cheaper than' when today is in the bottom half", () => {
  // 90 days, today (last) is the lowest price → percentile ~1%
  const prices = Array.from({ length: 89 }, (_, i) => 15000 + i);
  prices.push(10000);
  const readings = makeReadings(prices);
  const result = computeBandPos90d(readings);
  assert.ok(result !== null);
  assert.ok(result.percentile90d <= 50, `expected <=50, got ${result.percentile90d}`);
  assert.ok(result.note.startsWith("Over the past 90 days: cheaper than"),
    `unexpected note: ${result.note}`);
  assert.ok(result.note.includes("90 days."), `window length not disclosed: ${result.note}`);
});

test("computeBandPos90d note says 'more expensive than' when today is in the top half", () => {
  // 90 days, today (last) is the highest price → percentile 100%
  const prices = Array.from({ length: 89 }, (_, i) => 10000 + i);
  prices.push(20000);
  const readings = makeReadings(prices);
  const result = computeBandPos90d(readings);
  assert.ok(result !== null);
  assert.ok(result.percentile90d > 50, `expected >50, got ${result.percentile90d}`);
  assert.ok(result.note.startsWith("Over the past 90 days: more expensive than"),
    `unexpected note: ${result.note}`);
});

test("computeBandPos90d window is independent of the 30-day window's percentile", () => {
  // Oldest 60 days very expensive (outside the 30d window, inside the 90d window),
  // most-recent 29 days cheap, today slightly above those recent cheap days — the two
  // windows must disagree here, proving the 90d line is a genuinely different read
  // from the 30d verdict, not a relabeled copy of it.
  const prices = [
    ...Array.from({ length: 60 }, () => 20000),
    ...Array.from({ length: 29 }, () => 14000),
    14500, // today: top of the last 30 days, but well below the 90d history
  ];
  const readings = makeReadings(prices);
  const signals30d = computeGoodPriceSignals(readings);
  const band90d = computeBandPos90d(readings);
  assert.ok(signals30d !== null && band90d !== null);
  assert.ok(signals30d.percentile30d >= 70, `30d percentile: ${signals30d.percentile30d}`);
  assert.ok(band90d.percentile90d < 70, `90d percentile: ${band90d.percentile90d}`);
});

// ── computeTrendResidual30d (audit finding, 2026-07-18) ─────────────────────────
// Fixes the percentile's blind spot: it cannot tell "cheap and still falling"
// from "cheap and stabilizing" (both read as the same low percentile). A
// SUPPORTING line only — never changes computeGoodPriceSignals' verdict hierarchy.
// Inlined from app.js (see file header note).

const MIN_DAYS_TREND = app.run("MIN_DAYS_TREND");
const FLAT_SLOPE_INR_PER_DAY = app.run("FLAT_SLOPE_INR_PER_DAY");
const CHEAP_PERCENTILE_MAX = app.run("CHEAP_PERCENTILE_MAX");
const STILL_FALLING_Z = app.run("STILL_FALLING_Z");

test("computeTrendResidual30d returns null below MIN_DAYS_TREND (10 distinct days)", () => {
  const readings = makeReadings(Array.from({ length: 9 }, (_, i) => 14000 + i * 10));
  assert.equal(computeTrendResidual30d(readings), null);
});

test("theilSenFit recovers the exact slope/intercept on a noiseless line", () => {
  const points = Array.from({ length: 15 }, (_, i) => ({ x: i, y: 14000 - 30 * i }));
  const { slope, intercept } = theilSenFit(points);
  assert.ok(Math.abs(slope - -30) < 1e-9, `expected slope -30, got ${slope}`);
  assert.ok(Math.abs(intercept - 14000) < 1e-9, `expected intercept 14000, got ${intercept}`);
});

// Small fixed jitter (deterministic, not Math.random) around the trend line —
// real price data is never perfectly linear; a noiseless line makes every
// residual exactly 0, which collapses MAD to 0 and residZ to 0 by the
// guard (robustStd > 0 ? ... : 0), masking the behavior under test.
const TREND_JITTER = [0, 12, -8, 5, -15, 9, -3, 14, -6, 2, -11, 7, -4, 10, -9, 3];

test("cheap + still falling: steep drop on top of an existing downtrend", () => {
  // Mirrors the 2026-06-10→06-25 selloff the audit found: a multi-day slide
  // (~-₹40/day) where the most recent reading drops sharply further below its
  // own trend line — the "still falling, hasn't found a floor" case.
  const prices = Array.from({ length: 16 }, (_, i) => 13590 - 40 * i + TREND_JITTER[i]);
  prices[15] -= 300; // sharp drop on the last day
  const readings = makeReadings(prices);
  const signals = computeGoodPriceSignals(readings);
  const trend = computeTrendResidual30d(readings, signals.percentile30d);
  assert.ok(trend !== null);
  assert.equal(trend.trendState, "falling");
  assert.ok(signals.percentile30d <= CHEAP_PERCENTILE_MAX, `expected cheap, percentile=${signals.percentile30d}`);
  assert.ok(trend.residZ < STILL_FALLING_Z, `expected residZ < -1, got ${trend.residZ}`);
  assert.ok(trend.note.startsWith("Cheap, but still falling"), `unexpected note: ${trend.note}`);
});

test("cheap + stabilizing: downtrend followed by a bounce back toward the line", () => {
  // Same slide as above, but the last reading ticks back UP toward/above the trend
  // line instead of continuing down — the audit's "cheap and stabilizing" case
  // (2026-06-27/07-01: percentile still low, residZ flips positive).
  const prices = Array.from({ length: 15 }, (_, i) => 13590 - 40 * i + TREND_JITTER[i]);
  prices.push(prices[14] + 50); // bounce back up on the last day
  const readings = makeReadings(prices);
  const signals = computeGoodPriceSignals(readings);
  const trend = computeTrendResidual30d(readings, signals.percentile30d);
  assert.ok(trend !== null);
  assert.ok(signals.percentile30d <= CHEAP_PERCENTILE_MAX, `expected cheap, percentile=${signals.percentile30d}`);
  assert.ok(trend.residZ >= STILL_FALLING_Z, `expected residZ >= -1, got ${trend.residZ}`);
  assert.equal(trend.note, "Cheap, and steadying — despite the recent dip, today's price is back close to its usual trend for the month.");
});

test("not cheap: mid-range percentile gets a plain trend note, no 'cheap' framing", () => {
  const prices = Array.from({ length: 15 }, (_, i) => 14000 + 40 * i); // rising, ends high-mid
  const readings = makeReadings(prices);
  const signals = computeGoodPriceSignals(readings);
  const trend = computeTrendResidual30d(readings, signals.percentile30d);
  assert.ok(trend !== null);
  assert.ok(signals.percentile30d > CHEAP_PERCENTILE_MAX, `expected not-cheap, percentile=${signals.percentile30d}`);
  assert.equal(trend.trendState, "rising");
  assert.ok(!trend.note.startsWith("Cheap"), `should not use cheap framing: ${trend.note}`);
  assert.ok(trend.note.includes("climbing"), `expected a rising-trend note, got: ${trend.note}`);
});

test("flat trend: |slope| below FLAT_SLOPE_INR_PER_DAY reads as roughly flat", () => {
  const prices = Array.from({ length: 12 }, () => 14000);
  const readings = makeReadings(prices);
  const trend = computeTrendResidual30d(readings, 50); // not cheap
  assert.ok(trend !== null);
  assert.equal(trend.trendState, "flat");
  assert.equal(trend.note, "Prices have been steady this month, close to their usual trend.");
});

test("computeTrendResidual30d never overrides computeGoodPriceSignals' verdict fields", () => {
  const prices = Array.from({ length: 16 }, (_, i) => 13590 - 40 * i);
  const readings = makeReadings(prices);
  const signals = computeGoodPriceSignals(readings);
  const before = { verdictLead: signals.verdictLead, verdictType: signals.verdictType, proofLine: signals.proofLine };
  computeTrendResidual30d(readings, signals.percentile30d); // supporting line only, no mutation
  assert.deepEqual(
    { verdictLead: signals.verdictLead, verdictType: signals.verdictType, proofLine: signals.proofLine },
    before,
  );
});

// ── computeSupportDistance90d (audit finding, 2026-07-18) ───────────────────────
// Scoped before building: correlation between residZ and this distance across the
// real prices.json history is r≈0.48 (90-day window) — moderate, not redundant.
// Fixes residZ's blind spot: it cannot tell "falling away from trend but still
// mid-range" (2026-05-28: residZ -2.71, 4.6% above the 90-day low) from "falling
// away from trend AND sitting on the actual floor" (2026-06-19: residZ -2.13,
// 0.15% above the 90-day low) — near-identical trend-residual readings, different
// situations. A SUPPORTING line only — never changes computeGoodPriceSignals'
// verdict hierarchy. Inlined from app.js (see file header note).

const MIN_DAYS_SUPPORT = app.run("MIN_DAYS_SUPPORT");
const FULL_DAYS_SUPPORT = app.run("FULL_DAYS_SUPPORT");
const NEAR_SUPPORT_PCT = app.run("NEAR_SUPPORT_PCT");

test("computeSupportDistance90d returns null below MIN_DAYS_SUPPORT (60 distinct days)", () => {
  const readings = makeReadings(Array.from({ length: 59 }, (_, i) => 14000 + i));
  assert.equal(computeSupportDistance90d(readings, 50), null);
});

test("computeSupportDistance90d returns a result at exactly MIN_DAYS_SUPPORT with a caveat", () => {
  const readings = makeReadings(Array.from({ length: 60 }, (_, i) => 14000 + i));
  const result = computeSupportDistance90d(readings, 50);
  assert.ok(result !== null);
  assert.equal(result.nDays, 60);
  assert.ok(result.note.includes("Only 60 distinct days"),
    `expected a data-sufficiency caveat, got: ${result.note}`);
});

test("computeSupportDistance90d has no caveat once FULL_DAYS_SUPPORT (90) is reached", () => {
  const readings = makeReadings(Array.from({ length: 90 }, (_, i) => 14000 + i));
  const result = computeSupportDistance90d(readings, 50);
  assert.ok(result !== null);
  assert.equal(result.nDays, 90);
  assert.ok(!result.note.includes("indicative"), `expected no caveat, got: ${result.note}`);
});

test("cheap + at support: today sets the 90-day low", () => {
  // Mirrors 2026-06-19: today is the cheapest day in the 30d window AND the
  // lowest price in the whole 90-day window → distPct = 0, "testing a floor".
  const prices = Array.from({ length: 59 }, (_, i) => 14000 + i * 5);
  prices.push(13000); // today: lowest of all 60 days
  const readings = makeReadings(prices);
  const signals = computeGoodPriceSignals(readings);
  const support = computeSupportDistance90d(readings, signals.percentile30d);
  assert.ok(support !== null);
  assert.ok(signals.percentile30d <= CHEAP_PERCENTILE_MAX, `expected cheap, percentile=${signals.percentile30d}`);
  assert.equal(support.distPct, 0);
  assert.ok(support.note.startsWith("Cheap, and sitting right at its 3-month low"),
    `unexpected note: ${support.note}`);
});

test("cheap + well above recent lows: an older dip outside the 30d window sets a lower floor", () => {
  // Mirrors 2026-05-28: today is cheap within the 30d window, but a deeper dip
  // 31-60 days ago (outside that window) set the real 90-day floor well below
  // today's price — distinct from the "at support" case above.
  const prices = [
    ...Array.from({ length: 31 }, () => 12000),                    // old floor, outside 30d window
    ...Array.from({ length: 29 }, (_, i) => 14000 + i * 5),        // recent 29 days, within 30d window
    13800,                                                          // today: cheapest of the last 30, but well above 12000
  ];
  const readings = makeReadings(prices);
  const signals = computeGoodPriceSignals(readings);
  const support = computeSupportDistance90d(readings, signals.percentile30d);
  assert.ok(support !== null);
  assert.ok(signals.percentile30d <= CHEAP_PERCENTILE_MAX, `expected cheap, percentile=${signals.percentile30d}`);
  assert.equal(support.low90d, 12000);
  assert.ok(support.distPct > NEAR_SUPPORT_PCT, `expected well above support, got distPct=${support.distPct}`);
  assert.ok(support.note.startsWith("Cheap, but still"), `unexpected note: ${support.note}`);
  assert.ok(support.note.includes("12,000"), `expected the floor price in the note: ${support.note}`);
});

test("not cheap: mid-range percentile far from the 90-day low gets the plain distance note", () => {
  const prices = Array.from({ length: 90 }, (_, i) => 13000 + i * 10); // steadily rising
  const readings = makeReadings(prices);
  const signals = computeGoodPriceSignals(readings);
  const support = computeSupportDistance90d(readings, signals.percentile30d);
  assert.ok(support !== null);
  assert.ok(signals.percentile30d > CHEAP_PERCENTILE_MAX, `expected not-cheap, percentile=${signals.percentile30d}`);
  assert.ok(support.distPct > NEAR_SUPPORT_PCT, `expected well above support, got distPct=${support.distPct}`);
  assert.ok(!support.note.startsWith("Cheap"), `should not use cheap framing: ${support.note}`);
  assert.ok(support.note.startsWith(support.distPct.toFixed(1)), `expected plain distance framing: ${support.note}`);
});

test("computeSupportDistance90d never overrides computeGoodPriceSignals' verdict fields", () => {
  const prices = Array.from({ length: 60 }, (_, i) => 14000 - 5 * i);
  const readings = makeReadings(prices);
  const signals = computeGoodPriceSignals(readings);
  const before = { verdictLead: signals.verdictLead, verdictType: signals.verdictType, proofLine: signals.proofLine };
  computeSupportDistance90d(readings, signals.percentile30d); // supporting line only, no mutation
  assert.deepEqual(
    { verdictLead: signals.verdictLead, verdictType: signals.verdictType, proofLine: signals.proofLine },
    before,
  );
});

// ── Gaps found by mutating the REAL app.js (2026-09-21) ───────────────────────────
// With the tests running against real code, two single-token mutations still passed every test:
// computeBandPos90d's `p <= current` -> `p < current`, and computeTrendResidual30d's MAD scale
// 1.4826 -> 1.0. These pin both.

test("computeBandPos90d: today counts itself -- at the 90-day high it reads 100, at the low 1/n", () => {
  // Strictly rising over 60 distinct days: today is the highest price.
  const rising = makeReadings(Array.from({ length: 60 }, (_, i) => 14000 + i * 10));
  assert.equal(computeBandPos90d(rising).percentile90d, 100);
  // Strictly falling: today is the lowest, so only today itself is <= today.
  const falling = makeReadings(Array.from({ length: 60 }, (_, i) => 15000 - i * 10));
  assert.equal(computeBandPos90d(falling).percentile90d, Math.round((1 / 60) * 100));
});

test("computeTrendResidual30d: residZ uses the normal-consistent MAD scale (z -0.80 steadying, z -1.34 still falling)", () => {
  // Deterministic noise around a flat 14000 with a known MAD; only today's deviation varies.
  const noise = [3, -2, 4, -3, 2, -4, 3, -1, 2, -3, 4, -2];
  const series = (lastDev) => makeReadings([...noise.map((n) => 14000 + n), 14000 + lastDev]);
  const steadying = computeTrendResidual30d(series(-6), 10);
  // With the 1.4826 scale z is about -0.80 (above STILL_FALLING_Z = -1); an un-scaled MAD would
  // make it about -1.19 and flip the note.
  assert.ok(steadying.residZ > -1 && steadying.residZ < -0.7, `residZ ${steadying.residZ}`);
  assert.equal(steadying.note, app.t("trendCheapSteadying"));
  const falling = computeTrendResidual30d(series(-8), 10);
  assert.ok(falling.residZ < -1, `residZ ${falling.residZ}`);
  assert.notEqual(falling.note, steadying.note);
});
