// app.js — Buyer-focused gold rate tracker.

// Sentry.init() lives here (not an inline <script> in index.html) so it runs after the
// deferred Sentry bundle has loaded — see index.html's comment on that script tag for why
// it's deferred and why an inline init script there would race ahead of it.
if (typeof Sentry !== "undefined") {
  Sentry.init({
    dsn: "https://PLACEHOLDER@o000000.ingest.sentry.io/0000000", // TODO: replace with your project DSN
    sampleRate: 1.0,        // capture every error event
    tracesSampleRate: 0.0,  // no performance tracing (not needed)
    environment: "production",
  });
}

const DATA_URL      = "data/prices.json";
const FORECAST_URL  = "data/forecast.json";
const BACKTEST_URL  = "data/backtest.json";
const DRIFT_URL     = "data/drift_metrics.json";
const METRICS_URL   = "data/metrics_history.json";
const COVERAGE_URL  = "data/coverage_metrics.json";
const CALIBRATION_URL = "data/calibration.json";

// Staleness threshold (hours) shared with Python inference.py _STALE_THRESHOLD_H.
// Per ADR 025 this now gates Tanishq *enrichment* freshness, not primary staleness.
const STALE_THRESHOLD_H = 8;

// D4: True when running as an installed PWA launched from the home screen.
// navigator.standalone is iOS WebKit's proprietary flag (true/false/undefined).
// matchMedia display-mode:standalone is the W3C standard (patchy on older iOS).
// OR'ing both gives best cross-platform coverage without false positives.
const IS_STANDALONE =
  window.matchMedia("(display-mode: standalone)").matches ||
  window.navigator.standalone === true;

// True on iOS/iPadOS Safari (and any other iOS browser -- all iOS browsers are
// WebKit under the hood, so this applies regardless of what's in the UA string
// beyond the OS itself). Classic UA check plus the iPadOS 13+ case, where an
// iPad reports "MacIntel" like desktop Safari but is touch-capable.
const IS_IOS =
  /iPad|iPhone|iPod/.test(navigator.userAgent) ||
  (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

const INSTALL_PROMPT_DISMISSED_KEY = "install-prompt-dismissed";
const FIRST_VISIT_DISMISSED_KEY = "first-visit-dismissed";

const fmtINR = (n) =>
  typeof n === "number"
    ? n.toLocaleString("en-IN", { maximumFractionDigits: 0 })
    : "—";

function rupee(n) {
  if (typeof n !== "number") return "—";
  return `<span class="rupee">₹</span>${fmtINR(n)}`;
}

// English keeps its existing hand-tuned short form ("2h ago") unchanged — only Hindi
// gets a real Intl.RelativeTimeFormat path, which handles Hindi's grammar/pluralization
// correctly via CLDR data (not a word-swap of the English template). numeric:"auto" lets
// it say "अभी-अभी"-equivalent phrasing where natural, but we drive the actual "just now"
// case ourselves for consistency with the English branch's own explicit just-now case.
function fmtRelative(iso) {
  const d    = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (currentLang === "hi") {
    if (diff < 60) return t("relJustNow");
    const rtf = new Intl.RelativeTimeFormat("hi-IN", { numeric: "auto", style: "long" });
    if (diff < 3600)  return rtf.format(-Math.round(diff / 60), "minute");
    if (diff < 86400) return rtf.format(-Math.round(diff / 3600), "hour");
    return rtf.format(-Math.round(diff / 86400), "day");
  }
  if (diff < 60)    return t("relJustNow");
  if (diff < 3600)  return t("relMinAgo", { n: Math.round(diff / 60) });
  if (diff < 86400) return t("relHoursAgo", { n: Math.round(diff / 3600) });
  return t("relDaysAgo", { n: Math.round(diff / 86400) });
}

// Digit grouping stays en-IN regardless of UI language — Indian digit grouping
// (₹13,33,330) is a REGIONAL convention, not a language one, and hi-IN's default
// numbering system can silently switch to Devanagari digits (०१२३…) depending on the
// browser's ICU data. numberingSystem:"latn" pins Arabic digits explicitly for the
// Hindi date path below, matching how Indian Hindi media actually writes dates.
function fmtDate(iso) {
  const locale = currentLang === "hi" ? "hi-IN" : "en-IN";
  return new Date(iso).toLocaleString(locale, {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
    numberingSystem: "latn",
  });
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

// Human-readable label for tier-3 fusion_sources (e.g. ["grt","malabar"] -> "GRT, Malabar").
// Never crashes on a missing/null sources list — falls back to a generic label.
function fusionSourcesLabel(sources) {
  const NAMES = { grt: t("fusionSourceGrt"), malabar: t("fusionSourceMalabar"), kalyan: t("fusionSourceKalyan") };
  const labels = (sources || []).map(s => NAMES[s] || s);
  return labels.length ? labels.join(", ") : t("fusionSourceFallback");
}

// One reading per IST calendar day (latest timestamp wins).
// Used in display/chart paths AND in the daily-average computations (vs-7d/vs-30d avg,
// verdict avg30d). allReadings stays raw for everything else: conformal PI, backtest
// inputs, trend slope, current price — those must not be time-averaged away.
function dedupeByISTDay(readings) {
  const byDay = new Map();
  for (const r of readings) {
    const key = new Date(r.timestamp).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
    byDay.set(key, r);
  }
  return [...byDay.values()];
}

// ─── DISPLAY-ONLY DEDUP (Φ8C' / absorbed from Ψ3C.3) ────────────────────────
// prices.json is NEVER modified. ML pipeline reads it directly.

function dedupReadings(readings) {
  if (readings.length === 0) return [];
  const groups = [];
  let g = { reading: readings[0], endTimestamp: readings[0].timestamp, count: 1 };
  for (let i = 1; i < readings.length; i++) {
    if (readings[i]["22k"] === g.reading["22k"]) {
      g.endTimestamp = readings[i].timestamp;
      g.count++;
    } else {
      groups.push(g);
      g = { reading: readings[i], endTimestamp: readings[i].timestamp, count: 1 };
    }
  }
  groups.push(g);
  return groups;
}

function fmtDateShort(iso) {
  const locale = currentLang === "hi" ? "hi-IN" : "en-IN";
  return new Intl.DateTimeFormat(locale, {
    timeZone: "Asia/Kolkata", day: "numeric", month: "short", numberingSystem: "latn",
  }).format(new Date(iso));
}

let chart            = null;
let allReadings      = [];
let currentRange     = "30";   // tracks active chart tab for refreshData()
let pwaHelpDismissed = false; // D5: set true when user taps ✕; survives re-renders
let chartPinnedIndex  = null;  // index of tapped chart point; null = no callout
let trackRecordChart  = null;  // Chart.js instance for forecast-vs-actual section
let displayedPrice    = null;  // Φ16-4: last rendered hero price; drives number tick
let _heroTickRaf      = null;  // Φ16-4: RAF handle; cancelled when a new tick starts
let lastForecast      = null;  // Φ16-2: stored for stale-banner re-evaluation on online restore
let lastBacktest      = null;  // cached so applyLanguage() can re-render methodology/track-record without re-fetching
let lastDrift         = null;
let lastCoverage      = null;
let lastCalibration   = null;

// Ψ3C.2: stagger card-enter animation across a list of elements.
// Forces a reflow between remove/add so the animation restarts each time.
function staggerEnter(elements, step = 30) {
  elements.forEach((el, i) => {
    if (!el) return;
    el.classList.remove("card-enter");
    void el.offsetWidth; // reflow triggers animation restart
    el.style.animationDelay = `${i * step}ms`;
    el.classList.add("card-enter");
  });
}

// Φ16-4: count/tick hero price from old value to new (~400ms ease-out cubic).
// JS-driven — must gate on matchMedia explicitly; the global CSS override won't suppress a JS counter.
function animateNumberTick(el, fromVal, toVal, durationMs = 400) {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    el.innerHTML = rupee(toVal);
    return;
  }
  if (_heroTickRaf) { cancelAnimationFrame(_heroTickRaf); _heroTickRaf = null; }
  const start = performance.now();
  const step = (now) => {
    const t      = Math.min((now - start) / durationMs, 1);
    const eased  = 1 - Math.pow(1 - t, 3); // ease-out cubic
    el.innerHTML = rupee(Math.round(fromVal + (toVal - fromVal) * eased));
    if (t < 1) {
      _heroTickRaf = requestAnimationFrame(step);
    } else {
      _heroTickRaf = null;
      el.innerHTML = rupee(toVal); // snap to exact final value
    }
  };
  _heroTickRaf = requestAnimationFrame(step);
}

// No fetch here ever waited on another fetch's result — only render ordering did — but
// with no timeout a single stalled connection could still hang a section on its "Loading…"
// placeholder indefinitely. LOAD_TIMEOUT_MS bounds every fetch so a stall degrades to an
// honest error within a fixed budget instead of hanging forever (render-smoke incident).
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

async function load() {
  const data = await loadJSON(DATA_URL);
  data.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
  return data;
}

// ─── VERDICT ──────────────────────────────────────────────────────────────────

/**
 * Compute a buyer-facing verdict from price history and forecast.
 * Returns { type, headline, reason, icon } where type is:
 *   "down"    — prices falling, no rush
 *   "up"      — prices rising, consider acting sooner
 *   "flat"    — no strong signal
 *   "unknown" — insufficient data
 *
 * THREE-BUCKET RULES (deterministic):
 *
 * TRENDING_DOWN → "Trending down this week"
 *   Condition: 7-day slope < −₹100
 *              AND (forecast below current  OR  current below 30d avg)
 *   Why two signals: a single 7-day slope can be noisy (festival spikes,
 *   weekend data gaps). A confirming signal from forecast or 30d mean
 *   reduces false alarms.
 *
 * TRENDING_UP → "Trending up this week"
 *   Condition: 7-day slope > +₹100
 *              AND (forecast above current  OR  current above 30d avg)
 *
 * FLAT → "Roughly flat this week"  (DEFAULT)
 *   Condition: slope within ±₹100, OR the two signals conflict.
 */
function computeVerdict(prices, forecast) {
  const SLOPE_THRESHOLD = 100; // ₹ change over 7 days to count as a trend

  if (!prices || prices.length < 2) {
    return {
      type: "unknown",
      icon: "○",
      headline: t("verdictHeadlineUnknown"),
      reason: t("verdictReasonUnknown"),
    };
  }

  const now     = Date.now();
  const current = prices[prices.length - 1]["22k"];

  // 7-day slope: oldest reading within the last 7 days vs current.
  const ms7d    = 7 * 24 * 3600 * 1000;
  const within7d = prices.filter(p => now - new Date(p.timestamp).getTime() <= ms7d);
  const ref7d    = within7d.length > 1
    ? within7d[0]
    : prices[Math.max(0, prices.length - 5)];
  const slope7d  = current - ref7d["22k"];

  // 30-day average over IST-day-deduped daily series — one reading per day, latest wins.
  // Deduped so "30-day average" means the avg of 30 daily prices, not time-weighted over ~8
  // readings/day (a flat-held price would otherwise dominate the average).
  const within30d = prices.filter(p => now - new Date(p.timestamp).getTime() <= 30 * 24 * 3600 * 1000);
  const daily30d  = dedupeByISTDay(within30d);
  const avg30d    = daily30d.length > 0
    ? Math.round(daily30d.reduce((s, p) => s + p["22k"], 0) / daily30d.length)
    : current;
  const vsAvg30d  = current - avg30d;

  // Forecast direction (0 if unavailable).
  const forecastDelta = (forecast && typeof forecast.predicted_22k === "number")
    ? forecast.predicted_22k - current
    : 0;

  // ── Classify ──
  if (slope7d < -SLOPE_THRESHOLD && (forecastDelta < 0 || vsAvg30d < 0)) {
    const absDelta = fmtINR(Math.abs(Math.round(slope7d)));
    const avgDelta = vsAvg30d < 0 ? fmtINR(Math.abs(vsAvg30d)) : null;
    return {
      type: "down",
      icon: "↓",
      headline: t("verdictHeadlineDown"),
      reason: t("verdictReasonDown", { delta: absDelta, avgDelta }),
    };
  }

  if (slope7d > SLOPE_THRESHOLD && (forecastDelta > 0 || vsAvg30d > 0)) {
    const delta    = fmtINR(Math.round(slope7d));
    const avgDelta = vsAvg30d > 0 ? fmtINR(Math.abs(vsAvg30d)) : null;
    return {
      type: "up",
      icon: "↑",
      headline: t("verdictHeadlineUp"),
      reason: t("verdictReasonUp", { delta, avgDelta }),
    };
  }

  // Flat — describe magnitude of stability.
  const absSlope = Math.abs(Math.round(slope7d));
  const dirWordKey = slope7d > 0 ? "dirWordUp" : slope7d < 0 ? "dirWordDown" : "dirWordUnchanged";
  const flatReason = absSlope < 20
    ? t("verdictReasonFlatBarely")
    : t("verdictReasonFlatMoved", { dirWord: t(dirWordKey), amount: fmtINR(absSlope) });
  return {
    type: "flat",
    icon: "→",
    headline: t("verdictHeadlineFlat"),
    reason: flatReason,
  };
}

// ─── TODAY'S CHANGE ────────────────────────────────────────────────────────────

// Returns { delta, basis } or null. `basis` is "today" when the change is measured
// against an earlier reading from the SAME IST day, or "since last" when it falls
// back to the previous reading (which — given scrape gaps — may be from yesterday).
// The UI labels the change accordingly so we never call a yesterday-to-now move "today".
function computeTodayChange(readings) {
  if (readings.length < 2) return null;
  const latest   = readings[readings.length - 1];
  const istDay   = (iso) =>
    new Date(iso).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
  const todayKey = istDay(latest.timestamp);

  // Walk backward to find the index of the earliest reading of today (IST).
  let earliestTodayIdx = -1;
  for (let i = readings.length - 1; i >= 0; i--) {
    if (istDay(readings[i].timestamp) === todayKey) {
      earliestTodayIdx = i;
    } else {
      break;
    }
  }

  const earliestToday = earliestTodayIdx >= 0 ? readings[earliestTodayIdx] : null;
  const sinceLast = {
    delta: latest["22k"] - readings[readings.length - 2]["22k"],
    basis: "since last",
  };

  // If today has only one reading or we couldn't find an earlier one, use readings[-2].
  if (!earliestToday || earliestToday === latest) {
    return sinceLast;
  }

  // Sanity guard: if today's first reading differs from the reading immediately before
  // it (yesterday's close) by more than 3%, treat it as a potential scraper anomaly —
  // a bad opening reading would make "today's change" wildly misleading. Fall back to
  // comparing latest against readings[-2] (the reading right before latest), which is
  // recent and not subject to the same day-boundary artifact.
  if (earliestTodayIdx > 0) {
    const prevClose = readings[earliestTodayIdx - 1];
    const pctChange = Math.abs(earliestToday["22k"] - prevClose["22k"]) / prevClose["22k"];
    if (pctChange > 0.03) {
      return sinceLast;
    }
  }

  return { delta: latest["22k"] - earliestToday["22k"], basis: "today" };
}

// ─── COMPARISON CARD VALUES ───────────────────────────────────────────────────

function computeComparisons(readings) {
  if (readings.length === 0) return null;
  const now     = Date.now();
  const current = readings[readings.length - 1]["22k"];
  const avg     = (arr) => Math.round(arr.reduce((s, v) => s + v, 0) / arr.length);
  const p22     = (r) => r["22k"];

  // raw7d/raw30d stay raw so vsLow reflects the actual extreme price reached during the period.
  // prices7d/prices30d are deduped to one reading per IST day so vs-7d/vs-30d avg = avg of daily
  // prices, not time-weighted over ~8 readings/day (a flat-held price repeats ~8×/day and
  // would otherwise dominate the average — see dedupeByISTDay() comment for scope of this rule).
  const raw7d    = readings.filter(r => now - new Date(r.timestamp).getTime() <= 7 * 86400e3);
  const raw30d   = readings.filter(r => now - new Date(r.timestamp).getTime() <= 30 * 86400e3);
  const prices7d  = dedupeByISTDay(raw7d).map(p22);
  const prices30d = dedupeByISTDay(raw30d).map(p22);
  const spanDays  = Math.round((now - new Date(readings[0].timestamp).getTime()) / 86400e3);

  return {
    vs7d:     prices7d.length  > 1 ? current - avg(prices7d)          : null,
    vs30d:    prices30d.length > 1 ? current - avg(prices30d)         : null,
    vsLow:    raw30d.length    > 0 ? current - Math.min(...raw30d.map(p22)) : null,
    spanDays,
  };
}

// ─── GOOD-PRICE SIGNALS ───────────────────────────────────────────────────────
// Computes the two 30-day signals for "Is today a good price?":
//   percentile30d — % of IST-day-deduped daily prices in the last 30 days that
//                   are at/below today's price (distribution-aware position)
//   vsAvg30d      — today's price minus the mean of those 30 daily prices
//
// Returns null when fewer than 5 distinct IST days are available in the 30d window
// (not enough data for a meaningful signal).
// The "90-day band position" signal is deferred until ~90 distinct days accumulate
// (currently 48 days); see Decision Log Φ11-2 band-at-90d revisit trigger.

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
    verdictLead  = t("verdictLeadCheap");
    supportLine1 = t("supportLine1Cheap");
  } else if (percentile30d <= 40) {
    verdictType  = "below-mid";
    verdictLead  = t("verdictLeadBelowMid");
    supportLine1 = t("supportLine1BelowMid");
  } else if (percentile30d <= 70) {
    verdictType  = "mid";
    verdictLead  = t("verdictLeadMid");
    supportLine1 = t("supportLine1Mid");
  } else {
    verdictType  = "high";
    verdictLead  = t("verdictLeadHigh");
    supportLine1 = t("supportLine1High");
  }

  // Unified proof line — consistent frame (cheaper-than / pricier-than), phrased as an
  // actual day count (not a percentage) since a count of real days reads more concretely
  // than an abstract "83%" to a non-technical buyer. Counted directly from prices30d
  // rather than back-derived from the rounded percentile30d, so this never mismatches
  // percentile30d's own rounding.
  const daysCheaperThanToday = prices30d.filter(p => p > current).length;
  const daysPricierThanToday = prices30d.filter(p => p < current).length;
  const proofLine = percentile30d <= 50
    ? t("proofLineCheaper", { days: daysCheaperThanToday, total: nDays30d })
    : t("proofLinePricier", { days: daysPricierThanToday, total: nDays30d });

  // Data-sufficiency degrade note (norm #5) — shown when < 30 distinct days
  const dataSuffNote = nDays30d < 30
    ? t("dataSuffNote", { n: nDays30d })
    : null;

  const absVsAvg = fmtINR(Math.abs(vsAvg30d));
  const supportLine2 = vsAvg30d < 0
    ? t("supportLine2Below", { amount: absVsAvg })
    : vsAvg30d > 0
      ? t("supportLine2Above", { amount: absVsAvg })
      : t("supportLine2At");

  // Divergence: percentile says cheap/low but vs-avg says above average, or vice versa.
  const divergenceNote =
    (percentile30d <= 40 && vsAvg30d > 0) ||
    (percentile30d >= 70 && vsAvg30d < 0)
      ? t("divergenceNote")
      : null;

  return { percentile30d, vsAvg30d, avg30d, nDays30d, verdictLead, verdictType, proofLine, dataSuffNote, supportLine1, supportLine2, divergenceNote };
}

