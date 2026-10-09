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
// (docs/PLAIN_LANGUAGE_AUDIT.md). Language-aware (2026-10): in Hindi it returns the
// matching Hindi phrase, so a Hindi sentence never carries an English fragment
// (pv2ConfidenceNote/pv2RangeOddsClause already interpolated this into hi text).
// The exact percentage/sample-size this rounds away is not lost -- it's preserved,
// unrounded, on how-we-know.html (how-we-know-strings.js's methAccurateP2/
// methBandAccuracy* keys read the same source data).
function fractionOutOf10Phrase(pct, forceLang) {
  const n = Math.max(0, Math.min(10, Math.floor(pct / 10)));
  // currentLang is a `let` declared further down this file; this only runs at call time.
  if (forceLang !== "en" && typeof currentLang !== "undefined" && currentLang === "hi") {
    if (n === 0) return "10 में से 1 बार भी नहीं";
    return `लगभग 10 में से ${n} बार`;
  }
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
    pageDescription: "22K gold rate — closely matched to real shop prices, compared with Tanishq's listed rate when reachable. See if today's price is high or low compared to recent weeks.",
    karatToggleAriaLabel: "Gold purity",
    perGram: "per gram",
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
      ? `The price of 22K gold in shops: Tanishq's listed rate is typically read every ${params.hours}h (longest wait in the last ${params.days} days, counting any wait still going: ${params.longestHours}h; computed ${params.asOf}) and our figure is compared with it when possible. We always say plainly when a price is an estimate.`
      : "The price of 22K gold in shops, checked on a regular schedule and compared with Tanishq's listed rate when possible. We always say plainly when a price is an estimate.",
    shareLabel: "Share",
    shareTextWithPrice: ({ price }) => `Today's 22K gold price is ₹${price}/gram — check Gold Tracker`,
    shareTextGeneric: "Check today's gold price on Gold Tracker",
    shareCopied: "Link copied!",
    heroAriaLabel: "Current 22K gold price and buying verdict",
    eyebrow: "22K gold · per gram",
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
    calcRateUsedIbja: ({ rate }) => `Rate used: 22K ₹${rate}/g — our estimate`,
    calcRateUsedFusion: ({ rate }) => `Rate used: 22K ₹${rate}/g — market-consensus estimate`,
    // ADR 059 P2: dated -- on a stale Tanishq path this figure can be days old.
    calcRateUsedTanishq: ({ rate, date }) => `Rate used: 22K ₹${rate}/g — Tanishq's listed rate on ${date}`,
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
    thDelta: "Change",
    loadingText: "Loading…",
    historyCardsAriaLabel: "Price readings",
    trackRecordAriaLabel: "How far the estimate was from the official rate each day, in a test on past weeks",
    trackRecordHeading: "A test on past weeks",
    trackRecordCaption: "In a test on past weeks: how far the one-day-ahead estimate was from the official rate each day, in ₹ per gram. Above the line means the estimate was too high, below the line too low. A filled dot means the official rate stayed inside the estimate's range that day; a hollow diamond means it fell outside. These are past results, not our live estimates.",
    trackRecordChartAriaLabel: "Daily gap between the estimate and the official rate in a test on past weeks, with marks for days inside or outside the range",
    methodologySummary: "How this works",
    // U1 audit (2026-09-23): "calibrate it to match" -> "adjust it to match" (no
    // jargon), and IBJA now gets its one plain-words explanation right here, the
    // single most prominent explanatory sentence on the page (U1's "explained
    // once, or avoided" rule) -- short mentions elsewhere (e.g. calcRateUsedIbja's
    // "IBJA-based estimate") rely on this one. Also dropped the literal
    // "n=${params.n}" clause below (same fix as firstVisitText above).
    footerBody: (params) => `Prices come from <a href="https://ibjarates.com/" target="_blank" rel="noopener">IBJA</a>, India's official daily gold rate, adjusted to match shop prices, and from <a href="https://www.tanishq.co.in/gold-rate.html?lang=en_IN" target="_blank" rel="noopener">Tanishq</a>'s listed rate when available.`,
    footerMuted: "Not financial advice. Rates are indicative.",
    // Shown only while visit counting is on (analytics.js adds it to the footer). English only: Hindi falls back to this
    // through t() (no Hindi key on purpose; do not machine-translate it).
    privacyNote: "We count visits to this page. The count uses no cookies and stores nothing in your browser. Each visit is counted once, with which page was opened (and whether it was opened from the installed app), your browser's language setting, the website you came from, and the hour. GoatCounter, the free service that keeps the counts, says it does not store your internet address or your full browser details: it uses them only in memory, for up to 8 hours, so that one visit to a page is not counted twice. Nothing is sent, and nothing is counted, if your browser sends a Do Not Track or Global Privacy Control signal.",
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
    // ADR 065: the range is for the next official-rate update (it changes twice each working day).
    goodPriceTomorrow: ({ low, high }) => `Next price update: likely <strong>₹${low}</strong> – <strong>₹${high}</strong>.`,
    // ADR 064: the next move's direction, only when inference's next_fix.direction.show is true.
    directionUp: ({ pct }) => `World gold prices have moved since India's last official rate was set, so the next official rate is more likely to be <strong>higher</strong> (about ${pct}% chance).`,
    directionDown: ({ pct }) => `World gold prices have moved since India's last official rate was set, so the next official rate is more likely to be <strong>lower</strong> (about ${pct}% chance).`,
    directionUnclear: () => "World gold prices have not moved enough since India's last official rate to say whether the next one will be higher or lower.",
    directionWhy: () => "India's official rate follows world gold and the rupee with a delay. We do not predict gold prices themselves.",
    // U1 audit (2026-09-23): "volatile"/"volatility" are on the banned-term list
    // (docs/PLAIN_LANGUAGE_AUDIT.md) -- reworded to "swinging"/"bouncing around",
    // same meaning, no jargon.
    volNoteElevated: ({ z }) => `Gold has been swinging more than usual lately. Over the past month, its price typically went up or down by about ₹${z} in 5 days.`,
    volNoteCalm: ({ z }) => `Gold has been steadier than usual lately. Over the past month, its price typically went up or down by about ₹${z} in 5 days.`,
    volNoteNormal: ({ z }) => `Over the past month, gold's price typically went up or down by about ₹${z} in 5 days — about as much as usual.`,
    volNoteFallback: ({ z }) => `Over the past month, gold's price typically went up or down by about ₹${z} in 5 days.`,
    weeklyMovementNote: ({ amount, pairs }) => `Looking back, gold has typically moved about ₹${amount} from one week to the next (based on ${pairs} weekly comparisons).`,
    weeklyMovementSuffAppend: ({ n }) => ` (Only ${n} distinct days in this 90-day window so far — treat as indicative.)`,

    // ── Reliability (promoted from methodology accordion) ──────────────────────
    // U1/U2 audit (2026-09-23): was "right {pct}% of the time (checked {n}
    // times)" -- a raw percentage read as a statistical claim, not a buyer-plain
    // one. fractionOutOf10Phrase floors so this never overstates (see its own
    // comment above). The exact percentage and n are not lost -- they're on
    // how-we-know.html (methAccurateP2CoveragePct), unrounded.
    reliabilityCoverage: ({ pct }) => `The real price has stayed inside the range we show ${fractionOutOf10Phrase(pct, "en")} so far.`,
    // Range hit rate from the walk-forward re-run on past days (next_fix.range_record): worded
    // as a test on past days, never as what the shown ranges did live.
    reliabilityCoverageTested: ({ pct }) => `In a test on past days, the real price stayed inside a range like this ${fractionOutOf10Phrase(pct, "en")}.`,
    reliabilityUnknown: "Still building a track record for this — check back later.",
    reliabilityDriftOnTrack: "Recent accuracy has stayed in line with the historical average.",
    reliabilityDriftWatch: "Recent accuracy has drifted a bit from the historical average — we're keeping an eye on it.",
    reliabilityDriftRetrain: "Our recent estimates have been further off than usual — we're adjusting them.",

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
    bannerIbjaToday: "This is today's estimated price, from India's official gold rate — we couldn't check it against the shop rate just now.",
    bannerIbjaCarryForward: ({ weekday }) => `This is an estimated price, from India's official gold rate on ${weekday} (the latest one) — we couldn't check it against the shop rate just now.`,
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
      ? ` In a test on past days, the real price landed within about ₹${amount}/gram of this estimate ${fractionOutOf10Phrase(coverage, "en")}.`
      : ` Based on past comparisons, the real price lands within about ₹${amount}/gram of this estimate.`,
    // R3: appended only when Tanishq confirmation itself has been silent for
    // TIER_DEGRADED_THRESHOLD_H, not just this cycle -- distinct from the
    // routine (silent) ibja_calibrated case above it.
    bannerTanishqLongSilent: ({ rel }) => ` We haven't been able to read Tanishq's listed rate recently — the last successful check was ${rel}.`,
    bannerFusion: ({ sources }) => `This is an estimated price based on other jewellers' rates (${sources}) — we couldn't reach Tanishq or the official rate just now.`,
    bannerStaleConfirmed: ({ rel }) => `We couldn't get a live price update — this is the last confirmed price, from ${rel}.`,
    unknownTime: "an unknown time",
    bannerRefreshFailed: ({ rel }) => `Couldn't refresh — this is the last update, from ${rel}`,
    fusionSourceGrt: "GRT",
    fusionSourceMalabar: "Malabar",
    fusionSourceFallback: "retail consensus",

    // ── Freshness pill ───────────────────────────────────────────────────────────
    freshnessEstimated: ({ rel }) => `Estimated · ${rel}`,
    freshnessEstimatedAria: ({ rel }) => `Estimated shop price, official rate updated ${rel}`,
    freshnessAsOfClose: ({ weekday }) => `As of ${weekday} close`,
    freshnessAsOfCloseAria: ({ weekday }) => `Estimated shop price, from ${weekday}'s official rate`,
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
    // E2 (GG, 2026-09-25): the hero's label line says exactly what the figure is. Tanishq is
    // named only for a real Tanishq reading (app.js heroDisplayState); every estimate says
    // "Our estimate". {when} comes from whenToday/whenYesterday/whenOnDate (IST).
    heroLabelTanishqLive: ({ when }) => `Tanishq's listed 22K rate, checked ${when}`,
    heroLabelTanishqLastChecked: ({ when }) => `Tanishq's listed 22K rate, last checked ${when}`,
    heroLabelEstimateIbja: "Our estimate for today, from India's official gold rate",
    heroLabelEstimateFusion: "Our estimate for today, based on other jewellers' listed rates",
    heroTanishqLastRate: ({ when, price }) => `Tanishq's listed rate, checked ${when}: ₹${price}`,
    // GG 4b (2026-09-25): past 36 h the old figure is still shown, always with date AND time.
    heroTanishqOldRate: ({ when, price }) => `Tanishq's listed rate when last checked, ${when}: ₹${price} — not updated since`,
    whenToday: ({ time }) => `${time} today`,
    whenYesterday: ({ time }) => `${time} yesterday`,
    whenOnDate: ({ time, date }) => `${time}, ${date}`,
    sparklineRange: ({ min, max }) => `₹${min} – ₹${max}`,
    sparklineRangeEstimate: ({ min, max }) => `₹${min} – ₹${max} (estimate)`,
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
    chart22kLabel: ({ k = 22 } = {}) => `${k}K (₹/g)`,
    chart22kTooltip: ({ value, k = 22 }) => `${k}K: ₹${value}`,
    // GG 4c (2026-09-25): the trend chart plots our IBJA-based estimate, never a retailer's rate.
    chartEstimateLabel: ({ k = 22 } = {}) => `${k}K estimate (₹/g)`,
    chartEstimateTooltip: ({ value, k = 22 }) => `${k}K estimate: ≈ ₹${value}`,
    chartNoteEstimate: "Estimate from India's official daily gold rate",
    chartNoteTanishq: "Tanishq's listed rate",
    chartWhatHappened: "Actual price",
    chartFlatHoldEstimate: "Our estimate",
    chartErrInRange: "Official rate inside the range",
    chartErrOutRange: "Official rate outside the range",
    chartErrTooHigh: ({ value }) => `₹${value} too high`,
    chartErrTooLow: ({ value }) => `₹${value} too low`,
    chartErrExact: () => "Exactly right",
    chartTooltipLabeled: ({ label, value }) => `${label}: ₹${value}`,

    // ── Driver context ────────────────────────────────────────────────────────────
    // Weekly headline = lead + two parts. Each part is worded by ITS OWN sign: a part can push
    // against the week's total (e.g. gold down overall while a weaker rupee added some back).
    driverHeadline: ({ lead, first, second }) => `${lead} — ${first}, and ${second}.`,
    driverWeekUp: ({ total }) => `Gold is up about ₹${total} this week`,
    driverWeekDown: ({ total }) => `Gold is down about ₹${total} this week`,
    driverPartGoldAdded: ({ gold }) => `global gold prices added about ₹${gold}`,
    driverPartGoldTookOff: ({ gold }) => `global gold prices took off about ₹${gold}`,
    driverPartGoldFlat: "global gold prices were roughly flat",
    driverPartRupeeAdded: ({ inr }) => `a weaker rupee added about ₹${inr}`,
    driverPartRupeeTookOff: ({ inr }) => `a stronger rupee took off about ₹${inr}`,
    driverPartRupeeFlat: "the rupee was roughly flat",
    driverUpMixed: ({ total }) => `Gold is up about ₹${total} this week, from a mix of global prices and the rupee.`,
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
    accSummaryIntro: "We regularly compare our estimate with real shop prices and correct it when it drifts.",
    accSummaryDirectionOff: "We don't guess whether prices will rise or fall next.",
    accSummaryLinkText: "See the numbers behind this →",

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

    // ── Page v2 (item 6, flagged OFF) — five-jobs view ───────────────────────────
    // Draft Hindi translations now exist below (hi block, same key names) -- machine-drafted,
    // plain and conversational, listed in the PR body under "New strings for native Hindi
    // review" pending a native speaker's pass before page_v2 ever ships live. t()'s English
    // fallback (see its own comment) still applies to any key a review finds needs reverting.
    pv2AriaLabel: "A five-question view of today's gold price",
    pv2Job1Heading: "1. What's the price now?",
    pv2SourceEstimate: "An estimate, matched to India's official gold rate and Tanishq's own numbers.",
    pv2SourceConsensus: "An estimate from other jewellers' rates — Tanishq and the official rate were both unavailable just now.",
    pv2SourceConfirmed: "Confirmed live at Tanishq.",
    pv2PriceUnavailable: "We don't have a price to show right now.",
    pv2Job2Heading: "2. How sure are we?",
    pv2ConfidenceNote: ({ frac }) => `We show a range, not just one number, because gold prices move day to day. In a test on past days, that range held the real price ${frac}.`,
    pv2ConfidenceUnknown: "We don't have a recent enough track record to say how often our range holds — check back soon.",
    pv2Job3Heading: "3. How much could it move?",
    pv2RangeOneDay: ({ low, high }) => `By the next trading day: likely between ₹${low} and ₹${high}.`,
    pv2RangeSevenDay: ({ low, high }) => `Over the next 7 days: likely between ₹${low} and ₹${high}.`,
    pv2RangeOddsClause: ({ frac }) => ` A range like this has held ${frac} in the past.`,
    pv2RangeUnavailable: "We don't have a short-term range to show today.",
    pv2Job4Heading: "4. Is it a good price?",
    pv2Weekly30dLabel: "Compared with the last month:",
    pv2Weekly90dLabel: "Compared with the last three months:",
    pv2WeeklyLower: ({ count, n }) => `Lower than on ${count} of the last ${n} weeks.`,
    pv2WeeklyHigher: ({ count, n }) => `Higher than on ${count} of the last ${n} weeks.`,
    pv2WeeklyAboutSame: ({ n }) => `About the same as most of the last ${n} weeks.`,
    pv2WeeklyTooLittleData: "We don't have enough weeks of price history yet to say.",
    pv2Job5Heading: "5. What will I pay?",
    // F1 (markup_meter): Tanishq-vs-market only, per the brief -- no store-to-store
    // comparison (terms decision pending GG, see PR body).
    pv2MarkupHeading: "How Tanishq compares to the market",
    pv2MarkupLine: ({ pct, suffix }) => `Tanishq is charging about ${pct}% above the market rate today${suffix}`,
    pv2MarkupSuffixHigher: " — higher than usual.",
    pv2MarkupSuffixLower: " — lower than usual.",
    pv2MarkupSuffixUsual: " — about usual.",
    // F2 (wait_or_buy): heading only -- the sentence body comes verbatim from
    // data/wait_or_buy_today.json (see app.js's pv2ExtractSentences), never built here.
    pv2WaitOrBuyHeading: "Should I wait or buy now?",
    // F4 (event_watch): heading only -- same verbatim-sentence contract as F2 above.
    pv2EventWatchHeading: "Upcoming events that could move the price",
  },

  hi: {
    // Hindi wording follows docs/HINDI_GLOSSARY.md (one concept, one wording). Rewritten 2026-10 for
    // natural spoken Hindi; LLM-consensus checked, NOT native-reviewed (reports/hindi_audit_2026-10/).
    // ── Static shell (index.html) ──────────────────────────────────────────────
    pageTitle: "आज सोने का भाव · क्या यह सही भाव है?",
    pageDescription: "22 कैरेट सोने का भाव — असली दुकान के भाव के क़रीब, और जब मिल सके तब Tanishq के बताए भाव से मिलाकर। देखें कि आज का भाव पिछले कुछ हफ़्तों के मुक़ाबले ऊपर है या नीचे।",
    accSummaryIntro: "हम अपने अनुमान को नियमित रूप से दुकान के असली भाव से मिलाते हैं, और भाव से दूर जाने पर उसे ठीक करते हैं।",
    accSummaryDirectionOff: "हम यह अंदाज़ा नहीं लगाते कि भाव आगे बढ़ेगा या घटेगा।",
    accSummaryLinkText: "इनके पीछे के आंकड़े देखें →",
    karatToggleAriaLabel: "सोने की शुद्धता",
    perGram: "प्रति ग्राम",
    appTitle: "Gold Tracker",
    refreshLabel: "डेटा रीफ़्रेश करें",
    pwaHelpBtnLabel: "iPhone पर ऑटो-रीफ़्रेश के बारे में",
    pwaHelpBtnTitle: "ऑटो-रीफ़्रेश के बारे में",
    pwaHelpPanelText: 'iOS बैकग्राउंड में होम-स्क्रीन ऐप्स को कम बार अपडेट करता है। ताज़ा भाव के लिए <strong>↻</strong> दबाएं। अगर भाव अटका रहे, तो ऐप स्विचर खोलें (ऊपर स्वाइप करके रोके रखें), इस ऐप को स्वाइप करके हटा दें और होम स्क्रीन से दोबारा खोलें — इससे पूरा रीलोड हो जाएगा।',
    dismissLabel: "बंद करें",
    installPromptText: 'जल्दी खोलने के लिए इसे होम स्क्रीन पर जोड़ लें: <strong>Share</strong> दबाएं, फिर <strong>Add to Home Screen</strong>।',
    firstVisitText: (params) => params
      ? `दुकानों में 22 कैरेट सोने का भाव: Tanishq का बताया भाव आमतौर पर हर ${params.hours} घंटे में पढ़ा जाता है (पिछले ${params.days} दिनों में सबसे लंबा इंतज़ार, चल रहा इंतज़ार भी गिनकर: ${params.longestHours} घंटे; ${params.asOf} को निकाला गया) और जब हो सके तब हमारे भाव को उससे मिलाया जाता है। भाव अनुमानित हो तो हम साफ़ बता देते हैं।`
      : "दुकानों में 22 कैरेट सोने का भाव, तय समय पर जांचा जाता है और जब हो सके तब Tanishq के बताए भाव से मिलाया जाता है। भाव अनुमानित हो तो हम साफ़ बता देते हैं।",
    shareLabel: "शेयर करें",
    shareTextWithPrice: ({ price }) => `आज 22 कैरेट सोने का भाव ₹${price}/ग्राम है — Gold Tracker पर देखें`,
    shareTextGeneric: "Gold Tracker पर आज का सोने का भाव देखें",
    shareCopied: "लिंक कॉपी हो गया!",
    heroAriaLabel: "22 कैरेट सोने का आज का भाव और ख़रीदने का सुझाव",
    eyebrow: "22 कैरेट सोना · प्रति ग्राम",
    todayLabel: "आज",
    sinceLastLabel: "पिछली बार से",
    sparklineLabelLeft: "7 दिन",
    comparisonAriaLabel: "आज का भाव हाल के औसत से कैसा है",
    cmpHeading7d: "7 दिन के औसत से",
    cmpHeading30d: "30 दिन के औसत से",
    cmpHeadingFloor: "30 दिन का सबसे कम",
    karatAriaLabel: "24 कैरेट और 18 कैरेट सोने का भाव",
    karatLabel24: "24 कैरेट",
    karatLabel18: "18 कैरेट",
    karatSub24: "प्रति ग्राम · 99.9% शुद्ध",
    karatSub18: "प्रति ग्राम · 75% शुद्ध",

    // ── Purchase calculator ─────────────────────────────────────────────────────
    calcAriaLabel: "ख़रीद का ख़र्च निकालने वाला कैलकुलेटर",
    calcHeading: "आपको कितना पड़ेगा?",
    calcGramsLabel: "ग्राम",
    calcGramsAriaLabel: "ग्राम में मात्रा",
    calcPresetsLegend: "गहने का प्रकार",
    calcPresetCoins: "सिक्के और सादी चेन",
    calcPresetCoinsRange: "सोने की कीमत का 3–8%, आम तौर पर 5%",
    calcPresetPlain: "सादी चूड़ियां और अंगूठियां",
    calcPresetPlainRange: "सोने की कीमत का 8–12%, आम तौर पर 10%",
    calcPresetIntricate: "बारीक काम या एंटीक डिज़ाइन",
    calcPresetIntricateRange: "सोने की कीमत का 15–25%, आम तौर पर 20%",
    calcPresetCustom: "अपना",
    calcPresetCustomHint: "अपने ज्वेलर का मेकिंग चार्ज पता है? नीचे डालें।",
    calcMakingModePct: "सोने की कीमत का %",
    calcMakingModePerGram: "₹ प्रति ग्राम",
    calcCustomValueLabel: "मेकिंग चार्ज",
    calcCustomInvalid: "मेकिंग चार्ज 0 या उससे ज़्यादा डालें।",
    calcKaratLabel22: "22 कैरेट",
    calcRowGoldValue: "सोने की कीमत",
    calcRowMaking: "मेकिंग चार्ज",
    calcRowMakingWithPct: ({ pct }) => `मेकिंग चार्ज (${pct}%)`,
    calcRowGst: ({ pct }) => `GST (${pct}%)`,
    calcRowTotal: "कुल",
    calcRangeLabel: ({ range }) => `रेंज ${range}`,
    calcOtherKaratsRange: ({ k24, k18 }) => `24 कैरेट: ${k24} · 18 कैरेट: ${k18}`,
    calcRateUsedIbja: ({ rate }) => `इस्तेमाल हुआ भाव: 22 कैरेट ₹${rate}/ग्राम — हमारा अनुमान`,
    calcRateUsedFusion: ({ rate }) => `इस्तेमाल हुआ भाव: 22 कैरेट ₹${rate}/ग्राम — दुकानों के औसत भाव का अनुमान`,
    calcRateUsedTanishq: ({ rate, date }) => `इस्तेमाल हुआ भाव: 22 कैरेट ₹${rate}/ग्राम — ${date} को Tanishq का बताया भाव`,
    calcEstimatedNote: "आज का भाव अनुमानित है, इसलिए यह कुल भी अनुमानित है।",
    calcStaleNote: ({ rel }) => `यह आख़िरी पक्के भाव से निकाला गया है, जो ${rel} का है।`,
    calcEstimateStoresVary: "अनुमान — हर दुकान पर थोड़ा फ़र्क़ होता है।",
    calcDisclaimer: "आपके ज्वेलर का बिल अलग होगा — हॉलमार्किंग (HUID) शुल्क, नग, वेस्टेज और दुकान का अपना भाव इसमें शामिल नहीं हैं।",
    calcEmptyState: "ख़र्च देखने के लिए ग्राम में मात्रा डालें।",

    commentaryAriaLabel: "बाज़ार का हाल",
    todaysReadEyebrow: "आज का हाल",
    modelSignalAriaLabel: "आज का भाव हाल के भाव से कैसा है",
    goodPriceHeading: "क्या आज ख़रीदने का सही समय है?",
    driverAriaLabel: "सोने का भाव किस वजह से बदल रहा है",
    driverHeading: "भाव किस वजह से बदल रहा है?",
    chartAriaLabel: "भाव का ट्रेंड चार्ट",
    priceTrendHeading: "भाव का ट्रेंड",
    rangeToggleAriaLabel: "चार्ट की अवधि",
    rangeAll: "सारे",
    sectionKaratNote: "22 कैरेट · प्रति ग्राम",
    chartCanvasAriaLabel: "सोने के भाव का ट्रेंड चार्ट",
    historyAriaLabel: "पुराने भाव",
    historyHeading: "पुराने भाव",
    thWhen: "कब",
    thDelta: "बदलाव",
    loadingText: "लोड हो रहा है…",
    historyCardsAriaLabel: "दर्ज किए गए भाव",
    methodologySummary: "यह कैसे काम करता है",
    footerBody: (params) => `भाव <a href="https://ibjarates.com/" target="_blank" rel="noopener">IBJA</a> (भारत का आधिकारिक रोज़ का सोने का भाव) से लिए जाते हैं और दुकान के भाव से मिलाकर ठीक किए जाते हैं; जब मिल सके तब <a href="https://www.tanishq.co.in/gold-rate.html?lang=en_IN" target="_blank" rel="noopener">Tanishq</a> का बताया भाव भी देखा जाता है।`,
    footerMuted: "यह निवेश की सलाह नहीं है। भाव मोटे अंदाज़े के लिए हैं।",
    bottomNavAriaLabel: "पेज के हिस्से",
    navHome: "होम",
    navTrend: "ट्रेंड",
    navHistory: "पुराने भाव",
    navInfo: "जानकारी",
    langToggleAriaLabel: "भाषा बदलें",

    // ── Verdict (computeVerdict) ────────────────────────────────────────────────
    verdictHeadlineUnknown: "अभी काफ़ी डेटा नहीं है",
    verdictReasonUnknown: "कुछ और भाव दर्ज होने के बाद फिर देखें।",
    verdictHeadlineDown: "इस हफ़्ते सोना सस्ता हो रहा है",
    verdictHeadlineUp: "इस हफ़्ते सोना महंगा हो रहा है",
    verdictHeadlineFlat: "इस हफ़्ते भाव स्थिर है",
    verdictReasonDown: ({ delta, avgDelta }) =>
      avgDelta != null
        ? `इस हफ़्ते भाव ₹${delta} घटा है, और महीने के आम भाव से ₹${avgDelta} कम है।`
        : `इस हफ़्ते भाव ₹${delta} घटा है।`,
    verdictReasonUp: ({ delta, avgDelta }) =>
      avgDelta != null
        ? `इस हफ़्ते भाव ₹${delta} बढ़ा है, और महीने के आम भाव से ₹${avgDelta} ज़्यादा है।`
        : `इस हफ़्ते भाव ₹${delta} बढ़ा है।`,
    verdictReasonFlatBarely: "इस हफ़्ते भाव में मुश्किल से कोई बदलाव हुआ — इसमें कुछ करने की ज़रूरत नहीं।",
    verdictReasonFlatMoved: ({ dirWord, amount }) =>
      `इस हफ़्ते भाव ${dirWord}, ₹${amount} तक — यह आम उतार-चढ़ाव है, इसमें कुछ करने की ज़रूरत नहीं।`,
    dirWordUp: "थोड़ा ऊपर गया",
    dirWordDown: "थोड़ा नीचे आया",
    dirWordUnchanged: "जस का तस रहा",
    heroFallbackReason: "पहले भाव का इंतज़ार है।",
    noChangeLabel: "कोई बदलाव नहीं",

    // ── Comparison cards ────────────────────────────────────────────────────────
    avgLabel7d: "7 दिन का औसत",
    avgLabel30d: "30 दिन का औसत",
    cmpCheaperThan: ({ avgLabel }) => `${avgLabel} से सस्ता`,
    cmpPricierThan: ({ avgLabel }) => `${avgLabel} से महंगा`,
    cmpAtAvg: "औसत के बराबर",
    cmpNotEnoughData: "काफ़ी डेटा नहीं",
    cmpAtLow: "सबसे कम पर",
    cmpLowestPrice: "इस महीने का सबसे कम भाव",
    cmpAboveLowest: "इस महीने के सबसे कम भाव से ऊपर",

    // ── Today's read (composeTodaysRead) ───────────────────────────────────────
    readNoSignals: "आज के बारे में कुछ कहने लायक़ पुराना भाव अभी हमारे पास नहीं है — कुछ और भाव दर्ज होने के बाद फिर देखें।",
    readNoTrendCheap: "आज का भाव इस महीने के हिसाब से कम है।",
    readNoTrendHigh: "आज का भाव इस महीने के हिसाब से ज़्यादा है।",
    readNoTrendMid: "आज का भाव इस महीने के आम भाव के आसपास है।",
    readCheapStillFalling: "आज का भाव इस महीने के हिसाब से कम है, और अभी भी गिर रहा है — अभी टिका नहीं है।",
    readCheapSteadying: "आज का भाव इस महीने के हिसाब से कम है, और हाल की गिरावट के बाद अब टिकता दिख रहा है।",
    readHighRising: "आज का भाव इस महीने के हिसाब से ज़्यादा है, और अभी भी चढ़ रहा है।",
    readHighSlowed: "आज का भाव इस महीने के हिसाब से ज़्यादा है, हालांकि चढ़ने की रफ़्तार धीमी हो गई है।",
    readFalling: "पिछले एक महीने में भाव नरम पड़ा है, हालांकि आज का भाव अभी ख़ास सस्ता नहीं है।",
    readRising: "पिछले एक महीने में भाव चढ़ा है, हालांकि आज का भाव अभी ख़ास महंगा नहीं है।",
    readFlat: "इस महीने भाव काफ़ी टिका रहा है — आज का भाव आम भाव के आसपास है।",

    // ── Good-price signals ──────────────────────────────────────────────────────
    verdictLeadCheap: "इस महीने आप आम भाव से कम दे रहे हैं",
    verdictLeadBelowMid: "इस महीने आप आम भाव से थोड़ा कम दे रहे हैं",
    verdictLeadMid: "इस महीने आप लगभग आम भाव ही दे रहे हैं",
    verdictLeadHigh: "इस महीने आप आम भाव से थोड़ा ज़्यादा दे रहे हैं",
    supportLine1Cheap: "इस महीने के ज़्यादातर दिनों से सस्ता।",
    supportLine1BelowMid: "इस महीने के आम भाव से थोड़ा कम।",
    supportLine1Mid: "इस महीने के बीच के भाव के आसपास।",
    supportLine1High: "इस महीने के ज़्यादातर दिनों से महंगा।",
    proofLineCheaper: ({ days, total }) => `पिछले ${total} में से ${days} दिनों के भाव से सस्ता।`,
    proofLinePricier: ({ days, total }) => `पिछले ${total} में से ${days} दिनों के भाव से महंगा।`,
    dataSuffNote: ({ n }) => `इसमें सिर्फ़ ${n} अलग-अलग दिन हैं — इसे मोटा अंदाज़ा ही समझें।`,
    supportLine2Below: ({ amount }) => `इस महीने के आम भाव से ₹${amount} कम।`,
    supportLine2Above: ({ amount }) => `इस महीने के आम भाव से ₹${amount} ज़्यादा।`,
    supportLine2At: "इस महीने के आम भाव के बराबर।",
    divergenceNote: "(इन दोनों में पूरी तरह मेल नहीं है — एक दिन गिनता है, दूसरा रुपये का असली फ़र्क़ नापता है। ऊपर की हेडलाइन के लिए हम दिनों की गिनती वाला तरीक़ा लेते हैं।)",
    goodPriceTomorrow: ({ low, high }) => `अगला भाव अपडेट: शायद <strong>₹${low}</strong> – <strong>₹${high}</strong> के बीच।`,
    volNoteElevated: ({ z }) => `हाल में सोने के भाव में आम से ज़्यादा उतार-चढ़ाव रहा है। पिछले एक महीने में 5 दिनों के अंदर भाव आम तौर पर करीब ₹${z} ऊपर या नीचे गया।`,
    volNoteCalm: ({ z }) => `हाल में सोने का भाव आम से ज़्यादा टिका रहा है। पिछले एक महीने में 5 दिनों के अंदर भाव आम तौर पर करीब ₹${z} ऊपर या नीचे गया।`,
    volNoteNormal: ({ z }) => `पिछले एक महीने में 5 दिनों के अंदर सोने का भाव आम तौर पर करीब ₹${z} ऊपर या नीचे गया — यह आम चाल के आसपास ही है।`,
    volNoteFallback: ({ z }) => `पिछले एक महीने में 5 दिनों के अंदर सोने का भाव आम तौर पर करीब ₹${z} ऊपर या नीचे गया।`,
    weeklyMovementNote: ({ amount, pairs }) => `पीछे देखें तो सोने का भाव आम तौर पर एक हफ़्ते से अगले हफ़्ते में करीब ₹${amount} बदलता रहा है (${pairs} हफ़्तों की तुलना के आधार पर)।`,
    weeklyMovementSuffAppend: ({ n }) => ` (90 दिनों के इस दायरे में अभी सिर्फ़ ${n} अलग-अलग दिन हैं — इसे मोटा अंदाज़ा ही समझें।)`,

    // ── Reliability (promoted from methodology accordion) ──────────────────────
    // reliabilityCoverage / calibrationConfidenceAppend have no hi entry on purpose (#2400 rule: Hindi falls back to English until reviewed).
    reliabilityUnknown: "अभी इसका रिकॉर्ड बन रहा है — कुछ समय बाद फिर देखें।",
    reliabilityDriftRetrain: "हाल के हमारे अनुमान आम से ज़्यादा ग़लत रहे हैं — हम उन्हें सुधार रहे हैं।",

    // ── 90-day band position ────────────────────────────────────────────────────
    band90dCheaper: ({ pct, n }) => `पिछले 90 दिनों में: ${n} दिनों में से ${pct}% दिनों के भाव से सस्ता।`,
    band90dMoreExpensive: ({ pct, n }) => `पिछले 90 दिनों में: ${n} दिनों में से ${pct}% दिनों के भाव से महंगा।`,
    band90dSuffAppend: ({ n }) => ` (इस दायरे में अभी सिर्फ़ ${n} अलग-अलग दिन हैं — इसे मोटा अंदाज़ा ही समझें।)`,

    // ── 30-day trend residual ───────────────────────────────────────────────────
    trendCheapStillFalling: ({ slope }) => `सस्ता है, पर अभी भी गिर रहा है — आज का भाव इस महीने के आम ट्रेंड से काफ़ी नीचे है (रोज़ करीब ₹${slope} की गिरावट)।`,
    trendCheapSteadying: "सस्ता है, और टिक रहा है — हाल की गिरावट के बावजूद आज का भाव इस महीने के आम ट्रेंड के फिर से क़रीब आ गया है।",
    trendFalling: ({ slope }) => `इस महीने भाव रोज़ करीब ₹${slope} घट रहा है।`,
    trendRising: ({ slope }) => `इस महीने भाव रोज़ करीब ₹${slope} बढ़ रहा है।`,
    trendFlat: "इस महीने भाव टिका रहा है, अपने आम ट्रेंड के क़रीब।",

    // ── 90-day support distance ─────────────────────────────────────────────────
    supportCheapAtSupport: ({ low, n }) => `सस्ता है, और 3 महीने के सबसे कम भाव (₹${low}) पर है — पिछले ${n} दिनों में यह इससे नीचे नहीं गया।`,
    supportCheapNotAtSupport: ({ pct, low }) => `सस्ता है, पर 3 महीने के सबसे कम भाव (₹${low}) से अभी ${pct}% ऊपर है।`,
    supportNotCheapAtSupport: ({ low }) => `3 महीने के सबसे कम भाव (₹${low}) पर है, भले ही यह इस महीने के सबसे सस्ते दिनों में नहीं है।`,
    supportNotCheapNotAtSupport: ({ pct, low, n }) => `3 महीने के सबसे कम भाव (₹${low}) से ${pct}% ऊपर (पिछले ${n} दिनों में)।`,
    supportSuffAppend: ({ n }) => ` (90 दिनों के इस दायरे में अभी सिर्फ़ ${n} अलग-अलग दिन हैं — इसे मोटा अंदाज़ा ही समझें।)`,

    // ── State banners ────────────────────────────────────────────────────────────
    bannerIbjaToday: "यह आज का अनुमानित भाव है, जो भारत के आधिकारिक रोज़ के भाव से निकाला गया है — हम इसे अभी दुकान के भाव से मिला नहीं पाए।",
    bannerIbjaCarryForward: ({ weekday }) => `यह एक अनुमानित भाव है, जो ${weekday} के भारत के आधिकारिक भाव (सबसे ताज़ा) से निकाला गया है — हम इसे अभी दुकान के भाव से मिला नहीं पाए।`,
    bannerTanishqLongSilent: ({ rel }) => ` हाल में हम Tanishq का बताया भाव पढ़ नहीं पाए — आख़िरी बार सफलतापूर्वक ${rel} देखा था।`,
    bannerFusion: ({ sources }) => `यह दूसरे ज्वेलर्स के भाव (${sources}) पर आधारित अनुमानित भाव है — हम अभी Tanishq या आधिकारिक भाव तक नहीं पहुंच पाए।`,
    bannerStaleConfirmed: ({ rel }) => `ताज़ा भाव नहीं मिल पाया — यह आख़िरी पक्का भाव है, जो ${rel} का है।`,
    unknownTime: "किसी अनजान समय",
    bannerRefreshFailed: ({ rel }) => `रीफ़्रेश नहीं हो पाया — यह आख़िरी अपडेट है, जो ${rel} का है`,
    fusionSourceGrt: "GRT",
    fusionSourceMalabar: "Malabar",
    fusionSourceFallback: "दुकानों का औसत भाव",

    // ── Freshness pill ───────────────────────────────────────────────────────────
    freshnessEstimated: ({ rel }) => `अनुमानित · ${rel}`,
    freshnessEstimatedAria: ({ rel }) => `दुकान के भाव का अनुमान, आधिकारिक भाव ${rel} अपडेट हुआ`,
    freshnessAsOfClose: ({ weekday }) => `${weekday} के बंद भाव के अनुसार`,
    freshnessAsOfCloseAria: ({ weekday }) => `दुकान के भाव का अनुमान, ${weekday} के आधिकारिक भाव से`,
    freshnessConsensus: ({ rel }) => `दुकानों के औसत भाव का अनुमान · ${rel}`,
    freshnessConsensusAria: ({ rel }) => `दुकानों के औसत भाव का अनुमान, ${rel} अपडेट हुआ`,
    freshnessAwaiting: "पहले भाव का इंतज़ार",
    freshnessNotUpdating: ({ rel }) => `अपडेट नहीं हो रहा · ${rel}`,
    freshnessNotUpdatingAria: ({ rel }) => `अपडेट नहीं हो रहा, आख़िरी बार ${rel} अपडेट हुआ`,
    freshnessStale: ({ rel }) => `पुराना · ${rel}`,
    freshnessStaleAria: ({ rel }) => `भाव पुराना है, आख़िरी बार ${rel} अपडेट हुआ`,
    freshnessOkAria: ({ rel }) => `${rel} अपडेट हुआ`,

    // ── Offline banner ───────────────────────────────────────────────────────────
    offlineWithTime: ({ rel }) => `आप ऑफ़लाइन हैं — ${rel} का भाव दिखा रहे हैं`,
    offlineNoData: "आप ऑफ़लाइन हैं — अभी तक कोई भाव लोड नहीं हुआ",

    // ── Hero ──────────────────────────────────────────────────────────────────────
    heroEstimatedRange: ({ low, high }) => `अनुमानित रेंज ₹${low}–₹${high}`,
    heroLabelTanishqLive: ({ when }) => `Tanishq की साइट पर दिया 22 कैरेट भाव, ${when} देखा गया`,
    heroLabelTanishqLastChecked: ({ when }) => `Tanishq की साइट पर दिया 22 कैरेट भाव, आख़िरी बार ${when} देखा गया`,
    heroLabelEstimateIbja: "आज के लिए हमारा अनुमान, भारत के आधिकारिक रोज़ के भाव से",
    heroLabelEstimateFusion: "आज के लिए हमारा अनुमान, दूसरे ज्वेलर्स के बताए भाव पर आधारित",
    heroTanishqLastRate: ({ when, price }) => `Tanishq का बताया भाव, ${when} देखा गया: ₹${price}`,
    heroTanishqOldRate: ({ when, price }) => `Tanishq का बताया भाव, आख़िरी बार ${when} देखा गया: ₹${price} — तब से अपडेट नहीं हुआ`,
    whenToday: ({ time }) => `आज ${time}`,
    whenYesterday: ({ time }) => `बीते कल ${time}`,
    whenOnDate: ({ time, date }) => `${date}, ${time}`,
    sparklineRange: ({ min, max }) => `₹${min} – ₹${max}`,
    sparklineRangeEstimate: ({ min, max }) => `₹${min} – ₹${max} (अनुमान)`,
    sparklineAria: ({ dir, delta }) => `7 दिन का भाव ट्रेंड: ${dir} ₹${delta}`,
    trendDirUp: "बढ़त",
    trendDirDown: "गिरावट",

    // ── History ───────────────────────────────────────────────────────────────────
    historySince: ({ date }) => `${date} से`,
    historyRange: ({ from, to }) => `${from} – ${to}`,
    historyRangeCard: ({ from, to }) => `${from}–${to}`,
    historyNoReadings: "अभी तक कोई भाव दर्ज नहीं हुआ।",
    historyShowMore: ({ n }) => `${n} और दिखाएं`,
    historyShowLess: "कम दिखाएं",

    // ── Chart labels (Chart.js legend/tooltip) ─────────────────────────────────
    chart22kLabel: ({ k = 22 } = {}) => `${k} कैरेट (₹/ग्राम)`,
    chart22kTooltip: ({ value, k = 22 }) => `${k} कैरेट: ₹${value}`,
    chartEstimateLabel: ({ k = 22 } = {}) => `${k} कैरेट अनुमान (₹/ग्राम)`,
    chartEstimateTooltip: ({ value, k = 22 }) => `${k} कैरेट अनुमान: ≈ ₹${value}`,
    chartNoteEstimate: "भारत के आधिकारिक रोज़ के भाव से निकाला अनुमान",
    chartNoteTanishq: "Tanishq का बताया भाव",
    chartWhatHappened: "असली भाव",
    chartFlatHoldEstimate: "हमारा अनुमान",
    chartTooltipLabeled: ({ label, value }) => `${label}: ₹${value}`,

    // ── Driver context ────────────────────────────────────────────────────────────
    driverHeadline: ({ lead, first, second }) => `${lead} — ${first}, और ${second}।`,
    driverWeekUp: ({ total }) => `इस हफ़्ते सोना करीब ₹${total} महंगा हुआ है`,
    driverWeekDown: ({ total }) => `इस हफ़्ते सोना करीब ₹${total} सस्ता हुआ है`,
    driverPartGoldAdded: ({ gold }) => `दुनिया के बाज़ार में सोने के भाव से करीब ₹${gold} बढ़े`,
    driverPartGoldTookOff: ({ gold }) => `दुनिया के बाज़ार में सोने के भाव से करीब ₹${gold} घटे`,
    driverPartGoldFlat: "दुनिया के बाज़ार में सोने का भाव लगभग टिका रहा",
    driverPartRupeeAdded: ({ inr }) => `कमज़ोर रुपये से करीब ₹${inr} बढ़े`,
    driverPartRupeeTookOff: ({ inr }) => `मज़बूत रुपये से करीब ₹${inr} घटे`,
    driverPartRupeeFlat: "रुपया लगभग टिका रहा",
    driverUpMixed: ({ total }) => `इस हफ़्ते सोना करीब ₹${total} महंगा हुआ है, दुनिया के बाज़ार के भाव और रुपये, दोनों के मिले-जुले असर से।`,
    driverDownMixed: ({ total }) => `इस हफ़्ते सोना करीब ₹${total} सस्ता हुआ है, दुनिया के बाज़ार के भाव और रुपये, दोनों के मिले-जुले असर से।`,
    driverRupeeWeakened: ({ pct, mechanism }) => `रुपया इस महीने करीब ${pct}% कमज़ोर हुआ है —${mechanism}`,
    driverRupeeStrengthened: ({ pct, mechanism }) => `रुपया इस महीने करीब ${pct}% मज़बूत हुआ है —${mechanism}`,
    driverMechanismWeaker: " कमज़ोर रुपये से बाहर से आया सोना भारत में महंगा पड़ता है।",
    driverMechanismStronger: " मज़बूत रुपये से बाहर से आया सोना भारत में सस्ता पड़ता है।",
    driverGoldUp: ({ pct }) => `दुनिया के बाज़ार में सोने का भाव इस महीने करीब ${pct}% बढ़ा है।`,
    driverGoldDown: ({ pct }) => `दुनिया के बाज़ार में सोने का भाव इस महीने करीब ${pct}% घटा है।`,
    driverPremiumDominated: "भारत में सोने का भाव दुनिया के भाव और रुपये से जितना समझ आता है, उससे ज़्यादा बदला है — शायद आयात के ख़र्च या यहां त्योहारों की मांग की वजह से।",
    driverAllFlat: "इस महीने कुछ ख़ास नहीं बदला — दुनिया के भाव, रुपया और यहां की मांग, सब शांत रहे।",
    driverStateUnavailable: "इस महीने दुनिया के भाव और रुपया शांत रहे हैं — यहां की मांग का डेटा अलग से जांचने के लिए उपलब्ध नहीं है।",

    // ── Error / degrade paths ────────────────────────────────────────────────────
    errPriceUnavailable: "भाव उपलब्ध नहीं",
    errCouldntLoadPrice: "ताज़ा भाव लोड नहीं हो पाया। अपना इंटरनेट जांचें और फिर कोशिश करें।",
    errCouldntLoadHistory: "पुराने भाव लोड नहीं हो पाए।",
    errCouldntLoadMethodology: "यह हिस्सा लोड नहीं हुआ — आम तौर पर इंटरनेट कट जाने से ऐसा होता है। ऊपर दिया आज का सोने का भाव इससे प्रभावित नहीं है। दोबारा कोशिश करने के लिए पेज रीफ़्रेश करें।",

    // ── Relative time (fmtRelative) ──────────────────────────────────────────────
    relJustNow: "अभी-अभी",
    relMinAgo: ({ n }) => `${n} मिनट पहले`,
    relHoursAgo: ({ n }) => `${n} घंटे पहले`,
    relDaysAgo: ({ n }) => `${n} दिन पहले`,

    // ── Page v2 (item 6, flagged OFF) — five-jobs view ───────────────────────────
    pv2AriaLabel: "आज के सोने के भाव पर पांच आसान सवाल",
    pv2Job1Heading: "1. अभी भाव क्या है?",
    pv2SourceEstimate: "एक अनुमान, जो भारत के आधिकारिक भाव और Tanishq के भाव से मिलाकर बनाया गया है।",
    pv2SourceConsensus: "दूसरे ज्वेलर्स के भाव से बना अनुमान — अभी Tanishq और आधिकारिक भाव, दोनों नहीं मिल पाए।",
    pv2SourceConfirmed: "Tanishq पर अभी का पक्का भाव।",
    pv2PriceUnavailable: "अभी दिखाने के लिए कोई भाव नहीं है।",
    pv2Job2Heading: "2. हमें कितना भरोसा है?",
    pv2ConfidenceUnknown: "हमारे पास इतना ताज़ा रिकॉर्ड नहीं है कि बता सकें हमारी रेंज कितनी बार सही रहती है — कुछ समय बाद फिर देखें।",
    pv2Job3Heading: "3. भाव कितना बदल सकता है?",
    pv2RangeOneDay: ({ low, high }) => `अगले कारोबारी दिन तक: ₹${low} से ₹${high} के बीच रहने की संभावना है।`,
    pv2RangeSevenDay: ({ low, high }) => `अगले 7 दिनों में: ₹${low} से ₹${high} के बीच रहने की संभावना है।`,
    pv2RangeOddsClause: ({ frac }) => ` ऐसी रेंज पहले ${frac} सही साबित हुई है।`,
    pv2RangeUnavailable: "आज दिखाने के लिए कोई छोटी अवधि की रेंज नहीं है।",
    pv2Job4Heading: "4. क्या यह अच्छा भाव है?",
    pv2Weekly30dLabel: "पिछले महीने की तुलना में:",
    pv2Weekly90dLabel: "पिछले तीन महीनों की तुलना में:",
    pv2WeeklyLower: ({ count, n }) => `पिछले ${n} हफ़्तों में से ${count} हफ़्तों से कम।`,
    pv2WeeklyHigher: ({ count, n }) => `पिछले ${n} हफ़्तों में से ${count} हफ़्तों से ज़्यादा।`,
    pv2WeeklyAboutSame: ({ n }) => `पिछले ${n} हफ़्तों में से ज़्यादातर के लगभग बराबर।`,
    pv2WeeklyTooLittleData: "यह बताने के लिए अभी हमारे पास काफ़ी हफ़्तों का भाव नहीं है।",
    pv2Job5Heading: "5. मुझे कितना देना होगा?",
    pv2MarkupHeading: "Tanishq का भाव बाज़ार के मुक़ाबले कैसा है",
    pv2MarkupLine: ({ pct, suffix }) => `Tanishq का आज का भाव बाज़ार भाव से करीब ${pct}% ज़्यादा है${suffix}`,
    pv2MarkupSuffixHigher: " — आम से ज़्यादा।",
    pv2MarkupSuffixLower: " — आम से कम।",
    pv2MarkupSuffixUsual: " — लगभग आम।",
    pv2WaitOrBuyHeading: "इंतज़ार करूं या अभी ख़रीदूं?",
    pv2EventWatchHeading: "आने वाली ऐसी बातें जो भाव बदल सकती हैं",
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
