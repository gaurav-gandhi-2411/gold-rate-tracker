// how-we-know-strings.js — Technical content catalogue for how-we-know.html ONLY
// (en/hi). Loaded after i18n.js (reuses its currentLang/getLang/setLang globals,
// same convention app.js itself uses for i18n.js) and before how-we-know.js.
//
// U2 (2026-09-23, docs/PLAIN_LANGUAGE_AUDIT.md): the main page speaks plainly;
// every technical detail (p-values, MAE, raw coverage percentages+sample sizes,
// direction-signal internals) lives here instead, linked from the main page's
// accuracy-summary accordion ("See the full numbers and how we test all of
// this"). This is a SEPARATE catalogue from i18n.js's STRINGS on purpose, not a
// section of it: scripts/check_plain_language.py's banned-term sweep scans
// i18n.js in full (both languages), so keeping technical strings out of that
// file is what lets them stay technical without needing a growing allowlist.
// Most of the en block below is an unmodified copy of i18n.js's old meth* keys
// (see i18n.js's git history) -- same numbers, same wording, just relocated.
//
// The hi block covers every en key (2026-10 wording pass, docs/HINDI_GLOSSARY.md); it is
// LLM-consensus checked, not native-reviewed. tHwk() still falls back to English for any
// key a future en addition has not yet got a Hindi entry for.

// The next-rate model's test record (i18n.js directionSignalState().record), one sentence per
// language for both the On and the Off states. nForward: how many of the n days were calls made at
// the time (forecast.json next_fix.track_record.n_forward); the rest are re-runs on past data.
const _hwkRecordEn = ({ right, n, upRight, nForward }) => `Tested on ${n} past days it had not seen: right ${right} times, against ${upRight} for "gold usually rises". ${Number.isInteger(nForward) && nForward > 0 ? `${nForward} of those days were calls made at the time; the rest are re-runs on past data.` : "These are re-runs on past data, not calls made at the time."}`;
const _hwkRecordHi = ({ right, n, upRight, nForward }) => `ऐसे ${n} पुराने दिनों पर जांचा जो इसने पहले नहीं देखे थे: ${right} बार सही, जबकि "सोना आम तौर पर बढ़ता है" मानने पर ${upRight} बार। ${Number.isInteger(nForward) && nForward > 0 ? `इनमें से ${nForward} दिनों के अंदाज़े उसी समय लगाए गए थे; बाकी पुराने आंकड़ों पर दोबारा चलाकर निकाले गए नतीजे हैं।` : "ये पुराने आंकड़ों पर दोबारा चलाकर निकाले गए नतीजे हैं, उस समय लगाए गए अंदाज़े नहीं।"}`;