// ─── 90-DAY BAND POSITION (Φ11-2 revisit trigger) ─────────────────────────────
// Deferred at Φ11-2 pending ~90 distinct IST days (48 at the time — too much overlap
// with the 30-day window to be an independent read). That threshold is met as of
// 2026-07-17. This is a SUPPORTING line only — it never changes the 30-day verdict
// hierarchy above (verdictLead/verdictType/proofLine) — and always names its own
// 90-day window + day count so it can't be mistaken for the 30-day read.
//
// Returns null below MIN_DAYS_90D (not enough history for a 90-day comparison to be
// meaningfully different from the 30-day one). Below FULL_DAYS_90D, appends an
// honest data-sufficiency caveat inline rather than hiding the line — same
// graceful-degrade pattern as the 30-day dataSuffNote above.
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
    ? t("band90dCheaper", { pct: 100 - percentile90d, n: nDays90d })
    : t("band90dMoreExpensive", { pct: percentile90d, n: nDays90d });
  if (nDays90d < FULL_DAYS_90D) {
    note += t("band90dSuffAppend", { n: nDays90d });
  }

  return { percentile90d, nDays90d, note };
}

// ─── 30-DAY TREND RESIDUAL (audit finding, 2026-07-18) ────────────────────────
// The 30-day percentile above is a pure range position — it cannot tell "cheap
// and still falling" from "cheap and stabilizing". Confirmed on the 2026-06
// selloff: percentile30d read "cheap" (3-20) for ~15 straight sessions
// (2026-06-10 to 06-25) while the price kept dropping, falsified the next day
// every time. This fits a Theil-Sen (median-of-pairwise-slopes) line over the
// same 30-day window — robust to the odd promotional-price outlier, unlike
// OLS — and reports today's price as a robust z-score residual off that line:
// strongly negative means today is still falling away from its own recent
// trend; near zero or positive means the price has leveled off or turned back
// toward/above it.
//
// A SUPPORTING line only, mirroring computeBandPos90d: self-labeling, gracefully
// degrades to null on thin data, and never changes the verdict hierarchy
// (verdictLead/verdictType/proofLine) in computeGoodPriceSignals. Descriptive,
// not predictive — it describes where today sits relative to the recent trend,
// it does not forecast tomorrow's move.
//
// Returns null below MIN_DAYS_TREND (Theil-Sen needs enough points for the
// median-of-slopes and residual-MAD estimates to be stable, not noise).
const MIN_DAYS_TREND = 10;
const FLAT_SLOPE_INR_PER_DAY = 5; // |slope| below this reads as "flattened out"
const CHEAP_PERCENTILE_MAX = 40;  // matches computeGoodPriceSignals' below-mid cutoff
const STILL_FALLING_Z = -1;       // residZ below this reads as "not yet stabilized"

function theilSenFit(points) {
  // points: [{x, y}]. Median of all pairwise slopes, then median residual as
  // the intercept — the standard robust (Theil-Sen) line fit.
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
  const robustStd = 1.4826 * mad; // normal-consistent scale of the MAD

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
    note = t("trendCheapStillFalling", { slope: slopeAbs });
  } else if (isCheap) {
    note = t("trendCheapSteadying");
  } else if (trendState === "falling") {
    note = t("trendFalling", { slope: slopeAbs });
  } else if (trendState === "rising") {
    note = t("trendRising", { slope: slopeAbs });
  } else {
    note = t("trendFlat");
  }

  return { slope, residual, residZ, trendState, nDays, note };
}

// ─── DISTANCE TO 90-DAY SUPPORT (audit finding, 2026-07-18) ───────────────────
// The trend-residual line above measures deviation from the recent 30-day slope —
// it cannot distinguish "falling away from trend but still mid-range" from
// "falling away from trend AND sitting on the actual 90-day floor". Confirmed on
// real history: 2026-05-28 (residZ -2.71, price 4.6% above its 90-day low) and
// 2026-06-19 (residZ -2.13, price 0.15% above its 90-day low) carry near-identical
// trend-residual readings but describe different situations — one mid-range and
// falling, the other testing its own floor. Correlation between residZ and this
// distance across the real prices.json history: r≈0.48 (90-day window) —
// moderate, not redundant (scoped 2026-07-18 before building).
//
// Support here is descriptive, not predictive: the plain trailing-90-day low, no
// smoothing or local-minima detection — the simplest construction that stays
// stable day to day and reads as one sentence ("N% above its 3-month low"). A
// SUPPORTING line only, mirroring computeBandPos90d: self-labeling, gracefully
// degrades to null on thin data, never changes the verdict hierarchy
// (verdictLead/verdictType/proofLine) in computeGoodPriceSignals.
//
// Returns null below MIN_DAYS_SUPPORT (need enough of the 90-day window filled
// in for "recent floor" to mean more than the last few readings).
const MIN_DAYS_SUPPORT = 60;
const FULL_DAYS_SUPPORT = 90;
const NEAR_SUPPORT_PCT = 2; // within this % of the 90-day low reads as "at" support

function computeSupportDistance90d(readings, percentile30d) {
  if (!readings || readings.length < 2) return null;

  const now     = Date.now();
  const current = readings[readings.length - 1]["22k"];

  const within90d = readings.filter(
    r => now - new Date(r.timestamp).getTime() <= 90 * 86400e3,
  );
  const daily90d = dedupeByISTDay(within90d);
  const nDays = daily90d.length;
  if (nDays < MIN_DAYS_SUPPORT) return null;

  const low90d      = Math.min(...daily90d.map(r => r["22k"]));
  const distPct     = ((current - low90d) / low90d) * 100;
  const nearSupport = distPct <= NEAR_SUPPORT_PCT;
  const isCheap      = typeof percentile30d === "number" && percentile30d <= CHEAP_PERCENTILE_MAX;

  let note;
  if (isCheap && nearSupport) {
    note = t("supportCheapAtSupport", { low: fmtINR(low90d), n: nDays });
  } else if (isCheap) {
    note = t("supportCheapNotAtSupport", { pct: distPct.toFixed(1), low: fmtINR(low90d) });
  } else if (nearSupport) {
    note = t("supportNotCheapAtSupport", { low: fmtINR(low90d) });
  } else {
    note = t("supportNotCheapNotAtSupport", { pct: distPct.toFixed(1), low: fmtINR(low90d), n: nDays });
  }
  if (nDays < FULL_DAYS_SUPPORT) {
    note += t("supportSuffAppend", { n: nDays });
  }

  return { distPct, low90d, nDays, note };
}

// Typical week-over-week price movement, purely historical — distinct from
// headline.vol_context's 5-day RECENT realized-vol estimate (computed server-side
// in ml/volatility.py from just the last 20 days, feeding the "moving about ±₹X
// over 5 days lately" note below). This one looks back further (90 days) and asks
// a different question: not "how choppy has it been lately" but "if I wait a
// week, how much has the price actually tended to move, historically". Answers
// "is waiting worth it?" without predicting anything — every comparison is
// (price today) vs (price exactly 7 calendar days earlier), median of the
// absolute differences. Median, not mean, matching this file's existing
// preference for robust-over-outlier-sensitive stats (see computeTrendResidual30d's
// own robustStd). Same MIN/FULL day-window gating convention as
// computeSupportDistance90d above.
const MIN_DAYS_MOVEMENT  = 60;
const FULL_DAYS_MOVEMENT = 90;

function computeWeeklyMovement(readings) {
  if (!readings || readings.length < 2) return null;

  const now = Date.now();
  const within90d = readings.filter(
    r => now - new Date(r.timestamp).getTime() <= FULL_DAYS_MOVEMENT * 86400e3,
  );
  const daily = dedupeByISTDay(within90d);
  const nDays = daily.length;
  if (nDays < MIN_DAYS_MOVEMENT) return null;

  // Pure UTC-millisecond arithmetic (not Date.setDate/getDate, which operate in
  // the browser's local timezone) so "7 days ago" means the same thing
  // regardless of the visitor's own timezone — only istDayKey's own IST
  // timeZone option determines which calendar day a timestamp falls on.
  const byDayKey = new Map(daily.map(r => [istDayKey(new Date(r.timestamp)), r["22k"]]));
  const diffs = [];
  for (const r of daily) {
    const weekAgo = new Date(new Date(r.timestamp).getTime() - 7 * 86400000);
    const priorPrice = byDayKey.get(istDayKey(weekAgo));
    if (priorPrice != null) diffs.push(Math.abs(r["22k"] - priorPrice));
  }
  if (diffs.length === 0) return null;

  diffs.sort((a, b) => a - b);
  const mid = Math.floor(diffs.length / 2);
  const median = diffs.length % 2 === 0 ? (diffs[mid - 1] + diffs[mid]) / 2 : diffs[mid];

  let note = t("weeklyMovementNote", { amount: fmtINR(Math.round(median)), pairs: diffs.length });
  if (nDays < FULL_DAYS_MOVEMENT) {
    note += t("weeklyMovementSuffAppend", { n: nDays });
  }

  return { median, pairs: diffs.length, nDays, note };
}

// Recent-vs-historical forecast error ratio, from drift_metrics.json — shared by the
// promoted reliability note (model-signal-section) and the methodology accordion's
// detailed drift stats, so the two never state a different number for the same
// underlying data. Raw (unrounded) numbers returned; each caller formats/rounds at
// its own display site, matching this file's existing convention elsewhere.
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

// ─── PURCHASE COST ESTIMATE ───────────────────────────────────────────────────
// Itemised "what will it cost me?" estimate for a gold jewellery purchase, the
// way an Indian retail invoice is built up:
//   gold value = ratePerGram × grams
//   making     = gold value × makingPct/100   (making charges — design-dependent,
//                                               jeweller-specific; the caller supplies it)
//   GST        = (gold value + making) × gstPct/100   (3% on gold jewellery in India)
//   total      = gold value + making + GST
//
// No making-charge default is baked in here (it varies far too widely by design
// to assume) — makingPct defaults to 0 so the bare metal+GST figure is the floor;
// the UI layer owns the user-entered default and its framing. gstPct defaults to
// the current 3% India rate. Returns null on any invalid (non-finite / negative)
// input so the caller can show a neutral empty state rather than NaN.
function computePurchaseCost({ ratePerGram, grams, makingPct = 0, gstPct = 3 }) {
  const vals = [ratePerGram, grams, makingPct, gstPct];
  if (!vals.every(Number.isFinite)) return null;
  if (ratePerGram < 0 || grams < 0 || makingPct < 0 || gstPct < 0) return null;

  const goldValue = ratePerGram * grams;
  const making = goldValue * (makingPct / 100);
  const gst = (goldValue + making) * (gstPct / 100);
  const total = goldValue + making + gst;

  return {
    goldValue: Math.round(goldValue),
    making: Math.round(making),
    gst: Math.round(gst),
    total: Math.round(total),
  };
}

