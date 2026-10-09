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

// Date only: next_fix.target_time is midnight UTC of the target day, which prints as 05:30 am in IST,
// a time nothing is published or measured at (page audit, 2026-10-09). Show the day, not a clock time.
function fmtISTDate(iso) {
  if (!iso) return "—";
  const locale = currentLang === "hi" ? "hi-IN" : "en-IN";
  try {
    return new Intl.DateTimeFormat(locale, {
      timeZone: "Asia/Kolkata", day: "numeric", month: "short", year: "numeric", numberingSystem: "latn",
    }).format(new Date(iso));
  } catch (_) { return "—"; }
}

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
// asserted as current. G4 (2026-09-25): the age check itself now lives in
// i18n.js's isMeasurementFresh (CLAIM_MAX_AGE_DAYS=14) -- shared with app.js
// and with this page's own coverage/backtest freshness checks below, instead
// of each render path re-deriving its own 14-day arithmetic.
function deriveMeasuredBandCoverage(bandCoverage, nowMs = Date.now()) {
  if (
    !bandCoverage ||
    typeof bandCoverage.coverage !== "number" ||
    typeof bandCoverage.n !== "number" ||
    !isMeasurementFresh(bandCoverage.generated_at_utc, nowMs)
  ) {
    return null;
  }
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
    // G4 (2026-09-25): stale coverage (>CLAIM_MAX_AGE_DAYS) falls to methRangeSub's
    // own no-times fallback (plain "Range: ₹X – ₹Y"), same as a missing measurement.
    // Page audit (2026-10-09): while the next-fix model sets the range, quote the hit rate of THAT
    // range (next_fix.range_record, a test on past days), as the main page does -- not the retired
    // flat-hold band's live record, which belongs to a different range.
    const rr      = fc?.next_fix?.range_record;
    const nfCov   = fc?.next_fix?.active === true && typeof rr?.coverage === "number" && rr.n > 0;
    const covSrc  = nfCov ? { coverage: rr.coverage, n: rr.n } : coverage;
    const covOk   = nfCov || (coverage && typeof coverage.coverage === "number" && coverage.n > 0
      && isMeasurementFresh(coverage.generated_at_utc, Date.now()));
    const timesN  = covOk ? Math.max(0, Math.min(10, Math.floor(covSrc.coverage * 10 + 1e-9))) : null;
    const times   = covOk ? fractionOutOf10Phrase(covSrc.coverage * 100) : null;
    parts.push(`
      <div class="meth-section">
        <h3 class="meth-heading">${tHwk("methNextDayRangeHeading")}</h3>
        <div class="meth-stats">
          <div class="meth-stat">
            <div class="meth-stat-label">${tHwk("methEstimateLabel")}</div>
            <div class="meth-stat-value">₹${fmtINR(pred22k)}</div>
            ${hasPI ? `<div class="meth-stat-sub">${tHwk(nfCov ? "methRangeSubTested" : "methRangeSub", { low: fmtINR(lower), high: fmtINR(upper), times, n: timesN })}</div>` : ""}
          </div>
          <div class="meth-stat">
            <div class="meth-stat-label">${tHwk("methMethodLabel")}</div>
            <div class="meth-stat-value">${tHwk(fc.next_fix?.active !== true ? "methAssumeNoChange" : fc.next_fix.mode === "after_us_close" ? "methNextFixModel" : "methNextFixHold")}</div>
            <div class="meth-stat-sub">${tHwk(fc.next_fix?.active !== true ? "methCoversMoves" : fc.next_fix.mode === "after_us_close" ? "methNextFixModelSub" : "methNextFixHoldSub")}</div>
          </div>
        </div>
        ${fc.target_time ? `<p class="meth-text" style="margin-top:8px">${tHwk("methTargetLine", { date: fmtISTDate(fc.target_time) })}</p>` : ""}
        <p class="meth-text" style="margin-top:12px">${tHwk("methNextDayExplainer")}</p>
      </div>
    `);
  }

  // ADR 064: the next-fix model's own out-of-sample record (forecast.json next_fix.track_record).
  const nfTr = fc?.next_fix?.track_record;
  const nfDirOn = nfTr?.direction_gate_ship === true && typeof nfTr.direction_accuracy === "number" && nfTr.n > 0;
  if (nfDirOn) {
    parts.push(`
      <div class="meth-section">
        <h3 class="meth-heading">${tHwk("methDirectionHeading")}</h3>
        <div class="meth-stat">
          <div class="meth-stat-label">${tHwk("methStatusLabel")}</div>
          <div class="meth-stat-value">${tHwk("methDirectionOn")}</div>
          <div class="meth-stat-sub">${tHwk("methDirectionOnSub", {
            right: Math.round(nfTr.direction_accuracy * nfTr.n), n: nfTr.n,
            upRight: Math.round(nfTr.always_up_accuracy * nfTr.n),
          })}</div>
        </div>
        <p class="meth-note">${tHwk("methDirectionOnNote")}</p>
        <p class="meth-note">${tHwk("methDirectionSource")}</p>
      </div>
    `);
  } else if (fc?.chronos_companion?.status === "success") {
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

    // G4 (2026-09-25): stale coverage (>CLAIM_MAX_AGE_DAYS) falls to the same
    // "still building a track record" wording as a genuinely missing measurement
    // (methAccurateP2CoverageUnknown already covers both -- no new string needed).
    const rrA       = fc?.next_fix?.range_record;
    const nfCovA    = fc?.next_fix?.active === true && typeof rrA?.coverage === "number" && rrA.n > 0;
    const covSrcA   = nfCovA ? { coverage: rrA.coverage, n: rrA.n } : coverage;
    const hasCoverage = nfCovA || (coverage && typeof coverage.coverage === "number" && coverage.n > 0
      && isMeasurementFresh(coverage.generated_at_utc, Date.now()));
    const coverPct = hasCoverage ? Math.round(covSrcA.coverage * 100) : null;
    const coverN   = hasCoverage ? covSrcA.n : null;

    // G4 (2026-09-25): P1 (naive/AI MAE + p-value) and P3 (direction accuracy)
    // both come from this SAME weekly backtest.json run -- when backtest_run_at
    // is more than CLAIM_MAX_AGE_DAYS old (weekly-backtest.yml stopped running),
    // omit both paragraphs entirely rather than assert a weeks-old number as
    // current (rule 98a). P2 (coverage, independently sourced/gated above) and
    // the static P4 explainer are unaffected and keep rendering.
    const btFresh = isMeasurementFresh(bt.backtest_run_at, Date.now());
    const nextFixHtml = nfTr && typeof nfTr.mae_model === "number" && nfTr.n > 0 ? `
        <p class="meth-text"><strong>${tHwk("methNextFixStrong")}</strong><br>
        ${tHwk("methNextFixP", {
          n: nfTr.n, modelMae: fmtINR(Math.round(nfTr.mae_model)), flatMae: fmtINR(Math.round(nfTr.mae_flat)),
          pct: Math.round(Math.abs(nfTr.mae_change_pct)),
        })}</p>` : "";
    const p1Html = btFresh ? `
        <p class="meth-text"><strong>${tHwk("methAccurateP1Strong")}</strong><br>
        ${tHwk("methAccurateP1", {
          n, naiveMae,
          chronosBullet: maePctWorse != null ? tHwk("methAccurateP1ChronosBullet", { chronosMae, maePctWorse, pValOp, pValText }) : "",
        })}</p>` : "";
    const p3Html = btFresh ? `
        <p class="meth-text"><strong>${tHwk("methAccurateP3Strong")}</strong><br>
        ${tHwk("methAccurateP3", { dirAllDisplay, n })}</p>` : "";

    parts.push(`
      <div class="meth-section meth-how-good">
        <h3 class="meth-heading">${tHwk("methHowAccurateHeading")}</h3>
        ${nextFixHtml}
        ${p1Html}
        <p class="meth-text"><strong>${tHwk("methAccurateP2Strong", {
          rangeStr,
          coverageText: hasCoverage
            ? tHwk(nfCovA ? "methAccurateP2CoverageTested" : "methAccurateP2CoveragePct", { pct: coverPct, n: coverN })
            : tHwk("methAccurateP2CoverageUnknown"),
        })}</strong><br>
        ${tHwk(nfCovA ? "methAccurateP2Tested" : "methAccurateP2")}</p>
        ${p3Html}
        <p class="meth-text"><strong>${tHwk("methAccurateP4Strong")}</strong><br>
        ${tHwk("methAccurateP4")}</p>
      </div>
    `);
  }

  // Removed 2026-10-09 (page audit): the "Estimate accuracy - last 7 days" block compared the newest
  // reading's gap to the previous run's forecast (hours ahead, about a third of them exactly 0) with a
  // 5-day-ahead backtest error, so "on track" could never fail. The live model's real record is the
  // next-rate paragraph above; computeAccuracyDrift() stays defined but nothing renders it.

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
