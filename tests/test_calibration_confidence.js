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
// Drives the REAL renderStaleBanner / i18n strings (tests/helpers/load_app.js). This file used to
// paste copies of deriveMeasuredBandCoverage, calibrationConfidenceAppend and the clause
// construction; the Hindi case even defined its own function inside the test and asserted on it.
//
// Run: node --test tests/test_calibration_confidence.js  (from repo root)

import { test } from "node:test";
import assert from "node:assert/strict";

import { loadApp } from "./helpers/load_app.js";

// The confidence clause is appended by the real renderStaleBanner in its "IBJA published today"
// state; run that, then slice the base sentence off to get just the clause.
function buildConfidenceClause(forecast, bandCoverage, nowMs = Date.now()) {
  const app = loadApp({ nowMs });
  try {
    const f = { ...forecast, price_source: "ibja_calibrated", ibja_asof: new Date(nowMs).toISOString() };
    app.renderStaleBanner(f, bandCoverage);
    const text = app.element("stale-banner").textContent;
    const base = app.t("bannerIbjaToday");
    assert.ok(text.startsWith(base), `unexpected banner text: ${text}`);
    return text.slice(base.length);
  } finally {
    app.dispose();
  }
}

const NOW = Date.parse("2026-09-10T12:00:00Z");
const FORECAST_WITH_BAND = { nominal_coverage: 80, band_half_width: 245.7 };

// U2 (2026-09-23, docs/PLAIN_LANGUAGE_AUDIT.md): the clause now renders a
// floored "N times out of 10" phrase (i18n.js's fractionOutOf10Phrase)
// instead of a raw "X%" — same underlying divergence proof (the real
// measured value drives the text, never a hardcoded nominal default), just
// asserting on the new plain-language wording. The exact percentage/n this
// rounds away are no longer on the main page at all (moved to
// how-we-know.html's "Band accuracy" section, same source data) — not
// something this clause renders anymore, in any form.

test("divergence proof: measured coverage (45.3% -> 4/10) renders instead of the 80% nominal constant (8/10)", () => {
  const bandCoverage = {
    coverage: 0.453,
    n: 60,
    generated_at_utc: "2026-09-09T00:00:00Z", // 1 day old — fresh
  };
  const clause = buildConfidenceClause(FORECAST_WITH_BAND, bandCoverage, NOW);
  assert.match(clause, /4 times out of 10/);
  assert.doesNotMatch(clause, /8 times out of 10/);
  assert.doesNotMatch(clause, /n=/);
});

test("divergence proof: measured coverage well above nominal also renders the real fraction (9/10), not 8/10", () => {
  const bandCoverage = { coverage: 0.923, n: 12, generated_at_utc: "2026-09-10T00:00:00Z" };
  const clause = buildConfidenceClause(FORECAST_WITH_BAND, bandCoverage, NOW);
  assert.match(clause, /9 times out of 10/);
  assert.doesNotMatch(clause, /8 times out of 10/);
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
  assert.match(buildConfidenceClause(FORECAST_WITH_BAND, exactlyBoundary, NOW), /7 times out of 10/);
  assert.doesNotMatch(buildConfidenceClause(FORECAST_WITH_BAND, justPast, NOW), /times out of 10/);
});

test("no band at all (nominal_coverage/band_half_width absent from forecast) → no clause, regardless of a valid measurement", () => {
  const forecastNoBand = { nominal_coverage: null, band_half_width: null };
  const freshBandCoverage = { coverage: 0.453, n: 60, generated_at_utc: "2026-09-09T00:00:00Z" };
  assert.equal(buildConfidenceClause(forecastNoBand, freshBandCoverage, NOW), "");
});

test("Hindi calibrationConfidenceAppend falls back to the reworded English (no HI entry yet, pending native review)", () => {
  // U2 (2026-09-23, docs/PLAIN_LANGUAGE_AUDIT.md): calibrationConfidenceAppend's
  // Hindi entry was removed when the English shape changed (raw %+n= -> a
  // floored fraction phrase) rather than machine-translated -- t()'s own
  // fallback (STRINGS[lang]?.[key] ?? STRINGS.en[key]) means Hindi readers see
  // the reworded English until a native speaker adds the Hindi version. This
  // is the intended, documented behaviour, not a regression.
  const hi = loadApp({ lang: "hi" });
  try {
    const withData = hi.t("calibrationConfidenceAppend", { amount: 246, coverage: 45.3, n: 60 });
    assert.match(withData, /4 times out of 10/);
    assert.doesNotMatch(withData, /8 times out of 10/);
    const withoutData = hi.t("calibrationConfidenceAppend", { amount: 246, coverage: null, n: null });
    assert.doesNotMatch(withoutData, /times out of 10/);
    assert.doesNotMatch(withData, /[\u0900-\u097F]/, "expected English fallback -- no HI entry exists for this key yet");
  } finally {
    hi.dispose();
  }
});
