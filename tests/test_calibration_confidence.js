// tests/test_calibration_confidence.js
// AE1 (production audit, 2026-09-10): the calibration-band confidence clause
// ("...lands within about ₹X/gram of this estimate about Y% of the time")
// previously rendered ml.calibration.NOMINAL_COVERAGE_PCT (a hardcoded design
// target, always 80) as if it were a measured accuracy claim -- the client had
// no fetch path to data/calibration_band_coverage.json (the real walk-forward
// measurement) at all. On 2026-09-06 the real measured coverage was 68.9%,
// resolvably below the 80% nominal target (Wilson CI upper bound 78.3%), and
// the banner would still have said "about 80% of the time."
//
// This file proves the fix by constructing exactly that divergence: a
// forecast carrying nominal_coverage=80 alongside a measured bandCoverage
// object with a materially different value (45.3% -- the same number the
// walk-forward audit that introduced NOMINAL_COVERAGE_PCT itself found for
// the OLD Gaussian-substitute band, see ml/calibration.py's own docstring).
// If any future change reverts to sourcing the rendered percentage from
// nominal_coverage instead of the measured file, these tests fail.
//
// Functions inlined from app.js/i18n.js since app.js has no module system —
// see tests/test_good_price.js for the established pattern. Must match
// app.js's deriveMeasuredBandCoverage and i18n.js's calibrationConfidenceAppend
// exactly.
//
// Run: node --test tests/test_calibration_confidence.js  (from repo root)

import { test } from "node:test";
import assert from "node:assert/strict";

const BAND_COVERAGE_MAX_AGE_DAYS = 14;

// ── Inline copy of app.js's deriveMeasuredBandCoverage ──
function deriveMeasuredBandCoverage(bandCoverage, nowMs = Date.now()) {
  if (
    !bandCoverage ||
    typeof bandCoverage.coverage !== "number" ||
    typeof bandCoverage.n !== "number" ||
    typeof bandCoverage.generated_at_utc !== "string"
  ) {
    return null;
  }
  const generatedMs = Date.parse(bandCoverage.generated_at_utc);
  if (Number.isNaN(generatedMs)) return null;
  const ageDays = (nowMs - generatedMs) / 86_400_000;
  if (ageDays > BAND_COVERAGE_MAX_AGE_DAYS) return null;
  return { coverage: Math.round(bandCoverage.coverage * 1000) / 10, n: bandCoverage.n };
}

// ── Inline copy of i18n.js's EN calibrationConfidenceAppend ──
function calibrationConfidenceAppend({ amount, coverage, n }) {
  return coverage != null && n != null
    ? ` Based on past comparisons, the real price has landed within about ₹${amount}/gram of this estimate about ${coverage}% of the time so far (n=${n} weeks measured).`
    : ` Based on past comparisons, the real price lands within about ₹${amount}/gram of this estimate.`;
}

// ── Inline copy of app.js's renderStaleBanner's confidence-clause construction ──
// (the part relevant to this claim — not the whole banner-state decision,
// which tests/test_stale_banner.js already covers).
function buildConfidenceClause(forecast, bandCoverage, nowMs = Date.now()) {
  if (typeof forecast.nominal_coverage !== "number" || typeof forecast.band_half_width !== "number") {
    return "";
  }
  const measured = deriveMeasuredBandCoverage(bandCoverage, nowMs);
  return calibrationConfidenceAppend({
    amount: Math.round(forecast.band_half_width),
    coverage: measured ? measured.coverage : null,
    n: measured ? measured.n : null,
  });
}

const NOW = Date.parse("2026-09-10T12:00:00Z");
const FORECAST_WITH_BAND = { nominal_coverage: 80, band_half_width: 245.7 };

test("divergence proof: measured coverage (45.3%) renders instead of the 80% nominal constant", () => {
  const bandCoverage = {
    coverage: 0.453,
    n: 60,
    generated_at_utc: "2026-09-09T00:00:00Z", // 1 day old — fresh
  };
  const clause = buildConfidenceClause(FORECAST_WITH_BAND, bandCoverage, NOW);
  assert.match(clause, /45\.3%/);
  assert.doesNotMatch(clause, /80%/);
  assert.match(clause, /n=60 weeks measured/);
});

