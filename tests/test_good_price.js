// tests/test_good_price.js
// Tests for computeGoodPriceSignals, computeBandPos90d, and computeTrendResidual30d
// (the "Is today a good price?" card signals). Functions inlined from app.js since
// app.js has no module system.
//
// Run: node --test tests/test_good_price.js  (from repo root)

import { test } from "node:test";
import assert from "node:assert/strict";

// ── Inline helpers (must match app.js) ────────────────────────────────────────

function dedupeByISTDay(readings) {
  const byDay = new Map();
  for (const r of readings) {
    const key = new Date(r.timestamp).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
    byDay.set(key, r);
  }
  return [...byDay.values()];
}

const fmtINR = (n) =>
  typeof n === "number"
    ? n.toLocaleString("en-IN", { maximumFractionDigits: 0 })
    : "—";

function computeGoodPriceSignals(readings) {
  if (!readings || readings.length < 2) return null;

  const now     = Date.now();
  const current = readings[readings.length - 1]["22k"];

  const within30d = readings.filter(
    r => now - new Date(r.timestamp).getTime() <= 30 * 86400e3,
  );
  const daily30d  = dedupeByISTDay(within30d);
  const nDays30d  = daily30d.length;
  if (nDays30d < 5) return null;

  const prices30d     = daily30d.map(r => r["22k"]);
  const percentile30d = Math.round(
    prices30d.filter(p => p <= current).length / nDays30d * 100,
  );

  const avg30d   = Math.round(prices30d.reduce((s, p) => s + p, 0) / nDays30d);
  const vsAvg30d = current - avg30d;

  // Four-tier verdict (Φ18A)
  let verdictLead, verdictType, supportLine1;
  if (percentile30d <= 20) {
    verdictType  = "cheap";
    verdictLead  = "Today's price is low for the past month";
    supportLine1 = "Cheaper than most days this past month.";
  } else if (percentile30d <= 40) {
    verdictType  = "below-mid";
    verdictLead  = "Today's price is on the lower side this month";
    supportLine1 = "Below average for the past month.";
  } else if (percentile30d <= 70) {
    verdictType  = "mid";
    verdictLead  = "Today's price is around usual levels lately";
    supportLine1 = "Around the middle of the past month.";
  } else {
    verdictType  = "high";
    verdictLead  = "Today's price is on the higher side this month";
    supportLine1 = "Pricier than most days this past month.";
  }

  // Unified proof line — consistent frame (cheaper-than / more-expensive-than)
  const proofLine = percentile30d <= 50
    ? `Cheaper than ${100 - percentile30d}% of the ${nDays30d} days in the past month.`
    : `More expensive than ${percentile30d}% of the ${nDays30d} days in the past month.`;

  // Data-sufficiency degrade note (norm #5) — shown when < 30 distinct days
  const dataSuffNote = nDays30d < 30
    ? `Only ${nDays30d} distinct days in the window — treat as indicative.`
    : null;

  const absVsAvg = fmtINR(Math.abs(vsAvg30d));
  const supportLine2 = vsAvg30d < 0
    ? `₹${absVsAvg} below the 30-day average.`
    : vsAvg30d > 0
      ? `₹${absVsAvg} above the 30-day average.`
      : "At the 30-day average.";

  // Divergence: percentile says cheap/low but vs-avg says above average, or vice versa.
  const divergenceNote =
    (percentile30d <= 40 && vsAvg30d > 0) ||
    (percentile30d >= 70 && vsAvg30d < 0)
      ? "(The two measures diverge here — the percentile counts days, the average measures distance. The headline follows the percentile.)"
      : null;

  return { percentile30d, vsAvg30d, avg30d, nDays30d, verdictLead, verdictType, proofLine, dataSuffNote, supportLine1, supportLine2, divergenceNote };
}

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
  assert.equal(signals.verdictLead, "Today's price is low for the past month");
  assert.equal(signals.supportLine1, "Cheaper than most days this past month.");
});