// ─── RENDERERS ────────────────────────────────────────────────────────────────

// IST calendar-day key, matching dedupeByISTDay's convention.
function istDayKey(d) {
  return d.toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
}

// Weekday name in the active UI language — e.g. "Friday" / "शुक्रवार" — via CLDR data,
// not a lookup table. Only used for display (banner/pill text); istDayKey above stays
// en-IN unconditionally since it's purely an internal grouping key, never shown.
function weekdayLong(d) {
  const locale = currentLang === "hi" ? "hi-IN" : "en-IN";
  return d.toLocaleDateString(locale, { weekday: "long", timeZone: "Asia/Kolkata", numberingSystem: "latn" });
}

function renderStaleBanner(forecast, calibration) {
  const banner = document.getElementById("stale-banner");
  if (!banner) return;
  // Offline banner takes precedence — "Offline" already explains staleness; don't stack both.
  const offlineBanner = document.getElementById("offline-banner");
  if (offlineBanner && !offlineBanner.hidden) return;
  // Always reset first so a refresh-error or prior stale message is cleared on success.
  banner.hidden = true;
  if (!forecast) return;

  // Per ADR 025, IBJA-calibrated is now the PRIMARY display path (Tanishq not
  // enriching this cycle is the expected steady state, not an error) — trust
  // inference.py's price_source gate rather than re-deriving freshness here.
  if (forecast.price_source === "ibja_calibrated" && forecast.ibja_asof) {
    const ibjaDate = new Date(forecast.ibja_asof);
    const isToday  = istDayKey(ibjaDate) === istDayKey(new Date());
    banner.textContent = isToday
      ? t("bannerIbjaToday")
      : t("bannerIbjaCarryForward", { weekday: weekdayLong(ibjaDate) });
    // Scoped to this banner only — calibration.json's residual numbers measure
    // exactly this estimation mechanism (IBJA→Tanishq), not fusion_consensus's
    // separate multi-source-disagreement math below, so appending this note
    // anywhere else would cite a number that doesn't apply to that estimate.
    // Prefer residual_std_oos (out-of-sample, walk-forward — see
    // ml/calibration.py's walk_forward_validate()) over the in-sample
    // residual_std, matching ml/inference.py's own _try_ibja_calibrated()
    // fallback order for the est_low/est_high band width, so this sentence
    // and that band are always describing the same confidence.
    const confidence = calibration?.residual_std_oos ?? calibration?.residual_std;
    if (typeof confidence === "number") {
      banner.textContent += t("calibrationConfidenceAppend", { amount: fmtINR(Math.round(confidence)) });
    }
    banner.hidden = false;
    return;
  }

  // Tier 3: both Tanishq and IBJA unavailable this cycle — live GRT/Malabar/
  // Kalyan consensus (ADR 026) is the only estimate available.
  if (forecast.price_source === "fusion_consensus") {
    banner.textContent = t("bannerFusion", { sources: fusionSourcesLabel(forecast.fusion_sources) });
    banner.hidden = false;
    return;
  }

  // Tanishq path: fresh scrape stays silent; genuinely stale (IBJA also
  // unavailable/too old) falls to the honest last-confirmed-price state.
  if (!forecast.scraped_at) return;
  // Use the fresher of forecast.scraped_at and the latest raw-price reading's
  // timestamp. ml.inference runs continue-on-error in check-price.yml, so it
  // can fail independently of the scrape step — leaving forecast.json (and
  // forecast.scraped_at) stale while prices.json keeps updating. Without this
  // fallback, this banner could show "unavailable" while the freshness pill
  // (which reads prices.json directly on this same Tanishq path) shows "ok".
  let scrapedAtMs = new Date(forecast.scraped_at).getTime();
  if (allReadings.length > 0) {
    const latestReadingMs = new Date(allReadings[allReadings.length - 1].timestamp).getTime();
    if (latestReadingMs > scrapedAtMs) scrapedAtMs = latestReadingMs;
  }
  const scrapeAgeH = (Date.now() - scrapedAtMs) / 3_600_000;
  if (scrapeAgeH <= STALE_THRESHOLD_H) return; // scraped-fresh — banner stays hidden

  banner.textContent = t("bannerStaleConfirmed", { rel: fmtRelative(forecast.scraped_at) });
  banner.hidden = false;
}

function renderFreshness(readings, forecast) {
  const pill = document.getElementById("freshness-pill");
  if (!pill) return;
  pill.classList.remove("freshness--ok", "freshness--warn", "freshness--stale");

  // Per ADR 025: reflect whichever source is actually driving the displayed
  // price, not Tanishq's scrape recency alone — Tanishq being stale is now the
  // expected steady state and must not read as a failure when IBJA is healthy.
  if (forecast && forecast.price_source === "ibja_calibrated" && forecast.ibja_asof) {
    const ibjaDate = new Date(forecast.ibja_asof);
    const isToday  = istDayKey(ibjaDate) === istDayKey(new Date());
    const rel      = fmtRelative(forecast.ibja_asof);
    if (isToday) {
      pill.className   = "freshness-pill freshness--ok";
      pill.textContent = t("freshnessEstimated", { rel });
      pill.setAttribute("aria-label", t("freshnessEstimatedAria", { rel }));
    } else {
      const dayLabel = weekdayLong(ibjaDate);
      pill.className   = "freshness-pill freshness--warn";
      pill.textContent = t("freshnessAsOfClose", { weekday: dayLabel });
      pill.setAttribute("aria-label", t("freshnessAsOfCloseAria", { weekday: dayLabel }));
    }
    return;
  }

  // Tier 3: both Tanishq and IBJA unavailable this cycle — same branch order
  // and same forecast.price_source values as renderStaleBanner, so the pill
  // and banner can never disagree about which state is active (PR #237).
  if (forecast && forecast.price_source === "fusion_consensus") {
    const rel = fmtRelative(forecast.predicted_at);
    pill.className   = "freshness-pill freshness--warn";
    pill.textContent = t("freshnessConsensus", { rel });
    pill.setAttribute("aria-label", t("freshnessConsensusAria", { rel }));
    return;
  }

  if (readings.length === 0) {
    pill.textContent = t("freshnessAwaiting");
    pill.className   = "freshness-pill";
    return;
  }
  const latest = readings[readings.length - 1];
  const ageH   = (Date.now() - new Date(latest.timestamp).getTime()) / 3_600_000;
  const rel    = fmtRelative(latest.timestamp);
  if (ageH >= 18) {
    pill.className   = "freshness-pill freshness--stale";
    pill.textContent = t("freshnessNotUpdating", { rel });
    pill.setAttribute("aria-label", t("freshnessNotUpdatingAria", { rel }));
  } else if (ageH >= 8) {
    pill.className   = "freshness-pill freshness--warn";
    pill.textContent = t("freshnessStale", { rel });
    pill.setAttribute("aria-label", t("freshnessStaleAria", { rel }));
  } else {
    pill.className   = "freshness-pill freshness--ok";
    pill.textContent = rel;
    pill.setAttribute("aria-label", t("freshnessOkAria", { rel }));
  }

  // D5: Auto-open iOS help panel when standalone + data is ≥ 12h stale,
  // but only if the user hasn't dismissed it this session (FIX 1). Only
  // reachable here (IBJA not serving an estimate) — i.e. a genuine outage.
  if (IS_STANDALONE && ageH >= 12 && !pwaHelpDismissed) {
    const panel = document.getElementById("pwa-help-panel");
    if (panel) panel.hidden = false;
  }
}

// ─── OFFLINE BANNER (Φ16) ────────────────────────────────────────────────────
// Called on init, on offline/online events, and after each successful refresh.
// Offline takes precedence over #stale-banner: the user doesn't need both.
function updateOfflineBanner() {
  const offlineBanner = document.getElementById("offline-banner");
  const staleBanner   = document.getElementById("stale-banner");
  if (!offlineBanner) return;

  if (!navigator.onLine) {
    const rel = allReadings.length > 0
      ? fmtRelative(allReadings[allReadings.length - 1].timestamp)
      : null;
    offlineBanner.textContent = rel
      ? t("offlineWithTime", { rel })
      : t("offlineNoData");
    offlineBanner.hidden = false;
    if (staleBanner) staleBanner.hidden = true;
  } else {
    offlineBanner.hidden = true;
  }
}

function renderHero(readings, forecast) {
  const skelEl        = document.getElementById("hero-skeleton");
  const eyeEl         = document.getElementById("hero-eyebrow");
  const priceEl       = document.getElementById("hero-price");
  const rangeEl       = document.getElementById("hero-estimate-range");
  const lastConfEl    = document.getElementById("hero-last-confirmed");
  const changeEl      = document.getElementById("hero-change");
  const verdictEl     = document.getElementById("verdict-banner");

  if (skelEl) skelEl.hidden = true;
  if (eyeEl)  eyeEl.hidden  = false;
  const locEl = document.getElementById("hero-location");
  if (locEl) locEl.hidden = false;

  if (readings.length === 0) {
    priceEl.innerHTML = "—"; // XSS-safe: static literal string, no external data
    priceEl.hidden    = false;
    if (rangeEl) rangeEl.hidden = true;
    if (lastConfEl) lastConfEl.hidden = true;
    if (verdictEl) {
      document.getElementById("verdict-icon").textContent    = "○";
      document.getElementById("verdict-headline").textContent = t("verdictHeadlineUnknown");
      document.getElementById("verdict-reason").textContent  = t("heroFallbackReason");
      verdictEl.dataset.type = "unknown";
      verdictEl.hidden       = false;
    }
    return;
  }

  const latest    = readings[readings.length - 1];
  const newPrice  = latest["22k"];
  const prevPrice = displayedPrice; // capture before update — animateNumberTick uses this as fromVal

  // ibja_calibrated (tier 2) and fusion_consensus (tier 3) render identically here
  // — the distinguishing honest labeling lives in the banner/pill (renderStaleBanner/
  // renderFreshness), not duplicated a third time in the hero itself.
  const isEstimateTier = forecast && (
    forecast.price_source === "ibja_calibrated" || forecast.price_source === "fusion_consensus"
  ) && forecast.current_22k != null && forecast.est_low != null && forecast.est_high != null;

  if (isEstimateTier) {
    // Estimate tier (IBJA-calibrated or fusion-consensus) — bounded range still
    // shown (ADR 021 §4), but as a small secondary line below the hero, not
    // jammed into the headline itself.
    // The point estimate AND the range crammed into one giant number reads as
    // garbled/stale at a glance; the ≈ prefix plus the stale-banner already signal
    // "estimate" without a third hedge competing for attention in the headline.
    // XSS-safe: rupee()/fmtINR wrap numbers only; all values are integers from forecast.json.
    displayedPrice = forecast.current_22k;
    priceEl.innerHTML = `≈ ${rupee(forecast.current_22k)}`;
    priceEl.hidden = false;
    if (rangeEl) {
      rangeEl.textContent = t("heroEstimatedRange", { low: fmtINR(forecast.est_low), high: fmtINR(forecast.est_high) });
      rangeEl.hidden = false;
    }
    // Honest secondary line: the actual last-observed Tanishq reading, dated —
    // never implied current. prices.json holds only genuine scraped Tanishq
    // readings (never IBJA/estimate data), so `latest` here is always a real
    // observation; this line naturally shows the freshest one once a scrape
    // succeeds again (no separate "reachable again" wiring needed — same data,
    // same render path, whatever `latest` currently is).
    if (lastConfEl) {
      lastConfEl.textContent = t("heroLastConfirmed", { price: fmtINR(newPrice), date: fmtDateShort(latest.timestamp) });
      lastConfEl.hidden = false;
    }
  } else {
    displayedPrice = newPrice;
    if (rangeEl) rangeEl.hidden = true;
    // Hero already IS the last-confirmed Tanishq reading here — a secondary
    // line repeating it would be pure noise, not honesty.
    if (lastConfEl) lastConfEl.hidden = true;
    // Φ16-4: tick when price changes on a live refresh; first render and no-change case are instant.
    // priceEl.hidden guard: element hidden means skeleton is still showing — don't animate there.
    if (prevPrice !== null && prevPrice !== newPrice && !priceEl.hidden) {
      animateNumberTick(priceEl, prevPrice, newPrice);
    } else {
      // XSS-safe: rupee() wraps a number with fmtINR (toLocaleString); numbers cannot contain HTML
      priceEl.innerHTML = rupee(newPrice);
    }
    priceEl.hidden = false;
  }

  // Other karat prices — same as above, rupee() on a number is injection-proof
  const r24 = document.getElementById("rate-24");
  const r18 = document.getElementById("rate-18");
  if (r24) r24.innerHTML = rupee(latest["24k"]);
  if (r18) r18.innerHTML = rupee(latest["18k"]);

  // Today's change
  const change = computeTodayChange(readings);
  if (change !== null) {
    const todayDelta = change.delta;
    const dir    = todayDelta > 0 ? "up" : todayDelta < 0 ? "down" : "flat";
    const arrow  = dir === "up" ? "↑" : dir === "down" ? "↓" : "→";
    const sign   = dir === "up" ? "+" : dir === "down" ? "−" : "";
    changeEl.dataset.direction = dir;
    changeEl.querySelector(".hero-change-arrow").textContent  = arrow;
    changeEl.querySelector(".hero-change-amount").textContent =
      todayDelta === 0 ? t("noChangeLabel") : `${sign}₹${fmtINR(Math.abs(todayDelta))}`;
    // Honest label: "today" only when measured within the same IST day; otherwise
    // "since last" (the prior reading may be from yesterday when scrapes have gapped).
    const labelEl = changeEl.querySelector(".hero-change-label");
    if (labelEl) labelEl.textContent = t(change.basis === "today" ? "todayLabel" : "sinceLastLabel");
    changeEl.hidden = false;
  }

  // Verdict
  const verdict = computeVerdict(readings, forecast);
  document.getElementById("verdict-icon").textContent    = verdict.icon;
  document.getElementById("verdict-headline").textContent = verdict.headline;
  document.getElementById("verdict-reason").textContent  = verdict.reason;
  verdictEl.dataset.type = verdict.type;
  verdictEl.hidden       = false;

  renderSparkline(readings);
}

// ─── PURCHASE CALCULATOR ────────────────────────────────────────────────────
// UI for computePurchaseCost() (defined above) — grams + optional making-charge
// input, three karat totals. Honesty rule: the 22K figure uses exactly the same
// rate and the same isEstimateTier gate renderHero() uses for the hero price
// (recomputed here rather than shared via a module var, matching this file's
// existing per-render-function style) -- when the hero price shows "≈", the
// calculator's 22K total carries the same "≈" and the same estimated-price
// note, so a buyer never sees a confident-looking total built on an estimate.
// 24K/18K always come from the last real Tanishq reading (latest["24k"/"18k"])
// -- same as the karat-strip cards above, which never show "≈" either; this
// app has never estimated 24K/18K independently, only 22K, so inventing an
// estimate qualifier for them here would claim more than the data supports.
const CALC_GST_PCT = 3; // India's GST rate on gold jewellery — matches computePurchaseCost's own default