test("divergence proof: measured coverage well above nominal also renders the real number, not 80", () => {
  const bandCoverage = { coverage: 0.923, n: 12, generated_at_utc: "2026-09-10T00:00:00Z" };
  const clause = buildConfidenceClause(FORECAST_WITH_BAND, bandCoverage, NOW);
  assert.match(clause, /92\.3%/);
  assert.doesNotMatch(clause, /80%/);
});

test("no measurement available (fetch failed / file missing) → omits the percentage, does NOT fall back to nominal_coverage", () => {
  const clause = buildConfidenceClause(FORECAST_WITH_BAND, null, NOW);
  assert.doesNotMatch(clause, /%/);
  assert.match(clause, /₹246\/gram of this estimate\.$/);
});

test("stale measurement (older than BAND_COVERAGE_MAX_AGE_DAYS) → omits the percentage, does NOT fall back to nominal_coverage", () => {
  const staleBandCoverage = {
    coverage: 0.689, // the real 2026-09-06 reading
    n: 74,
    generated_at_utc: "2026-08-01T00:00:00Z", // far more than 14 days before NOW
  };
  const clause = buildConfidenceClause(FORECAST_WITH_BAND, staleBandCoverage, NOW);
  assert.doesNotMatch(clause, /%/);
  assert.doesNotMatch(clause, /68\.9/);
  assert.doesNotMatch(clause, /80/);
});

test("malformed measurement (coverage not a number) → omits the percentage, does NOT fall back to nominal_coverage", () => {
  const malformed = { coverage: "N/A", n: 74, generated_at_utc: "2026-09-09T00:00:00Z" };
  const clause = buildConfidenceClause(FORECAST_WITH_BAND, malformed, NOW);
  assert.doesNotMatch(clause, /%/);
});

test("exactly at the freshness boundary (14 days old) is still usable; just past it is not", () => {
  const exactlyBoundary = {
    coverage: 0.7,
    n: 50,
    generated_at_utc: new Date(NOW - 14 * 86_400_000).toISOString(),
  };
  const justPast = {
    coverage: 0.7,
    n: 50,
    generated_at_utc: new Date(NOW - 14 * 86_400_000 - 60_000).toISOString(),
  };
  assert.match(buildConfidenceClause(FORECAST_WITH_BAND, exactlyBoundary, NOW), /70%/);
  assert.doesNotMatch(buildConfidenceClause(FORECAST_WITH_BAND, justPast, NOW), /%/);
});

test("no band at all (nominal_coverage/band_half_width absent from forecast) → no clause, regardless of a valid measurement", () => {
  const forecastNoBand = { nominal_coverage: null, band_half_width: null };
  const freshBandCoverage = { coverage: 0.453, n: 60, generated_at_utc: "2026-09-09T00:00:00Z" };
  assert.equal(buildConfidenceClause(forecastNoBand, freshBandCoverage, NOW), "");
});

test("Hindi calibrationConfidenceAppend also sources coverage/n from the measurement, never a hardcoded 80", () => {
  function calibrationConfidenceAppendHi({ amount, coverage, n }) {
    return coverage != null && n != null
      ? ` पिछली तुलनाओं के आधार पर, असली कीमत अब तक लगभग ${coverage}% बार इस अनुमान के ₹${amount}/ग्राम के दायरे में रही है (n=${n} हफ़्तों का मापन)।`
      : ` पिछली तुलनाओं के आधार पर, असली कीमत इस अनुमान के ₹${amount}/ग्राम के दायरे में रहती है।`;
  }
  const withData = calibrationConfidenceAppendHi({ amount: 246, coverage: 45.3, n: 60 });
  assert.match(withData, /45\.3%/);
  assert.doesNotMatch(withData, /80%/);
  const withoutData = calibrationConfidenceAppendHi({ amount: 246, coverage: null, n: null });
  assert.doesNotMatch(withoutData, /%/);
});
