// tests/test_claim_freshness.js
// G4 (2026-09-25): hide ANY accuracy/coverage claim on the live page whose measurement
// is older than CLAIM_MAX_AGE_DAYS (14). i18n.js's isMeasurementFresh() is the one
// shared, fail-closed check every claim family below now goes through (rule 98a: a
// missing/unparseable/future-dated timestamp is NOT fresh, same as a too-old one --
// never silently pass, never substitute a default/design-target number).
//
// Drives the REAL isMeasurementFresh/renderModelSignal/renderForecastVsActual from
// app.js and the REAL renderFullMethodology from how-we-know.js (tests/helpers/
// load_app.js, tests/helpers/load_how_we_know.js) -- not pasted copies.
//
// Run: node --test tests/test_claim_freshness.js  (from repo root)

import { test } from "node:test";
import assert from "node:assert/strict";

import { loadApp } from "./helpers/load_app.js";
import { loadHowWeKnow } from "./helpers/load_how_we_know.js";

const DAY_MS = 86_400_000;
const NOW = Date.parse("2026-09-25T12:00:00Z");
const isoDaysAgo = (days, from = NOW) => new Date(from - days * DAY_MS).toISOString();

// ── isMeasurementFresh (i18n.js) — the shared helper itself ─────────────────────────

test("isMeasurementFresh: a recent timestamp is fresh", () => {
  const app = loadApp({ nowMs: NOW });
  try {
    assert.equal(app.pure("isMeasurementFresh")(isoDaysAgo(1), NOW), true);
  } finally {
    app.dispose();
  }
});

test("isMeasurementFresh: exactly 14 days old is still fresh (boundary is inclusive)", () => {
  const app = loadApp({ nowMs: NOW });
  try {
    assert.equal(app.pure("isMeasurementFresh")(isoDaysAgo(14), NOW), true);
  } finally {
    app.dispose();
  }
});

test("isMeasurementFresh: 14 days + 1ms old is NOT fresh", () => {
  const app = loadApp({ nowMs: NOW });
  try {
    const ts = new Date(NOW - 14 * DAY_MS - 1).toISOString();
    assert.equal(app.pure("isMeasurementFresh")(ts, NOW), false);
  } finally {
    app.dispose();
  }
});

test("isMeasurementFresh: missing timestamp (null/undefined) is NOT fresh", () => {
  const app = loadApp({ nowMs: NOW });
  try {
    const fresh = app.pure("isMeasurementFresh");
    assert.equal(fresh(null, NOW), false);
    assert.equal(fresh(undefined, NOW), false);
  } finally {
    app.dispose();
  }
});

test("isMeasurementFresh: garbage/unparseable timestamp is NOT fresh", () => {
  const app = loadApp({ nowMs: NOW });
  try {
    assert.equal(app.pure("isMeasurementFresh")("not-a-date", NOW), false);
  } finally {
    app.dispose();
  }
});

test("isMeasurementFresh: future-dated timestamp is NOT fresh (clock skew / bad data, fail closed)", () => {
  const app = loadApp({ nowMs: NOW });
  try {
    const oneHourFromNow = new Date(NOW + 3_600_000).toISOString();
    assert.equal(app.pure("isMeasurementFresh")(oneHourFromNow, NOW), false);
  } finally {
    app.dispose();
  }
});

// ── Claim family: model-signal reliability coverage note (app.js) ───────────────────
// coverage_metrics.json's generated_at_utc previously drove NOTHING -- reliabilityCoverage
// rendered off `coverage.coverage`/`coverage.n` alone, with no age check at all.

function dailyReadings(n = 45) {
  const out = [];
  for (let i = n - 1; i >= 0; i--) {
    const ts = new Date(NOW - i * DAY_MS).toISOString();
    out.push({ timestamp: ts, "22k": 14000 + (n - i) * 5, "24k": 15200, "18k": 11400 });
  }
  return out;
}

function reliabilityNoteText(coverage) {
  const app = loadApp({ nowMs: NOW });
  try {
    const fc = { headline: { lower: 13800, upper: 14200 } };
    app.renderModelSignal(fc, dailyReadings(), null, coverage, null);
    const html = app.element("model-signal-body").innerHTML;
    const m = html.match(/<p class="outlook-reliability-note">([\s\S]*?)<\/p>/);
    if (!m) throw new Error("real renderModelSignal rendered no reliability note");
    return m[1];
  } finally {
    app.dispose();
  }
}

test("reliabilityCoverage: fresh coverage_metrics.json renders the measured fraction", () => {
  const coverage = { coverage: 0.73, n: 63, generated_at_utc: isoDaysAgo(2) };
  const text = reliabilityNoteText(coverage);
  assert.match(text, /stayed inside the range/);
  assert.doesNotMatch(text, /building a track record/);
});

test("reliabilityCoverage: coverage_metrics.json older than 14 days falls to reliabilityUnknown, not a stale fraction", () => {
  const coverage = { coverage: 0.73, n: 63, generated_at_utc: isoDaysAgo(30) };
  const text = reliabilityNoteText(coverage);
  assert.match(text, /building a track record/);
  assert.doesNotMatch(text, /stayed inside the range/);
});

test("reliabilityCoverage: missing generated_at_utc (old cached shape) falls to reliabilityUnknown", () => {
  const coverage = { coverage: 0.73, n: 63 };
  const text = reliabilityNoteText(coverage);
  assert.match(text, /building a track record/);
});

// ── Claim family: past-estimate track-record chart (app.js) ─────────────────────────
// This chart IS an accuracy claim (past estimate vs actual) sourced from the same
// weekly backtest.json run as how-we-know.html's MAE/direction figures.

