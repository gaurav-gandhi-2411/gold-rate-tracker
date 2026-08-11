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

const STRINGS = {
  en: {
    // ── Static shell (index.html) ──────────────────────────────────────────────
    pageTitle: "Gold Rate Today · Is it a good price?",
    pageDescription: "22K gold rate — an IBJA-calibrated estimate, confirmed against live Tanishq retail when reachable. See if today's price is high or low compared to recent weeks.",
    appTitle: "Gold Tracker",
    refreshLabel: "Refresh data",
    pwaHelpBtnLabel: "About auto-refresh on iPhone",
    pwaHelpBtnTitle: "About auto-refresh",
    pwaHelpPanelText: 'iOS limits how often home-screen apps update in the background. Tap <strong>↻</strong> to get the latest prices. If prices remain stuck, open the App Switcher (swipe up and hold), then swipe this app away and reopen from Home Screen — that forces a full reload.',
    dismissLabel: "Dismiss",
    installPromptText: 'Add this to your Home Screen for quicker access: tap <strong>Share</strong>, then <strong>Add to Home Screen</strong>.',
    firstVisitText: "22K gold retail price, checked every 3 hours and confirmed against Tanishq's live rate when possible. We always say plainly when a price is an estimate.",
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
    calcMakingLabel: "Making charge (%)",
    calcMakingAriaLabel: "Making charge percentage",
    calcMakingHint: "Varies by jeweler and design — enter yours if you know it.",
    calcKaratLabel22: "22 KT",
    calcRowGoldValue: "Gold value",
    calcRowMaking: "Making charge",
    calcRowGst: ({ pct }) => `GST (${pct}%)`,
    calcRowTotal: "Total",
    calcOtherKarats: ({ k24, k18 }) => `24 KT: ₹${k24} · 18 KT: ₹${k18}`,
    calcEstimatedNote: "Today's price is an estimate, so this total is too.",
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
    footerBody: 'We use <a href="https://ibjarates.com/" target="_blank" rel="noopener">IBJA</a>\'s official gold benchmark and calibrate it to match real shop prices, checking against <a href="https://www.tanishq.co.in/gold-rate.html?lang=en_IN" target="_blank" rel="noopener">Tanishq</a>\'s live rate when we can. Prices are checked every 3 hours — IBJA itself only updates once a day, so the number sometimes stays the same for a while.',
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
    volNoteElevated: ({ z }) => `Gold has been more volatile than usual lately — about ±₹${z} over 5 days.`,
    volNoteCalm: ({ z }) => `Gold has been calmer than usual lately — about ±₹${z} over 5 days.`,
    volNoteNormal: ({ z }) => `Gold has been moving about ±₹${z} over 5 days lately.`,
    volNoteFallback: ({ z }) => `Gold's price typically moves about ±₹${z} over 5 days.`,
    weeklyMovementNote: ({ amount, pairs }) => `Looking back, gold has typically moved about ₹${amount} from one week to the next (based on ${pairs} weekly comparisons).`,
    weeklyMovementSuffAppend: ({ n }) => ` (Only ${n} distinct days in this 90-day window so far — treat as indicative.)`,

    // ── Reliability (promoted from methodology accordion) ──────────────────────
    reliabilityCoverage: ({ pct, n }) => `Our estimated range has been right ${pct}% of the time (checked ${n} times).`,
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
    calibrationConfidenceAppend: ({ amount }) => ` Based on past comparisons, this kind of estimate is typically within about ₹${amount}/gram of the real price.`,
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

    // ── Methodology ───────────────────────────────────────────────────────────────
    methHowWeCallTrendHeading: "How we call a trend",
    methHowWeCallTrendIntro: "We only call a trend when two separate checks agree — that way one odd reading doesn't set off a false alarm.",
    methRuleCheaper: "<strong>Getting cheaper:</strong> price has dropped more than ₹100 in a week, and the estimate or monthly average agrees",
    methRulePricier: "<strong>Getting pricier:</strong> price has climbed more than ₹100 in a week, and the estimate or monthly average agrees",
    methRuleSteady: "<strong>Steady:</strong> everything else — movement within ₹100 either way, or the two checks disagree",
    methNextDayRangeHeading: "Next trading day range",
    methEstimateLabel: "22K estimate",
    methRangeSub: ({ low, high }) => `Right about 4 times out of 5: ₹${low} – ₹${high}`,
    methMethodLabel: "Method",
    methAssumeNoChange: "Assume no change",
    methCoversMoves: "Covers most of the usual day-to-day moves",
    methTargetLine: ({ date }) => `Target: ${date}`,
    methNextDayExplainer: 'This is just for the next reading, not several days out — based on how much the price has typically moved by the next check over our last 30 test runs. (The "moves about ±₹X over 5 days" note above is a separate, longer-range estimate.)',
    methDirectionHeading: "Direction signal",
    methStatusLabel: "Status",
    methDirectionOff: "Off — not yet reliable",
    methDirectionSub: 'no model beats "gold usually rises" yet',
    methDirectionNote: 'We test our price-direction models every week. So far, none of them beat just assuming "gold usually goes up" — so we don\'t show a chance-of-rising percentage or tell you to buy or sell. The trend labels above (Getting cheaper/pricier/Steady) describe what already happened this week — they\'re not a prediction of what happens next.',
    methDirectionUnavailable: "Direction signal unavailable this cycle.",
    methHowAccurateHeading: "How accurate is this?",
    methAccurateP1Strong: "We assume tomorrow's price is about the same as today's",
    methAccurateP1: ({ n, naiveMae, chronosBullet }) =>
      `Gold prices are hard to predict even a few days out — every model we tried did worse than simply guessing "no change." Tested over ${n} time windows from 2022–2026:<br>&bull; Guessing "no change" was off by ₹${naiveMae}/g on average<br>${chronosBullet}So "no change" is what we go with.`,
    methAccurateP1ChronosBullet: ({ chronosMae, maePctWorse, pVal }) => `&bull; Our AI model was off by ₹${chronosMae}/g — ${maePctWorse}% worse (p&thinsp;=&thinsp;${pVal})<br>`,
    methRangeStrFallback: "the current range",
    methAccurateP2Strong: ({ rangeStr, coverageText }) => `Our ${rangeStr} range has been right ${coverageText}`,
    methAccurateP2CoveragePct: ({ pct, n }) => `${pct}% of the time (checked ${n} times so far)`,
    methAccurateP2CoverageUnknown: "close to on target so far — still building a track record",
    methAccurateP2: "It's based on just the last 30 test runs, so it's a small sample. We narrowed this range in July 2026 after realizing it had been sized for 5-day moves but only ever checked against next-day prices — so the percentage above may look better than it really is for a while, until enough checks have happened under the corrected, narrower range. We'll call it fully proven once that settles.",
    methAccurateP3Strong: "About the direction signal",
    methAccurateP3: ({ dirAllDisplay, n }) => `Our AI was right ${dirAllDisplay} of the time across ${n} test windows. But gold rises on roughly 70% of trading days anyway — so just guessing "up" every time would score about as well, with no model needed. We don't claim any edge here. The Getting cheaper/pricier labels above come from the recent 7-day trend, not from this AI.`,
    methAccurateP4Strong: "What would change this",
    methAccurateP4: "If gold started moving up and down more evenly (not mostly up), or if a model started reliably beating the \"gold usually rises\" guess in testing, we'd turn this back on. We'll update this section if that happens.",
    methDriftHeading: "Estimate accuracy — last 7 days",
    methRecentError: "Recent avg. error",
    methHistoricalError: "Historical avg. error",
    methAccuracyDrift: "Accuracy drift",
    ratioOnTrack: "on track",
    ratioWatch: "watch",
    ratioRetrain: "retraining recommended",
    ratioRetrainSub: "may need recalibration",

    // ── Error / degrade paths ────────────────────────────────────────────────────
    errPriceUnavailable: "Price unavailable",
    errCouldntLoadPrice: "Couldn't load the latest price. Check your connection and try again.",
    errCouldntLoadHistory: "Couldn't load price history.",
    errCouldntLoadMethodology: "Couldn't load model details — check your connection and reload.",

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
    firstVisitText: "22K सोने की खुदरा कीमत, हर 3 घंटे में जांची जाती है और जब संभव हो तो Tanishq की लाइव दर से पुष्टि की जाती है। कीमत अनुमानित हो तो हम साफ़ बता देते हैं।",
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
    calcMakingLabel: "मेकिंग चार्ज (%)",
    calcMakingAriaLabel: "मेकिंग चार्ज प्रतिशत",
    calcMakingHint: "जौहरी और डिज़ाइन के हिसाब से बदलता है — अगर पता हो तो अपना % डालें।",
    calcKaratLabel22: "22 KT",
    calcRowGoldValue: "सोने की कीमत",
    calcRowMaking: "मेकिंग चार्ज",
    calcRowGst: ({ pct }) => `GST (${pct}%)`,
    calcRowTotal: "कुल",
    calcOtherKarats: ({ k24, k18 }) => `24 KT: ₹${k24} · 18 KT: ₹${k18}`,
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
    footerBody: 'हम <a href="https://ibjarates.com/" target="_blank" rel="noopener">IBJA</a> के आधिकारिक सोने के बेंचमार्क का इस्तेमाल करते हैं और इसे असली दुकान की कीमतों से मिलाकर कैलिब्रेट करते हैं, और जब मुमकिन हो तो <a href="https://www.tanishq.co.in/gold-rate.html?lang=en_IN" target="_blank" rel="noopener">Tanishq</a> की लाइव कीमत से भी जांचते हैं। हर 3 घंटे में कीमत जांची जाती है — IBJA खुद दिन में एक बार अपडेट होता है, इसलिए कभी-कभी नंबर कुछ समय तक वही रहता है।',
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
    reliabilityCoverage: ({ pct, n }) => `हमारी अनुमानित रेंज अब तक ${pct}% बार सही रही है (${n} बार जांची गई)।`,
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
    calibrationConfidenceAppend: ({ amount }) => ` पिछली तुलनाओं के आधार पर, इस तरह का अनुमान आमतौर पर असली कीमत के ₹${amount}/ग्राम के दायरे में रहता है।`,
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

    // ── Methodology ───────────────────────────────────────────────────────────────
    methHowWeCallTrendHeading: "हम ट्रेंड कैसे तय करते हैं",
    methHowWeCallTrendIntro: "हम ट्रेंड तभी बताते हैं जब दो अलग जांच एक-दूसरे से सहमत हों — इससे एक अजीब रीडिंग की वजह से झूठी चेतावनी नहीं मिलती।",
    methRuleCheaper: "<strong>कीमत घटना:</strong> एक हफ्ते में कीमत ₹100 से ज़्यादा गिरी हो, और अनुमान या महीने का औसत भी इससे सहमत हो",
    methRulePricier: "<strong>कीमत बढ़ना:</strong> एक हफ्ते में कीमत ₹100 से ज़्यादा बढ़ी हो, और अनुमान या महीने का औसत भी इससे सहमत हो",
    methRuleSteady: "<strong>स्थिर:</strong> बाकी सभी मामले — ₹100 के अंदर घट-बढ़, या दोनों जांच आपस में असहमत हों",
    methNextDayRangeHeading: "अगले कारोबारी दिन की रेंज",
    methEstimateLabel: "22K अनुमान",
    methRangeSub: ({ low, high }) => `लगभग 5 में से 4 बार: ₹${low} – ₹${high}`,
    methMethodLabel: "तरीका",
    methAssumeNoChange: "कोई बदलाव न मानें",
    methCoversMoves: "ज़्यादातर सामान्य रोज़ाना घट-बढ़ को कवर करता है",
    methTargetLine: ({ date }) => `लक्ष्य समय: ${date}`,
    methNextDayExplainer: 'यह सिर्फ़ अगली रीडिंग के लिए है, कई दिन आगे के लिए नहीं — पिछले 30 टेस्ट रन में अगली रीडिंग तक कीमत आमतौर पर कितनी बदली, उस पर आधारित है। (ऊपर वाला "5 दिनों में करीब ±₹X" वाला नोट एक अलग, लंबे समय का अनुमान है।)',
    methDirectionHeading: "दिशा का संकेत",
    methStatusLabel: "स्थिति",
    methDirectionOff: "बंद — अभी भरोसेमंद नहीं",
    methDirectionSub: '"सोना आमतौर पर बढ़ता है" वाले अंदाज़े को अभी कोई मॉडल मात नहीं दे पाया',
    methDirectionNote: 'हम हर हफ्ते अपने दिशा-संकेत मॉडल टेस्ट करते हैं। अभी तक कोई भी सिर्फ़ "सोना आमतौर पर बढ़ता है" मान लेने से बेहतर नहीं निकला — इसलिए हम बढ़ने की संभावना वाला प्रतिशत नहीं दिखाते, न ही ख़रीदने-बेचने को कहते हैं। ऊपर दिए गए ट्रेंड लेबल (कीमत घटना/बढ़ना/स्थिर) बताते हैं कि इस हफ्ते क्या हुआ — यह आगे क्या होगा, इसका अंदाज़ा नहीं है।',
    methDirectionUnavailable: "इस बार दिशा का संकेत उपलब्ध नहीं है।",
    methHowAccurateHeading: "यह कितना सटीक है?",
    methAccurateP1Strong: "हम मानते हैं कि कल की कीमत आज जैसी ही रहेगी",
    methAccurateP1: ({ n, naiveMae, chronosBullet }) =>
      `सोने की कीमत का कुछ दिन आगे का अंदाज़ा लगाना भी मुश्किल है — हमने जितने भी मॉडल आज़माए, वे सब सिर्फ़ "कोई बदलाव नहीं" मान लेने से भी कमज़ोर निकले। 2022–2026 के बीच ${n} टाइम विंडो पर टेस्ट किया गया:<br>&bull; "कोई बदलाव नहीं" मानने पर औसतन ₹${naiveMae}/ग्राम का फ़र्क़ आया<br>${chronosBullet}इसलिए हम "कोई बदलाव नहीं" वाला अंदाज़ा ही इस्तेमाल करते हैं।`,
    methAccurateP1ChronosBullet: ({ chronosMae, maePctWorse, pVal }) => `&bull; हमारे AI मॉडल में ₹${chronosMae}/ग्राम का फ़र्क़ आया — ${maePctWorse}% ज़्यादा ख़राब (p&thinsp;=&thinsp;${pVal})<br>`,
    methRangeStrFallback: "मौजूदा रेंज",
    methAccurateP2Strong: ({ rangeStr, coverageText }) => `हमारी ${rangeStr} रेंज ${coverageText}`,
    methAccurateP2CoveragePct: ({ pct, n }) => `अब तक ${pct}% बार सही रही है (अब तक ${n} बार जांची गई)`,
    methAccurateP2CoverageUnknown: "अब तक लगभग लक्ष्य के अनुसार रही है — अभी इसका रिकॉर्ड बन रहा है",
    methAccurateP2: "यह सिर्फ़ पिछले 30 टेस्ट रन पर आधारित है, तो यह एक छोटा सैंपल है। जुलाई 2026 में हमने इस रेंज को छोटा किया, यह पता चलने के बाद कि यह 5-दिन के बदलाव के हिसाब से बनाई गई थी लेकिन हमेशा अगले-दिन की कीमतों के हिसाब से जांची जाती थी — इसलिए ऊपर दिया गया प्रतिशत कुछ समय तक असल से बेहतर दिख सकता है, जब तक कि सही, छोटी रेंज के तहत काफ़ी जांच न हो जाए। जब यह स्थिर हो जाएगा, तब हम इसे पूरी तरह सही मानेंगे।",
    methAccurateP3Strong: "दिशा के संकेत के बारे में",
    methAccurateP3: ({ dirAllDisplay, n }) => `हमारा AI ${n} टेस्ट विंडो में ${dirAllDisplay} बार सही निकला। लेकिन सोना वैसे भी करीब 70% ट्रेडिंग दिनों में बढ़ता है — तो बिना किसी मॉडल के हर बार सिर्फ़ "बढ़ेगा" कहने पर भी लगभग उतना ही सही होगा। हम यहां कोई बढ़त होने का दावा नहीं करते। ऊपर दिए गए "कीमत घटना/बढ़ना" वाले लेबल हाल के 7-दिन के ट्रेंड से आते हैं, इस AI से नहीं।`,
    methAccurateP4Strong: "यह कब बदलेगा",
    methAccurateP4: 'अगर सोना ऊपर-नीचे ज़्यादा बराबर मात्रा में होने लगे (सिर्फ़ बढ़ने के बजाय), या कोई मॉडल टेस्टिंग में "सोना आमतौर पर बढ़ता है" वाले अंदाज़े को लगातार मात देने लगे, तो हम इसे फिर से चालू करेंगे। ऐसा होने पर हम इस सेक्शन को अपडेट करेंगे।',
    methDriftHeading: "अनुमान की सटीकता — पिछले 7 दिन",
    methRecentError: "हाल की औसत त्रुटि",
    methHistoricalError: "ऐतिहासिक औसत त्रुटि",
    methAccuracyDrift: "सटीकता में बदलाव",
    ratioOnTrack: "ठीक चल रहा है",
    ratioWatch: "नज़र रखनी होगी",
    ratioRetrain: "दोबारा ट्रेनिंग की सलाह",
    ratioRetrainSub: "दोबारा कैलिब्रेशन की ज़रूरत हो सकती है",

    // ── Error / degrade paths ────────────────────────────────────────────────────
    errPriceUnavailable: "कीमत उपलब्ध नहीं",
    errCouldntLoadPrice: "ताज़ी कीमत लोड नहीं हो पाई। अपना कनेक्शन जांचें और फिर कोशिश करें।",
    errCouldntLoadHistory: "कीमत का इतिहास लोड नहीं हो पाया।",
    errCouldntLoadMethodology: "मॉडल की जानकारी लोड नहीं हो पाई — कनेक्शन जांचकर दोबारा लोड करें।",

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