function renderCalculator(readings, forecast) {
  const skelEl    = document.getElementById("calc-skeleton");
  const resultsEl = document.getElementById("calc-results");
  const gramsEl   = document.getElementById("calc-grams");
  const makingEl  = document.getElementById("calc-making");
  if (!resultsEl || !gramsEl || !makingEl) return;

  if (!readings || readings.length === 0) {
    if (skelEl) skelEl.hidden = false;
    resultsEl.innerHTML = "";
    return;
  }
  if (skelEl) skelEl.hidden = true;

  const latest = readings[readings.length - 1];
  const isEstimateTier = forecast && (
    forecast.price_source === "ibja_calibrated" || forecast.price_source === "fusion_consensus"
  ) && forecast.current_22k != null;

  const rate22 = isEstimateTier ? forecast.current_22k : latest["22k"];
  const rate24 = latest["24k"];
  const rate18 = latest["18k"];

  const grams     = parseFloat(gramsEl.value);
  const makingPct = parseFloat(makingEl.value);

  // grams === 0 is valid input to computePurchaseCost() (returns an all-zero
  // result, not null) -- but a ₹0 total reads as broken, not "you haven't
  // entered anything yet". Treat <= 0 as the empty state explicitly.
  const c22 = grams > 0 ? computePurchaseCost({ ratePerGram: rate22, grams, makingPct, gstPct: CALC_GST_PCT }) : null;
  const c24 = grams > 0 ? computePurchaseCost({ ratePerGram: rate24, grams, makingPct, gstPct: CALC_GST_PCT }) : null;
  const c18 = grams > 0 ? computePurchaseCost({ ratePerGram: rate18, grams, makingPct, gstPct: CALC_GST_PCT }) : null;

  if (!c22 || !c24 || !c18) {
    // XSS-safe: t() returns a catalogue literal only.
    resultsEl.innerHTML = `<p class="calc-empty">${t("calcEmptyState")}</p>`;
    return;
  }

  // XSS-safe: every interpolated value is either fmtINR(number) or a t()
  // catalogue literal — no external data reaches this template.
  resultsEl.innerHTML = `
    <div class="calc-result-card">
      <div class="calc-result-karat">${isEstimateTier ? "≈ " : ""}${t("calcKaratLabel22")}</div>
      <div class="calc-result-row"><span>${t("calcRowGoldValue")}</span><span>₹${fmtINR(c22.goldValue)}</span></div>
      ${c22.making > 0 ? `<div class="calc-result-row"><span>${t("calcRowMaking")}</span><span>₹${fmtINR(c22.making)}</span></div>` : ""}
      <div class="calc-result-row"><span>${t("calcRowGst", { pct: CALC_GST_PCT })}</span><span>₹${fmtINR(c22.gst)}</span></div>
      <div class="calc-result-row calc-result-row--total"><span>${t("calcRowTotal")}</span><span>₹${fmtINR(c22.total)}</span></div>
      ${isEstimateTier ? `<p class="calc-estimated-note">${t("calcEstimatedNote")}</p>` : ""}
    </div>
    <p class="calc-other-karats">${t("calcOtherKarats", { k24: fmtINR(c24.total), k18: fmtINR(c18.total) })}</p>
  `;
}

function bindCalculatorInputs() {
  const gramsEl  = document.getElementById("calc-grams");
  const makingEl = document.getElementById("calc-making");
  if (!gramsEl || !makingEl) return;
  const onInput = () => renderCalculator(allReadings, lastForecast);
  gramsEl.addEventListener("input", onInput);
  makingEl.addEventListener("input", onInput);
}

function renderSparkline(readings) {
  const wrap    = document.getElementById("sparkline-wrap");
  const svgEl   = document.getElementById("sparkline");
  const rangeEl = document.getElementById("sparkline-range");

  const now  = Date.now();
  const pts  = dedupeByISTDay(readings.filter(r => now - new Date(r.timestamp).getTime() <= 7 * 86400e3));
  if (pts.length < 2) { wrap.hidden = true; return; }

  const prices = pts.map(p => p["22k"]);
  const min22k = Math.min(...prices);
  const max22k = Math.max(...prices);
  const span   = max22k - min22k || 1;

  const W = 300, H = 56, PX = 2, PY = 6;
  const toX = (i) => PX + (i / (prices.length - 1)) * (W - 2 * PX);
  const toY = (p) => PY + (1 - (p - min22k) / span) * (H - 2 * PY);

  const coords   = prices.map((p, i) => `${toX(i).toFixed(1)},${toY(p).toFixed(1)}`);
  const firstX   = coords[0].split(",")[0];
  const lastX    = coords[coords.length - 1].split(",")[0];
  const fillPath = `M ${coords[0]} L ${coords.slice(1).join(" L ")} L ${lastX},${H} L ${firstX},${H} Z`;

  const trendDown = prices[prices.length - 1] <= prices[0];
  const lineClr   = trendDown ? "#6a9a72" : "#c66a4b";
  const fillClr   = trendDown ? "rgba(106,154,114,0.18)" : "rgba(198,106,75,0.15)";
  const netChange = prices[prices.length - 1] - prices[0];

  svgEl.setAttribute("aria-label", t("sparklineAria", {
    dir: trendDown ? t("trendDirDown") : t("trendDirUp"),
    delta: fmtINR(Math.abs(Math.round(netChange))),
  }));
  // XSS-safe: all interpolated values are derived from numeric price data
  // (coords = toFixed(1) floats, fillClr/lineClr = hardcoded rgba/hex literals).
  svgEl.innerHTML = `
    <path d="${fillPath}" fill="${fillClr}" stroke="none"/>
    <polyline points="${coords.join(" ")}" fill="none" stroke="${lineClr}"
              stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>
  `;

  rangeEl.textContent = t("sparklineRange", { min: fmtINR(min22k), max: fmtINR(max22k) });
  wrap.hidden = false;
}

function renderComparisons(readings) {
  const section = document.getElementById("comparison-section");
  const cmp     = computeComparisons(readings);
  if (!cmp || readings.length < 2) { section.hidden = true; return; }

  function setCard(valueId, subId, cardId, delta, avgLabel) {
    const valEl  = document.getElementById(valueId);
    const subEl  = document.getElementById(subId);
    const card   = document.getElementById(cardId);
    if (delta === null) {
      valEl.textContent        = "—";
      subEl.textContent        = t("cmpNotEnoughData");
      card.dataset.sentiment   = "neutral";
      return;
    }
    const abs = fmtINR(Math.abs(delta));
    if (delta < 0) {
      valEl.textContent      = `−₹${abs}`;
      subEl.textContent      = t("cmpCheaperThan", { avgLabel });
      card.dataset.sentiment = "good";
    } else if (delta > 0) {
      valEl.textContent      = `+₹${abs}`;
      subEl.textContent      = t("cmpPricierThan", { avgLabel });
      card.dataset.sentiment = "caution";
    } else {
      valEl.textContent      = t("cmpAtAvg");
      subEl.textContent      = avgLabel;
      card.dataset.sentiment = "neutral";
    }
  }

  setCard("cmp-7d-value",  "cmp-7d-sub",  "cmp-7d",  cmp.vs7d,  t("avgLabel7d"));
  setCard("cmp-30d-value", "cmp-30d-sub", "cmp-30d", cmp.vs30d, t("avgLabel30d"));

  const lowVal  = document.getElementById("cmp-low-value");
  const lowSub  = document.getElementById("cmp-low-sub");
  const lowCard = document.getElementById("cmp-low");
  if (cmp.vsLow === null) {
    lowVal.textContent        = "—";
    lowSub.textContent        = "";
    lowCard.dataset.sentiment = "neutral";
  } else if (cmp.vsLow === 0) {
    lowVal.textContent        = t("cmpAtLow");
    lowSub.textContent        = t("cmpLowestPrice");
    lowCard.dataset.sentiment = "good";
  } else {
    lowVal.textContent        = `+₹${fmtINR(cmp.vsLow)}`;
    lowSub.textContent        = t("cmpAboveLowest");
    lowCard.dataset.sentiment = cmp.vsLow < 300 ? "neutral" : "caution";
  }

  section.hidden = false;
}

// ─── TODAY'S READ ────────────────────────────────────────────────────────────
// Was previously the Groq-generated commentary.json blurb, which — per audit —
// only restated numbers already shown elsewhere on the page (price, 24K/18K)
// rather than telling the buyer anything they could act on. Replaced with a
// deterministic sentence composed client-side from signals already computed
// for the good-price card below (computeGoodPriceSignals/computeTrendResidual30d)
// — same inputs, same trust surface, no LLM call, and no risk of duplicating
// content the good-price card states more precisely a few lines down. Purely
// qualitative (no ₹/% figures) so it reads as a plain-language headline for
// the numbers that follow, not a repeat of them. Descriptive only — never
// implies a future direction (DARK gate, same as computeVerdict/driver context).
//
// commentary.json / the Groq generation step this replaces is now unused by the
// frontend — left in place rather than deleted here since retiring the pipeline
// step itself is out of this session's scope (layout/copy only, no ml/ changes).
function composeTodaysRead(readings) {
  const signals = computeGoodPriceSignals(readings ?? []);
  if (!signals) {
    return t("readNoSignals");
  }

  const isCheap = signals.verdictType === "cheap" || signals.verdictType === "below-mid";
  const isHigh  = signals.verdictType === "high";
  const trend   = computeTrendResidual30d(readings ?? [], signals.percentile30d);

  if (!trend) {
    if (isCheap) return t("readNoTrendCheap");
    if (isHigh)  return t("readNoTrendHigh");
    return t("readNoTrendMid");
  }

  const { trendState, residZ } = trend;
  if (isCheap && trendState === "falling" && residZ < STILL_FALLING_Z) {
    return t("readCheapStillFalling");
  }
  if (isCheap) {
    return t("readCheapSteadying");
  }
  if (isHigh && trendState === "rising") {
    return t("readHighRising");
  }
  if (isHigh) {
    return t("readHighSlowed");
  }
  if (trendState === "falling") {
    return t("readFalling");
  }
  if (trendState === "rising") {
    return t("readRising");
  }
  return t("readFlat");
}

function renderTodaysRead(readings) {
  const textEl = document.getElementById("commentary-text");
  const metaEl = document.getElementById("commentary-meta");
  if (!textEl) return;

  const skelEl = document.getElementById("commentary-skeleton");
  if (skelEl) skelEl.hidden = true;
  textEl.hidden = false;
  // No separate staleness concept — this is computed fresh from the same
  // readings/forecast every render, unlike the old LLM blurb it replaced.
  if (metaEl) metaEl.hidden = true;

  textEl.textContent = composeTodaysRead(readings);
}

function computeTrendDescription(readings, nDays = 7) {
  if (!readings || readings.length < 2) return null;
  const cutoff = Date.now() - nDays * 86400 * 1000;
  const recent = readings.filter(r => new Date(r.timestamp).getTime() >= cutoff);
  if (recent.length < 2) return null;
  const first = recent[0]["22k"];
  const last  = readings[readings.length - 1]["22k"];
  const delta = last - first;
  const abs   = Math.abs(delta);
  if (abs < 100) return `Roughly flat over the past ${nDays} days`;
  const dir  = delta > 0 ? "up" : "down";
  const sign = delta > 0 ? "+" : "−";
  return `Trending ${dir} — ${sign}₹${fmtINR(abs)} over the past ${nDays} days`;
}

