// how-we-know.js — the full technical breakdown, moved off the main page's
// accordion (U2, 2026-09-23, docs/PLAIN_LANGUAGE_AUDIT.md). Renders from the
// SAME data files app.js reads (forecast.json, backtest.json, drift_metrics.json,
// coverage_metrics.json, calibration_band_coverage.json) — same numbers, just
// spelled out in full instead of rounded to a plain-language phrase.
//
// Deliberately a SEPARATE script from app.js, not a shared function call, even
// though this duplicates a handful of small helpers (fmtINR, fmtIST, loadJSON,
// computeAccuracyDrift, deriveMeasuredBandCoverage — copied verbatim from
// app.js, same behaviour). app.js's init() IIFE assumes index.html's full DOM
// (hero skeleton, calculator inputs, chart canvas, comparison cards, etc.) and
// touches dozens of element ids this page doesn't have; loading app.js here
// would fetch/render all of that for nothing (and pull in Chart.js/Sentry this
// page has no use for). Keeping the duplication small and named beats a shared
// function that has to defensively null-check a DOM shape it was never really
// designed for.

// ── Small helpers duplicated from app.js (see that file for the originals) ────

const fmtINR = (n) =>
  typeof n === "number"
    ? n.toLocaleString("en-IN", { maximumFractionDigits: 0 })
    : "—";

function fmtIST(iso) {
  if (!iso) return "—";
  const locale = currentLang === "hi" ? "hi-IN" : "en-IN";
  try {
    return new Intl.DateTimeFormat(locale, {
      timeZone: "Asia/Kolkata",
      day: "numeric", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit", hour12: true,
      numberingSystem: "latn",
    }).format(new Date(iso));
  } catch (_) { return "—"; }
}

const LOAD_TIMEOUT_MS = 10_000;

