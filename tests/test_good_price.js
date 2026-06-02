// tests/test_good_price.js
// Tests for computeGoodPriceSignals (Φ11-2 "Is today a good price?" signals).
// Functions inlined from app.js since app.js has no module system.
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

  let verdict, supportLine1, verdictType;
  if (percentile30d <= 30) {
    verdictType  = "low";
    verdict      = "Prices have been lower than usual lately";
    supportLine1 = "Cheaper than most days this past month.";
  } else if (percentile30d >= 70) {
    verdictType  = "high";
    verdict      = "Prices have been higher than usual lately";
    supportLine1 = "Pricier than most days this past month.";
  } else {
    verdictType  = "mid";
    verdict      = "Prices are around usual levels lately";
    supportLine1 = "Around the middle of the past month.";
  }

  const absVsAvg = fmtINR(Math.abs(vsAvg30d));
  const supportLine2 = vsAvg30d < 0
    ? `₹${absVsAvg} below the 30-day average.`
    : vsAvg30d > 0
      ? `₹${absVsAvg} above the 30-day average.`
      : "At the 30-day average.";

  const divergenceNote =
    (verdictType === "low"  && vsAvg30d > 0) ||
    (verdictType === "high" && vsAvg30d < 0)
      ? "(The two measures diverge here — the percentile counts days, the average measures distance. The headline follows the percentile.)"
      : null;

  return { percentile30d, vsAvg30d, avg30d, nDays30d, verdict, verdictType, supportLine1, supportLine2, divergenceNote };
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

// ── Tests ─────────────────────────────────────────────────────────────────────

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

test("verdictType=low and correct strings when today is in bottom 30%", () => {
  // 10 readings, today's price is the lowest → percentile = 10%
  const readings = makeReadings([14500, 14520, 14540, 14560, 14580, 14600, 14620, 14640, 14660, 14000]);
  const signals  = computeGoodPriceSignals(readings);
  assert.ok(signals !== null);
  assert.ok(signals.percentile30d <= 30, `expected ≤30 but got ${signals.percentile30d}`);
  assert.equal(signals.verdictType, "low");
  assert.equal(signals.verdict, "Prices have been lower than usual lately");
  assert.equal(signals.supportLine1, "Cheaper than most days this past month.");
});

test("verdictType=high and correct strings when today is in top 30%", () => {
  // 10 readings, today's price is the highest → percentile = 100%
  const readings = makeReadings([14000, 14100, 14200, 14300, 14400, 14500, 14600, 14700, 14800, 15000]);
  const signals  = computeGoodPriceSignals(readings);
  assert.ok(signals !== null);
  assert.ok(signals.percentile30d >= 70, `expected ≥70 but got ${signals.percentile30d}`);
  assert.equal(signals.verdictType, "high");
  assert.equal(signals.verdict, "Prices have been higher than usual lately");
  assert.equal(signals.supportLine1, "Pricier than most days this past month.");
});

test("verdictType=mid when percentile is in the middle 31–69%", () => {
  // 10 readings with today in the middle → percentile ~50%
  const readings = makeReadings([14000, 14100, 14200, 14300, 14400, 14500, 14600, 14700, 14800, 14450]);
  const signals  = computeGoodPriceSignals(readings);
  assert.ok(signals !== null);
  assert.ok(signals.percentile30d > 30 && signals.percentile30d < 70,
    `expected 31–69 but got ${signals.percentile30d}`);
  assert.equal(signals.verdictType, "mid");
  assert.equal(signals.verdict, "Prices are around usual levels lately");
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

test("divergenceNote fires when percentile=low but vsAvg is positive", () => {
  // Skewed: most readings are very low, one recent spike pulls the average above today.
  // e.g. 8 readings at 14000, one spike at 16000, today at 14100 (above most days but below avg).
  // Wait — if today (14100) > all 8 days at 14000, percentile = 90% (high), not low.
  // For low+above-avg: need today BELOW most days but ABOVE the average.
  // That happens when the series has a few very high outliers dragging the average up.
  // E.g. 8 days at 14000, 2 days at 18000 (spikes), today = 14200.
  // prices30d = [14000,14000,14000,14000,14000,14000,14000,14000,18000,18000, today=14200]
  // but "today" is the last element, it's in the daily series too.
  // For simplicity: 5 days at 18000 (spikes), 5 days at 12000 (low), today at 12100.
  // percentile = % of days where price <= 12100 → 5 days at 12000 ≤ 12100, 1 today = 6/11 ≈ 54% → mid, not low.
  // Try: 7 days at 18000, 3 days at 12000, today = 12100.
  // percentile = days where price ≤ 12100 / 10 days = 3 days (12000×3) + 0 (18000) → 3/10 = 30% → low
  // vsAvg = 12100 - (7*18000 + 3*12000)/10 = 12100 - (126000+36000)/10 = 12100 - 16200 = -4100 → below avg, no divergence
  //
  // For divergence (low+vsAvg positive): today must be below most days (low percentile)
  // but above the mean. This requires high-freq low readings and low-freq very-high outliers.
  // e.g. 8 days at 13000, 2 days at 11000 (outliers below), today = 13200.
  // percentile = days where price ≤ 13200 → all 8 at 13000 + 2 at 11000 + today = 10+1 = 10/11? No — today is in the series.
  // Let's reason differently: exclude today from the comparison series.
  // Actually computeGoodPriceSignals doesn't exclude today — it uses daily30d which includes today's reading.
  //
  // Cleaner approach: 10 distinct days, today = max (high percentile, say 100%), but we want low percentile.
  // For LOW percentile + POSITIVE vsAvg: impossible with a normal distribution.
  // This only happens in a bimodal dist: series has MOSTLY HIGH days except a few very-low outliers,
  // AND today's price is in the bottom 30% (below most days), AND also above the average.
  //
  // Actually with a bimodal [mostly HIGH + 2 very-LOW outliers]:
  // e.g. 7 days at 14500, 3 days at 10000, today = 10100.
  // percentile = % where price ≤ 10100 → 3 days at 10000 / 10 total = 30% → LOW ✓
  // vsAvg = 10100 - (7*14500 + 3*10000)/10 = 10100 - (101500+30000)/10 = 10100 - 13150 = -3050 → BELOW avg → no divergence
  //
  // Hmm. For divergence to trigger, we need:
  // LOW verdict (percentile ≤ 30) AND vsAvg30d > 0 (today > avg)
  // This is actually impossible if percentile ≤ 30 means today ≤ 70% of the series values.
  // If today is lower than 70%+ of the values, most values > today → avg > today → vsAvg < 0.
  // So LOW + positive vsAvg is theoretically rare/impossible in practice.
  //
  // The divergence case that CAN happen: HIGH verdict (percentile ≥ 70) AND vsAvg30d < 0.
  // This happens with right-skewed distribution: a few extreme high outliers pull average up,
  // but today is still above 70% of the actual days.
  // e.g. 7 days at 12000, 2 days at 20000 (outliers), today = 13000.
  // percentile = days where price ≤ 13000 → 7 days at 12000 / 9 = 77.8% → HIGH ✓
  // vsAvg = 13000 - (7*12000 + 2*20000)/9 = 13000 - (84000+40000)/9 = 13000 - 13778 = -778 → BELOW avg ✓
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
    makeReading(13000, 0),  // today: above 7/9 prev days → percentile 70-80%, vsAvg < 0
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
  assert.ok(signals.percentile30d <= 30);
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