test("verdictType=below-mid when percentile is in 21-40", () => {
  // 10 readings, today at 3rd-lowest → percentile = 30%
  const readings = makeReadings([14000, 14050, 14100, 14600, 14650, 14700, 14750, 14800, 14850, 14090]);
  const signals  = computeGoodPriceSignals(readings);
  assert.ok(signals !== null);
  assert.ok(signals.percentile30d > 20 && signals.percentile30d <= 40,
    `expected 21-40 but got ${signals.percentile30d}`);
  assert.equal(signals.verdictType, "below-mid");
  assert.equal(signals.verdictLead, "Today's price is on the lower side this month");
  assert.equal(signals.supportLine1, "Below average for the past month.");
});

test("verdictType=high and correct strings when today is in top 30%", () => {
  // 10 readings, today's price is the highest → percentile = 100%
  const readings = makeReadings([14000, 14100, 14200, 14300, 14400, 14500, 14600, 14700, 14800, 15000]);
  const signals  = computeGoodPriceSignals(readings);
  assert.ok(signals !== null);
  assert.ok(signals.percentile30d >= 70, `expected ≥70 but got ${signals.percentile30d}`);
  assert.equal(signals.verdictType, "high");
  assert.equal(signals.verdictLead, "Today's price is on the higher side this month");
  assert.equal(signals.supportLine1, "Pricier than most days this past month.");
});

test("verdictType=mid when percentile is in the middle 41-70%", () => {
  // 10 readings with today in the middle → percentile ~60%
  const readings = makeReadings([14000, 14100, 14200, 14300, 14400, 14500, 14600, 14700, 14800, 14450]);
  const signals  = computeGoodPriceSignals(readings);
  assert.ok(signals !== null);
  assert.ok(signals.percentile30d > 40 && signals.percentile30d <= 70,
    `expected 41-70 but got ${signals.percentile30d}`);
  assert.equal(signals.verdictType, "mid");
  assert.equal(signals.verdictLead, "Today's price is around usual levels lately");
  assert.equal(signals.supportLine1, "Around the middle of the past month.");
});

test("supportLine2 says 'below' when today is under 30d average", () => {
  // All readings at 14500 except today (lower) → today is below average
  const readings = makeReadings([14500, 14500, 14500, 14500, 14200]);
  const signals  = computeGoodPriceSignals(readings);
  assert.ok(signals !== null);
  assert.ok(signals.vsAvg30d < 0);
  assert.ok(signals.supportLine2.includes("below the 30-day average"));
});

test("supportLine2 says 'above' when today is over 30d average", () => {
  // All readings at 14000 except today (higher) → today is above average
  const readings = makeReadings([14000, 14000, 14000, 14000, 14500]);
  const signals  = computeGoodPriceSignals(readings);
  assert.ok(signals !== null);
  assert.ok(signals.vsAvg30d > 0);
  assert.ok(signals.supportLine2.includes("above the 30-day average"));
});

