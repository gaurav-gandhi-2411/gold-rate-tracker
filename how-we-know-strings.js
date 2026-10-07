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
    methMethodLabel: "Method",
    methAssumeNoChange: "Assume no change",
    methNextFixModel: "World prices after the official rate",
    methNextFixModelSub: "how gold and the rupee moved since India's rate was last set",
    methNextFixHold: "Latest official rate",
    methNextFixHoldSub: "our best guess until the world prices after it are in",
    methNextFixStrong: "For the next official rate, we use world prices",
    methNextFixP: ({ n, modelMae, flatMae, pct }) =>
      `India's official rate is set once each afternoon, but gold and the rupee keep trading worldwide until late at night. We use that later move to estimate the next rate. Tested on ${n} days it had not seen: off by ₹${modelMae}/g on average, against ₹${flatMae}/g for "no change" (${pct}% closer). For a week or more ahead, "no change" is still the best we have.`,
    methDirectionOn: "On — for the next price move only",
    methDirectionOnSub: ({ right, n, upRight }) => `Tested on ${n} past days it had not seen: right ${right} times, against ${upRight} for "gold usually rises". These are past results, not live calls.`,
    methDirectionOnNote: "We show whether the next move is more likely up or down, with its chance, only while it keeps beating \"gold usually rises\" on days it has not seen — we re-check this every few hours. It is not advice to buy or sell; that would need a much stronger record.",
    methCoversMoves: "Covers most of the usual day-to-day moves",
    methTargetLine: ({ date }) => `Target: ${date}`,
    methNextDayExplainer: 'This is just for the next reading, not several days out — based on how much the price has typically moved by the next check over our last 30 test runs. (The "moves about ±₹X over 5 days" note on the main page is a separate, longer-range estimate.)',

    // ── Direction signal ─────────────────────────────────────────────────────────
    methDirectionHeading: "Direction signal",
    methStatusLabel: "Status",
    methDirectionOff: "Off — not yet reliable",
    methDirectionSub: 'no model beats "gold usually rises" yet',
    methDirectionNote: 'We test our price-direction models every week. So far, none of them beat just assuming "gold usually goes up" — so we don\'t show a chance-of-rising percentage or tell you to buy or sell. The trend labels on the main page (Getting cheaper/pricier/Steady) describe what already happened this week — they\'re not a prediction of what happens next.',
    methDirectionUnavailable: "Direction signal unavailable this cycle.",

    // ── How accurate is this ─────────────────────────────────────────────────────
    methHowAccurateHeading: "How accurate is this?",
    methAccurateP1Strong: "We assume tomorrow's price is about the same as today's",
    methAccurateP1: ({ n, naiveMae, chronosBullet }) =>
      `Gold prices are hard to predict even a few days out — every model we tried did worse than simply guessing "no change." Tested over ${n} time windows from 2022–2026:<br>&bull; Guessing "no change" was off by ₹${naiveMae}/g on average<br>${chronosBullet}So "no change" is what we go with.`,
    methAccurateP1ChronosBullet: ({ chronosMae, maePctWorse, pValOp, pValText }) => `&bull; Our AI model was off by ₹${chronosMae}/g — ${maePctWorse}% worse (p&thinsp;${pValOp}&thinsp;${pValText})<br>`,
    methRangeStrFallback: "the current range",
    methAccurateP2Strong: ({ rangeStr, coverageText }) => `Our ${rangeStr} range has been right ${coverageText}`,
    methAccurateP2CoveragePct: ({ pct, n }) => `${pct}% of the time (checked ${n} times so far)`,
    methAccurateP2CoverageUnknown: "close to on target so far — still building a track record",
    methAccurateP2: "It's based on just the last 30 test runs, so it's a small sample. We narrowed this range in July 2026 after realizing it had been sized for 5-day moves but only ever checked against next-day prices — so the percentage above may look better than it really is for a while, until enough checks have happened under the corrected, narrower range. We'll call it fully proven once that settles.",
    methAccurateP3Strong: "About the direction signal",
    // AE2 (audit 2026-09-10): "roughly 70%" was a hand-typed, one-time
    // snapshot (ADR 019, 2026-06-02, a specific 165-fold backtest's P(actual
    // up)) -- not sourced from any field this codebase currently tracks
    // live (direction_baseline.json's always_up_accuracy is a different
    // metric/horizon; backtest.json's own dir_acc_5d_naive is a hardcoded
    // 0.5 constant, not a measured up-day frequency). Reworded to make the
    // same honest point -- a naive "always guess up" strategy is a strong,
    // hard-to-beat baseline in this regime (ADR 019's actual finding) --
    // without asserting a specific number nothing currently measures.
    methAccurateP3: ({ dirAllDisplay, n }) => `Our AI was right ${dirAllDisplay} of the time across ${n} test windows. But gold has historically risen far more often than it's fallen — so just guessing "up" every time would score close to as well, with no model needed. We don't claim any edge here. The Getting cheaper/pricier labels on the main page come from the recent 7-day trend, not from this AI.`,
    methAccurateP4Strong: "What would change this",
    methAccurateP4: "If gold started moving up and down more evenly (not mostly up), or if a model started reliably beating the \"gold usually rises\" guess in testing, we'd turn this back on. We'll update this section if that happens.",

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
    methBandAccuracyText: ({ amount, pct, n, asOf }) => `In a test on past weeks, the real price landed within about ₹${amount}/gram of the displayed estimate ${pct}% of the time (n=${n} weeks measured, as of ${asOf}).`,
    methBandAccuracyUnknown: "No measurement in the last 14 days — the next weekly re-check will refresh this.",
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
    methMethodLabel: "तरीक़ा",
    methAssumeNoChange: "कोई बदलाव न मानना",
    methNextFixModel: "आधिकारिक भाव के बाद दुनिया के भाव",
    methNextFixModelSub: "भारत का भाव तय होने के बाद सोना और रुपया कितना बदले",
    methNextFixHold: "सबसे ताज़ा आधिकारिक भाव",
    methNextFixHoldSub: "इसके बाद के दुनिया के भाव आने तक यही हमारा सबसे अच्छा अंदाज़ा है",
    methNextFixStrong: "अगले आधिकारिक भाव के लिए हम दुनिया के भाव देखते हैं",
    methNextFixP: ({ n, modelMae, flatMae, pct }) =>
      `भारत का आधिकारिक भाव रोज़ दोपहर में एक बार तय होता है, पर सोना और रुपया देर रात तक दुनिया भर में बिकते-ख़रीदे जाते हैं। उस बाद की चाल से हम अगले भाव का अनुमान लगाते हैं। ऐसे ${n} दिनों पर जांचा जो इसने पहले नहीं देखे थे: औसतन ₹${modelMae}/ग्राम का फ़र्क़, जबकि "कोई बदलाव नहीं" मानने पर ₹${flatMae}/ग्राम (${pct}% बेहतर)। एक हफ़्ते या उससे आगे के लिए "कोई बदलाव नहीं" ही अभी हमारे पास सबसे अच्छा अंदाज़ा है।`,
    methDirectionOn: "चालू — सिर्फ़ अगली चाल के लिए",
    methDirectionOnNote: "हम यह तभी दिखाते हैं कि अगली चाल ऊपर की ओर होने की संभावना ज़्यादा है या नीचे की, और कितनी — जब तक यह नए दिनों पर \"सोना आम तौर पर बढ़ता है\" वाले अंदाज़े से बेहतर रहता है; इसे हर कुछ घंटों में दोबारा जांचा जाता है। यह ख़रीदने या बेचने की सलाह नहीं है; उसके लिए इससे कहीं मज़बूत रिकॉर्ड चाहिए।",
    methCoversMoves: "रोज़ की ज़्यादातर आम घट-बढ़ इसमें आ जाती है",
    methTargetLine: ({ date }) => `किस दिन के लिए: ${date}`,
    methNextDayExplainer: 'यह सिर्फ़ अगले भाव के लिए है, कई दिन आगे के लिए नहीं — हमारे पिछले 30 टेस्ट में अगली जांच तक भाव आम तौर पर कितना बदला, उस पर आधारित है। (मुख्य पेज का "5 दिनों में करीब ±₹X" वाला नोट एक अलग, लंबे समय का अनुमान है।)',

    // ── Direction signal ─────────────────────────────────────────────────────────
    methDirectionHeading: "ऊपर या नीचे का संकेत",
    methStatusLabel: "स्थिति",
    methDirectionOff: "बंद — अभी भरोसे लायक़ नहीं",
    methDirectionSub: '"सोना आम तौर पर बढ़ता है" वाले अंदाज़े को अभी कोई मॉडल मात नहीं दे पाया',
    methDirectionNote: 'हम हर हफ़्ते भाव के ऊपर-नीचे जाने का अंदाज़ा लगाने वाले अपने मॉडल जांचते हैं। अभी तक कोई भी सिर्फ़ "सोना आम तौर पर बढ़ता है" मान लेने से बेहतर नहीं निकला — इसलिए हम बढ़ने की संभावना का प्रतिशत नहीं दिखाते, और ख़रीदने-बेचने को भी नहीं कहते। मुख्य पेज के ट्रेंड लेबल (सस्ता हो रहा है / महंगा हो रहा है / स्थिर) बताते हैं कि इस हफ़्ते क्या हो चुका है — वे आगे क्या होगा, इसका अंदाज़ा नहीं हैं।',
    methDirectionUnavailable: "इस बार ऊपर-नीचे का संकेत उपलब्ध नहीं है।",

    // ── How accurate is this ─────────────────────────────────────────────────────
    methHowAccurateHeading: "यह कितना सही है?",
    methAccurateP1Strong: "हम मानते हैं कि कल का भाव आज जैसा ही रहेगा",
    methAccurateP1: ({ n, naiveMae, chronosBullet }) =>
      `सोने के भाव का कुछ दिन आगे का अंदाज़ा लगाना भी मुश्किल है — हमने जितने भी मॉडल आज़माए, सब सिर्फ़ "कोई बदलाव नहीं" मान लेने से भी कमज़ोर निकले। 2022–2026 के बीच ${n} अलग-अलग समय-खंडों पर टेस्ट किया गया:<br>&bull; "कोई बदलाव नहीं" मानने पर औसतन ₹${naiveMae}/ग्राम का फ़र्क़ आया<br>${chronosBullet}इसलिए हम "कोई बदलाव नहीं" वाला अंदाज़ा ही रखते हैं।`,
    methAccurateP1ChronosBullet: ({ chronosMae, maePctWorse, pValOp, pValText }) => `&bull; हमारे AI मॉडल में ₹${chronosMae}/ग्राम का फ़र्क़ आया — ${maePctWorse}% ज़्यादा ग़लत (p&thinsp;${pValOp}&thinsp;${pValText})<br>`,
    methRangeStrFallback: "मौजूदा",
    methAccurateP2: "यह सिर्फ़ पिछले 30 टेस्ट पर आधारित है, इसलिए नमूना छोटा है। जुलाई 2026 में हमने इस रेंज को छोटा किया, क्योंकि पता चला कि इसे 5 दिन की चाल के हिसाब से बनाया गया था पर हमेशा अगले दिन के भाव से जांचा जाता था — इसलिए ऊपर का प्रतिशत कुछ समय तक असल से बेहतर दिख सकता है, जब तक सुधारी हुई, छोटी रेंज पर काफ़ी जांच न हो जाएं। जब यह टिक जाएगा, तब हम इसे पूरी तरह पक्का मानेंगे।",
    methAccurateP3Strong: "ऊपर-नीचे के संकेत के बारे में",
    methAccurateP3: ({ dirAllDisplay, n }) => `हमारा AI ${n} टेस्ट में ${dirAllDisplay} बार सही निकला। लेकिन सोना इतिहास में गिरने से कहीं ज़्यादा बार बढ़ा है — इसलिए बिना किसी मॉडल के हर बार सिर्फ़ "बढ़ेगा" कहने पर भी लगभग उतना ही सही निकलता। हम यहां किसी बढ़त का दावा नहीं करते। मुख्य पेज के "सस्ता / महंगा हो रहा है" वाले लेबल हाल के 7 दिन के ट्रेंड से आते हैं, इस AI से नहीं।`,
    methAccurateP4Strong: "इसमें बदलाव कब होगा",
    methAccurateP4: 'अगर सोना ऊपर-नीचे ज़्यादा बराबरी से चलने लगे (ज़्यादातर बढ़ने के बजाय), या कोई मॉडल टेस्ट में "सोना आम तौर पर बढ़ता है" वाले अंदाज़े को लगातार मात देने लगे, तो हम इसे फिर चालू करेंगे। ऐसा होने पर हम यह हिस्सा अपडेट करेंगे।',

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
    methBandAccuracyUnknown: "पिछले 14 दिनों में कोई माप नहीं हुआ — अगली साप्ताहिक जांच इसे अपडेट कर देगी।",
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