function renderModelSignal(fc, readings, bt, coverage, drift) {
  const section = document.getElementById("model-signal-section");
  if (!section) return;

  const skelEl = document.getElementById("model-signal-skeleton");
  if (skelEl) skelEl.hidden = true;

  const signals = computeGoodPriceSignals(readings ?? []);
  if (!signals) {
    section.hidden = true;
    return;
  }
  const bandPos90d = computeBandPos90d(readings ?? []);
  const trendResidual = computeTrendResidual30d(readings ?? [], signals.percentile30d);
  const supportDistance90d = computeSupportDistance90d(readings ?? [], signals.percentile30d);

  const hl = fc?.headline;
  const hasPI = hl && typeof hl.lower === "number" && typeof hl.upper === "number";

  // Tomorrow's likely range, plain language — the actual next-trading-day conformal
  // band (same source as methodology's "Next trading day range" — hl.lower/upper,
  // with the same fc.lower/upper fallback for older cached shapes). Previously this
  // number only existed inside the collapsed methodology accordion as "80% range:
  // ₹X – ₹Y", which is exactly the kind of ML-reader framing a regular buyer has no
  // use for; the number itself (what to expect tomorrow) is the single most
  // actionable thing the forecast produces, so it belongs in the default view.
  // "next trading day" (not "tomorrow") stays accurate across weekends/holidays —
  // methodology's own heading uses the same phrase for the same reason.
  const rangeLower = hl?.lower ?? fc?.lower;
  const rangeUpper = hl?.upper ?? fc?.upper;
  const hasRange = typeof rangeLower === "number" && typeof rangeUpper === "number";
  // XSS-safe: fmtINR() wraps numbers only.
  const tomorrowRangeHtml = hasRange
    ? `<p class="good-price-tomorrow">${t("goodPriceTomorrow", { low: fmtINR(rangeLower), high: fmtINR(rangeUpper) })}</p>`
    : "";

  // Reliability — plain-language promotion of coverage_metrics.json (empirical
  // hit-rate of the range stated above) + drift_metrics.json (recent vs historical
  // error), previously buried inside the collapsed methodology accordion
  // (methAccurateP2/methDriftHeading). Only rendered alongside the range statement
  // it's actually validating (hasRange) — a reliability claim with nothing to
  // anchor it to reads as a floating, unverifiable assertion. Sample size (n)
  // stays visible deliberately: "right 95% of the time" alone is pure reassurance;
  // naming how many times we've actually checked is what makes it a real, honest
  // claim instead of a vibe. computeAccuracyDrift() is shared with
  // renderMethodology()'s own drift section so the two never disagree on the
  // same underlying numbers.
  let reliabilityHtml = "";
  if (hasRange) {
    const hasCoverage = coverage && typeof coverage.coverage === "number" && coverage.n > 0;
    const coverageNote = hasCoverage
      ? t("reliabilityCoverage", { pct: Math.round(coverage.coverage * 100), n: coverage.n })
      : t("reliabilityUnknown");

    const accDrift = computeAccuracyDrift(drift);
    const driftKeySuffix = accDrift?.ratioLabelKey === "ratioOnTrack" ? "OnTrack"
      : accDrift?.ratioLabelKey === "ratioWatch" ? "Watch"
      : accDrift?.ratioLabelKey === "ratioRetrain" ? "Retrain"
      : null;
    const driftNote = driftKeySuffix ? t(`reliabilityDrift${driftKeySuffix}`) : "";

    // XSS-safe: coverageNote/driftNote are t() catalogue literals only.
    reliabilityHtml = `
      <div class="outlook-reliability">
        <p class="outlook-reliability-note">${coverageNote}${driftNote ? ` ${driftNote}` : ""}</p>
      </div>
    `;
  }

  // Volatility context — dynamic realized-vol estimate (Phi10B) with static-PI fallback.
  // Shows "has been moving about ±Rs.X lately" — magnitude only, no direction (ADR 005).
  let volatilityHtml = "";
  if (hasPI) {
    const volCtx = hl.vol_context;
    let Z, volNote;
    if (volCtx && typeof volCtx.half_width === "number" && !volCtx.is_degraded) {
      Z = Math.round(volCtx.half_width / 50) * 50;
      const regime = volCtx.regime ?? "normal";
      if (regime === "elevated") {
        volNote = t("volNoteElevated", { z: fmtINR(Z) });
      } else if (regime === "calm") {
        volNote = t("volNoteCalm", { z: fmtINR(Z) });
      } else {
        volNote = t("volNoteNormal", { z: fmtINR(Z) });
      }
    } else {
      // Fallback: vol estimate degraded or absent → the dedicated 5-day static-PI
      // reference (vol_context.static_pi_half). NOT hl.conformal_pi_half — since
      // ADR 022 that field is the next-trading-day (h=1) band and would understate a
      // "5 days" claim. Old cached forecast.json missing vol_context entirely still
      // falls back to conformal_pi_half (pre-ADR-022 shape) rather than break.
      const piHalf = hl.vol_context?.static_pi_half ?? hl.conformal_pi_half ?? (hl.upper - hl.lower) / 2;
      Z = Math.round(piHalf / 50) * 50;
      volNote = t("volNoteFallback", { z: fmtINR(Z) });
    }

    // Typical weekly movement — deliberately in the SAME card as the 5-day note
    // above rather than its own separate bordered block, so the two read as one
    // "how much does this move" cluster with two different timeframes, not two
    // unrelated stats competing for attention. See computeWeeklyMovement()'s own
    // comment for exactly how it differs from the 5-day note (90-day historical
    // median vs 20-day recent realized-vol).
    const weeklyMovement = computeWeeklyMovement(readings ?? []);

    // XSS-safe: fmtINR() wraps numbers only; volNote/weeklyMovement.note are t()-built strings.
    volatilityHtml = `
      <div class="outlook-volatility">
        <p class="outlook-volatility-note">${volNote}</p>
        ${weeklyMovement ? `<p class="outlook-weekly-movement-note">${weeklyMovement.note}</p>` : ""}
      </div>
    `;
  }

  // XSS-safe: verdictLead/proofLine/dataSuffNote/supportLine1/supportLine2/divergenceNote/
  // bandPos90d.note/trendResidual.note/supportDistance90d.note are hardcoded string
  // literals or fmtINR(number) from computeGoodPriceSignals/computeBandPos90d/
  // computeTrendResidual30d/computeSupportDistance90d — no external data.
  // tomorrowRangeHtml/reliabilityHtml/volatilityHtml built above, same
  // fmtINR(number)/t()-literal-only rule.
  document.getElementById("model-signal-body").innerHTML = `
    <div class="outlook-card">
      <p class="good-price-verdict good-price-verdict--${signals.verdictType}">${signals.verdictLead}</p>
      <p class="good-price-proof">${signals.proofLine}</p>
      ${signals.dataSuffNote ? `<p class="good-price-data-note">${signals.dataSuffNote}</p>` : ""}
      <ul class="good-price-supporting">
        <li>${signals.supportLine1}</li>
        <li>${signals.supportLine2}</li>
        ${signals.divergenceNote ? `<li class="good-price-divergence">${signals.divergenceNote}</li>` : ""}
        ${trendResidual ? `<li class="good-price-trend">${trendResidual.note}</li>` : ""}
        ${bandPos90d ? `<li class="good-price-band-90d">${bandPos90d.note}</li>` : ""}
        ${supportDistance90d ? `<li class="good-price-support-90d">${supportDistance90d.note}</li>` : ""}
      </ul>
      ${tomorrowRangeHtml}
      ${reliabilityHtml}
      ${volatilityHtml}
    </div>
  `;

  section.hidden = false;
}

// ─── DRIVER CONTEXT (Φ14-2) ──────────────────────────────────────────────────
// Renders honest attribution of recent gold moves. PAST-TENSE ONLY — describes what
// already happened, never implies a future direction (ADR 005 + Φ14 spec).
//
// Three driver-state branches (30d):
//   1. A driver moved > DRIVER_THRESHOLD_PCT → state it + mechanism sentence
//   2. Both drivers muted but premium moved → "local factors" copy (humble about why)
//   3. Everything flat → "stable" copy
//
// Attribution headline (7d) shown only when attribution_valid=true.
// Degrades visibly: section hidden when macro not fresh or driver_context absent (norm #8).

const _DC_DRIVER_THRESHOLD_PCT  = 2.0;  // mechanism sentence fires at >2% (clearly noticeable)
const _DC_PREMIUM_THRESHOLD_PCT = 1.0;  // "premium moved" at >1% log-space %

function renderDriverContext(fc) {
  const section = document.getElementById("driver-context-section");
  const body    = document.getElementById("driver-context-body");
  const skelEl  = document.getElementById("driver-context-skeleton");
  if (!section || !body) return;

  const dc = fc?.driver_context;

  if (!dc || !dc.macro_fresh) {
    section.hidden = true;
    return;
  }

  const w7  = dc.windows?.["7d"];
  const w30 = dc.windows?.["30d"];
  const ds  = dc.driver_state;

  if (!ds) {
    section.hidden = true;
    return;
  }

  const inrPct   = ds.usd_inr_30d_pct_change ?? 0;
  const goldPct  = ds.gold_usd_30d_pct_change ?? 0;
  const premPct30 = w30?.delta_pct_premium ?? 0;

  const inrMoved  = Math.abs(inrPct)   > _DC_DRIVER_THRESHOLD_PCT;
  const goldMoved = Math.abs(goldPct)  > _DC_DRIVER_THRESHOLD_PCT;
  const premMoved = Math.abs(premPct30) > _DC_PREMIUM_THRESHOLD_PCT;

  // --- Attribution headline (7d) — only when attribution_valid ---
  let headlineHtml = "";
  if (w7?.attribution_valid && w7?.total_move_rs_per_g != null) {
    const total    = Math.round(w7.total_move_rs_per_g);
    const inrPt    = Math.round(w7.usdinr_contrib_rs_per_g ?? 0);
    const goldPt   = Math.round(w7.gold_usd_contrib_rs_per_g ?? 0);
    const absTotal = Math.abs(total);
    const inrAbs   = Math.abs(inrPt);
    const goldAbs  = Math.abs(goldPt);
    let headline;

    if (total >= 0) {
      if (inrAbs >= goldAbs && inrAbs > 10) {
        headline = t("driverUpInrDominant", { total: fmtINR(absTotal), inr: fmtINR(inrAbs), gold: fmtINR(goldAbs) });
      } else if (goldAbs > 10) {
        headline = t("driverUpGoldDominant", { total: fmtINR(absTotal), gold: fmtINR(goldAbs), inr: fmtINR(inrAbs) });
      } else {
        headline = t("driverUpMixed", { total: fmtINR(absTotal) });
      }
    } else {
      if (inrAbs >= goldAbs && inrAbs > 10) {
        headline = t("driverDownInrDominant", { total: fmtINR(absTotal), inr: fmtINR(inrAbs) });
      } else if (goldAbs > 10) {
        const inrNote = inrAbs > 10
          ? t("driverDownGoldDominantInrNoteAdded", { inr: fmtINR(inrAbs) })
          : t("driverDownGoldDominantInrNoteFlat");
        headline = t("driverDownGoldDominant", { total: fmtINR(absTotal), gold: fmtINR(goldAbs), inrNote });
      } else {
        headline = t("driverDownMixed", { total: fmtINR(absTotal) });
      }
    }
    // XSS-safe: headline built from fmtINR(number) and catalogue string literals only
    headlineHtml = `<p class="driver-headline">${headline}</p>`;
  }

  // --- Driver-state supporting (30d, three-branch) ---
  let driverStateText;
  if (inrMoved || goldMoved) {
    // Branch 1: at least one driver moved > 2%
    const parts = [];
    if (inrMoved) {
      const mechanism = inrPct > 0 ? t("driverMechanismWeaker") : t("driverMechanismStronger");
      const key = inrPct > 0 ? "driverRupeeWeakened" : "driverRupeeStrengthened";
      parts.push(t(key, { pct: Math.abs(inrPct).toFixed(1), mechanism }));
    }
    if (goldMoved) {
      const key = goldPct > 0 ? "driverGoldUp" : "driverGoldDown";
      parts.push(t(key, { pct: Math.abs(goldPct).toFixed(1) }));
    }
    driverStateText = parts.join(" ");
  } else if (premMoved) {
    // Branch 2: premium-dominated — both drivers muted (<2%), premium moved (>1%)
    driverStateText = t("driverPremiumDominated");
  } else {
    // Branch 3: everything flat
    driverStateText = t("driverAllFlat");
  }

  // XSS-safe: driverStateText is a catalogue literal or toFixed(1) on a number
  body.innerHTML = `
    <div class="driver-context-card">
      ${headlineHtml}
      <p class="driver-state">${driverStateText}</p>
    </div>
  `;

  if (skelEl) skelEl.hidden = true;
  section.hidden = false;
}

// Φ8C' / Ψ3C.3: persistent tap-to-reveal price callout drawn via Chart.js afterDraw.
const CALLOUT_PLUGIN = {
  id: "phi8cCallout",
  afterDraw(ch) {
    if (chartPinnedIndex === null) return;
    const meta = ch.getDatasetMeta(0);
    const dp   = meta.data[chartPinnedIndex];
    if (!dp) return;
    const { ctx, chartArea } = ch;
    const x = dp.x;
    const label = ch.data.labels[chartPinnedIndex] || "";
    const value = ch.data.datasets[0].data[chartPinnedIndex];
    ctx.save();
    ctx.strokeStyle = "rgba(224,155,46,0.50)";
    ctx.lineWidth   = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(x, chartArea.top);
    ctx.lineTo(x, chartArea.bottom);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.font = "bold 13px DM Sans, system-ui, sans-serif";
    const pw = ctx.measureText(`₹${fmtINR(value)}`).width;
    ctx.font = "11px DM Sans, system-ui, sans-serif";
    const dw = ctx.measureText(label).width;
    const bW = Math.max(pw, dw) + 24;
    const bH = 40;
    const bR = 7;
    let bx = x - bW / 2;
    bx = Math.max(chartArea.left, Math.min(bx, chartArea.right - bW));
    const by = chartArea.top + 4;
    ctx.beginPath();
    ctx.moveTo(bx + bR, by);
    ctx.lineTo(bx + bW - bR, by);
    ctx.quadraticCurveTo(bx + bW, by, bx + bW, by + bR);
    ctx.lineTo(bx + bW, by + bH - bR);
    ctx.quadraticCurveTo(bx + bW, by + bH, bx + bW - bR, by + bH);
    ctx.lineTo(bx + bR, by + bH);
    ctx.quadraticCurveTo(bx, by + bH, bx, by + bH - bR);
    ctx.lineTo(bx, by + bR);
    ctx.quadraticCurveTo(bx, by, bx + bR, by);
    ctx.closePath();
    ctx.fillStyle   = "#241E16";
    ctx.fill();
    ctx.strokeStyle = "#E09B2E";
    ctx.lineWidth   = 1;
    ctx.stroke();
    ctx.fillStyle   = "#E09B2E";
    ctx.font        = "bold 13px DM Sans, system-ui, sans-serif";
    ctx.textAlign   = "center";
    ctx.textBaseline = "alphabetic";
    ctx.fillText(`₹${fmtINR(value)}`, bx + bW / 2, by + 16);
    ctx.fillStyle = "#9A9282";
    ctx.font      = "11px DM Sans, system-ui, sans-serif";
    ctx.fillText(label, bx + bW / 2, by + 31);
    ctx.restore();
  },
};

// Chart.js colors read from CSS custom properties (not hardcoded hex) so both
// charts adapt to light/dark automatically instead of being permanently
// dark-tuned -- gridColor/axisColor previously stayed the same hex in light
// mode, where they were tuned for contrast against --ink, not --surface's
// light-mode white. Tooltip backgrounds intentionally stay a fixed dark
// literal below (not tokenized) -- a dark floating overlay reads fine
// against either page theme, same convention Chart.js tooltips use by
// default, so there's no legibility gap there to fix.
function getChartColors() {
  const s = getComputedStyle(document.documentElement);
  const get = (name, fallback) => (s.getPropertyValue(name) || fallback).trim();
  return {
    gold: get("--gold", "#E09B2E"),
    axis: get("--cream-mute", "#9A9282"),
    grid: get("--line", "#3A3028"),
  };
}

function hexToRgba(hex, alpha) {
  const h    = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n    = parseInt(full, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

function renderChart(readings, range) {
  chartPinnedIndex = null;

  let filtered = readings;
  if (range !== "all") {
    const cutoff = Date.now() - parseInt(range, 10) * 86400 * 1000;
    filtered     = readings.filter(r => new Date(r.timestamp).getTime() >= cutoff);
  }

  // One point per IST calendar day (latest reading wins) — same rule history uses.
  // Connects real daily prices with a clean straight line; no stepped plateaus,
  // no fill, no doubled boundary dots. Matches the clean daily-line look of Tanishq.
  const chartPts = dedupeByISTDay(filtered);
  const labels   = chartPts.map(r => fmtDateShort(r.timestamp));
  const data22   = chartPts.map(r => r["22k"]);
  const lastIdx  = data22.length - 1;

  const colors    = getChartColors();
  const goldLine  = colors.gold;
  const axisColor = colors.axis;
  const gridColor = colors.grid;
  const ctx       = document.getElementById("chart");

  if (chart) chart.destroy();

  chart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: t("chart22kLabel"),
        data: data22,
        borderColor: goldLine,
        // Gradient fill under the line (gold fading to transparent) instead
        // of a flat/no fill -- Chart.js calls this per-render since chartArea
        // isn't known until the canvas has a layout pass.
        backgroundColor: (context) => {
          const { ctx: c, chartArea } = context.chart;
          if (!chartArea) return "transparent";
          const gradient = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
          gradient.addColorStop(0, hexToRgba(goldLine, 0.22));
          gradient.addColorStop(1, hexToRgba(goldLine, 0));
          return gradient;
        },
        fill: true,
        borderWidth: 1.5,
        // Only the latest point gets a visible marker -- a "you are here"
        // emphasis, not a dot on every day (which would look busy on a
        // 30-90 point line).
        pointRadius: data22.map((_, i) => (i === lastIdx ? 4 : 0)),
        pointHoverRadius: 4,
        pointBackgroundColor: goldLine,
        pointBorderWidth: 0,
        tension: 0,
        spanGaps: true,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      onClick: (event, elements) => {
        if (elements.length > 0) {
          const idx = elements[0].index;
          chartPinnedIndex = (chartPinnedIndex === idx) ? null : idx;
        } else {
          chartPinnedIndex = null;
        }
        chart.update("none");
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          // Fixed dark literal, not gridColor -- the tooltip bg below is also
          // a fixed dark literal by design (see getChartColors' comment), so
          // its border shouldn't flip to a light-mode token and mismatch it.
          backgroundColor: "#241E16",
          borderColor: "#3A3028",
          borderWidth: 1,
          titleColor: axisColor,
          bodyColor: "#F5EDE0",
          padding: 12,
          callbacks: {
            label: (c) => t("chart22kTooltip", { value: fmtINR(c.parsed.y) }),
          },
        },
        phi8cCallout: {},
      },
      scales: {
        x: {
          ticks:  { color: axisColor, maxTicksLimit: 5, autoSkip: true, font: { family: "DM Sans", size: 11 } },
          grid:   { color: "transparent" },
          border: { color: gridColor },
        },
        y: {
          // Quiet gridlines -- half the token's own opacity so they read as
          // a faint reference, not a ruled grid competing with the line.
          ticks:  { color: axisColor, font: { family: "DM Sans", size: 11 }, callback: (v) => "₹" + fmtINR(v) },
          grid:   { color: hexToRgba(gridColor, 0.5) },
          border: { display: false },
        },
      },
    },
    plugins: [CALLOUT_PLUGIN],
  });
}

