// i18n.js — Language catalogue + translate helper (en/hi). Plain script, no module system,
// loaded before app.js so STRINGS/t()/getLang()/setLang() are ordinary globals — same
// convention app.js itself uses.
//
// Every catalogue entry is either a plain string (static, no interpolation) or a function
// `(params) => string` for anything with interpolated values OR conditional clause structure.
// Functions, not `{token}` substitution, because several English strings are built by
// conditionally appending a clause (see verdict.reasonDown/Up) or joining independent
// sentences (driver.branch1*) — Hindi needs to own the FULL sentence shape, not have an
// English-ordered template with numbers dropped in. A naive placeholder-replacer can't
// reorder clauses; a function can.
//
// Numbers/dates arrive PRE-FORMATTED (via the lang-aware fmtINR/fmtRelative/fmtDate/fmtIST
// in app.js) — this file only owns grammar and word order, never digit formatting itself.

const LANG_STORAGE_KEY = "lang";
const SUPPORTED_LANGS = ["en", "hi"];

// Converts a measured percentage into an honest "N times out of 10" phrase for
// plain-language surfaces (main page). Always FLOORS, never rounds up, so the
// claim can't overstate accuracy -- 78% becomes "about 7 times out of 10", not 8
// (docs/PLAIN_LANGUAGE_AUDIT.md). English-only: reliabilityCoverage and
// calibrationConfidenceAppend below call this directly; their hi equivalents are
// on the pending-native-review list further down (STRINGS.hi has no entry for
// either key right now) rather than a machine translation of the reworded English.
// The exact percentage/sample-size this rounds away is not lost -- it's preserved,
// unrounded, on how-we-know.html (how-we-know-strings.js's methAccurateP2/
// methBandAccuracy* keys read the same source data).
function fractionOutOf10Phrase(pct) {
  const n = Math.max(0, Math.min(10, Math.floor(pct / 10)));
  if (n === 0) return "less than 1 time out of 10";
  if (n === 1) return "about 1 time out of 10";
  return `about ${n} times out of 10`;
}

// Formats a p-value for display without ever printing "0.0000" -- a p-value that
// rounds to zero at the shown precision reads as "exactly zero" (a misreading of
// what the statistic means; it's never literally 0), not "very small". Returns
// { op, text } rather than one joined string because the caller's template needs
// to render "=" vs "<" as its own HTML-entity-spaced token (e.g.
// `p&thinsp;${op}&thinsp;${text}`) -- see how-we-know.js's use of this.
// op is "&lt;" (HTML-entity, since callers innerHTML the result) whenever value
// rounds to 0 at `decimals` places; "=" otherwise. text is always `decimals`
// digits after the point. Returns null for a non-finite/missing value so callers
// can fall back to their own placeholder the same way they already do for a null
// input p-value.
function formatPValue(value, decimals = 4) {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  const smallestRepresentable = Math.pow(10, -decimals);
  if (Math.abs(value) < smallestRepresentable / 2) {
    return { op: "&lt;", text: smallestRepresentable.toFixed(decimals) };
  }
  return { op: "=", text: value.toFixed(decimals) };
}

// G4 (2026-09-25): every accuracy/coverage claim on the page must be HIDDEN,
// never shown with a stale or defaulted number, once its own measurement is
// older than CLAIM_MAX_AGE_DAYS (rule 98a: fail closed, not open). This
// generalizes the pattern app.js's deriveMeasuredBandCoverage introduced for
// data/calibration_band_coverage.json alone (AE1, 2026-09-10) into one shared
// check every claim family uses (band coverage, model-signal reliability note,
// how-we-know.html's coverage/backtest sections, the track-record chart).
// Defined here rather than in app.js because how-we-know.html loads i18n.js
// but NOT app.js (see how-we-know.js's own header comment for why it's a
// separate script) — this is the one file both pages already share.
// A missing/unparseable/future-dated timestamp is NOT fresh, same as a
// too-old one: a clock skew or a malformed field is exactly the kind of
// "couldn't verify" case rule 98a says must deny, not silently pass.
const CLAIM_MAX_AGE_DAYS = 14;

function isMeasurementFresh(isoTimestamp, nowMs = Date.now(), maxAgeDays = CLAIM_MAX_AGE_DAYS) {
  if (typeof isoTimestamp !== "string") return false;
  const generatedMs = Date.parse(isoTimestamp);
  if (Number.isNaN(generatedMs)) return false;
  const ageDays = (nowMs - generatedMs) / 86_400_000;
  return ageDays >= 0 && ageDays <= maxAgeDays;
}