const STRINGS_HWK = {
  en: {
    hwkPageTitle: "How we know · Gold Rate Today",
    hwkPageDescription: "The full numbers behind Gold Rate Today's estimate: how the range is built, how accurate it has actually been, and why there's no rising/falling forecast.",
    hwkHeading: "How we know",
    hwkIntro: "This page shows the full detail behind the short summary on the main page — the exact numbers, and how we check them.",
    hwkBackLink: "← Back to Gold Rate Today",
    hwkLoading: "Loading…",
    hwkEmpty: "Not enough data yet to show this — check back once a few more readings have come in.",
    hwkError: "This page didn't load — usually because the internet connection dropped. The gold price on the main page is not affected. Please refresh this page to try again.",

    // ── How we call a trend ──────────────────────────────────────────────────────
    methHowWeCallTrendHeading: "How we call a trend",
    methHowWeCallTrendIntro: "We only call a trend when two separate checks agree — that way one odd reading doesn't set off a false alarm.",
    methRuleCheaper: "<strong>Getting cheaper:</strong> price has dropped more than ₹100 in a week, and the estimate or monthly average agrees",
    methRulePricier: "<strong>Getting pricier:</strong> price has climbed more than ₹100 in a week, and the estimate or monthly average agrees",
    methRuleSteady: "<strong>Steady:</strong> everything else — movement within ₹100 either way, or the two checks disagree",

    // ── Next trading day range ───────────────────────────────────────────────────
    methNextDayRangeHeading: "Next trading day range",
    methEstimateLabel: "22K estimate",
    methRangeSub: ({ low, high, times }) => (times ? `Right ${times} so far: ₹${low} – ₹${high}` : `Range: ₹${low} – ₹${high}`),
    methRangeSubTested: ({ low, high, times }) => (times ? `In a test on past days, right ${times}: ₹${low} – ₹${high}` : `Range: ₹${low} – ₹${high}`),
    methMethodLabel: "Method",
    methAssumeNoChange: "Assume no change",
    methNextFixModel: "World prices after the official rate",
    methNextFixModelSub: "how gold and the rupee moved since India's rate was last set",
    methNextFixHold: "Latest official rate",
    methNextFixHoldSub: "our best guess until the world prices after it are in",
    methNextFixStrong: "For the next official rate, we use world prices",
    methNextFixP: ({ n, modelMae, flatMae, pct }) =>
      `India's official rate is set twice each working day, in the morning and the afternoon, but gold and the rupee keep trading worldwide until late at night. After the afternoon rate, we use that later move to estimate the next afternoon rate. Tested on ${n} days it had not seen: off by ₹${modelMae}/g on average, against ₹${flatMae}/g for "no change" (${pct}% closer). For a week or more ahead, "no change" is still the best we have.`,
    methDirectionOn: "On — for the next price move only",
    methDirectionOnSub: (r) => `${_hwkRecordEn(r)} The re-run days also helped us pick the model, so live results can be a little worse.`,
    methDirectionOnNote: "We show whether the next move is more likely up or down, with its chance, only while it keeps beating \"gold usually rises\" on days it has not seen — we re-check this every few hours. It is not advice to buy or sell; that would need a much stronger record.",
    methDirectionSource: "The chance comes from the same estimate that gives the price range above, not from any other model: it looks at how far world gold and the rupee have moved since India's last official rate was set, and how closely India's rate has followed those moves in the past. Other models we run in the background are never used for this number.",
    methCoversMoves: "Covers most of the usual day-to-day moves",
    methTargetLine: ({ date }) => `Target: ${date}`,
    // B2 (2026-11-01): "our last 30 test runs" was hand-typed, and it describes only the older flat
    // range (ml/inference.py, _CONFORMAL_FOLDS; backtest.json naive_error_recent.folds_window). While
    // the next-rate model sets the range (next_fix.active), it is ml/nextfix.py's band instead: the
    // recent misses of the estimate shown, scaled by current volatility -- methNextDayExplainerNextFix.
    methNextDayExplainer: ({ n } = {}) => `This is just for the next reading, not several days out — based on how much the price has typically moved by the next check over our ${Number.isInteger(n) && n > 0 ? `last ${n}` : "recent"} test runs. (The "about ₹X in 5 days" note on the main page is a separate, longer-range estimate.)`,
    methNextDayExplainerNextFix: 'This is just for the next official rate, not several days out. The range comes from how far the official rate has recently landed from our estimate, made wider or narrower for how much prices are moving now. (The "about ₹X in 5 days" note on the main page is a separate, longer-range estimate.)',

    // ── Direction signal ─────────────────────────────────────────────────────────
    methDirectionHeading: "Direction signal",
    methStatusLabel: "Status",
    methDirectionOff: "Off — not yet reliable",
    methDirectionSub: 'no model beats "gold usually rises" yet',
    methDirectionNote: 'We test our price-direction models every week. So far, none of them beat just assuming "gold usually goes up" — so we don\'t show a chance-of-rising percentage or tell you to buy or sell. The trend labels on the main page (Getting cheaper/pricier/Steady) describe what already happened this week — they\'re not a prediction of what happens next.',
    methDirectionUnavailable: "Direction signal unavailable this cycle.",
    // 2026-11-02: the signal is approved (data/direction_promotion_record.json) and the main page
    // shows it from the US close until India's next morning rate ("after_us_close"); the rest of
    // the day the estimate holds the latest official rate and has no direction. The status says
    // that, or that the data is not up to date (i18n.js directionSignalState().hold).
    methDirectionOffHold: "Off until the US market closes",
    methDirectionOffPaused: "Off right now",
    methDirectionRecordSub: (r) => _hwkRecordEn(r),
    methDirectionHoldNote: "Our estimate of the next official rate gives a direction (more likely higher or lower) only from the US market's close until India's next morning rate is set. The rest of the day the estimate is simply India's latest official rate, so there is no direction to show. It comes back after the next US close. It is never advice to buy or sell.",
    methDirectionPausedNote: "The main page shows the direction only while our latest data is complete and up to date and the model is passing its regular checks. Right now one of these is not the case, so no direction is shown. It is never advice to buy or sell.",

    // ── How accurate is this ─────────────────────────────────────────────────────
    methHowAccurateHeading: "How accurate is this?",
    methAccurateP1Strong: "We assume tomorrow's price is about the same as today's",
    methAccurateP1: ({ n, naiveMae, chronosBullet, span }) =>
      `Gold prices are hard to predict even a few days out — every model we tried did worse than simply guessing "no change." Tested over ${n} time windows${span ? ` from ${span}` : ""}:<br>&bull; Guessing "no change" was off by ₹${naiveMae}/g on average<br>${chronosBullet}So "no change" is what we go with.`,
    methAccurateP1ChronosBullet: ({ chronosMae, maePctWorse, pValOp, pValText }) => `&bull; Our AI model was off by ₹${chronosMae}/g — ${maePctWorse}% worse (p&thinsp;${pValOp}&thinsp;${pValText})<br>`,
    methRangeStrFallback: "the current range",
    methAccurateP2Strong: ({ rangeStr, coverageText }) => `Our ${rangeStr} range has been right ${coverageText}`,
    methAccurateP2CoveragePct: ({ pct, n }) => `${pct}% of the time (checked ${n} times so far)`,
    methAccurateP2CoverageTested: ({ pct, n }) => `${pct}% of the time in a test on ${n} past days`,
    methAccurateP2Tested: "This is a test on past days, not a live record: each day's range is sized from the model's own recent misses, then checked against what the official rate turned out to be. Live results can differ, and the figure stays a test result until enough real days have passed.",
    methAccurateP2CoverageUnknown: "close to on target so far — still building a track record",
    methAccurateP2: ({ n } = {}) => `It's based on just the ${Number.isInteger(n) && n > 0 ? `last ${n}` : "most recent"} test runs, so it's a small sample. We narrowed this range in July 2026 after realizing it had been sized for 5-day moves but only ever checked against next-day prices — so the percentage above may look better than it really is for a while, until enough checks have happened under the corrected, narrower range. We'll call it fully proven once that settles.`,
    methAccurateP3Strong: "About the 5-day AI model",
    // AE2 (audit 2026-09-10): "roughly 70%" was a hand-typed, one-time
    // snapshot (ADR 019, 2026-06-02, a specific 165-fold backtest's P(actual
    // up)) -- not sourced from any field this codebase currently tracks
    // live (direction_baseline.json's always_up_accuracy is a different
    // metric/horizon; backtest.json's own dir_acc_5d_naive is a hardcoded
    // 0.5 constant, not a measured up-day frequency). Reworded to make the
    // same honest point -- a naive "always guess up" strategy is a strong,
    // hard-to-beat baseline in this regime (ADR 019's actual finding) --
    // without asserting a specific number nothing currently measures.
    // 2026-11-02 (GG brief): P3/P4 are about the 5-day AI model (data/backtest.json), not the
    // next-rate estimate's up-or-down call in "Direction signal", and compare it with what the
    // backtest compares it with: a coin toss (ml/backtest.py dir_acc_5d_naive = 0.5). The old
    // "gold has risen far more often" contradicted this page's own always-up count.
    methAccurateP3: ({ dirAllDisplay, n }) => `Our 5-day AI model called the direction right ${dirAllDisplay} of the time across ${n} test windows; a coin toss would be right about half the time. We don't claim any edge for it, and it is not what gives the up-or-down call in "Direction signal" above. The Getting cheaper/pricier labels on the main page come from the recent 7-day trend, not from this AI.`,
    methAccurateP4Strong: "What would change this",
    methAccurateP4: "If the 5-day model started reliably beating a coin toss in testing, we would say so here. It would still not be advice to buy or sell.",

    // ── Estimate accuracy drift ──────────────────────────────────────────────────
    methDriftHeading: "Estimate accuracy — last 7 days",
    methRecentError: "Recent avg. error",
    methHistoricalError: "Historical avg. error",
    methAccuracyDrift: "Accuracy drift",
    ratioOnTrack: "on track",
    ratioWatch: "watch",
    ratioRetrain: "retraining recommended",
    ratioRetrainSub: "may need recalibration",

    // ── Band accuracy (measured) ─────────────────────────────────────────────────
    // U2 (2026-09-23): preserves the exact numbers that used to sit inline in the
    // main page's stale-banner (i18n.js's calibrationConfidenceAppend), which now
    // shows a floored "N times out of 10" phrase instead. Same source field
    // (data/calibration_band_coverage.json via app.js's deriveMeasuredBandCoverage,
    // duplicated in how-we-know.js — see that file's own comment).
    methBandAccuracyHeading: "Band accuracy (measured)",
    methBandAccuracyText: ({ amount, pct, n, asOf }) => `In a test on past weeks, the real price landed within about ₹${amount}/gram of the displayed estimate ${pct}% of the time (n=${n} days measured, as of ${asOf}).`,
    methBandAccuracyUnknown: ({ days }) => `No measurement in the last ${days} days — the next weekly re-check will refresh this.`,
  },

  hi: {
    // Hindi wording follows docs/HINDI_GLOSSARY.md. Rewritten 2026-10; LLM-consensus checked, NOT
    // native-reviewed (reports/hindi_audit_2026-10/). This page is the technical surface, so a few
    // everyday-spoken technical words (मॉडल, टेस्ट, AI) are kept where no plain word exists.
    hwkPageTitle: "हम कैसे जानते हैं · आज सोने का भाव",
    hwkPageDescription: "आज के सोने के भाव के अनुमान के पीछे के पूरे आंकड़े: रेंज कैसे बनती है, यह असल में कितनी सही रही है, और भाव के बढ़ने-घटने का अंदाज़ा क्यों नहीं दिखाया जाता।",
    hwkHeading: "हम कैसे जानते हैं",
    hwkIntro: "यहां मुख्य पेज के छोटे सार के पीछे की पूरी जानकारी है — सटीक आंकड़े, और हम उन्हें कैसे जांचते हैं।",
    hwkBackLink: "← आज सोने का भाव पर वापस",
    hwkLoading: "लोड हो रहा है…",
    hwkEmpty: "यह दिखाने के लिए अभी काफ़ी डेटा नहीं है — कुछ और भाव दर्ज होने के बाद फिर देखें।",
    hwkError: "यह पेज लोड नहीं हुआ — आम तौर पर इंटरनेट कट जाने से ऐसा होता है। मुख्य पेज पर सोने का भाव इससे प्रभावित नहीं है। दोबारा कोशिश करने के लिए यह पेज रीफ़्रेश करें।",

    // ── How we call a trend ──────────────────────────────────────────────────────
    methHowWeCallTrendHeading: "हम ट्रेंड कैसे तय करते हैं",
    methHowWeCallTrendIntro: "हम ट्रेंड तभी बताते हैं जब दो अलग जांच एक-दूसरे से मेल खाएं — इससे एक अजीब भाव की वजह से झूठा संकेत नहीं मिलता।",
    methRuleCheaper: "<strong>सस्ता हो रहा है:</strong> एक हफ़्ते में भाव ₹100 से ज़्यादा गिरा हो, और अनुमान या महीने का औसत भी यही दिखाए",
    methRulePricier: "<strong>महंगा हो रहा है:</strong> एक हफ़्ते में भाव ₹100 से ज़्यादा बढ़ा हो, और अनुमान या महीने का औसत भी यही दिखाए",
    methRuleSteady: "<strong>स्थिर:</strong> बाक़ी सब मामले — ₹100 के अंदर घट-बढ़, या दोनों जांच एक-दूसरे से न मिलें",

    // ── Next trading day range ───────────────────────────────────────────────────
    methNextDayRangeHeading: "अगले कारोबारी दिन की रेंज",
    methEstimateLabel: "22 कैरेट अनुमान",
    methRangeSub: ({ low, high, n }) => (n != null ? `अब तक लगभग 10 में से ${n} बार सही: ₹${low} – ₹${high}` : `रेंज: ₹${low} – ₹${high}`),
    methRangeSubTested: ({ low, high, n }) => (n != null ? `पिछले दिनों के टेस्ट में लगभग 10 में से ${n} बार सही: ₹${low} – ₹${high}` : `रेंज: ₹${low} – ₹${high}`),
    methMethodLabel: "तरीक़ा",
    methAssumeNoChange: "कोई बदलाव न मानना",
    methNextFixModel: "आधिकारिक भाव के बाद दुनिया के भाव",
    methNextFixModelSub: "भारत का भाव तय होने के बाद सोना और रुपया कितना बदले",
    methNextFixHold: "सबसे ताज़ा आधिकारिक भाव",
    methNextFixHoldSub: "इसके बाद के दुनिया के भाव आने तक यही हमारा सबसे अच्छा अंदाज़ा है",
    methNextFixStrong: "अगले आधिकारिक भाव के लिए हम दुनिया के भाव देखते हैं",
    methNextFixP: ({ n, modelMae, flatMae, pct }) =>
      `भारत का आधिकारिक भाव हर कामकाजी दिन दो बार तय होता है, सुबह और दोपहर में, पर सोना और रुपया देर रात तक दुनिया भर में बिकते-ख़रीदे जाते हैं। दोपहर के भाव के बाद की उस चाल से हम अगली दोपहर के भाव का अनुमान लगाते हैं। ऐसे ${n} दिनों पर जांचा जो इसने पहले नहीं देखे थे: औसतन ₹${modelMae}/ग्राम का फ़र्क़, जबकि "कोई बदलाव नहीं" मानने पर ₹${flatMae}/ग्राम (${pct}% बेहतर)। एक हफ़्ते या उससे आगे के लिए "कोई बदलाव नहीं" ही अभी हमारे पास सबसे अच्छा अंदाज़ा है।`,
    methDirectionOn: "चालू — सिर्फ़ अगली चाल के लिए",
    methDirectionOnNote: "हम यह तभी दिखाते हैं कि अगली चाल ऊपर की ओर होने की संभावना ज़्यादा है या नीचे की, और कितनी — जब तक यह नए दिनों पर \"सोना आम तौर पर बढ़ता है\" वाले अंदाज़े से बेहतर रहता है; इसे हर कुछ घंटों में दोबारा जांचा जाता है। यह ख़रीदने या बेचने की सलाह नहीं है; उसके लिए इससे कहीं मज़बूत रिकॉर्ड चाहिए।",
    methCoversMoves: "रोज़ की ज़्यादातर आम घट-बढ़ इसमें आ जाती है",
    methTargetLine: ({ date }) => `किस दिन के लिए: ${date}`,
    methNextDayExplainer: ({ n } = {}) => `यह सिर्फ़ अगले भाव के लिए है, कई दिन आगे के लिए नहीं — हमारे ${Number.isInteger(n) && n > 0 ? `पिछले ${n}` : "हाल के"} टेस्ट में अगली जांच तक भाव आम तौर पर कितना बदला, उस पर आधारित है। (मुख्य पेज का "5 दिनों में करीब ₹X" वाला नोट एक अलग, लंबे समय का अनुमान है।)`,
    methNextDayExplainerNextFix: 'यह सिर्फ़ अगले आधिकारिक भाव के लिए है, कई दिन आगे के लिए नहीं। रेंज इससे बनती है कि हाल में आधिकारिक भाव हमारे अनुमान से कितना दूर रहा; अभी भाव जितना घट-बढ़ रहा है, उसके हिसाब से रेंज बड़ी या छोटी होती है। (मुख्य पेज का "5 दिनों में करीब ₹X" वाला नोट एक अलग, लंबे समय का अनुमान है।)',

    // ── Direction signal ─────────────────────────────────────────────────────────
    methDirectionHeading: "ऊपर या नीचे का संकेत",
    methStatusLabel: "स्थिति",
    methDirectionOff: "बंद — अभी भरोसे लायक़ नहीं",
    methDirectionSub: '"सोना आम तौर पर बढ़ता है" वाले अंदाज़े को अभी कोई मॉडल मात नहीं दे पाया',
    methDirectionNote: 'हम हर हफ़्ते भाव के ऊपर-नीचे जाने का अंदाज़ा लगाने वाले अपने मॉडल जांचते हैं। अभी तक कोई भी सिर्फ़ "सोना आम तौर पर बढ़ता है" मान लेने से बेहतर नहीं निकला — इसलिए हम बढ़ने की संभावना का प्रतिशत नहीं दिखाते, और ख़रीदने-बेचने को भी नहीं कहते। मुख्य पेज के ट्रेंड लेबल (सस्ता हो रहा है / महंगा हो रहा है / स्थिर) बताते हैं कि इस हफ़्ते क्या हो चुका है — वे आगे क्या होगा, इसका अंदाज़ा नहीं हैं।',
    methDirectionUnavailable: "इस बार ऊपर-नीचे का संकेत उपलब्ध नहीं है।",
    // 2026-11-03, LLM consensus (Sonnet + Opus, docs/HINDI_CONTESTED.md): the two "On" strings had no
    // Hindi (the page fell back to English).
    methDirectionOnSub: (r) => `${_hwkRecordHi(r)} दोबारा चलाए गए इन्हीं दिनों से हमने मॉडल चुनने में भी मदद ली थी, इसलिए आगे के नतीजे थोड़े कमज़ोर हो सकते हैं।`,
    methDirectionSource: "यह संभावना उसी अनुमान से आती है जिससे ऊपर की भाव-रेंज बनती है, किसी और मॉडल से नहीं: यह देखता है कि भारत का पिछला आधिकारिक भाव तय होने के बाद दुनिया में सोना और रुपया कितना बदले हैं, और पहले भारत का भाव ऐसी चालों के साथ कितनी नज़दीकी से चला है। हम पर्दे के पीछे जो दूसरे मॉडल चलाते हैं, उनका इस संख्या में कभी इस्तेमाल नहीं होता।",
    // 2026-11-03, LLM consensus (docs/HINDI_CONTESTED.md): the reasons the signal is off.
    methDirectionOffHold: "अमेरिकी बाज़ार बंद होने तक बंद",
    methDirectionOffPaused: "अभी बंद",
    methDirectionRecordSub: (r) => _hwkRecordHi(r),
    methDirectionHoldNote: "अगले आधिकारिक भाव का हमारा अनुमान दिशा (ज़्यादा होने या कम होने की संभावना) सिर्फ़ अमेरिकी बाज़ार बंद होने से लेकर भारत का अगला सुबह का भाव तय होने तक बताता है। दिन के बाकी समय यह अनुमान बस भारत का सबसे ताज़ा आधिकारिक भाव होता है, इसलिए दिखाने के लिए कोई दिशा नहीं होती। अगली बार अमेरिकी बाज़ार बंद होने के बाद दिशा फिर दिखेगी। यह कभी ख़रीदने या बेचने की सलाह नहीं है।",
    methDirectionPausedNote: "मुख्य पेज दिशा तभी दिखाता है जब हमारा ताज़ा डेटा पूरा और अपडेटेड हो और मॉडल अपनी नियमित जांच में पास हो रहा हो। अभी इनमें से कोई एक बात पूरी नहीं है, इसलिए कोई दिशा नहीं दिखाई जा रही। यह कभी ख़रीदने या बेचने की सलाह नहीं है।",

    // ── How accurate is this ─────────────────────────────────────────────────────
    methHowAccurateHeading: "यह कितना सही है?",
    methAccurateP1Strong: "हम मानते हैं कि कल का भाव आज जैसा ही रहेगा",
    methAccurateP1: ({ n, naiveMae, chronosBullet, span }) =>
      `सोने के भाव का कुछ दिन आगे का अंदाज़ा लगाना भी मुश्किल है — हमने जितने भी मॉडल आज़माए, सब सिर्फ़ "कोई बदलाव नहीं" मान लेने से भी कमज़ोर निकले। ${span ? `${span} के बीच ` : ""}${n} अलग-अलग समय-खंडों पर टेस्ट किया गया:<br>&bull; "कोई बदलाव नहीं" मानने पर औसतन ₹${naiveMae}/ग्राम का फ़र्क़ आया<br>${chronosBullet}इसलिए हम "कोई बदलाव नहीं" वाला अंदाज़ा ही रखते हैं।`,
    methAccurateP1ChronosBullet: ({ chronosMae, maePctWorse, pValOp, pValText }) => `&bull; हमारे AI मॉडल में ₹${chronosMae}/ग्राम का फ़र्क़ आया — ${maePctWorse}% ज़्यादा ग़लत (p&thinsp;${pValOp}&thinsp;${pValText})<br>`,
    methRangeStrFallback: "मौजूदा",
    methAccurateP2: ({ n } = {}) => `यह सिर्फ़ ${Number.isInteger(n) && n > 0 ? `पिछले ${n}` : "हाल के कुछ"} टेस्ट पर आधारित है, इसलिए नमूना छोटा है। जुलाई 2026 में हमने इस रेंज को छोटा किया, क्योंकि पता चला कि इसे 5 दिन की चाल के हिसाब से बनाया गया था पर हमेशा अगले दिन के भाव से जांचा जाता था — इसलिए ऊपर का प्रतिशत कुछ समय तक असल से बेहतर दिख सकता है, जब तक सुधारी हुई, छोटी रेंज पर काफ़ी जांच न हो जाएं। जब यह टिक जाएगा, तब हम इसे पूरी तरह पक्का मानेंगे।`,
    methAccurateP3Strong: "5 दिन वाले AI मॉडल के बारे में",
    methAccurateP3: ({ dirAllDisplay, n }) => `हमारे 5 दिन वाले AI मॉडल ने ${n} टेस्ट में ${dirAllDisplay} मौकों पर दिशा सही बताई; सिक्का उछालने पर लगभग आधी बार सही निकलता। हम यह दावा नहीं करते कि यह सिक्का उछालने से बेहतर है, और ऊपर "ऊपर या नीचे का संकेत" वाला अंदाज़ा इससे नहीं आता। मुख्य पेज के "सस्ता / महंगा हो रहा है" वाले लेबल हाल के 7 दिन के ट्रेंड से आते हैं, इस AI से नहीं।`,
    methAccurateP4Strong: "इसमें बदलाव कब होगा",
    methAccurateP4: 'अगर 5 दिन वाला मॉडल टेस्ट में लगातार सिक्का उछालने से बेहतर निकलने लगे, तो हम यहां बताएंगे। तब भी यह ख़रीदने या बेचने की सलाह नहीं होगी।',

    // ── Estimate accuracy drift ──────────────────────────────────────────────────
    methRecentError: "हाल की औसत ग़लती",
    methHistoricalError: "पहले की औसत ग़लती",
    methAccuracyDrift: "सही रहने में बदलाव",
    ratioOnTrack: "ठीक चल रहा है",
    ratioWatch: "नज़र रखनी होगी",
    ratioRetrain: "दोबारा ट्रेनिंग की सलाह",
    ratioRetrainSub: "इसे फिर से ठीक करने की ज़रूरत हो सकती है",

    // ── Band accuracy (measured) ─────────────────────────────────────────────────
    methBandAccuracyHeading: "रेंज कितनी सही रही (नापा हुआ)",
    methBandAccuracyUnknown: ({ days }) => `पिछले ${days} दिनों में कोई माप नहीं हुआ — अगली साप्ताहिक जांच इसे अपडेट कर देगी।`,
  },
};

// tHwk(key, params) — same lookup/fallback shape as i18n.js's t(), against
// STRINGS_HWK instead of STRINGS, and always falling back to STRINGS_HWK.en
// (never STRINGS.en — the two catalogues don't share keys).
function tHwk(key, params) {
  const entry = STRINGS_HWK[currentLang]?.[key] ?? STRINGS_HWK.en[key];
  if (entry === undefined) return key; // missing key entirely — surface it, don't hide it
  return typeof entry === "function" ? entry(params) : entry;
}