function renderHistory(readings) {
  const tbody    = document.getElementById("history-body");
  const cardList = document.getElementById("history-cards");
  const showBtn  = document.getElementById("history-show-all");
  const skelEl   = document.getElementById("history-skeleton");

  if (skelEl) skelEl.hidden = true;

  const EMPTY_TABLE = `<tr><td colspan="5" class="empty">${t("historyNoReadings")}</td></tr>`;
  const EMPTY_CARDS = `<li class="hcard-empty">${t("historyNoReadings")}</li>`;

  if (readings.length === 0) {
    tbody.innerHTML    = EMPTY_TABLE;
    cardList.innerHTML = EMPTY_CARDS;
    if (showBtn) showBtn.hidden = true;
    return;
  }

  const allGroups = dedupReadings([...readings].reverse());
  const groups    = allGroups.slice(0, 50);

  // Shared truncation point for BOTH the desktop table and the mobile card
  // list -- one "Show N more" button drives both in lockstep (CSS decides
  // which is actually visible per viewport, see .history-table/.history-cards).
  // Previously only the card list truncated; the table rendered up to 50 rows
  // unconditionally, which is what made desktop History run ~8800px tall.
  const VISIBLE_GROUPS = 15;

  // ── Desktop table ────────────────────────────────────────────────────────────
  // XSS-safe: all interpolated values are numbers or date strings from prices.json.
  function buildTableHtml(count) {
    return groups.slice(0, count).map((g, i) => {
      const nextGroup = groups[i + 1];
      let deltaCell = `<span class="delta-flat">—</span>`;
      if (nextGroup) {
        const d = g.reading["22k"] - nextGroup.reading["22k"];
        if (d > 0)      deltaCell = `<span class="delta-up">↑ ₹${fmtINR(d)}</span>`;
        else if (d < 0) deltaCell = `<span class="delta-down">↓ ₹${fmtINR(Math.abs(d))}</span>`;
        else            deltaCell = `<span class="delta-flat">·</span>`;
      }
      // g.reading.timestamp = NEWEST reading in run; g.endTimestamp = OLDEST (first occurrence).
      let whenCell;
      if (g.count === 1) {
        whenCell = fmtDateShort(g.reading.timestamp);
      } else if (i === 0) {
        whenCell = t("historySince", { date: fmtDateShort(g.endTimestamp) });
      } else {
        whenCell = t("historyRange", { from: fmtDateShort(g.endTimestamp), to: fmtDateShort(g.reading.timestamp) });
      }
      return `<tr>
        <td>${whenCell}</td>
        <td class="num">${rupee(g.reading["22k"])}</td>
        <td class="num">${rupee(g.reading["24k"])}</td>
        <td class="num">${rupee(g.reading["18k"])}</td>
        <td class="num">${deltaCell}</td>
      </tr>`;
    }).join("");
  }

  // ── Mobile card list (dedup-grouped) ─────────────────────────────────────────
  function buildCardsHtml(count) {
    // XSS-safe: all interpolated values are numbers or date strings from prices.json.
    return groups.slice(0, count).map((g, absIdx) => {
      const nextGroup = groups[absIdx + 1];
      let deltaHtml   = "";
      if (nextGroup) {
        const d = g.reading["22k"] - nextGroup.reading["22k"];
        if (d > 0)      deltaHtml = `<span class="hcard-delta hcard-delta--up">↑ ₹${fmtINR(d)}</span>`;
        else if (d < 0) deltaHtml = `<span class="hcard-delta hcard-delta--down">↓ ₹${fmtINR(Math.abs(d))}</span>`;
      }
      let timeLabel;
      if (g.count === 1) {
        timeLabel = fmtDateShort(g.reading.timestamp);
      } else if (absIdx === 0) {
        timeLabel = t("historySince", { date: fmtDateShort(g.endTimestamp) });
      } else {
        timeLabel = t("historyRangeCard", { from: fmtDateShort(g.endTimestamp), to: fmtDateShort(g.reading.timestamp) });
      }
      return `<li class="history-card">
        <span class="hcard-time">${timeLabel}</span>
        <span class="hcard-price">${rupee(g.reading["22k"])}</span>
        ${deltaHtml}
      </li>`;
    }).join("");
  }

  tbody.innerHTML    = buildTableHtml(VISIBLE_GROUPS);
  cardList.innerHTML = buildCardsHtml(VISIBLE_GROUPS);

  const hiddenCount = Math.max(0, groups.length - VISIBLE_GROUPS);
  if (showBtn) {
    if (hiddenCount > 0) {
      showBtn.hidden = false;
      const moreLabel = t("historyShowMore", { n: hiddenCount });
      showBtn.textContent = moreLabel;
      let isExpanded = false;
      showBtn.onclick = () => {
        isExpanded = !isExpanded;
        const count = isExpanded ? groups.length : VISIBLE_GROUPS;
        tbody.innerHTML    = buildTableHtml(count);
        cardList.innerHTML = buildCardsHtml(count);
        showBtn.textContent = isExpanded ? t("historyShowLess") : moreLabel;
      };
    } else {
      showBtn.hidden = true;
    }
  }
}

function renderForecastVsActual(bt) {
  const section = document.getElementById("section-track-record");
  if (!section) return;
  if (!bt?.folds?.length) { section.hidden = true; return; }

  const folds = bt.folds
    .filter(f => !f.sub_30_context)
    .slice(-30);

  if (folds.length < 3) { section.hidden = true; return; }

  const labels  = folds.map(f => fmtDateShort(f.context_end_date + "T00:00:00Z"));
  const actuals = folds.map(f => typeof f.actuals[0] === "number" ? f.actuals[0] : null);
  const naives  = folds.map(f => typeof f.naive[0]   === "number" ? f.naive[0]   : null);

  const colors    = getChartColors();
  const goldLine  = colors.gold;
  const axisColor = colors.axis;
  const gridColor = colors.grid;
  const ctx       = document.getElementById("track-record-chart");
  if (!ctx) { section.hidden = true; return; }

  if (trackRecordChart) trackRecordChart.destroy();

  trackRecordChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: t("chartWhatHappened"),
          data: actuals,
          borderColor: goldLine,
          backgroundColor: "transparent",
          fill: false,
          borderWidth: 2,
          pointRadius: 3,
          pointBackgroundColor: goldLine,
          pointBorderWidth: 0,
          tension: 0.1,
          spanGaps: true,
        },
        {
          label: t("chartFlatHoldEstimate"),
          data: naives,
          borderColor: "#6B5E4E",
          backgroundColor: "transparent",
          fill: false,
          borderWidth: 1.5,
          pointRadius: 0,
          borderDash: [5, 4],
          tension: 0,
          spanGaps: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: true,
          labels: { color: axisColor, font: { family: "DM Sans", size: 11 }, boxWidth: 24 },
        },
        tooltip: {
          backgroundColor: "#241E16",
          borderColor: "#3A3028",
          borderWidth: 1,
          titleColor: axisColor,
          bodyColor: "#F5EDE0",
          padding: 12,
          callbacks: {
            label: (c) => t("chartTooltipLabeled", { label: c.dataset.label, value: fmtINR(c.parsed.y) }),
          },
        },
      },
      scales: {
        x: {
          ticks:  { color: axisColor, maxTicksLimit: 6, autoSkip: true, font: { family: "DM Sans", size: 11 } },
          grid:   { color: "transparent" },
          border: { color: gridColor },
        },
        y: {
          ticks:  { color: axisColor, font: { family: "DM Sans", size: 11 }, callback: v => "₹" + fmtINR(v) },
          grid:   { color: hexToRgba(gridColor, 0.5) },
          border: { display: false },
        },
      },
    },
  });

  section.hidden = false;
}

function renderMethodology(fc, bt, drift, coverage) {
  const body = document.getElementById("methodology-body");
  if (!body) return;

  const parts = [];

  // Hoist so direction-signal section and how-good section share the same all-windows accuracy.
  const dirAll = bt && typeof bt.dir_acc_5d_chronos === "number"
    ? `${Math.round(bt.dir_acc_5d_chronos * 100)}%`
    : null;

  // Verdict rule explanation
  parts.push(`
    <div class="meth-section">
      <h3 class="meth-heading">${t("methHowWeCallTrendHeading")}</h3>
      <p class="meth-text">${t("methHowWeCallTrendIntro")}</p>
      <ul class="meth-list">
        <li>${t("methRuleCheaper")}</li>
        <li>${t("methRulePricier")}</li>
        <li>${t("methRuleSteady")}</li>
      </ul>
    </div>
  `);

  // Forecast details
  if (fc && typeof (fc.headline?.predicted_22k ?? fc.predicted_22k) === "number") {
    const pred22k = fc.headline?.predicted_22k ?? fc.predicted_22k;
    const lower   = fc.headline?.lower ?? fc.lower;
    const upper   = fc.headline?.upper ?? fc.upper;
    const hasPI   = typeof lower === "number" && typeof upper === "number";
    parts.push(`
      <div class="meth-section">
        <h3 class="meth-heading">${t("methNextDayRangeHeading")}</h3>
        <div class="meth-stats">
          <div class="meth-stat">
            <div class="meth-stat-label">${t("methEstimateLabel")}</div>
            <div class="meth-stat-value">₹${fmtINR(pred22k)}</div>
            ${hasPI ? `<div class="meth-stat-sub">${t("methRangeSub", { low: fmtINR(lower), high: fmtINR(upper) })}</div>` : ""}
          </div>
          <div class="meth-stat">
            <div class="meth-stat-label">${t("methMethodLabel")}</div>
            <div class="meth-stat-value">${t("methAssumeNoChange")}</div>
            <div class="meth-stat-sub">${t("methCoversMoves")}</div>
          </div>
        </div>
        ${fc.target_time ? `<p class="meth-text" style="margin-top:8px">${t("methTargetLine", { date: fmtIST(fc.target_time) })}</p>` : ""}
        <p class="meth-text" style="margin-top:12px">${t("methNextDayExplainer")}</p>
      </div>
    `);
  }

  // Direction signal — DARK gate (ADR 019/020). We test direction models weekly;
  // none beats the "gold usually rises" base rate with significance, so we show NO
  // directional prediction and NO accuracy stat (a base-rate number dressed as
  // model accuracy reads as an edge we don't have). Qualitative "off" only.
  if (fc?.chronos_companion?.status === "success") {
    parts.push(`
      <div class="meth-section">
        <h3 class="meth-heading">${t("methDirectionHeading")}</h3>
        <div class="meth-stat">
          <div class="meth-stat-label">${t("methStatusLabel")}</div>
          <div class="meth-stat-value">${t("methDirectionOff")}</div>
          <div class="meth-stat-sub">${t("methDirectionSub")}</div>
        </div>
        <p class="meth-note">${t("methDirectionNote")}</p>
      </div>
    `);
  } else if (fc?.chronos_companion?.status === "failed") {
    parts.push(`<p class="meth-text">${t("methDirectionUnavailable")}</p>`);
  }

  // "How good is this?" — honest track record panel (Φ8C', ADR 019/020/012)
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
    const pVal     = bt.wilcoxon_signed_rank_p != null
      ? bt.wilcoxon_signed_rank_p.toFixed(4)
      : "—";
    const hl      = fc?.headline;
    const rangeStr = hl && typeof hl.lower === "number" && typeof hl.upper === "number"
      ? `₹${fmtINR(hl.lower)}–₹${fmtINR(hl.upper)}`
      : t("methRangeStrFallback");

    // Empirical coverage of the DISPLAYED band (headline.lower/upper), tracked from
    // resolved live decisions — not bt.pi_coverage_80_5d_avg, which measures Chronos's
    // own quantile PI (a different band, never shown as the headline range).
    const hasCoverage = coverage && typeof coverage.coverage === "number" && coverage.n > 0;
    const coverPct = hasCoverage ? Math.round(coverage.coverage * 100) : null;
    const coverN   = hasCoverage ? coverage.n : null;

    parts.push(`
      <div class="meth-section meth-how-good">
        <h3 class="meth-heading">${t("methHowAccurateHeading")}</h3>

        <p class="meth-text"><strong>${t("methAccurateP1Strong")}</strong><br>
        ${t("methAccurateP1", {
          n, naiveMae,
          chronosBullet: maePctWorse != null ? t("methAccurateP1ChronosBullet", { chronosMae, maePctWorse, pVal }) : "",
        })}</p>

        <p class="meth-text"><strong>${t("methAccurateP2Strong", {
          rangeStr,
          coverageText: hasCoverage ? t("methAccurateP2CoveragePct", { pct: coverPct, n: coverN }) : t("methAccurateP2CoverageUnknown"),
        })}</strong><br>
        ${t("methAccurateP2")}</p>

        <p class="meth-text"><strong>${t("methAccurateP3Strong")}</strong><br>
        ${t("methAccurateP3", { dirAllDisplay, n })}</p>

        <p class="meth-text"><strong>${t("methAccurateP4Strong")}</strong><br>
        ${t("methAccurateP4")}</p>
      </div>
    `);
  }

  // Live drift
  const accDrift = computeAccuracyDrift(drift);
  if (accDrift) {
    const rolling = accDrift.rolling != null ? Math.round(accDrift.rolling) : null;
    const baseMae = accDrift.baseMae != null ? Math.round(accDrift.baseMae) : null;
    const ratio   = accDrift.ratio != null ? accDrift.ratio.toFixed(2) : null;
    const ratioLabelKey = accDrift.ratioLabelKey;
    parts.push(`
      <div class="meth-section">
        <h3 class="meth-heading">${t("methDriftHeading")}</h3>
        <div class="meth-stats">
          <div class="meth-stat">
            <div class="meth-stat-label">${t("methRecentError")}</div>
            <div class="meth-stat-value">${rolling != null ? "₹" + fmtINR(rolling) : "—"}</div>
          </div>
          <div class="meth-stat">
            <div class="meth-stat-label">${t("methHistoricalError")}</div>
            <div class="meth-stat-value">${baseMae != null ? "₹" + fmtINR(baseMae) : "—"}</div>
          </div>
          <div class="meth-stat">
            <div class="meth-stat-label">${t("methAccuracyDrift")}</div>
            <div class="meth-stat-value">${ratio ?? "—"}</div>
            <div class="meth-stat-sub">${ratioLabelKey === "ratioRetrain" ? t("ratioRetrainSub") : (ratioLabelKey ? t(ratioLabelKey) : "")}</div>
          </div>
        </div>
      </div>
    `);
  }

  // XSS-safe: parts[] contains only hardcoded HTML templates with numeric/boolean
  // values from forecast.json and backtest.json. Today's-read text is rendered
  // separately via textContent in renderTodaysRead().
  body.innerHTML = parts.join("");
}