const STRINGS = {
  en: {
    // ── Static shell (index.html) ──────────────────────────────────────────────
    pageTitle: "Gold Rate Today · Is it a good price?",
    // U1 audit (2026-09-23): was "an IBJA-calibrated estimate" -- "calibrated" is
    // jargon a general buyer wouldn't parse. IBJA itself gets its one plain-words
    // explanation in footerBody below; this short meta description just says what
    // the number IS without needing to re-explain the acronym here too.
    pageDescription: "22K gold rate — closely matched to real shop prices, confirmed against live Tanishq retail when reachable. See if today's price is high or low compared to recent weeks.",
    appTitle: "Gold Tracker",
    refreshLabel: "Refresh data",
    pwaHelpBtnLabel: "About auto-refresh on iPhone",
    pwaHelpBtnTitle: "About auto-refresh",
    pwaHelpPanelText: 'iOS limits how often home-screen apps update in the background. Tap <strong>↻</strong> to get the latest prices. If prices remain stuck, open the App Switcher (swipe up and hold), then swipe this app away and reopen from Home Screen — that forces a full reload.',
    dismissLabel: "Dismiss",
    installPromptText: 'Add this to your Home Screen for quicker access: tap <strong>Share</strong>, then <strong>Add to Home Screen</strong>.',
    // R2 (audit 2026-09-04): was a hand-typed "checked every 3 hours" claim
    // that stopped being true once scheduled-trigger reliability degraded
    // (docs/RUNBOOK.md). params is null until data/cadence_metrics.json
    // resolves (app.js's renderCadenceStrings) — the fallback branch never
    // states a specific number it can't back up. X1b (audit 2026-09-05):
    // added the p90 worst-case alongside the median -- the median alone
    // hides the tail a real visitor can land on.
    // U1 audit (2026-09-23): dropped the literal "n=${params.n}" clause -- a
    // banned pattern (docs/PLAIN_LANGUAGE_AUDIT.md). The hours/worst-case/as-of
    // figures it sat next to are unaffected and stay in place.
    firstVisitText: (params) => params
      ? `The price of 22K gold in shops, checked about every ${params.hours}h (worst case recently ~${params.p90Hours ?? params.hours}h, as of ${params.asOf}) and confirmed against Tanishq's live rate when possible. We always say plainly when a price is an estimate.`
      : "The price of 22K gold in shops, checked on a regular schedule and confirmed against Tanishq's live rate when possible. We always say plainly when a price is an estimate.",
    shareLabel: "Share",
    shareTextWithPrice: ({ price }) => `Today's 22K gold price is ₹${price}/gram — check Gold Tracker`,
    shareTextGeneric: "Check today's gold price on Gold Tracker",
    shareCopied: "Link copied!",
    heroAriaLabel: "Current 22K gold price and buying verdict",
    eyebrow: "22K gold · per gram",
    heroLocation: "Tanishq retail price · pan-India",
    todayLabel: "today",
    sinceLastLabel: "since last",
    sparklineLabelLeft: "7 days",
    comparisonAriaLabel: "How today's price compares to recent averages",
    cmpHeading7d: "vs 7-day avg",
    cmpHeading30d: "vs 30-day avg",
    cmpHeadingFloor: "30-day floor",
    karatAriaLabel: "24K and 18K gold prices",
    karatLabel24: "24 KT",
    karatLabel18: "18 KT",
    karatSub24: "per gram · 99.9% pure",
    karatSub18: "per gram · 75% pure",

    // ── Purchase calculator ─────────────────────────────────────────────────────
    calcAriaLabel: "Purchase cost calculator",
    calcHeading: "How much would you pay?",
    calcGramsLabel: "Grams",
    calcGramsAriaLabel: "Quantity in grams",
    calcPresetsLegend: "Jewellery type",
    calcPresetCoins: "Coins & plain chains",
    calcPresetCoinsRange: "3–8% of gold value, typically 5%",
    calcPresetPlain: "Plain bangles & rings",
    calcPresetPlainRange: "8–12% of gold value, typically 10%",
    calcPresetIntricate: "Intricate or antique designs",
    calcPresetIntricateRange: "15–25% of gold value, typically 20%",
    calcPresetCustom: "Custom",
    calcPresetCustomHint: "Know your jeweller's exact rate? Enter it below.",
    calcMakingModePct: "% of gold value",
    calcMakingModePerGram: "₹ per gram",
    calcCustomValueLabel: "Making charge",
    calcCustomInvalid: "Enter a making charge of 0 or more.",
    calcKaratLabel22: "22 KT",
    calcRowGoldValue: "Gold value",
    calcRowMaking: "Making charge",
    calcRowMakingWithPct: ({ pct }) => `Making charge (${pct}%)`,
    calcRowGst: ({ pct }) => `GST (${pct}%)`,
    calcRowTotal: "Total",
    calcRangeLabel: ({ range }) => `Range ${range}`,
    calcOtherKaratsRange: ({ k24, k18 }) => `24 KT: ${k24} · 18 KT: ${k18}`,
    calcRateUsedIbja: ({ rate }) => `Rate used: 22K ₹${rate}/g — IBJA-based estimate`,
    calcRateUsedFusion: ({ rate }) => `Rate used: 22K ₹${rate}/g — market-consensus estimate`,
    calcRateUsedTanishq: ({ rate }) => `Rate used: 22K ₹${rate}/g — Tanishq store rate`,
    calcEstimatedNote: "Today's price is an estimate, so this total is too.",
    calcStaleNote: ({ rel }) => `Uses the last confirmed price, from ${rel}.`,
    calcEstimateStoresVary: "Estimate — stores vary.",
    calcDisclaimer:
      "Your jeweller's bill will differ — hallmarking (HUID) fees, stones, wastage and store pricing aren't included.",
    calcEmptyState: "Enter a quantity to see the cost.",

    commentaryAriaLabel: "Market commentary",
    todaysReadEyebrow: "Today's read",
    modelSignalAriaLabel: "How today's price compares to recent history",
    goodPriceHeading: "Is today a good time to buy?",
    driverAriaLabel: "What is driving gold prices",
    driverHeading: "What's moving the price?",
    chartAriaLabel: "Price trend chart",
    priceTrendHeading: "Price trend",
    rangeToggleAriaLabel: "Chart time range",
    rangeAll: "All",
    sectionKaratNote: "22K · per gram",
    chartCanvasAriaLabel: "Gold price trend chart",
    historyAriaLabel: "Price history",
    historyHeading: "History",
    thWhen: "When",
    thDelta: "Δ 22K",
    loadingText: "Loading…",
    historyCardsAriaLabel: "Price readings",
    trackRecordAriaLabel: "Past estimate accuracy — flat-hold vs actual prices",
    trackRecordHeading: "How past estimates have held up",
    trackRecordCaption: "30 recent five-day windows: flat-hold estimate (dashed) vs what actually happened (gold)",
    trackRecordChartAriaLabel: "Past flat-hold estimates vs actual gold prices",
    methodologySummary: "How this works — and how accurate it's been",
    // U1 audit (2026-09-23): "calibrate it to match" -> "adjust it to match" (no
    // jargon), and IBJA now gets its one plain-words explanation right here, the
    // single most prominent explanatory sentence on the page (U1's "explained
    // once, or avoided" rule) -- short mentions elsewhere (e.g. calcRateUsedIbja's
    // "IBJA-based estimate") rely on this one. Also dropped the literal
    // "n=${params.n}" clause below (same fix as firstVisitText above).
    footerBody: (params) => `We use <a href="https://ibjarates.com/" target="_blank" rel="noopener">IBJA</a> (the India Bullion and Jewellers Association, which publishes an official gold price every working day) and adjust it to match real shop prices, checking against <a href="https://www.tanishq.co.in/gold-rate.html?lang=en_IN" target="_blank" rel="noopener">Tanishq</a>'s live rate when we can. ${
      params
        ? `Prices are checked about every ${params.hours}h (worst case recently ~${params.p90Hours ?? params.hours}h, as of ${params.asOf})`
        : "Prices are checked on a regular schedule"
    } — IBJA itself only updates once a day, so the number sometimes stays the same for a while.`,
    footerMuted: "Not financial advice. Rates are indicative.",
    bottomNavAriaLabel: "Page sections",
    navHome: "Home",
    navTrend: "Trend",
    navHistory: "History",
    navInfo: "Info",
    langToggleAriaLabel: "Switch language",

    // ── Verdict (computeVerdict) ────────────────────────────────────────────────
    verdictHeadlineUnknown: "Not enough data yet",
    verdictReasonUnknown: "Check back once we've collected a few more readings.",
    verdictHeadlineDown: "Getting cheaper this week",
    verdictHeadlineUp: "Getting pricier this week",
    verdictHeadlineFlat: "Steady this week",
    verdictReasonDown: ({ delta, avgDelta }) =>
      avgDelta != null
        ? `Down ₹${delta} this week, and ₹${avgDelta} below the usual price for the month.`
        : `Down ₹${delta} this week.`,
    verdictReasonUp: ({ delta, avgDelta }) =>
      avgDelta != null
        ? `Up ₹${delta} this week, and ₹${avgDelta} above the usual price for the month.`
        : `Up ₹${delta} this week.`,
    verdictReasonFlatBarely: "Barely moved this week — nothing to react to.",
    verdictReasonFlatMoved: ({ dirWord, amount }) =>
      `Prices ${dirWord} ₹${amount} this week — that's normal movement, nothing to react to.`,
    dirWordUp: "edged up",
    dirWordDown: "edged down",
    dirWordUnchanged: "unchanged",
    heroFallbackReason: "Awaiting first price reading.",
    noChangeLabel: "no change",

    // ── Comparison cards ────────────────────────────────────────────────────────
    // avgLabel7d/30d are bare noun phrases (no trailing postposition) — used both
    // standalone (cmpAtAvg branch) and inside cmpCheaperThan/cmpPricierThan, which
    // supply their own comparative postposition. Distinct from cmpHeading7d/30d
    // above (the static card heading), which doesn't need to grammatically combine
    // with anything else.
    avgLabel7d: "7d avg",
    avgLabel30d: "30d avg",
    cmpCheaperThan: ({ avgLabel }) => `cheaper than ${avgLabel}`,
    cmpPricierThan: ({ avgLabel }) => `pricier than ${avgLabel}`,
    cmpAtAvg: "at avg",
    cmpNotEnoughData: "not enough data",
    cmpAtLow: "at low",
    cmpLowestPrice: "this month's lowest price",
    cmpAboveLowest: "above this month's lowest",

    // ── Today's read (composeTodaysRead) ───────────────────────────────────────
    readNoSignals: "We don't have enough price history yet to say much about today — check back once a few more readings come in.",
    readNoTrendCheap: "Today's price is on the low side for the month.",
    readNoTrendHigh: "Today's price is on the higher side for the month.",
    readNoTrendMid: "Today's price is sitting around its usual range this month.",
    readCheapStillFalling: "Today's price is on the low side for the month, and it's still sliding — it hasn't leveled off yet.",
    readCheapSteadying: "Today's price is on the low side for the month, and it looks like it's steadying after a recent dip.",
    readHighRising: "Today's price is on the higher side for the month, and it's still climbing.",
    readHighSlowed: "Today's price is on the higher side for the month, though the climb has slowed.",
    readFalling: "Prices have eased over the past month, though today isn't especially cheap yet.",
    readRising: "Prices have climbed over the past month, though today isn't especially expensive yet.",
    readFlat: "Prices have been fairly steady this month — today sits around the usual range.",

    // ── Good-price signals ──────────────────────────────────────────────────────
    verdictLeadCheap: "You're paying less than usual this month",
    verdictLeadBelowMid: "You're paying a little less than usual this month",
    verdictLeadMid: "You're paying about the usual amount this month",
    verdictLeadHigh: "You're paying a bit more than usual this month",
    supportLine1Cheap: "Cheaper than most days this month.",
    supportLine1BelowMid: "A bit below the usual price this month.",
    supportLine1Mid: "Right around the middle for this month.",
    supportLine1High: "Pricier than most days this month.",
    proofLineCheaper: ({ days, total }) => `Cheaper than ${days} of the last ${total} days.`,
    proofLinePricier: ({ days, total }) => `Pricier than ${days} of the last ${total} days.`,
    dataSuffNote: ({ n }) => `Only ${n} distinct days in the window — treat as indicative.`,
    supportLine2Below: ({ amount }) => `₹${amount} below the usual price for the month.`,
    supportLine2Above: ({ amount }) => `₹${amount} above the usual price for the month.`,
    supportLine2At: "Right at the usual price for the month.",
    divergenceNote: "(These two don't quite agree — one counts days, the other measures the actual rupee gap. We go with the day-count for the headline above.)",
    goodPriceTomorrow: ({ low, high }) => `Likely to stay between <strong>₹${low}</strong> and <strong>₹${high}</strong> by the next trading day.`,
    // U1 audit (2026-09-23): "volatile"/"volatility" are on the banned-term list
    // (docs/PLAIN_LANGUAGE_AUDIT.md) -- reworded to "swinging"/"bouncing around",
    // same meaning, no jargon.
    volNoteElevated: ({ z }) => `Gold has been swinging more than usual lately — about ±₹${z} over 5 days.`,
    volNoteCalm: ({ z }) => `Gold has been steadier than usual lately — about ±₹${z} over 5 days.`,
    volNoteNormal: ({ z }) => `Gold has been moving about ±₹${z} over 5 days lately.`,
    volNoteFallback: ({ z }) => `Gold's price typically moves about ±₹${z} over 5 days.`,
    weeklyMovementNote: ({ amount, pairs }) => `Looking back, gold has typically moved about ₹${amount} from one week to the next (based on ${pairs} weekly comparisons).`,
    weeklyMovementSuffAppend: ({ n }) => ` (Only ${n} distinct days in this 90-day window so far — treat as indicative.)`,

    // ── Reliability (promoted from methodology accordion) ──────────────────────
    // U1/U2 audit (2026-09-23): was "right {pct}% of the time (checked {n}
    // times)" -- a raw percentage read as a statistical claim, not a buyer-plain
    // one. fractionOutOf10Phrase floors so this never overstates (see its own
    // comment above). The exact percentage and n are not lost -- they're on
    // how-we-know.html (methAccurateP2CoveragePct), unrounded.
    reliabilityCoverage: ({ pct }) => `The real price has stayed inside the range we show ${fractionOutOf10Phrase(pct)} so far.`,
    reliabilityUnknown: "Still building a track record for this — check back later.",
    reliabilityDriftOnTrack: "Recent accuracy has stayed in line with the historical average.",
    reliabilityDriftWatch: "Recent accuracy has drifted a bit from the historical average — we're keeping an eye on it.",
    reliabilityDriftRetrain: "Recent errors have run notably higher than the historical average — we're due to recalibrate.",

    // ── 90-day band position ────────────────────────────────────────────────────
    band90dCheaper: ({ pct, n }) => `Over the past 90 days: cheaper than ${pct}% of the ${n} days.`,
    band90dMoreExpensive: ({ pct, n }) => `Over the past 90 days: more expensive than ${pct}% of the ${n} days.`,
    band90dSuffAppend: ({ n }) => ` (Only ${n} distinct days in this window so far — treat as indicative.)`,

    // ── 30-day trend residual ───────────────────────────────────────────────────
    trendCheapStillFalling: ({ slope }) => `Cheap, but still falling — today is well below its usual trend for the month (dropping about ₹${slope} a day).`,
    trendCheapSteadying: "Cheap, and steadying — despite the recent dip, today's price is back close to its usual trend for the month.",
    trendFalling: ({ slope }) => `Prices have been slipping about ₹${slope} a day this month.`,
    trendRising: ({ slope }) => `Prices have been climbing about ₹${slope} a day this month.`,
    trendFlat: "Prices have been steady this month, close to their usual trend.",

    // ── 90-day support distance ─────────────────────────────────────────────────
    supportCheapAtSupport: ({ low, n }) => `Cheap, and sitting right at its 3-month low (₹${low}) — it hasn't dropped below this in ${n} days.`,
    supportCheapNotAtSupport: ({ pct, low }) => `Cheap, but still ${pct}% above its lowest price in 3 months (₹${low}).`,
    supportNotCheapAtSupport: ({ low }) => `Right at its lowest price in 3 months (₹${low}), even though it's not among the cheapest days this month.`,
    supportNotCheapNotAtSupport: ({ pct, low, n }) => `${pct}% above its lowest price in 3 months (₹${low}, over the last ${n} days).`,
    supportSuffAppend: ({ n }) => ` (Only ${n} distinct days in this 90-day window so far — treat as indicative.)`,

    // ── State banners ────────────────────────────────────────────────────────────
    bannerIbjaToday: "This is today's estimated price, based on IBJA's official gold benchmark — we couldn't confirm it against the shop rate just now.",
    bannerIbjaCarryForward: ({ weekday }) => `This is an estimated price, based on IBJA's ${weekday} close (their most recent official rate) — we couldn't confirm it against the shop rate just now.`,
    // AE1 (audit 2026-09-10): coverage/n now come from data/calibration_band_coverage.json's
    // actual walk-forward measurement (app.js's deriveMeasuredBandCoverage), never
    // ml.calibration.NOMINAL_COVERAGE_PCT (a hardcoded design target) -- coverage/n are
    // null, not defaulted to that target, whenever no fresh measurement exists, so this
    // falls through to the amount-only clause below rather than asserting an unbacked
    // number.
    // U1/U2 audit (2026-09-23): was "...about {coverage}% of the time so far
    // (n={n} weeks measured)" -- literal "n=" is a banned pattern
    // (docs/PLAIN_LANGUAGE_AUDIT.md), and a raw percentage+sample-size clause is
    // exactly the "coverage 73% (n=63...)" shape GG's spec calls out. Reworded to
    // the same floored fraction phrase as reliabilityCoverage above; the sample
    // size (weeks measured) moves to how-we-know.html's "Band accuracy" section,
    // which reads the same calibration_band_coverage.json field.
    calibrationConfidenceAppend: ({ amount, coverage, n }) => coverage != null && n != null
      ? ` Based on past comparisons, the real price has landed within about ₹${amount}/gram of this estimate ${fractionOutOf10Phrase(coverage)} so far.`
      : ` Based on past comparisons, the real price lands within about ₹${amount}/gram of this estimate.`,
    // R3: appended only when Tanishq confirmation itself has been silent for
    // TIER_DEGRADED_THRESHOLD_H, not just this cycle -- distinct from the
    // routine (silent) ibja_calibrated case above it.
    bannerTanishqLongSilent: ({ rel }) => ` Tanishq hasn't confirmed this price in a while — the last successful check was ${rel}.`,
    bannerFusion: ({ sources }) => `This is an estimated price based on other jewellers' rates (${sources}) — we couldn't reach Tanishq or IBJA just now.`,
    bannerStaleConfirmed: ({ rel }) => `We couldn't get a live price update — this is the last confirmed price, from ${rel}.`,
    unknownTime: "an unknown time",
    bannerRefreshFailed: ({ rel }) => `Couldn't refresh — this is the last update, from ${rel}`,
    fusionSourceGrt: "GRT",
    fusionSourceMalabar: "Malabar",
    fusionSourceKalyan: "Kalyan",
    fusionSourceFallback: "retail consensus",

    // ── Freshness pill ───────────────────────────────────────────────────────────
    freshnessEstimated: ({ rel }) => `Estimated · ${rel}`,
    freshnessEstimatedAria: ({ rel }) => `Estimated retail price, IBJA benchmark updated ${rel}`,
    freshnessAsOfClose: ({ weekday }) => `As of ${weekday} close`,
    freshnessAsOfCloseAria: ({ weekday }) => `Estimated retail price, as of ${weekday}'s IBJA close`,
    freshnessConsensus: ({ rel }) => `Consensus estimate · ${rel}`,
    freshnessConsensusAria: ({ rel }) => `Retail consensus estimate, updated ${rel}`,
    freshnessAwaiting: "Awaiting first reading",
    freshnessNotUpdating: ({ rel }) => `Not updating · ${rel}`,
    freshnessNotUpdatingAria: ({ rel }) => `Not updating, last updated ${rel}`,
    freshnessStale: ({ rel }) => `Stale · ${rel}`,
    freshnessStaleAria: ({ rel }) => `Data stale, last updated ${rel}`,
    freshnessOkAria: ({ rel }) => `Updated ${rel}`,

    // ── Offline banner ───────────────────────────────────────────────────────────
    offlineWithTime: ({ rel }) => `You're offline — showing prices from ${rel}`,
    offlineNoData: "You're offline — no prices loaded yet",

    // ── Hero ──────────────────────────────────────────────────────────────────────
    heroEstimatedRange: ({ low, high }) => `estimated range ₹${low}–₹${high}`,
    heroLastConfirmed: ({ price, date }) => `Tanishq last confirmed: ₹${price} (${date})`,
    sparklineRange: ({ min, max }) => `Low ₹${min} · High ₹${max}`,
    sparklineAria: ({ dir, delta }) => `7-day price trend: ${dir} ₹${delta}`,
    trendDirUp: "up",
    trendDirDown: "down",

    // ── History ───────────────────────────────────────────────────────────────────
    historySince: ({ date }) => `Since ${date}`,
    historyRange: ({ from, to }) => `${from} – ${to}`,
    historyRangeCard: ({ from, to }) => `${from}–${to}`,
    historyNoReadings: "No readings yet.",
    historyShowMore: ({ n }) => `Show ${n} more`,
    historyShowLess: "Show less",

    // ── Chart labels (Chart.js legend/tooltip) ─────────────────────────────────
    chart22kLabel: "22K (₹/g)",
    chart22kTooltip: ({ value }) => `22K: ₹${value}`,
    chartWhatHappened: "What happened",
    chartFlatHoldEstimate: "Flat-hold estimate",
    chartTooltipLabeled: ({ label, value }) => `${label}: ₹${value}`,

    // ── Driver context ────────────────────────────────────────────────────────────
    driverUpInrDominant: ({ total, inr, gold }) => `Gold is up about ₹${total} this week — mostly a weaker rupee (₹${inr}), plus a bit from global gold prices (₹${gold}).`,
    driverUpGoldDominant: ({ total, gold, inr }) => `Gold is up about ₹${total} this week — mostly global gold prices (₹${gold}), plus a bit from the rupee (₹${inr}).`,
    driverUpMixed: ({ total }) => `Gold is up about ₹${total} this week, from a mix of global prices and the rupee.`,
    driverDownInrDominant: ({ total, inr }) => `Gold is down about ₹${total} this week — mostly a stronger rupee (₹${inr}), with global gold roughly flat.`,
    driverDownGoldDominant: ({ total, gold, inrNote }) => `Gold is down about ₹${total} this week — global gold fell about ₹${gold}${inrNote}.`,
    driverDownGoldDominantInrNoteAdded: ({ inr }) => `, and the rupee added back ₹${inr}`,
    driverDownGoldDominantInrNoteFlat: ", with the rupee roughly flat",
    driverDownMixed: ({ total }) => `Gold is down about ₹${total} this week, from a mix of global prices and the rupee.`,
    driverRupeeWeakened: ({ pct, mechanism }) => `The rupee has weakened about ${pct}% this month —${mechanism}`,
    driverRupeeStrengthened: ({ pct, mechanism }) => `The rupee has strengthened about ${pct}% this month —${mechanism}`,
    driverMechanismWeaker: " a weaker rupee makes imported gold pricier in India.",
    driverMechanismStronger: " a stronger rupee makes imported gold cheaper in India.",
    driverGoldUp: ({ pct }) => `Global gold prices are up about ${pct}% this month.`,
    driverGoldDown: ({ pct }) => `Global gold prices are down about ${pct}% this month.`,
    driverPremiumDominated: "Indian gold has moved more than the global price or rupee explain — likely import costs or festival demand at home.",
    driverAllFlat: "Nothing much moved this month — global prices, the rupee, and local demand have all been quiet.",
    driverStateUnavailable: "Global prices and the rupee have been quiet this month — local demand data isn't available to check separately.",

    // ── Accuracy summary (methodology accordion) ────────────────────────────────
    // U2 (2026-09-23): the full technical methodology (verdict rule, next-day
    // range with its p-value, direction-signal detail, drift stats) moved to
    // how-we-know.html/how-we-know-strings.js -- see that file's own header
    // comment. This accordion now shows a short plain summary plus a link.
    // reliabilityDriftOnTrack/Watch/Retrain (already plain, defined above under
    // "Reliability") are reused here rather than duplicated.
    accSummaryIntro: "We check our price estimate against real shop prices regularly, and adjust when it drifts too far off.",
    accSummaryDirectionOff: "We don't try to guess whether prices will rise or fall next — none of the methods we've tested could do it reliably, so we don't show a guess.",
    accSummaryLinkText: "See the full numbers and how we test all of this →",

    // ── Error / degrade paths ────────────────────────────────────────────────────
    errPriceUnavailable: "Price unavailable",
    errCouldntLoadPrice: "Couldn't load the latest price. Check your connection and try again.",
    errCouldntLoadHistory: "Couldn't load price history.",
    // U1 audit (2026-09-23): was "Couldn't load model details" -- "model" is a
    // banned term. This is the error state for the plain accuracy-summary panel
    // (app.js's renderAccuracySummary), not a methodology dump anymore.
    errCouldntLoadMethodology: "This part didn't load — usually because the internet connection dropped. Today's gold price above is not affected. Please refresh the page to try again.",

    // ── Relative time (fmtRelative) ──────────────────────────────────────────────
    relJustNow: "just now",
    relMinAgo: ({ n }) => `${n} min ago`,
    relHoursAgo: ({ n }) => `${n}h ago`,
    relDaysAgo: ({ n }) => `${n}d ago`,
  },

  hi: {
    // ── Static shell (index.html) ──────────────────────────────────────────────
    pageTitle: "आज सोने का भाव · क्या यह सही कीमत है?",
    pageDescription: "22K सोने का भाव — IBJA पर आधारित अनुमान, जब संभव हो तो Tanishq की लाइव कीमत से जांचा गया। देखें कि आज की कीमत हाल के हफ्तों के मुक़ाबले ज़्यादा है या कम।",
    appTitle: "Gold Tracker",
    refreshLabel: "डेटा रीफ़्रेश करें",
    pwaHelpBtnLabel: "iPhone पर ऑटो-रीफ़्रेश के बारे में",
    pwaHelpBtnTitle: "ऑटो-रीफ़्रेश के बारे में",
    pwaHelpPanelText: 'iOS होम-स्क्रीन ऐप्स को बैकग्राउंड में कम बार अपडेट करता है। ताज़ी कीमत के लिए <strong>↻</strong> दबाएं। अगर कीमत अटकी रहे, तो ऐप स्विचर खोलें (ऊपर स्वाइप करके दबाए रखें), फिर इस ऐप को स्वाइप करके हटाएं और होम स्क्रीन से दोबारा खोलें — इससे पूरा रीलोड हो जाएगा।',
    dismissLabel: "बंद करें",
    installPromptText: 'तेज़ी से खोलने के लिए इसे होम स्क्रीन पर जोड़ें: <strong>Share</strong> दबाएं, फिर <strong>Add to Home Screen</strong>।',
    // U1 audit (2026-09-23): dropped the literal "n=${params.n}" clause, same
    // fix as the EN string above.
    firstVisitText: (params) => params
      ? `22K सोने की खुदरा कीमत, लगभग हर ${params.hours} घंटे में जांची जाती है (हाल में सबसे धीमी बार ~${params.p90Hours ?? params.hours} घंटे तक; ${params.asOf} तक) और जब संभव हो तो Tanishq की लाइव दर से पुष्टि की जाती है। कीमत अनुमानित हो तो हम साफ़ बता देते हैं।`
      : "22K सोने की खुदरा कीमत, नियमित समय पर जांची जाती है और जब संभव हो तो Tanishq की लाइव दर से पुष्टि की जाती है। कीमत अनुमानित हो तो हम साफ़ बता देते हैं।",
    shareLabel: "शेयर करें",
    shareTextWithPrice: ({ price }) => `आज 22K सोने की कीमत ₹${price}/ग्राम है — Gold Tracker पर देखें`,
    shareTextGeneric: "Gold Tracker पर आज की सोने की कीमत देखें",
    shareCopied: "लिंक कॉपी हो गया!",
    heroAriaLabel: "मौजूदा 22K सोने की कीमत और ख़रीद का सुझाव",
    eyebrow: "22K सोना · प्रति ग्राम",
    heroLocation: "Tanishq की खुदरा कीमत · पूरे भारत में",
    todayLabel: "आज",
    sinceLastLabel: "पिछली बार से",
    sparklineLabelLeft: "7 दिन",
    comparisonAriaLabel: "आज की कीमत हाल के औसत से कैसे मिलती है",
    cmpHeading7d: "7-दिन औसत से",
    cmpHeading30d: "30-दिन औसत से",
    cmpHeadingFloor: "30-दिन का न्यूनतम",
    karatAriaLabel: "24K और 18K सोने की कीमतें",
    karatLabel24: "24 KT",
    karatLabel18: "18 KT",
    karatSub24: "प्रति ग्राम · 99.9% शुद्ध",
    karatSub18: "प्रति ग्राम · 75% शुद्ध",

    // ── Purchase calculator ─────────────────────────────────────────────────────
    calcAriaLabel: "ख़रीद लागत कैलकुलेटर",
    calcHeading: "आपको कितना पड़ेगा?",
    calcGramsLabel: "ग्राम",
    calcGramsAriaLabel: "मात्रा (ग्राम में)",
    // Calculator range/estimate strings added 2026-09 (calcMakingMode*, calcStaleNote,
    // calcDisclaimer) and the jewellery-type presets added 2026-09-23 (calcPresetsLegend,
    // calcPresetCoins/Plain/Intricate/Custom + their *Range/*Hint variants,
    // calcCustomValueLabel, calcCustomInvalid, calcRowMakingWithPct, calcRangeLabel,
    // calcRateUsedIbja/Fusion/Tanishq, calcEstimateStoresVary) have NO Hindi entry yet on
    // purpose: pending native-speaker review rather than machine translation. t() falls
    // back to the English string until they're added here.
    calcKaratLabel22: "22 KT",
    calcRowGoldValue: "सोने की कीमत",
    calcRowMaking: "मेकिंग चार्ज",
    calcRowGst: ({ pct }) => `GST (${pct}%)`,
    calcRowTotal: "कुल",
    calcOtherKaratsRange: ({ k24, k18 }) => `24 KT: ${k24} · 18 KT: ${k18}`,
    calcEstimatedNote: "आज की कीमत अनुमानित है, इसलिए यह कुल भी अनुमानित है।",
    calcEmptyState: "कीमत देखने के लिए मात्रा डालें।",

    commentaryAriaLabel: "बाज़ार पर टिप्पणी",
    todaysReadEyebrow: "आज का सार",
    modelSignalAriaLabel: "आज की कीमत हाल के इतिहास से कैसे मिलती है",
    goodPriceHeading: "क्या आज ख़रीदने का सही समय है?",
    driverAriaLabel: "सोने की कीमत को क्या प्रभावित कर रहा है",
    driverHeading: "कीमत को क्या हिला रहा है?",
    chartAriaLabel: "कीमत का ट्रेंड चार्ट",
    priceTrendHeading: "कीमत का ट्रेंड",
    rangeToggleAriaLabel: "चार्ट की रेंज",
    rangeAll: "सभी",
    sectionKaratNote: "22K · प्रति ग्राम",
    chartCanvasAriaLabel: "सोने की कीमत का ट्रेंड चार्ट",
    historyAriaLabel: "कीमत का इतिहास",
    historyHeading: "इतिहास",
    thWhen: "कब",
    thDelta: "Δ 22K",
    loadingText: "लोड हो रहा है…",
    historyCardsAriaLabel: "कीमत की रीडिंग",
    trackRecordAriaLabel: "पिछले अनुमानों की सटीकता — फ़्लैट-होल्ड बनाम असल कीमत",
    trackRecordHeading: "पिछले अनुमान कितने सही रहे",
    trackRecordCaption: "हाल की 30 पांच-दिन विंडो: फ़्लैट-होल्ड अनुमान (डैश) बनाम असल में क्या हुआ (सोना)",
    trackRecordChartAriaLabel: "पिछले फ़्लैट-होल्ड अनुमान बनाम असल सोने की कीमतें",
    methodologySummary: "यह कैसे काम करता है — और कितना सटीक रहा है",
    // U1 audit (2026-09-23): dropped the literal "n=${params.n}" clause (same
    // fix as the EN string). "कैलिब्रेट करते हैं" (a transliterated loanword for
    // "calibrate") and the missing inline IBJA gloss the EN string now has are
    // NOT touched here -- flagged in docs/PLAIN_LANGUAGE_AUDIT.md as "HI needs
    // native review" rather than inventing a translation.
    footerBody: (params) => `हम <a href="https://ibjarates.com/" target="_blank" rel="noopener">IBJA</a> के आधिकारिक सोने के बेंचमार्क का इस्तेमाल करते हैं और इसे असली दुकान की कीमतों से मिलाकर कैलिब्रेट करते हैं, और जब मुमकिन हो तो <a href="https://www.tanishq.co.in/gold-rate.html?lang=en_IN" target="_blank" rel="noopener">Tanishq</a> की लाइव कीमत से भी जांचते हैं। ${
      params
        ? `लगभग हर ${params.hours} घंटे में कीमत जांची जाती है (हाल में सबसे धीमी बार ~${params.p90Hours ?? params.hours} घंटे तक; ${params.asOf} तक)`
        : "कीमत नियमित समय पर जांची जाती है"
    } — IBJA खुद दिन में एक बार अपडेट होता है, इसलिए कभी-कभी नंबर कुछ समय तक वही रहता है।`,
    footerMuted: "यह वित्तीय सलाह नहीं है। दरें संकेतात्मक हैं।",
    bottomNavAriaLabel: "पेज के सेक्शन",
    navHome: "होम",
    navTrend: "ट्रेंड",
    navHistory: "इतिहास",
    navInfo: "जानकारी",
    langToggleAriaLabel: "भाषा बदलें",

    // ── Verdict (computeVerdict) ────────────────────────────────────────────────
    verdictHeadlineUnknown: "अभी काफ़ी डेटा नहीं है",
    verdictReasonUnknown: "कुछ और रीडिंग जमा होने के बाद फिर देखें।",
    verdictHeadlineDown: "इस हफ्ते कीमत घट रही है",
    verdictHeadlineUp: "इस हफ्ते कीमत बढ़ रही है",
    verdictHeadlineFlat: "इस हफ्ते कीमत स्थिर है",
    verdictReasonDown: ({ delta, avgDelta }) =>
      avgDelta != null
        ? `इस हफ्ते ₹${delta} की गिरावट आई है, और यह महीने के सामान्य दाम से ₹${avgDelta} कम है।`
        : `इस हफ्ते ₹${delta} की गिरावट आई है।`,
    verdictReasonUp: ({ delta, avgDelta }) =>
      avgDelta != null
        ? `इस हफ्ते ₹${delta} की बढ़ोतरी हुई है, और यह महीने के सामान्य दाम से ₹${avgDelta} ज़्यादा है।`
        : `इस हफ्ते ₹${delta} की बढ़ोतरी हुई है।`,
    verdictReasonFlatBarely: "इस हफ्ते कीमत में मुश्किल से बदलाव आया है — घबराने की कोई बात नहीं।",
    verdictReasonFlatMoved: ({ dirWord, amount }) =>
      `इस हफ्ते कीमत ${dirWord} है, ₹${amount} तक — यह सामान्य उतार-चढ़ाव है, घबराने की बात नहीं।`,
    dirWordUp: "थोड़ी बढ़ी",
    dirWordDown: "थोड़ी घटी",
    dirWordUnchanged: "जस की तस रही",
    heroFallbackReason: "पहली कीमत रीडिंग का इंतज़ार है।",
    noChangeLabel: "कोई बदलाव नहीं",

    // ── Comparison cards ────────────────────────────────────────────────────────
    avgLabel7d: "7-दिन औसत",
    avgLabel30d: "30-दिन औसत",
    cmpCheaperThan: ({ avgLabel }) => `${avgLabel} से सस्ता`,
    cmpPricierThan: ({ avgLabel }) => `${avgLabel} से महंगा`,
    cmpAtAvg: "औसत के बराबर",
    cmpNotEnoughData: "काफ़ी डेटा नहीं",
    cmpAtLow: "न्यूनतम पर",
    cmpLowestPrice: "इस महीने की सबसे कम कीमत",
    cmpAboveLowest: "इस महीने के न्यूनतम से ज़्यादा",

    // ── Today's read (composeTodaysRead) ───────────────────────────────────────
    readNoSignals: "अभी इतना कीमत का इतिहास नहीं है कि आज के बारे में कुछ ठोस कहा जा सके — कुछ और रीडिंग आने के बाद फिर देखें।",
    readNoTrendCheap: "आज की कीमत इस महीने के हिसाब से कम है।",
    readNoTrendHigh: "आज की कीमत इस महीने के हिसाब से ज़्यादा है।",
    readNoTrendMid: "आज की कीमत इस महीने के सामान्य दायरे में है।",
    readCheapStillFalling: "आज की कीमत इस महीने के हिसाब से कम है, और अभी भी गिर रही है — अभी स्थिर नहीं हुई है।",
    readCheapSteadying: "आज की कीमत इस महीने के हिसाब से कम है, और हाल की गिरावट के बाद अब स्थिर होती दिख रही है।",
    readHighRising: "आज की कीमत इस महीने के हिसाब से ज़्यादा है, और अभी भी बढ़ रही है।",
    readHighSlowed: "आज की कीमत इस महीने के हिसाब से ज़्यादा है, हालांकि बढ़त धीमी पड़ गई है।",
    readFalling: "पिछले महीने कीमत में नरमी रही है, हालांकि आज की कीमत अभी ख़ास कम नहीं है।",
    readRising: "पिछले महीने कीमत बढ़ी है, हालांकि आज की कीमत अभी ख़ास ज़्यादा नहीं है।",
    readFlat: "इस महीने कीमत काफ़ी स्थिर रही है — आज की कीमत सामान्य दायरे में है।",

    // ── Good-price signals ──────────────────────────────────────────────────────
    verdictLeadCheap: "आप इस महीने सामान्य से कम कीमत दे रहे हैं",
    verdictLeadBelowMid: "आप इस महीने सामान्य से थोड़ी कम कीमत दे रहे हैं",
    verdictLeadMid: "आप इस महीने लगभग सामान्य कीमत दे रहे हैं",
    verdictLeadHigh: "आप इस महीने सामान्य से थोड़ी ज़्यादा कीमत दे रहे हैं",
    supportLine1Cheap: "इस महीने के ज़्यादातर दिनों से सस्ता।",
    supportLine1BelowMid: "इस महीने की सामान्य कीमत से थोड़ा कम।",
    supportLine1Mid: "इस महीने के बीचोंबीच के आसपास।",
    supportLine1High: "इस महीने के ज़्यादातर दिनों से महंगा।",
    proofLineCheaper: ({ days, total }) => `पिछले ${total} दिनों में से ${days} दिनों से सस्ता।`,
    proofLinePricier: ({ days, total }) => `पिछले ${total} दिनों में से ${days} दिनों से महंगा।`,
    dataSuffNote: ({ n }) => `इस दायरे में सिर्फ़ ${n} अलग दिन हैं — इसे संकेत के तौर पर लें, पक्का आंकड़ा नहीं।`,
    supportLine2Below: ({ amount }) => `इस महीने की सामान्य कीमत से ₹${amount} कम।`,
    supportLine2Above: ({ amount }) => `इस महीने की सामान्य कीमत से ₹${amount} ज़्यादा।`,
    supportLine2At: "इस महीने की सामान्य कीमत के बराबर।",
    divergenceNote: "(यहां दोनों आंकड़े पूरी तरह नहीं मिलते — एक दिन गिनता है, दूसरा असल रुपये का फ़र्क़ नापता है। ऊपर के हेडलाइन के लिए हम दिन-गिनती वाला आंकड़ा इस्तेमाल करते हैं।)",
    goodPriceTomorrow: ({ low, high }) => `अगले कारोबारी दिन तक कीमत <strong>₹${low}</strong> से <strong>₹${high}</strong> के बीच रहने की संभावना है।`,
    volNoteElevated: ({ z }) => `हाल में सोने में सामान्य से ज़्यादा उतार-चढ़ाव रहा है — 5 दिनों में करीब ±₹${z} तक।`,
    volNoteCalm: ({ z }) => `हाल में सोना सामान्य से ज़्यादा स्थिर रहा है — 5 दिनों में करीब ±₹${z} तक।`,
    volNoteNormal: ({ z }) => `हाल में सोने में 5 दिनों में करीब ±₹${z} तक की हलचल रही है।`,
    volNoteFallback: ({ z }) => `सोने की कीमत में आमतौर पर 5 दिनों में करीब ±₹${z} तक बदलाव होता है।`,
    weeklyMovementNote: ({ amount, pairs }) => `पीछे देखने पर, सोने की कीमत आमतौर पर एक हफ्ते में करीब ₹${amount} तक बदलती रही है (${pairs} हफ्तों की तुलना पर आधारित)।`,
    weeklyMovementSuffAppend: ({ n }) => ` (इस 90-दिन के दायरे में अभी तक सिर्फ़ ${n} अलग दिन हैं — इसे संकेत के तौर पर लें।)`,

    // ── Reliability (promoted from methodology accordion) ──────────────────────
    // U1/U2 audit (2026-09-23): reliabilityCoverage's EN shape changed (raw
    // %+n -> a floored fraction phrase) -- no HI entry yet on purpose, pending
    // native-speaker review (see the pending-review list near the calc* keys
    // above). t() falls back to the reworded English until it's added here.
    reliabilityUnknown: "अभी इसका रिकॉर्ड बन रहा है — कुछ समय बाद फिर देखें।",
    reliabilityDriftOnTrack: "हाल की सटीकता ऐतिहासिक औसत के मुताबिक बनी हुई है।",
    reliabilityDriftWatch: "हाल की सटीकता ऐतिहासिक औसत से थोड़ी अलग हुई है — हम नज़र बनाए हुए हैं।",
    reliabilityDriftRetrain: "हाल की त्रुटि ऐतिहासिक औसत से काफ़ी ज़्यादा रही है — हम मॉडल को दोबारा कैलिब्रेट करने वाले हैं।",

    // ── 90-day band position ────────────────────────────────────────────────────
    band90dCheaper: ({ pct, n }) => `पिछले 90 दिनों में: ${n} दिनों में से ${pct}% से सस्ता।`,
    band90dMoreExpensive: ({ pct, n }) => `पिछले 90 दिनों में: ${n} दिनों में से ${pct}% से महंगा।`,
    band90dSuffAppend: ({ n }) => ` (इस दायरे में अभी तक सिर्फ़ ${n} अलग दिन हैं — इसे संकेत के तौर पर लें।)`,

    // ── 30-day trend residual ───────────────────────────────────────────────────
    trendCheapStillFalling: ({ slope }) => `सस्ता है, लेकिन अभी भी गिर रहा है — आज की कीमत इस महीने के सामान्य ट्रेंड से काफ़ी नीचे है (करीब ₹${slope} रोज़ाना गिरावट)।`,
    trendCheapSteadying: "सस्ता है, और स्थिर हो रहा है — हाल की गिरावट के बावजूद, आज की कीमत इस महीने के सामान्य ट्रेंड के फिर से करीब आ गई है।",
    trendFalling: ({ slope }) => `इस महीने कीमत में रोज़ाना करीब ₹${slope} की गिरावट आ रही है।`,
    trendRising: ({ slope }) => `इस महीने कीमत में रोज़ाना करीब ₹${slope} की बढ़ोतरी हो रही है।`,
    trendFlat: "इस महीने कीमत स्थिर रही है, अपने सामान्य ट्रेंड के करीब।",

    // ── 90-day support distance ─────────────────────────────────────────────────
    supportCheapAtSupport: ({ low, n }) => `सस्ता है, और अपने 3-महीने के न्यूनतम (₹${low}) पर टिका है — पिछले ${n} दिनों में यह इससे नीचे नहीं गया।`,
    supportCheapNotAtSupport: ({ pct, low }) => `सस्ता है, लेकिन अभी भी अपनी 3-महीने की सबसे कम कीमत (₹${low}) से ${pct}% ऊपर है।`,
    supportNotCheapAtSupport: ({ low }) => `अपनी 3-महीने की सबसे कम कीमत (₹${low}) पर है, भले ही यह इस महीने के सबसे सस्ते दिनों में शामिल न हो।`,
    supportNotCheapNotAtSupport: ({ pct, low, n }) => `अपनी 3-महीने की सबसे कम कीमत (₹${low}) से ${pct}% ऊपर (पिछले ${n} दिनों में)।`,
    supportSuffAppend: ({ n }) => ` (इस 90-दिन के दायरे में अभी तक सिर्फ़ ${n} अलग दिन हैं — इसे संकेत के तौर पर लें।)`,

    // ── State banners ────────────────────────────────────────────────────────────
    bannerIbjaToday: "यह आज की अनुमानित कीमत है, IBJA के आधिकारिक सोने के बेंचमार्क पर आधारित — हम इसे अभी दुकान की कीमत से जांच नहीं पाए।",
    bannerIbjaCarryForward: ({ weekday }) => `यह एक अनुमानित कीमत है, IBJA के ${weekday} के बंद भाव पर आधारित (उनकी सबसे हाल की आधिकारिक दर) — हम इसे अभी दुकान की कीमत से जांच नहीं पाए।`,
    // AE1 (audit 2026-09-10): see the EN string's comment above — coverage/n are
    // the real walk-forward measurement, null (not a design-target default) when
    // no fresh reading exists.
    // U1/U2 audit (2026-09-23): the EN string's shape changed (raw %+n=
    // -> a floored fraction phrase, see i18n.js's fractionOutOf10Phrase) --
    // no HI entry yet on purpose, pending native-speaker review (see the
    // pending-review list near the calc* keys above). t() falls back to the
    // reworded English until it's added here.
    bannerTanishqLongSilent: ({ rel }) => ` काफी समय से Tanishq से इस कीमत की पुष्टि नहीं हुई है — आख़िरी सफल जांच ${rel} हुई थी।`,
    bannerFusion: ({ sources }) => `यह अन्य जौहरियों की दरों (${sources}) पर आधारित एक अनुमानित कीमत है — हम अभी Tanishq या IBJA तक नहीं पहुंच पाए।`,
    bannerStaleConfirmed: ({ rel }) => `हमें ताज़ी कीमत नहीं मिल पाई — यह आख़िरी पुष्टि की गई कीमत है, ${rel}।`,
    unknownTime: "अज्ञात समय",
    bannerRefreshFailed: ({ rel }) => `रीफ़्रेश नहीं हो पाया — यह आख़िरी अपडेट है, ${rel}`,
    fusionSourceGrt: "GRT",
    fusionSourceMalabar: "Malabar",
    fusionSourceKalyan: "Kalyan",
    fusionSourceFallback: "बाज़ार की औसत दर",

    // ── Freshness pill ───────────────────────────────────────────────────────────
    freshnessEstimated: ({ rel }) => `अनुमानित · ${rel}`,
    freshnessEstimatedAria: ({ rel }) => `अनुमानित खुदरा कीमत, IBJA बेंचमार्क ${rel} अपडेट हुआ`,
    freshnessAsOfClose: ({ weekday }) => `${weekday} के बंद भाव के अनुसार`,
    freshnessAsOfCloseAria: ({ weekday }) => `अनुमानित खुदरा कीमत, ${weekday} के IBJA बंद भाव के अनुसार`,
    freshnessConsensus: ({ rel }) => `औसत दर का अनुमान · ${rel}`,
    freshnessConsensusAria: ({ rel }) => `खुदरा बाज़ार की औसत दर का अनुमान, ${rel} अपडेट हुआ`,
    freshnessAwaiting: "पहली रीडिंग का इंतज़ार",
    freshnessNotUpdating: ({ rel }) => `अपडेट नहीं हो रहा · ${rel}`,
    freshnessNotUpdatingAria: ({ rel }) => `अपडेट नहीं हो रहा, आख़िरी बार ${rel} अपडेट हुआ`,
    freshnessStale: ({ rel }) => `पुराना · ${rel}`,
    freshnessStaleAria: ({ rel }) => `डेटा पुराना है, आख़िरी बार ${rel} अपडेट हुआ`,
    freshnessOkAria: ({ rel }) => `${rel} अपडेट हुआ`,

    // ── Offline banner ───────────────────────────────────────────────────────────
    offlineWithTime: ({ rel }) => `आप ऑफ़लाइन हैं — ${rel} की कीमत दिखाई जा रही है`,
    offlineNoData: "आप ऑफ़लाइन हैं — अभी तक कोई कीमत लोड नहीं हुई",

    // ── Hero ──────────────────────────────────────────────────────────────────────
    heroEstimatedRange: ({ low, high }) => `अनुमानित रेंज ₹${low}–₹${high}`,
    heroLastConfirmed: ({ price, date }) => `Tanishq की आख़िरी पुष्टि: ₹${price} (${date})`,
    sparklineRange: ({ min, max }) => `न्यूनतम ₹${min} · अधिकतम ₹${max}`,
    sparklineAria: ({ dir, delta }) => `7-दिन का कीमत ट्रेंड: ${dir} ₹${delta}`,
    trendDirUp: "बढ़त",
    trendDirDown: "गिरावट",

    // ── History ───────────────────────────────────────────────────────────────────
    historySince: ({ date }) => `${date} से`,
    historyRange: ({ from, to }) => `${from} – ${to}`,
    historyRangeCard: ({ from, to }) => `${from}–${to}`,
    historyNoReadings: "अभी तक कोई रीडिंग नहीं।",
    historyShowMore: ({ n }) => `${n} और दिखाएं`,
    historyShowLess: "कम दिखाएं",

    // ── Chart labels (Chart.js legend/tooltip) ─────────────────────────────────
    chart22kLabel: "22K (₹/ग्राम)",
    chart22kTooltip: ({ value }) => `22K: ₹${value}`,
    chartWhatHappened: "असल में क्या हुआ",
    chartFlatHoldEstimate: "फ़्लैट-होल्ड अनुमान",
    chartTooltipLabeled: ({ label, value }) => `${label}: ₹${value}`,

    // ── Driver context ────────────────────────────────────────────────────────────
    driverUpInrDominant: ({ total, inr, gold }) => `इस हफ्ते सोना करीब ₹${total} महंगा हुआ है — ज़्यादातर कमज़ोर रुपये (₹${inr}) की वजह से, और थोड़ा वैश्विक कीमतों (₹${gold}) से।`,
    driverUpGoldDominant: ({ total, gold, inr }) => `इस हफ्ते सोना करीब ₹${total} महंगा हुआ है — ज़्यादातर वैश्विक कीमतों (₹${gold}) की वजह से, और थोड़ा रुपये (₹${inr}) से।`,
    driverUpMixed: ({ total }) => `इस हफ्ते सोना करीब ₹${total} महंगा हुआ है, वैश्विक कीमतों और रुपये दोनों के मिले-जुले असर से।`,
    driverDownInrDominant: ({ total, inr }) => `इस हफ्ते सोना करीब ₹${total} सस्ता हुआ है — ज़्यादातर मज़बूत रुपये (₹${inr}) की वजह से, जबकि वैश्विक सोना लगभग स्थिर रहा।`,
    driverDownGoldDominant: ({ total, gold, inrNote }) => `इस हफ्ते सोना करीब ₹${total} सस्ता हुआ है — वैश्विक सोना करीब ₹${gold} गिरा${inrNote}।`,
    driverDownGoldDominantInrNoteAdded: ({ inr }) => `, और रुपये ने ₹${inr} वापस जोड़ दिए`,
    driverDownGoldDominantInrNoteFlat: ", जबकि रुपया लगभग स्थिर रहा",
    driverDownMixed: ({ total }) => `इस हफ्ते सोना करीब ₹${total} सस्ता हुआ है, वैश्विक कीमतों और रुपये दोनों के मिले-जुले असर से।`,
    driverRupeeWeakened: ({ pct, mechanism }) => `रुपया इस महीने करीब ${pct}% कमज़ोर हुआ है —${mechanism}`,
    driverRupeeStrengthened: ({ pct, mechanism }) => `रुपया इस महीने करीब ${pct}% मज़बूत हुआ है —${mechanism}`,
    driverMechanismWeaker: " कमज़ोर रुपये से भारत में आयातित सोना महंगा हो जाता है।",
    driverMechanismStronger: " मज़बूत रुपये से भारत में आयातित सोना सस्ता हो जाता है।",
    driverGoldUp: ({ pct }) => `वैश्विक सोने की कीमतें इस महीने करीब ${pct}% बढ़ी हैं।`,
    driverGoldDown: ({ pct }) => `वैश्विक सोने की कीमतें इस महीने करीब ${pct}% गिरी हैं।`,
    driverPremiumDominated: "भारत में सोने की कीमत वैश्विक कीमत या रुपये से ज़्यादा बदली है — शायद आयात लागत या त्योहारी मांग की वजह से।",
    driverAllFlat: "इस महीने ज़्यादा कुछ नहीं बदला — वैश्विक कीमतें, रुपया, और स्थानीय मांग, सब स्थिर रहे।",
    driverStateUnavailable: "इस महीने वैश्विक कीमतें और रुपया स्थिर रहे हैं — स्थानीय मांग का डेटा अलग से जांचने के लिए उपलब्ध नहीं है।",

    // ── Accuracy summary (methodology accordion) ────────────────────────────────
    // U2 (2026-09-23): full methodology moved to how-we-know.html/
    // how-we-know-strings.js (its Hindi block carries the meth* strings that
    // used to live here, unchanged). accSummaryIntro/DirectionOff/LinkText are
    // brand-new plain-language strings -- no HI entry yet on purpose, pending
    // native-speaker review (see the pending-review list near the calc* keys
    // above). t() falls back to English until they're added here.

    // ── Error / degrade paths ────────────────────────────────────────────────────
    errPriceUnavailable: "कीमत उपलब्ध नहीं",
    errCouldntLoadPrice: "ताज़ी कीमत लोड नहीं हो पाई। अपना कनेक्शन जांचें और फिर कोशिश करें।",
    errCouldntLoadHistory: "कीमत का इतिहास लोड नहीं हो पाया।",
    // U1/U2 audit (2026-09-23): EN meaning changed (methodology dump -> generic
    // "couldn't load this section") and the old HI text used "मॉडल" (a flagged
    // loanword) -- no HI entry yet on purpose, pending native-speaker review.
    // t() falls back to the reworded English until it's added here.

    // ── Relative time (fmtRelative) ──────────────────────────────────────────────
    relJustNow: "अभी-अभी",
    relMinAgo: ({ n }) => `${n} मिनट पहले`,
    relHoursAgo: ({ n }) => `${n} घंटे पहले`,
    relDaysAgo: ({ n }) => `${n} दिन पहले`,
  },
};