async function loadJSON(url, timeoutMs = LOAD_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${url}?t=${Date.now()}`, { signal: controller.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
    return await res.json();
  } catch (err) {
    if (err.name === "AbortError") throw new Error(`Timed out after ${timeoutMs}ms loading ${url}`);
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

function computeAccuracyDrift(drift) {
  if (!Array.isArray(drift) || drift.length === 0) return null;
  const now      = Date.now();
  const recent7d = drift.filter(e => e.residual != null && now - new Date(e.ts).getTime() <= 7 * 86400e3);
  const rolling  = recent7d.length > 0
    ? recent7d.reduce((s, e) => s + Math.abs(e.residual), 0) / recent7d.length
    : null;
  const withBase = [...drift].reverse().find(e => e.baseline_mae != null);
  const baseMae  = withBase ? withBase.baseline_mae : null;
  const ratio    = rolling != null && baseMae ? rolling / baseMae : null;
  const ratioLabelKey = ratio == null ? null : (ratio < 1 ? "ratioOnTrack" : ratio <= 1.5 ? "ratioWatch" : "ratioRetrain");
  return { rolling, baseMae, ratio, ratioLabelKey };
}

// Same rule-98a fail-closed shape as app.js's deriveMeasuredBandCoverage: a
// stale (>14 days) or malformed measurement returns null, never a stale number
// asserted as current.
const BAND_COVERAGE_MAX_AGE_DAYS = 14;

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
  return { coverage: Math.round(bandCoverage.coverage * 1000) / 10, n: bandCoverage.n, asOf: bandCoverage.generated_at_utc };
}

// ── Data URLs (same paths app.js reads) ────────────────────────────────────────

const FORECAST_URL = "data/forecast.json";
const BACKTEST_URL = "data/backtest.json";
const DRIFT_URL = "data/drift_metrics.json";
const COVERAGE_URL = "data/coverage_metrics.json";
const CALIBRATION_BAND_COVERAGE_URL = "data/calibration_band_coverage.json";

// ── Render ───────────────────────────────────────────────────────────────────
// Same section-by-section structure as app.js's old renderMethodology() (see
// its git history) — each section renders only if its own data is present, so
// a missing optional file degrades that one section, not the whole page. Adds
// one new section (Band accuracy) that the old accordion never had: the exact
// calibration_band_coverage.json numbers the main page's stale-banner now
// shows as a plain "N times out of 10" phrase instead of asserting them
// unrounded there.
function renderFullMethodology(fc, bt, drift, coverage, bandCoverage) {
  const body = document.getElementById("how-we-know-body");
  if (!body) return;

  const parts = [];

  const dirAll = bt && typeof bt.dir_acc_5d_chronos === "number"
    ? `${Math.round(bt.dir_acc_5d_chronos * 100)}%`
    : null;

  parts.push(`
    <div class="meth-section">
      <h3 class="meth-heading">${tHwk("methHowWeCallTrendHeading")}</h3>
      <p class="meth-text">${tHwk("methHowWeCallTrendIntro")}</p>
      <ul class="meth-list">
        <li>${tHwk("methRuleCheaper")}</li>
        <li>${tHwk("methRulePricier")}</li>
        <li>${tHwk("methRuleSteady")}</li>
      </ul>
    </div>
  `);

  if (fc && typeof (fc.headline?.predicted_22k ?? fc.predicted_22k) === "number") {
    const pred22k = fc.headline?.predicted_22k ?? fc.predicted_22k;
    const lower   = fc.headline?.lower ?? fc.lower;
    const upper   = fc.headline?.upper ?? fc.upper;
    const hasPI   = typeof lower === "number" && typeof upper === "number";
    // The range's own track record, floored (never the 80% target it aims for):
    // the same coverage figure the "How accurate" section shows unrounded.
    const covOk   = coverage && typeof coverage.coverage === "number" && coverage.n > 0;
    const timesN  = covOk ? Math.max(0, Math.min(10, Math.floor(coverage.coverage * 10 + 1e-9))) : null;
    const times   = covOk ? fractionOutOf10Phrase(coverage.coverage * 100) : null;
    parts.push(`
      <div class="meth-section">
        <h3 class="meth-heading">${tHwk("methNextDayRangeHeading")}</h3>
        <div class="meth-stats">
          <div class="meth-stat">
            <div class="meth-stat-label">${tHwk("methEstimateLabel")}</div>
            <div class="meth-stat-value">₹${fmtINR(pred22k)}</div>
            ${hasPI ? `<div class="meth-stat-sub">${tHwk("methRangeSub", { low: fmtINR(lower), high: fmtINR(upper), times, n: timesN })}</div>` : ""}
          </div>
          <div class="meth-stat">
            <div class="meth-stat-label">${tHwk("methMethodLabel")}</div>
            <div class="meth-stat-value">${tHwk("methAssumeNoChange")}</div>
            <div class="meth-stat-sub">${tHwk("methCoversMoves")}</div>
          </div>
        </div>
        ${fc.target_time ? `<p class="meth-text" style="margin-top:8px">${tHwk("methTargetLine", { date: fmtIST(fc.target_time) })}</p>` : ""}
        <p class="meth-text" style="margin-top:12px">${tHwk("methNextDayExplainer")}</p>
      </div>
    `);
  }

  if (fc?.chronos_companion?.status === "success") {
    parts.push(`
      <div class="meth-section">
        <h3 class="meth-heading">${tHwk("methDirectionHeading")}</h3>
        <div class="meth-stat">
          <div class="meth-stat-label">${tHwk("methStatusLabel")}</div>
          <div class="meth-stat-value">${tHwk("methDirectionOff")}</div>
          <div class="meth-stat-sub">${tHwk("methDirectionSub")}</div>
        </div>
        <p class="meth-note">${tHwk("methDirectionNote")}</p>
      </div>
    `);
  } else if (fc?.chronos_companion?.status === "failed") {
    parts.push(`<p class="meth-text">${tHwk("methDirectionUnavailable")}</p>`);
  }

  if (bt && typeof bt.mae_5d_avg_naive === "number") {
    const n           = bt.n_folds ?? "—";
    const naiveMae    = fmtINR(Math.round(bt.mae_5d_avg_naive));
    const chronosMae  = typeof bt.mae_5d_avg_chronos === "number"
      ? fmtINR(Math.round(bt.mae_5d_avg_chronos))
      : "—";
    const maePctWorse = typeof bt.mae_5d_avg_chronos === "number"
      ? Math.round(((bt.mae_5d_avg_chronos - bt.mae_5d_avg_naive) / bt.mae_5d_avg_naive) * 100)
      : null;
    const dirAllDisplay = dirAll ?? "—";
    // formatPValue (i18n.js) avoids ever printing "p = 0.0000" -- a p-value that
    // rounds to zero at 4 decimal places renders "p < 0.0001" instead.
    const pValFmt  = formatPValue(bt.wilcoxon_signed_rank_p, 4);
    const pValOp   = pValFmt?.op ?? "=";
    const pValText = pValFmt?.text ?? "—";
    const hl      = fc?.headline;
    const rangeStr = hl && typeof hl.lower === "number" && typeof hl.upper === "number"
      ? `₹${fmtINR(hl.lower)}–₹${fmtINR(hl.upper)}`
      : tHwk("methRangeStrFallback");

    const hasCoverage = coverage && typeof coverage.coverage === "number" && coverage.n > 0;
    const coverPct = hasCoverage ? Math.round(coverage.coverage * 100) : null;
    const coverN   = hasCoverage ? coverage.n : null;

    parts.push(`
      <div class="meth-section meth-how-good">
        <h3 class="meth-heading">${tHwk("methHowAccurateHeading")}</h3>

        <p class="meth-text"><strong>${tHwk("methAccurateP1Strong")}</strong><br>
        ${tHwk("methAccurateP1", {
          n, naiveMae,
          chronosBullet: maePctWorse != null ? tHwk("methAccurateP1ChronosBullet", { chronosMae, maePctWorse, pValOp, pValText }) : "",
        })}</p>

        <p class="meth-text"><strong>${tHwk("methAccurateP2Strong", {
          rangeStr,
          coverageText: hasCoverage ? tHwk("methAccurateP2CoveragePct", { pct: coverPct, n: coverN }) : tHwk("methAccurateP2CoverageUnknown"),
        })}</strong><br>
        ${tHwk("methAccurateP2")}</p>

        <p class="meth-text"><strong>${tHwk("methAccurateP3Strong")}</strong><br>
        ${tHwk("methAccurateP3", { dirAllDisplay, n })}</p>

        <p class="meth-text"><strong>${tHwk("methAccurateP4Strong")}</strong><br>
        ${tHwk("methAccurateP4")}</p>
      </div>
    `);
  }

  const accDrift = computeAccuracyDrift(drift);
  if (accDrift) {
    const rolling = accDrift.rolling != null ? Math.round(accDrift.rolling) : null;
    const baseMae = accDrift.baseMae != null ? Math.round(accDrift.baseMae) : null;
    const ratio   = accDrift.ratio != null ? accDrift.ratio.toFixed(2) : null;
    const ratioLabelKey = accDrift.ratioLabelKey;
    parts.push(`
      <div class="meth-section">
        <h3 class="meth-heading">${tHwk("methDriftHeading")}</h3>
        <div class="meth-stats">
          <div class="meth-stat">
            <div class="meth-stat-label">${tHwk("methRecentError")}</div>
            <div class="meth-stat-value">${rolling != null ? "₹" + fmtINR(rolling) : "—"}</div>
          </div>
          <div class="meth-stat">
            <div class="meth-stat-label">${tHwk("methHistoricalError")}</div>
            <div class="meth-stat-value">${baseMae != null ? "₹" + fmtINR(baseMae) : "—"}</div>
          </div>
          <div class="meth-stat">
            <div class="meth-stat-label">${tHwk("methAccuracyDrift")}</div>
            <div class="meth-stat-value">${ratio ?? "—"}</div>
            <div class="meth-stat-sub">${ratioLabelKey === "ratioRetrain" ? tHwk("ratioRetrainSub") : (ratioLabelKey ? tHwk(ratioLabelKey) : "")}</div>
          </div>
        </div>
      </div>
    `);
  }

  // Band accuracy (measured) — the exact numbers the main page's stale-banner
  // used to assert inline (i18n.js's calibrationConfidenceAppend), now a
  // floored plain-language phrase there. band_half_width is only meaningful
  // alongside a real measurement (rule 98a — nominal_coverage's presence alone
  // is not a measured claim, see app.js's own comment on this field).
  if (fc && typeof fc.band_half_width === "number" && typeof fc.nominal_coverage === "number") {
    const measured = deriveMeasuredBandCoverage(bandCoverage);
    parts.push(`
      <div class="meth-section">
        <h3 class="meth-heading">${tHwk("methBandAccuracyHeading")}</h3>
        <p class="meth-text">${measured
          ? tHwk("methBandAccuracyText", {
              amount: fmtINR(Math.round(fc.band_half_width)),
              pct: measured.coverage,
              n: measured.n,
              asOf: fmtIST(measured.asOf),
            })
          : tHwk("methBandAccuracyUnknown")}</p>
      </div>
    `);
  }

  // XSS-safe: parts[] contains only hardcoded HTML templates with numeric/boolean
  // values from forecast.json/backtest.json/calibration_band_coverage.json.
  // Overwriting innerHTML here removes the skeleton markup along with it —
  // see the comment on #how-we-know-body in how-we-know.html for why that's
  // deliberate rather than a `hidden`-attribute toggle.
  if (parts.length === 0) {
    body.innerHTML = `<p class="meth-loading">${tHwk("hwkEmpty")}</p>`;
    return false;
  }
  body.innerHTML = parts.join("");
  return true;
}

// ── Shared shell (header title, lang toggle, footer) — reuses i18n.js's t() ───

function applySharedShellStrings() {
  const title = document.getElementById("hwk-app-title");
  if (title) title.textContent = t("appTitle");
  const toggle = document.getElementById("hwk-lang-toggle");
  if (toggle) {
    toggle.setAttribute("aria-label", t("langToggleAriaLabel"));
    toggle.title = t("langToggleAriaLabel");
    toggle.textContent = currentLang === "hi" ? "EN" : "हिं";
  }
  const backLink = document.getElementById("hwk-back-link");
  if (backLink) backLink.textContent = tHwk("hwkBackLink");
  const heading = document.getElementById("hwk-heading");
  if (heading) heading.textContent = tHwk("hwkHeading");
  const intro = document.getElementById("hwk-intro");
  if (intro) intro.textContent = tHwk("hwkIntro");
  // No cadence params fetched on this page (rule 98a: never assert a specific
  // cadence number this page hasn't itself measured) — footerBody's own
  // null-params branch is the honest, unqualified fallback text.
  const footerEl = document.querySelector('[data-i18n-html="footerBody"]');
  if (footerEl) footerEl.innerHTML = t("footerBody", null);
  const footerMutedEl = document.querySelector('[data-i18n="footerMuted"]');
  if (footerMutedEl) footerMutedEl.textContent = t("footerMuted");
  document.title = tHwk("hwkPageTitle");
}

function bindLangToggle() {
  const toggle = document.getElementById("hwk-lang-toggle");
  if (!toggle) return;
  toggle.addEventListener("click", () => {
    setLang(currentLang === "hi" ? "en" : "hi");
    applySharedShellStrings();
    renderPage(); // re-render technical content in the new language
  });
}

// ── Loading / empty / error states ─────────────────────────────────────────────
// The loading state is the skeleton markup already sitting in #how-we-know-body's
// HTML (how-we-know.html) — nothing to show here for it. Empty/error both write
// their message into the same element renderFullMethodology() renders into,
// same "overwrite innerHTML" convention as the content path above.

function showError() {
  const body = document.getElementById("how-we-know-body");
  if (body) body.innerHTML = `<p class="meth-loading">${tHwk("hwkError")}</p>`;
}

let lastFc = null, lastBt = null, lastDrift = null, lastCoverage = null, lastBandCoverage = null;

function renderPage() {
  renderFullMethodology(lastFc, lastBt, lastDrift, lastCoverage, lastBandCoverage);
}

async function init() {
  applySharedShellStrings();
  bindLangToggle();

  let settled;
  try {
    settled = await Promise.allSettled([
      loadJSON(FORECAST_URL),
      loadJSON(BACKTEST_URL),
      loadJSON(DRIFT_URL),
      loadJSON(COVERAGE_URL),
      loadJSON(CALIBRATION_BAND_COVERAGE_URL),
    ]);
  } catch (err) {
    // Promise.allSettled itself never rejects — this is defence in depth only.
    console.error("how-we-know: unexpected failure", err);
    showError();
    return;
  }

  const [fc, bt, drift, coverage, bandCoverage] = settled.map(r => (r.status === "fulfilled" ? r.value : null));
  if (settled.every(r => r.status === "rejected")) {
    showError();
    return;
  }

  lastFc = fc; lastBt = bt; lastDrift = drift; lastCoverage = coverage; lastBandCoverage = bandCoverage;
  renderPage();
}

init();