// D3: Lightweight data re-fetch — prices + forecast only.
// Assigns to a local `fresh` first (FIX 2): allReadings is only committed
// after both fetches resolve, keeping state consistent on partial failure.
async function refreshData() {
  const btn = document.getElementById("refresh-btn");
  if (btn) { btn.classList.add("refresh-btn--spinning"); btn.disabled = true; }
  try {
    const freshPromise = load();
    const fcPromise    = loadJSON(FORECAST_URL).catch(() => null);
    const fresh = await freshPromise;
    const fc    = await fcPromise;
    allReadings  = fresh;
    lastForecast = fc;
    renderFreshness(allReadings, fc);
    renderComparisons(allReadings);
    renderHistory(allReadings);
    renderChart(allReadings, currentRange);
    renderHero(allReadings, fc);
    renderStaleBanner(fc, lastCalibration);
    renderTodaysRead(allReadings);
    renderModelSignal(fc, allReadings, lastBacktest, lastCoverage, lastDrift);
    renderDriverContext(fc);
    renderCalculator(allReadings, fc);
    updateOfflineBanner();
    // Ψ3C.2: stagger visible data cards to confirm refresh visually
    staggerEnter([
      document.getElementById("comparison-section"),
      document.querySelector(".karat-strip"),
      document.getElementById("calculator-section"),
      document.getElementById("model-signal-section"),
      document.getElementById("driver-context-section"),
    ]);
  } catch (err) {
    console.error("Refresh failed:", err);
    // Φ16-1: visible refresh-failure state — persists until next successful refresh.
    // renderStaleBanner() on the next success will reset this (it always hides first now).
    const banner = document.getElementById("stale-banner");
    if (banner) {
      const rel = allReadings.length > 0
        ? fmtRelative(allReadings[allReadings.length - 1].timestamp)
        : t("unknownTime");
      banner.textContent = t("bannerRefreshFailed", { rel });
      banner.hidden = false;
    }
  } finally {
    if (btn) { btn.classList.remove("refresh-btn--spinning"); btn.disabled = false; }
  }
}

function bindRangeToggle() {
  const buttons = document.querySelectorAll(".range-toggle button");
  buttons.forEach(btn => {
    btn.addEventListener("click", () => {
      if (btn.classList.contains("active")) return; // already selected — no-op
      buttons.forEach(b => { b.classList.remove("active"); b.setAttribute("aria-selected", "false"); });
      btn.classList.add("active");
      btn.setAttribute("aria-selected", "true");
      currentRange = btn.dataset.range;
      // Ψ3C.2: chart fade — fade out 150ms, render new data, fade in 200ms
      const wrap = document.querySelector(".chart-wrap");
      if (wrap) {
        wrap.classList.add("chart-fade-out");
        setTimeout(() => {
          renderChart(allReadings, currentRange);
          wrap.classList.remove("chart-fade-out");
          wrap.classList.add("chart-fade-in");
          wrap.addEventListener("animationend", () => wrap.classList.remove("chart-fade-in"), { once: true });
        }, 150);
      } else {
        renderChart(allReadings, currentRange);
      }
    });
  });
}

// ─── BOTTOM NAV SCROLLSPY ─────────────────────────────────────────────────────
// Option B: scroll-anchor nav. IntersectionObserver tracks which section is in view.
// Click handler uses scrollIntoView({behavior:"smooth"}); CSS scroll-margin-top handles
// the fixed header offset so sections don't slide under it.

const NAV_SECTIONS = [
  "section-home",
  "section-trend",
  "section-history",
  "section-info",
];

function initBottomNav() {
  const navItems = document.querySelectorAll(".bottom-nav-item");

  // Click: prevent default anchor jump, use smooth scrollIntoView instead.
  // CSS scroll-margin-top on each section accounts for the 52px header.
  //
  // iOS race fix (WI-Φ9B-3): the old order was scrollIntoView() then el.open=true.
  // On iOS, el.open=true triggers a layout reflow that cancels the in-flight smooth
  // scroll, so tap 1 opened but didn't scroll and tap 2 scrolled. Fix: open FIRST,
  // then scrollIntoView() inside one requestAnimationFrame so the expanded DETAILS
  // layout is stable before the browser computes the scroll target position.
  // Single rAF is sufficient — if a future iOS regression appears, escalate to
  // double-rAF: requestAnimationFrame(() => requestAnimationFrame(() => scroll...)).
  navItems.forEach(item => {
    item.addEventListener("click", (e) => {
      e.preventDefault();
      const sectionId = item.dataset.section;
      const el = document.getElementById(sectionId);
      if (!el) return;
      if (el.tagName === "DETAILS" && !el.open) el.open = true;
      requestAnimationFrame(() => {
        el.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
  });

  // Scrollspy: set active nav item as the user scrolls.
  // Strategy: last section whose top edge is ≤ (scrollY + HEADER_H + threshold).
  // requestAnimationFrame throttles scroll handler to one update per frame.
  // Read actual header height so standalone mode (taller header) is accounted for.
  const HEADER_H = document.getElementById("utility-row")?.getBoundingClientRect().height ?? 52;
  const THRESHOLD = 24; // px below header before marking a section active

  const sectionEls = NAV_SECTIONS
    .map(id => document.getElementById(id))
    .filter(Boolean);

  function updateActiveNav() {
    const scrollY = window.scrollY;
    const trigger = scrollY + HEADER_H + THRESHOLD;
    let activeId = sectionEls[0]?.id;

    for (const el of sectionEls) {
      // getBoundingClientRect().top + scrollY = element's offset from document top
      const elTop = el.getBoundingClientRect().top + scrollY;
      if (elTop <= trigger) activeId = el.id;
    }

    navItems.forEach(item => {
      const isActive = item.dataset.section === activeId;
      item.classList.toggle("active", isActive);
      item.setAttribute("aria-current", isActive ? "page" : "false");
    });
  }

  let ticking = false;
  window.addEventListener("scroll", () => {
    if (!ticking) {
      requestAnimationFrame(() => { updateActiveNav(); ticking = false; });
      ticking = true;
    }
  }, { passive: true });

  updateActiveNav(); // set initial state
}

// ─── PULL-TO-REFRESH — Ψ3C.2 ────────────────────────────────────────────────
//
// Gesture: touch at scrollY=0 and drag down ≥60px → call refreshData().
// iOS risk: overscroll-behavior-y:none only works Safari 16+; older iOS still
// rubber-bands. Real-device test required. Fallback: ↻ button still works.
//
// Safety rules (per spec):
//   - preventDefault() ONLY when actively pulling (deltaY > 0 AND scrollY === 0)
//   - overscroll-behavior-y:none (html.ptr-pulling class) ONLY during active pull
//   - No haptics (navigator.vibrate unreliable on iOS) — visual feedback only

function initPullToRefresh() {
  const indicator = document.getElementById("pull-indicator");
  const mainEl    = document.querySelector("main");
  if (!indicator || !mainEl) return;

  const THRESHOLD = 60; // px — minimum pull distance to trigger refresh

  let startY    = 0;
  let isPulling = false;

  function endPull(doRefresh) {
    isPulling = false;
    startY    = 0;
    document.documentElement.classList.remove("ptr-pulling");

    // Snap main back with a spring-like transition
    mainEl.style.transition = "transform 300ms ease-out";
    mainEl.style.transform  = "";
    mainEl.addEventListener("transitionend", () => { mainEl.style.transition = ""; }, { once: true });

    if (doRefresh) {
      indicator.classList.add("pull-indicator--loading");
      indicator.querySelector(".pull-icon").textContent = "↻";
      refreshData().finally(() => {
        indicator.style.opacity = "0";
        indicator.classList.remove("pull-indicator--loading", "pull-indicator--ready");
      });
    } else {
      indicator.style.opacity = "0";
      indicator.classList.remove("pull-indicator--ready");
    }
  }

  document.addEventListener("touchstart", (e) => {
    if (window.scrollY > 2) return; // only arm when at scroll top
    startY    = e.touches[0].clientY;
    isPulling = false;
  }, { passive: true });

  document.addEventListener("touchmove", (e) => {
    if (!startY) return;
    if (window.scrollY > 2) { startY = 0; return; } // scrolled away — disarm

    const deltaY = e.touches[0].clientY - startY;
    if (deltaY <= 0) {
      if (isPulling) endPull(false); // reversed direction — cancel
      return;
    }

    // Active pull: prevent native scroll/rubber-band for this gesture
    e.preventDefault();

    if (!isPulling) {
      isPulling = true;
      document.documentElement.classList.add("ptr-pulling");
    }

    // Dampen the translation: user moves 2px → content moves 1px (max 60px)
    const translatePx = Math.min(deltaY * 0.5, THRESHOLD);
    mainEl.style.transform = `translateY(${translatePx}px)`;

    const ratio = Math.min(deltaY / THRESHOLD, 1);
    indicator.style.opacity = String(ratio);

    if (ratio >= 1) {
      if (!indicator.classList.contains("pull-indicator--ready")) {
        indicator.classList.add("pull-indicator--ready");
        indicator.querySelector(".pull-icon").textContent = "↻"; // release cue
      }
    } else {
      indicator.classList.remove("pull-indicator--ready");
      indicator.querySelector(".pull-icon").textContent = "↓";
    }
  }, { passive: false }); // passive: false required to call preventDefault()

  document.addEventListener("touchend", (e) => {
    if (!isPulling) return;
    const releaseY = e.changedTouches[0]?.clientY ?? (startY + 0);
    endPull(releaseY - startY >= THRESHOLD);
  }, { passive: true });
}

// ─── SCROLL REVEALS (feel-alive pass) ──────────────────────────────────────────
// Fade/slide-in as sections enter the viewport. Progressive enhancement: elements
// only go to opacity:0 once `.reveal-armed` is added below, so a JS error or
// missing IntersectionObserver support never leaves content stuck invisible --
// see style.css's .reveal-on-scroll comment for the full contract. The hero card
// is deliberately excluded: price must render instantly, never scroll-gated.
function initScrollReveals() {
  const targets = document.querySelectorAll(".reveal-on-scroll");
  if (!targets.length || typeof IntersectionObserver === "undefined") return;

  try {
    targets.forEach((el) => el.classList.add("reveal-armed"));
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            entry.target.classList.add("reveal-visible");
            observer.unobserve(entry.target);
          }
        }
      },
      { rootMargin: "0px 0px -10% 0px", threshold: 0.1 },
    );
    targets.forEach((el) => observer.observe(el));
  } catch {
    // Setup failed after arming -- force visible rather than leave opacity:0 stuck.
    targets.forEach((el) => el.classList.add("reveal-visible"));
  }
}

// ─── LANGUAGE ─────────────────────────────────────────────────────────────────
// Applies the active language to every static (non-JS-rendered) string in
// index.html, driven entirely by data-i18n* attributes rather than a hardcoded
// element list — adding a new translatable static string only requires adding
// the attribute in index.html, no app.js change needed.
//   data-i18n="key"       → element.textContent = t(key)
//   data-i18n-html="key"  → element.innerHTML = t(key)   (only for strings that
//                            deliberately embed markup, e.g. <strong> in the
//                            footer/PWA-help text — every such key is a hardcoded
//                            catalogue literal, never external data, so this is
//                            XSS-safe the same way the rest of app.js's innerHTML
//                            uses already are)
//   data-i18n-attr="attr:key[,attr2:key2...]" → sets one or more attributes
function applyStaticStrings() {
  document.title = t("pageTitle");
  const metaDesc = document.querySelector('meta[name="description"]');
  if (metaDesc) metaDesc.setAttribute("content", t("pageDescription"));
  const ogDesc = document.querySelector('meta[property="og:description"]');
  if (ogDesc) ogDesc.setAttribute("content", t("pageDescription"));
  const twDesc = document.querySelector('meta[name="twitter:description"]');
  if (twDesc) twDesc.setAttribute("content", t("pageDescription"));

  document.querySelectorAll("[data-i18n]").forEach(el => {
    el.textContent = t(el.getAttribute("data-i18n"));
  });
  document.querySelectorAll("[data-i18n-html]").forEach(el => {
    el.innerHTML = t(el.getAttribute("data-i18n-html"));
  });
  document.querySelectorAll("[data-i18n-attr]").forEach(el => {
    el.getAttribute("data-i18n-attr").split(",").forEach(pair => {
      const [attr, key] = pair.split(":");
      if (attr && key) el.setAttribute(attr.trim(), t(key.trim()));
    });
  });
}

// Devanagari fonts (Sans for --heading/--sans, Serif for --display), loaded
// only when Hindi is active — English users' browsers never discover either
// @font-face at all (each unicode-range only matches when a Devanagari
// character is actually painted, see style.css's TOKENS comment), so this
// only needs to add the preload <link>s so the already-scoped font-faces
// start downloading immediately instead of waiting for CSSOM+layout to
// discover them, same reasoning as the three English fonts' own preload in
// index.html. Idempotent — safe to call on every applyLanguage(), only
// inserts each once. Mirrors the same two-font preload injected synchronously
// in index.html's <head> for a first Hindi load; this is the fallback path
// for a same-session language *switch* (index.html's script only runs once,
// at initial parse, keyed off the resolved starting language).
function applyDevanagariFont() {
  if (currentLang !== "hi") return;
  const devanagariFonts = [
    ["devanagari-preload-sans", "fonts/notosans-devanagari-variable.woff2"],
    ["devanagari-preload-serif", "fonts/notoserif-devanagari-variable.woff2"],
  ];
  for (const [id, href] of devanagariFonts) {
    if (document.getElementById(id)) continue;
    const link = document.createElement("link");
    link.id = id;
    link.rel = "preload";
    link.as = "font";
    link.type = "font/woff2";
    link.href = href;
    link.crossOrigin = "anonymous";
    document.head.appendChild(link);
  }
}