test("supportLine2 says 'At the 30-day average' when price equals avg", () => {
  // All readings at the same price → vsAvg30d = 0
  const readings = makeReadings([14000, 14000, 14000, 14000, 14000]);
  const signals  = computeGoodPriceSignals(readings);
  assert.ok(signals !== null);
  assert.equal(signals.vsAvg30d, 0);
  assert.equal(signals.supportLine2, "At the 30-day average.");
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

const MIN_DAYS_90D = 60;
const FULL_DAYS_90D = 90;

function computeBandPos90d(readings) {
  if (!readings || readings.length < 2) return null;

  const now     = Date.now();
  const current = readings[readings.length - 1]["22k"];

  const within90d = readings.filter(
    r => now - new Date(r.timestamp).getTime() <= 90 * 86400e3,
  );
  const daily90d = dedupeByISTDay(within90d);
  const nDays90d = daily90d.length;
  if (nDays90d < MIN_DAYS_90D) return null;

  const prices90d      = daily90d.map(r => r["22k"]);
  const percentile90d  = Math.round(
    prices90d.filter(p => p <= current).length / nDays90d * 100,
  );

  let note = percentile90d <= 50
    ? `Over the past 90 days: cheaper than ${100 - percentile90d}% of the ${nDays90d} days.`
    : `Over the past 90 days: more expensive than ${percentile90d}% of the ${nDays90d} days.`;
  if (nDays90d < FULL_DAYS_90D) {
    note += ` (Only ${nDays90d} distinct days in this window so far — treat as indicative.)`;
  }

  return { percentile90d, nDays90d, note };
}

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

const MIN_DAYS_TREND = 10;
const FLAT_SLOPE_INR_PER_DAY = 5;
const CHEAP_PERCENTILE_MAX = 40;
const STILL_FALLING_Z = -1;

function theilSenFit(points) {
  const slopes = [];
  for (let i = 0; i < points.length; i++) {
    for (let j = i + 1; j < points.length; j++) {
      const dx = points[j].x - points[i].x;
      if (dx !== 0) slopes.push((points[j].y - points[i].y) / dx);
    }
  }
  slopes.sort((a, b) => a - b);
  const midS = Math.floor(slopes.length / 2);
  const slope = slopes.length % 2 !== 0
    ? slopes[midS]
    : (slopes[midS - 1] + slopes[midS]) / 2;

  const intercepts = points.map(p => p.y - slope * p.x).sort((a, b) => a - b);
  const midI = Math.floor(intercepts.length / 2);
  const intercept = intercepts.length % 2 !== 0
    ? intercepts[midI]
    : (intercepts[midI - 1] + intercepts[midI]) / 2;

  return { slope, intercept };
}

function computeTrendResidual30d(readings, percentile30d) {
  if (!readings || readings.length < 2) return null;

  const now = Date.now();
  const within30d = readings.filter(
    r => now - new Date(r.timestamp).getTime() <= 30 * 86400e3,
  );
  const daily30d = dedupeByISTDay(within30d);
  const nDays = daily30d.length;
  if (nDays < MIN_DAYS_TREND) return null;

  const points = daily30d.map((r, i) => ({ x: i, y: r["22k"] }));
  const { slope, intercept } = theilSenFit(points);

  const absResiduals = points
    .map(p => Math.abs(p.y - (slope * p.x + intercept)))
    .sort((a, b) => a - b);
  const midR = Math.floor(absResiduals.length / 2);
  const mad = absResiduals.length % 2 !== 0
    ? absResiduals[midR]
    : (absResiduals[midR - 1] + absResiduals[midR]) / 2;
  const robustStd = 1.4826 * mad;

  const todayIdx = points.length - 1;
  const trendValue = slope * todayIdx + intercept;
  const residual = points[todayIdx].y - trendValue;
  const residZ = robustStd > 0 ? residual / robustStd : 0;

  let trendState;
  if (slope <= -FLAT_SLOPE_INR_PER_DAY) trendState = "falling";
  else if (slope >= FLAT_SLOPE_INR_PER_DAY) trendState = "rising";
  else trendState = "flat";

  const isCheap = typeof percentile30d === "number" && percentile30d <= CHEAP_PERCENTILE_MAX;
  const slopeAbs = fmtINR(Math.round(Math.abs(slope)));

  let note;
  if (isCheap && residZ < STILL_FALLING_Z) {
    note = `Cheap, but still falling — today is well below its own recent trend line (about ₹${slopeAbs}/day downhill over the month).`;
  } else if (isCheap) {
    note = "Cheap, and stabilizing — despite the recent dip, today's price is back near (or above) its own recent trend line.";
  } else if (trendState === "falling") {
    note = `Prices have been sliding about ₹${slopeAbs}/day over the past month.`;
  } else if (trendState === "rising") {
    note = `Prices have been climbing about ₹${slopeAbs}/day over the past month.`;
  } else {
    note = "Prices have been roughly flat over the past month, close to their own recent trend.";
  }

  return { slope, residual, residZ, trendState, nDays, note };
}

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
  assert.equal(trend.note, "Cheap, and stabilizing — despite the recent dip, today's price is back near (or above) its own recent trend line.");
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
  assert.equal(trend.note, "Prices have been roughly flat over the past month, close to their own recent trend.");
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
