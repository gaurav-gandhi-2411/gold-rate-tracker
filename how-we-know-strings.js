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
// The hi block only carries keys that already had a Hindi translation before
// this move (copied verbatim, unchanged). Brand-new keys added on this page
// (hwk*, methBandAccuracy*) have NO Hindi entry yet on purpose, pending
// native-speaker review — tHwk() falls back to English until they're added,
// same convention as i18n.js's own pending-review list.

const STRINGS_HWK = {
  en: {
    hwkPageTitle: "How we know · Gold Rate Today",
    hwkPageDescription: "The full numbers behind Gold Rate Today's estimate: how the range is built, how accurate it has actually been, and why there's no rising/falling forecast.",
    hwkHeading: "How we know",
    hwkIntro: "This page shows the full detail behind the short summary on the main page — the exact numbers, and how we check them.",
    hwkBackLink: "← Back to Gold Rate Today",
    hwkLoading: "Loading…",
    hwkEmpty: "Not enough data yet to show this — check back once a few more readings have come in.",
    hwkError: "We couldn't show these numbers right now. Please check your internet and refresh the page.",

    // ── How we call a trend ──────────────────────────────────────────────────────
    methHowWeCallTrendHeading: "How we call a trend",
    methHowWeCallTrendIntro: "We only call a trend when two separate checks agree — that way one odd reading doesn't set off a false alarm.",
    methRuleCheaper: "<strong>Getting cheaper:</strong> price has dropped more than ₹100 in a week, and the estimate or monthly average agrees",
    methRulePricier: "<strong>Getting pricier:</strong> price has climbed more than ₹100 in a week, and the estimate or monthly average agrees",
    methRuleSteady: "<strong>Steady:</strong> everything else — movement within ₹100 either way, or the two checks disagree",

    // ── Next trading day range ───────────────────────────────────────────────────
    methNextDayRangeHeading: "Next trading day range",
    methEstimateLabel: "22K estimate",
    methRangeSub: ({ low, high }) => `Right about 4 times out of 5: ₹${low} – ₹${high}`,
    methMethodLabel: "Method",
    methAssumeNoChange: "Assume no change",
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
    methAccurateP1ChronosBullet: ({ chronosMae, maePctWorse, pVal }) => `&bull; Our AI model was off by ₹${chronosMae}/g — ${maePctWorse}% worse (p&thinsp;=&thinsp;${pVal})<br>`,
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
    methBandAccuracyText: ({ amount, pct, n, asOf }) => `The real price has landed within about ₹${amount}/gram of the displayed estimate ${pct}% of the time so far (n=${n} weeks measured, as of ${asOf}).`,
    methBandAccuracyUnknown: "No measurement in the last 14 days — the next weekly re-check will refresh this.",
  },

  hi: {
    // ── How we call a trend ──────────────────────────────────────────────────────
    methHowWeCallTrendHeading: "हम ट्रेंड कैसे तय करते हैं",
    methHowWeCallTrendIntro: "हम ट्रेंड तभी बताते हैं जब दो अलग जांच एक-दूसरे से सहमत हों — इससे एक अजीब रीडिंग की वजह से झूठी चेतावनी नहीं मिलती।",
    methRuleCheaper: "<strong>कीमत घटना:</strong> एक हफ्ते में कीमत ₹100 से ज़्यादा गिरी हो, और अनुमान या महीने का औसत भी इससे सहमत हो",
    methRulePricier: "<strong>कीमत बढ़ना:</strong> एक हफ्ते में कीमत ₹100 से ज़्यादा बढ़ी हो, और अनुमान या महीने का औसत भी इससे सहमत हो",
    methRuleSteady: "<strong>स्थिर:</strong> बाकी सभी मामले — ₹100 के अंदर घट-बढ़, या दोनों जांच आपस में असहमत हों",

    // ── Next trading day range ───────────────────────────────────────────────────
    methNextDayRangeHeading: "अगले कारोबारी दिन की रेंज",
    methEstimateLabel: "22K अनुमान",
    methRangeSub: ({ low, high }) => `लगभग 5 में से 4 बार: ₹${low} – ₹${high}`,
    methMethodLabel: "तरीका",
    methAssumeNoChange: "कोई बदलाव न मानें",
    methCoversMoves: "ज़्यादातर सामान्य रोज़ाना घट-बढ़ को कवर करता है",
    methTargetLine: ({ date }) => `लक्ष्य समय: ${date}`,
    methNextDayExplainer: 'यह सिर्फ़ अगली रीडिंग के लिए है, कई दिन आगे के लिए नहीं — पिछले 30 टेस्ट रन में अगली रीडिंग तक कीमत आमतौर पर कितनी बदली, उस पर आधारित है। (मुख्य पेज वाला "5 दिनों में करीब ±₹X" वाला नोट एक अलग, लंबे समय का अनुमान है।)',

    // ── Direction signal ─────────────────────────────────────────────────────────
    methDirectionHeading: "दिशा का संकेत",
    methStatusLabel: "स्थिति",
    methDirectionOff: "बंद — अभी भरोसेमंद नहीं",
    methDirectionSub: '"सोना आमतौर पर बढ़ता है" वाले अंदाज़े को अभी कोई मॉडल मात नहीं दे पाया',
    methDirectionNote: 'हम हर हफ्ते अपने दिशा-संकेत मॉडल टेस्ट करते हैं। अभी तक कोई भी सिर्फ़ "सोना आमतौर पर बढ़ता है" मान लेने से बेहतर नहीं निकला — इसलिए हम बढ़ने की संभावना वाला प्रतिशत नहीं दिखाते, न ही ख़रीदने-बेचने को कहते हैं। मुख्य पेज पर दिए गए ट्रेंड लेबल (कीमत घटना/बढ़ना/स्थिर) बताते हैं कि इस हफ्ते क्या हुआ — यह आगे क्या होगा, इसका अंदाज़ा नहीं है।',
    methDirectionUnavailable: "इस बार दिशा का संकेत उपलब्ध नहीं है।",

    // ── How accurate is this ─────────────────────────────────────────────────────
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
    // AE2: see the EN string's comment above -- "करीब 70%" was a stale,
    // one-time snapshot, not sourced from any live-tracked field.
    methAccurateP3: ({ dirAllDisplay, n }) => `हमारा AI ${n} टेस्ट विंडो में ${dirAllDisplay} बार सही निकला। लेकिन सोना इतिहास में गिरने से कहीं ज़्यादा बार बढ़ा है — तो बिना किसी मॉडल के हर बार सिर्फ़ "बढ़ेगा" कहने पर भी लगभग उतना ही सही होगा। हम यहां कोई बढ़त होने का दावा नहीं करते। मुख्य पेज पर दिए गए "कीमत घटना/बढ़ना" वाले लेबल हाल के 7-दिन के ट्रेंड से आते हैं, इस AI से नहीं।`,
    methAccurateP4Strong: "यह कब बदलेगा",
    methAccurateP4: 'अगर सोना ऊपर-नीचे ज़्यादा बराबर मात्रा में होने लगे (सिर्फ़ बढ़ने के बजाय), या कोई मॉडल टेस्टिंग में "सोना आमतौर पर बढ़ता है" वाले अंदाज़े को लगातार मात देने लगे, तो हम इसे फिर से चालू करेंगे। ऐसा होने पर हम इस सेक्शन को अपडेट करेंगे।',

    // ── Estimate accuracy drift ──────────────────────────────────────────────────
    methDriftHeading: "अनुमान की सटीकता — पिछले 7 दिन",
    methRecentError: "हाल की औसत त्रुटि",
    methHistoricalError: "ऐतिहासिक औसत त्रुटि",
    methAccuracyDrift: "सटीकता में बदलाव",
    ratioOnTrack: "ठीक चल रहा है",
    ratioWatch: "नज़र रखनी होगी",
    ratioRetrain: "दोबारा ट्रेनिंग की सलाह",
    ratioRetrainSub: "दोबारा कैलिब्रेशन की ज़रूरत हो सकती है",

    // hwk*/methBandAccuracy* have NO Hindi entry yet on purpose -- brand new on
    // this page, pending native-speaker review. tHwk() falls back to English.
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