// Switches language, persists it, updates <html lang>, and re-renders every
// section from already-cached state (allReadings/lastForecast/lastBacktest/
// lastDrift/lastCoverage) — no re-fetch, no reload. Safe to call before the
// first data load resolves: every render* function already degrades to its
// own empty/loading state when passed empty readings.
function applyLanguage(lang) {
  setLang(lang);
  applyStaticStrings();
  applyDevanagariFont();
  renderFreshness(allReadings, lastForecast);
  renderComparisons(allReadings);
  renderHistory(allReadings);
  renderChart(allReadings, currentRange);
  renderHero(allReadings, lastForecast);
  renderStaleBanner(lastForecast, lastCalibration);
  renderTodaysRead(allReadings);
  renderModelSignal(lastForecast, allReadings, lastBacktest, lastCoverage, lastDrift);
  renderDriverContext(lastForecast);
  renderCalculator(allReadings, lastForecast);
  renderForecastVsActual(lastBacktest);
  renderMethodology(lastForecast, lastBacktest, lastDrift, lastCoverage);
  updateOfflineBanner();
  const toggle = document.getElementById("lang-toggle");
  if (toggle) toggle.textContent = lang === "hi" ? "EN" : "हिं"; // shows the OTHER language — tapping switches to it
}

// ─── INIT ─────────────────────────────────────────────────────────────────────

(async function init() {
  // Apply the starting language (persisted choice, or navigator.language default for a
  // first-time visitor — see getLang() in i18n.js) to every static string immediately,
  // before any data fetch. The synchronous head script in index.html already set
  // <html lang> and preloaded the Devanagari font before first paint if needed; this
  // covers the actual text content, which needs the DOM (post-parse) to exist.
  applyStaticStrings();
  applyDevanagariFont();
  const langToggle = document.getElementById("lang-toggle");
  if (langToggle) {
    langToggle.textContent = currentLang === "hi" ? "EN" : "हिं";
    langToggle.addEventListener("click", () => {
      applyLanguage(currentLang === "hi" ? "en" : "hi");
    });
  }

  bindRangeToggle();
  bindCalculatorInputs();
  initBottomNav();
  initPullToRefresh();
  initScrollReveals();

  // Φ16-2: register offline/online listeners before data load so they catch mid-load state changes.
  window.addEventListener("offline", updateOfflineBanner);
  window.addEventListener("online", () => {
    const offlineBanner = document.getElementById("offline-banner");
    if (offlineBanner) offlineBanner.hidden = true;
    renderStaleBanner(lastForecast, lastCalibration); // re-evaluate stale-banner now we're connected
  });

  // Ambient header: add elevation (.scrolled → border + shadow) only when content
  // is scrolling under the header. Passive listener — no layout work on scroll.
  const appHeader = document.getElementById("utility-row");
  if (appHeader) {
    const onScroll = () => appHeader.classList.toggle("scrolled", window.scrollY > 0);
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll(); // set initial state for pages loaded at non-zero scroll position
  }

  // D3: Refresh button — works in both browser and standalone mode.
  const refreshBtn = document.getElementById("refresh-btn");
  if (refreshBtn) refreshBtn.addEventListener("click", refreshData);

  // D4/D5: iOS help panel — revealed only in standalone mode.
  const helpBtn   = document.getElementById("pwa-help-btn");
  const helpPanel = document.getElementById("pwa-help-panel");
  const helpClose = document.getElementById("pwa-help-close");

  if (IS_STANDALONE) {
    if (helpBtn) {
      helpBtn.hidden = false;
      helpBtn.addEventListener("click", () => {
        if (helpPanel) helpPanel.hidden = !helpPanel.hidden;
      });
    }
    if (helpClose) {
      // ✕ sets the dismissed flag (FIX 1) — auto-open won't re-fire this session.
      // Manual ? toggle does not set the flag; only explicit dismissal does.
      helpClose.addEventListener("click", () => {
        pwaHelpDismissed = true;
        if (helpPanel) helpPanel.hidden = true;
      });
    }
  }

  // iOS Add-to-Home-Screen prompt: shown only to iOS Safari visitors not already
  // running standalone, self-dismisses permanently via localStorage (unlike the
  // pwa-help panel above, which is a per-session reminder for already-installed
  // users -- this is a one-time nudge, so it shouldn't keep coming back once
  // dismissed). Pure DOM/localStorage work, no data dependency -- runs before
  // any fetch below so it never competes with or delays the price render.
  if (IS_IOS && !IS_STANDALONE) {
    let dismissed = false;
    try {
      dismissed = localStorage.getItem(INSTALL_PROMPT_DISMISSED_KEY) === "1";
    } catch {
      // Storage access can throw (private browsing, disabled storage) -- treat
      // as not-dismissed rather than blocking the banner over a read failure.
    }
    if (!dismissed) {
      const installPanel = document.getElementById("install-prompt-panel");
      const installClose = document.getElementById("install-prompt-close");
      if (installPanel) installPanel.hidden = false;
      if (installClose) {
        installClose.addEventListener("click", () => {
          if (installPanel) installPanel.hidden = true;
          try {
            localStorage.setItem(INSTALL_PROMPT_DISMISSED_KEY, "1");
          } catch {
            // Best-effort persistence -- if storage is unavailable the banner
            // just reappears next visit, which is a degraded UX, not a bug.
          }
        });
      }
    }
  }

  // First-glance orientation strip: shown to every visitor until dismissed once
  // (same one-time-nudge localStorage pattern as the install prompt above, not
  // gated to iOS/standalone — this one's for everybody). Pure DOM/localStorage
  // work, no data dependency, runs before any fetch below for the same reason
  // the install prompt does.
  {
    let firstVisitDismissed = false;
    try {
      firstVisitDismissed = localStorage.getItem(FIRST_VISIT_DISMISSED_KEY) === "1";
    } catch {
      // Storage access can throw (private browsing, disabled storage) -- treat
      // as not-dismissed rather than blocking the strip over a read failure.
    }
    if (!firstVisitDismissed) {
      const firstVisitPanel = document.getElementById("first-visit-panel");
      const firstVisitClose = document.getElementById("first-visit-close");
      if (firstVisitPanel) firstVisitPanel.hidden = false;
      if (firstVisitClose) {
        firstVisitClose.addEventListener("click", () => {
          if (firstVisitPanel) firstVisitPanel.hidden = true;
          try {
            localStorage.setItem(FIRST_VISIT_DISMISSED_KEY, "1");
          } catch {
            // Best-effort persistence -- if storage is unavailable the strip
            // just reappears next visit, which is a degraded UX, not a bug.
          }
        });
      }
    }
  }

  // Share-a-snapshot: Web Share API where available (mobile browsers, most
  // desktop browsers as of 2026), clipboard-copy + toast fallback otherwise
  // (older desktop browsers). Shares the current displayed price as text plus
  // the page URL -- NOT an attached image; letting the URL carry the share
  // means WhatsApp/Telegram/etc. pull the live og:image preview themselves via
  // the existing OG pipeline (og.html -> og.png, refreshed every cycle) rather
  // than this needing to fetch/attach a static image file, which would also
  // go stale the moment the price next updates.
  let shareToastTimeout = null;
  function showToast(message) {
    const el = document.getElementById("share-toast");
    if (!el) return;
    el.textContent = message;
    el.hidden = false;
    requestAnimationFrame(() => el.classList.add("toast--visible"));
    if (shareToastTimeout) clearTimeout(shareToastTimeout);
    shareToastTimeout = setTimeout(() => {
      el.classList.remove("toast--visible");
      setTimeout(() => { el.hidden = true; }, 250);
    }, 2200);
  }

  async function shareSnapshot() {
    const shareText = displayedPrice != null
      ? t("shareTextWithPrice", { price: fmtINR(displayedPrice) })
      : t("shareTextGeneric");
    // Strip query/hash -- this page has none that matter to a recipient, and a
    // bare canonical URL is what actually matches og:url in the OG tags.
    const shareUrl = `${window.location.origin}${window.location.pathname}`;

    if (navigator.share) {
      try {
        await navigator.share({ title: t("pageTitle"), text: shareText, url: shareUrl });
      } catch (err) {
        // AbortError fires when the visitor just closes the native share sheet
        // without picking anything -- expected, not a failure worth logging.
        if (err.name !== "AbortError") console.error("Share failed:", err);
      }
      return;
    }

    try {
      await navigator.clipboard.writeText(shareUrl);
      showToast(t("shareCopied"));
    } catch (err) {
      console.error("Clipboard copy failed:", err);
    }
  }

  const shareBtn = document.getElementById("share-btn");
  if (shareBtn) shareBtn.addEventListener("click", shareSnapshot);

  // Kick off every data fetch immediately, in parallel — none of them has a real
  // dependency on another fetch's *result*, only on the RENDER order below. This
  // replaces what was a 3-stage serialized waterfall (prices -> forecast ->
  // {backtest,commentary,drift,coverage}, each stage starting only after the
  // previous one resolved) with one parallel batch. Combined with loadJSON's
  // LOAD_TIMEOUT_MS, this is the fix for the render-smoke "fresh-load" timeouts —
  // see docs/RUNBOOK.md's render-smoke section for the incident and diagnosis
  // (two render-blocking third-party <script> tags plus this serialized chain
  // together could exceed the smoke test's cold-load budget).
  const pricesPromise = load();
  const fcPromise = loadJSON(FORECAST_URL).catch(err => {
    if (typeof Sentry !== "undefined") Sentry.captureException(err, { extra: { url: FORECAST_URL } });
    return null;
  });
  const btPromise = loadJSON(BACKTEST_URL);
  const driftPromise = loadJSON(DRIFT_URL);
  const coveragePromise = loadJSON(COVERAGE_URL);
  const calibrationPromise = loadJSON(CALIBRATION_URL);
  // These four are only actually consumed much later (via Promise.allSettled, after
  // awaiting price+forecast and rendering the hero) — attach an inert catch to each
  // now so an early rejection (e.g. a timeout firing while we're still waiting on
  // prices) doesn't surface as a spurious unhandledrejection console error / Sentry
  // event in the meantime. Promise.allSettled below still sees the real outcome —
  // this doesn't replace the promise, just marks it handled.
  [btPromise, driftPromise, coveragePromise, calibrationPromise].forEach(p => p.catch(() => {}));

  // Load prices (critical path)
  try {
    allReadings = await pricesPromise;
  } catch (err) {
    if (typeof Sentry !== "undefined") Sentry.captureException(err, { extra: { url: DATA_URL } });
    console.error(err);
    console.warn("Could not load price data. If you just deployed, run the workflow once from the Actions tab.");
    const skelEl = document.getElementById("hero-skeleton");
    if (skelEl) skelEl.hidden = true;
    const priceEl = document.getElementById("hero-price");
    if (priceEl) { priceEl.textContent = t("errPriceUnavailable"); priceEl.hidden = false; }
    const commentaryTextEl = document.getElementById("commentary-text");
    if (commentaryTextEl) {
      commentaryTextEl.textContent = t("errCouldntLoadPrice");
      commentaryTextEl.hidden = false;
    }
    // Everything else renders from allReadings — degrade history/methodology honestly
    // too instead of leaving them on their skeleton placeholders forever.
    const historySkel = document.getElementById("history-skeleton");
    if (historySkel) historySkel.hidden = true;
    const historyBody = document.getElementById("history-body");
    if (historyBody) {
      historyBody.innerHTML = `<tr><td colspan="5" class="empty">${t("errCouldntLoadHistory")}</td></tr>`;
    }
    const methBody = document.getElementById("methodology-body");
    if (methBody) {
      methBody.innerHTML = `<p class="meth-loading">${t("errCouldntLoadMethodology")}</p>`;
    }
    // Tier 1/2 CLS fix (2026-08-10): comparison/model-signal/track-record/driver-context
    // sections no longer start `hidden` in HTML (so their skeletons/placeholders are genuinely
    // visible from page load) — renderComparisons()/renderModelSignal()/renderForecastVsActual()/
    // renderDriverContext() normally hide them on their own rare-edge fallback paths, but none
    // of those run on this total-failure path, so hide explicitly here too or they'd stay stuck
    // showing an unresolved skeleton forever.
    const comparisonSection = document.getElementById("comparison-section");
    if (comparisonSection) comparisonSection.hidden = true;
    const modelSignalSection = document.getElementById("model-signal-section");
    if (modelSignalSection) modelSignalSection.hidden = true;
    const trackRecordSection = document.getElementById("section-track-record");
    if (trackRecordSection) trackRecordSection.hidden = true;
    const driverContextSection = document.getElementById("driver-context-section");
    if (driverContextSection) driverContextSection.hidden = true;
    updateOfflineBanner();
    return;
  }

  // Render everything that doesn't need forecast immediately.
  renderFreshness(allReadings);
  renderComparisons(allReadings);
  renderHistory(allReadings);
  renderChart(allReadings, "30");

  // Await forecast, then render hero (hides skeleton, shows verdict).
  const fc = await fcPromise;
  renderFreshness(allReadings, fc); // re-render now IBJA-primary state is known
  renderHero(allReadings, fc);
  renderStaleBanner(fc);
  renderTodaysRead(allReadings);
  renderModelSignal(fc, allReadings);  // first render — coverage/drift not loaded yet, reliability note uses its own fallback text
  renderDriverContext(fc);
  renderCalculator(allReadings, fc);
  lastForecast = fc;
  // Φ16-5: stagger on initial load — consistent with refreshData() behaviour
  staggerEnter([
    document.getElementById("comparison-section"),
    document.querySelector(".karat-strip"),
    document.getElementById("calculator-section"),
    document.getElementById("model-signal-section"),
    document.getElementById("driver-context-section"),
  ]);
  updateOfflineBanner(); // update offline banner text now allReadings is populated

  // Remaining optional data (already in flight above; all gracefully degrade on failure).
  const [bt, drift, coverage, calibration] = await Promise.allSettled([
    btPromise,
    driftPromise,
    coveragePromise,
    calibrationPromise,
  ]);

  // Report any optional-fetch failures so silent pipeline breaks surface in Sentry.
  if (typeof Sentry !== "undefined") {
    const optionalUrls = [BACKTEST_URL, DRIFT_URL, COVERAGE_URL, CALIBRATION_URL];
    [bt, drift, coverage, calibration].forEach((r, i) => {
      if (r.status === "rejected") Sentry.captureException(r.reason, { extra: { url: optionalUrls[i] } });
    });
  }

  const btData = bt.status === "fulfilled" ? bt.value : null;
  lastBacktest = btData;
  lastDrift    = drift.status === "fulfilled" ? drift.value : null;
  lastCoverage = coverage.status === "fulfilled" ? coverage.value : null;
  lastCalibration = calibration.status === "fulfilled" ? calibration.value : null;
  renderModelSignal(fc, allReadings, btData, lastCoverage, lastDrift);  // re-render — coverage/drift now loaded
  renderStaleBanner(fc, lastCalibration);  // re-render — calibration confidence now loaded (no-op unless price_source is ibja_calibrated)
  renderForecastVsActual(btData);
  renderMethodology(fc, btData, lastDrift, lastCoverage);

  // Dismiss chart callout when tapping outside the chart canvas (Φ8C'/Ψ3C.3)
  const chartCanvas = document.getElementById("chart");
  document.addEventListener("click", (e) => {
    if (chartCanvas && e.target !== chartCanvas && chartPinnedIndex !== null) {
      chartPinnedIndex = null;
      if (chart) chart.update("none");
    }
  }, { passive: true, capture: false });
})();