// ── Language state + helpers ───────────────────────────────────────────────────

function getLang() {
  try {
    const stored = localStorage.getItem(LANG_STORAGE_KEY);
    if (stored && SUPPORTED_LANGS.includes(stored)) return stored;
  } catch {
    // Storage access can throw (private browsing, disabled storage) — fall through
    // to the navigator.language default below rather than crash on read.
  }
  // First-time visitor, no stored preference: default to Hindi if the browser's
  // language list indicates it, otherwise English. Stored preference (checked
  // above) always wins over this — this branch only runs when nothing is stored.
  try {
    const langs = navigator.languages || [navigator.language || ""];
    if (langs.some(l => l.toLowerCase().startsWith("hi"))) return "hi";
  } catch {
    // navigator.language access failing is not expected, but degrade to English
    // rather than throw during the earliest possible page-load path.
  }
  return "en";
}

let currentLang = getLang();

function setLang(lang) {
  if (!SUPPORTED_LANGS.includes(lang)) return;
  currentLang = lang;
  try {
    localStorage.setItem(LANG_STORAGE_KEY, lang);
  } catch {
    // Best-effort persistence — if storage is unavailable the choice just
    // doesn't survive reload, which is a degraded UX, not a bug.
  }
  document.documentElement.lang = lang;
}

// t(key, params) — look up the active language's entry, call it if it's a
// function, fall back to English if the key is missing from the active
// language's catalogue (never returns a blank string for a real key).
function t(key, params) {
  const entry = STRINGS[currentLang]?.[key] ?? STRINGS.en[key];
  if (entry === undefined) return key; // missing key entirely — surface it, don't hide it
  return typeof entry === "function" ? entry(params) : entry;
}