const folds = Array.from({ length: 5 }, (_, i) => ({
  context_end_date: new Date(NOW - (5 - i) * DAY_MS).toISOString().slice(0, 10),
  actuals: [13500 + i],
  naive: [13490 + i],
}));

function trackRecordHidden(backtestRunAt) {
  const app = loadApp({ nowMs: NOW });
  try {
    app.Chart = class { constructor() {} destroy() {} };
    app.getComputedStyle = () => ({ getPropertyValue: () => "" });
    const section = app.element("section-track-record");
    section.hidden = false;
    app.renderForecastVsActual({ folds, backtest_run_at: backtestRunAt });
    return section.hidden;
  } finally {
    app.dispose();
  }
}

test("track-record chart: fresh backtest_run_at renders the section", () => {
  assert.equal(trackRecordHidden(isoDaysAgo(3)), false);
});

test("track-record chart: backtest_run_at older than 14 days hides the section instead of showing weeks-old folds", () => {
  assert.equal(trackRecordHidden(isoDaysAgo(21)), true);
});

test("track-record chart: backtest_run_at absent entirely (old cached shape) hides the section", () => {
  assert.equal(trackRecordHidden(undefined), true);
});

// ── Claim family: how-we-know.html's coverage-based sections ────────────────────────

function renderHwk({ fc, bt, coverage, bandCoverage } = {}) {
  const ctx = loadHowWeKnow({ nowMs: NOW });
  try {
    ctx.renderFullMethodology(fc ?? null, bt ?? null, null, coverage ?? null, bandCoverage ?? null);
    return ctx.element("how-we-know-body").innerHTML;
  } finally {
    ctx.dispose();
  }
}

const FC_WITH_RANGE = { headline: { predicted_22k: 14000, lower: 13800, upper: 14200 } };
const BT_BASE = {
  mae_5d_avg_naive: 251.12,
  mae_5d_avg_chronos: 293.88,
  wilcoxon_signed_rank_p: 0.01,
  n_folds: 243,
  dir_acc_5d_chronos: 0.5226,
};

test("how-we-know: 'Next trading day range' shows the floored fraction when coverage is fresh", () => {
  const html = renderHwk({
    fc: FC_WITH_RANGE,
    coverage: { coverage: 0.73, n: 63, generated_at_utc: isoDaysAgo(2) },
  });
  assert.match(html, /Right about 7 times out of 10 so far/);
});

test("how-we-know: 'Next trading day range' falls to a plain range (no times phrase) when coverage is stale", () => {
  const html = renderHwk({
    fc: FC_WITH_RANGE,
    coverage: { coverage: 0.73, n: 63, generated_at_utc: isoDaysAgo(30) },
  });
  assert.doesNotMatch(html, /times out of 10 so far/);
  assert.match(html, /Range: ₹13,800 – ₹14,200/);
});

test("how-we-know: 'How accurate is this?' P1 (MAE) and P3 (direction) render when backtest_run_at is fresh", () => {
  const html = renderHwk({
    fc: FC_WITH_RANGE,
    bt: { ...BT_BASE, backtest_run_at: isoDaysAgo(5) },
    coverage: { coverage: 0.73, n: 63, generated_at_utc: isoDaysAgo(5) },
  });
  assert.match(html, /We assume tomorrow's price is about the same as today's/);
  assert.match(html, /About the direction signal/);
});

test("how-we-know: 'How accurate is this?' P1 and P3 are OMITTED (not shown as a stale number) when backtest_run_at is > 14 days old", () => {
  const html = renderHwk({
    fc: FC_WITH_RANGE,
    bt: { ...BT_BASE, backtest_run_at: isoDaysAgo(21) },
    coverage: { coverage: 0.73, n: 63, generated_at_utc: isoDaysAgo(5) },
  });
  assert.doesNotMatch(html, /We assume tomorrow's price is about the same as today's/);
  assert.doesNotMatch(html, /About the direction signal/);
  // P2 (coverage, independently gated/fresh here) and the static P4 explainer
  // must still render -- a stale backtest does not blank the whole section.
  assert.match(html, /range has been right/);
  assert.match(html, /What would change this/);
});

test("how-we-know: 'How accurate is this?' P2 falls to the CoverageUnknown wording when coverage is stale but backtest is fresh", () => {
  const html = renderHwk({
    fc: FC_WITH_RANGE,
    bt: { ...BT_BASE, backtest_run_at: isoDaysAgo(5) },
    coverage: { coverage: 0.73, n: 63, generated_at_utc: isoDaysAgo(30) },
  });
  assert.match(html, /still building a track record/);
  assert.match(html, /We assume tomorrow's price is about the same as today's/); // P1 unaffected
});

// ── Claim family: Band accuracy (measured) -- how-we-know.js's own copy of
// deriveMeasuredBandCoverage, now delegating to the shared isMeasurementFresh ─────────

const FC_WITH_BAND = { nominal_coverage: 80, band_half_width: 245.7 };

test("how-we-know: Band accuracy shows the measured figure when calibration_band_coverage.json is fresh", () => {
  const html = renderHwk({
    bandCoverage: { coverage: 0.709, n: 86, generated_at_utc: isoDaysAgo(2) },
    fc: FC_WITH_BAND,
  });
  assert.match(html, /landed within about ₹246\/gram/);
});

test("how-we-know: Band accuracy falls to methBandAccuracyUnknown when calibration_band_coverage.json is stale", () => {
  const html = renderHwk({
    bandCoverage: { coverage: 0.709, n: 86, generated_at_utc: isoDaysAgo(30) },
    fc: FC_WITH_BAND,
  });
  assert.doesNotMatch(html, /landed within about ₹246\/gram/);
  assert.match(html, /No measurement in the last 14 days/);
});
