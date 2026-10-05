# Hindi strings the judges contested (optional review list for GG)

LLM consensus, not native review. Three local judges (gemma2:9b, llama3.1:8b, qwen3:30b-a3b) rated each new
string blind; a string is listed here only when a MAJORITY of them scored at least one criterion 3 or lower
(30 of 353 new strings). Judge-pair agreement is modest (kappa 0.12-0.44, see
`reports/hindi_audit_2026-10/summary_stats.json` and the PR body), so most entries are judge noise or a judge lacking context; the
last column is the author's reading, not a judge output. Everything else (323 strings) was not contested.
A native speaker is still the real review; this list is only where to look first.

| Key | English | Old Hindi | New Hindi | Contested because |
|---|---|---|---|---|
| `trendDirUp` | up | बढ़त | बढ़त | all judges flag; judge saw a bare fragment without the sentence it is inserted into (inserted after 'ट्रेंड:' in the chart aria-label) |
| `methAssumeNoChange` | Assume no change | कोई बदलाव न मानें | कोई बदलाव न मानना | all judges flag; judge saw a bare fragment without the sentence it is inserted into |
| `dirWordUnchanged` | unchanged | जस की तस रही | जस का तस रहा | all judges flag; judge saw a bare fragment without the sentence it is inserted into (idiom जस का तस = 'as it was') |
| `calcEmptyState` | Enter a quantity to see the cost. | कीमत देखने के लिए मात्रा डालें। | ख़र्च देखने के लिए ग्राम में मात्रा डालें। | all judges flag; genuine wording choice, worth a native glance (ख़र्च vs कीमत for 'cost') |
| `ratioWatch` | watch | नज़र रखनी होगी | नज़र रखनी होगी | all judges flag; judge saw a bare fragment without the sentence it is inserted into |
| `calcRowGoldValue` | Gold value | सोने की कीमत | सोने की कीमत | all judges flag; judge prompt omitted the glossary's own exception (bill-line 'gold value' is सोने की कीमत, not भाव) |
| `bottomNavAriaLabel` | Page sections | पेज के सेक्शन | पेज के हिस्से | majority flags (2 of 3); judge saw a bare fragment without the sentence it is inserted into |
| `noChangeLabel` | no change | कोई बदलाव नहीं | कोई बदलाव नहीं | majority flags (2 of 3); judge saw a bare fragment without the sentence it is inserted into |
| `thDelta` | Change | बदलाव | बदलाव | majority flags (2 of 3); judge saw a bare fragment without the sentence it is inserted into (table column header) |
| `calcPresetCustom` | Custom | (none: showed English) | अपना | majority flags (2 of 3); judge saw a bare fragment without the sentence it is inserted into (a radio option: अपना = 'your own') |
| `rangeAll` | All | सभी | सारे | majority flags (2 of 3); judge saw a bare fragment without the sentence it is inserted into (chart range button) |
| `calcMakingModePct` | % of gold value | (none: showed English) | सोने की कीमत का % | majority flags (2 of 3); judge prompt omitted the glossary's own exception (bill-line 'gold value' is सोने की कीमत, not भाव) |
| `calcPresetCustomHint` | Know your jeweller's exact rate? Enter it below. | (none: showed English) | अपने ज्वेलर का मेकिंग चार्ज पता है? नीचे डालें। | majority flags (2 of 3); genuine wording choice, worth a native glance (English says 'exact rate'; the field is the making charge) |
| `thWhen` | When | कब | कब | majority flags (2 of 3); judge saw a bare fragment without the sentence it is inserted into (table column header) |
| `trendDirDown` | down | गिरावट | गिरावट | majority flags (2 of 3); judge saw a bare fragment without the sentence it is inserted into |
| `methRangeStrFallback` | the current range | मौजूदा रेंज | मौजूदा | majority flags (2 of 3); judge saw a bare fragment without the sentence it is inserted into (slots into 'हमारी रेंज (...) ...') |
| `ratioRetrainSub` | may need recalibration | दोबारा कैलिब्रेशन की ज़रूरत हो सकती है | इसे फिर से ठीक करने की ज़रूरत हो सकती है | majority flags (2 of 3); genuine wording choice, worth a native glance ('may need recalibration' has no everyday Hindi; chose इसे फिर से ठीक करना) |
| `karatToggleAriaLabel` | Gold purity | सोने की शुद्धता | सोने की शुद्धता | majority flags (2 of 3); genuine wording choice, worth a native glance (शुद्धता = purity) |
| `calcPresetIntricateRange` | 15–25% of gold value, typically 20% | (none: showed English) | सोने की कीमत का 15–25%, आम तौर पर 20% | majority flags (2 of 3); judge prompt omitted the glossary's own exception (bill-line 'gold value' is सोने की कीमत, not भाव) |
| `freshnessOkAria` | Updated {rel} | {rel} अपडेट हुआ | {rel} अपडेट हुआ | majority flags (2 of 3); judge saw a bare fragment without the sentence it is inserted into ({rel} is a relative time like '2 घंटे पहले') |
| `calcPresetPlainRange` | 8–12% of gold value, typically 10% | (none: showed English) | सोने की कीमत का 8–12%, आम तौर पर 10% | majority flags (2 of 3); judge prompt omitted the glossary's own exception (bill-line 'gold value' is सोने की कीमत, not भाव) |
| `calcPresetCoinsRange` | 3–8% of gold value, typically 5% | (none: showed English) | सोने की कीमत का 3–8%, आम तौर पर 5% | majority flags (2 of 3); judge prompt omitted the glossary's own exception (bill-line 'gold value' is सोने की कीमत, not भाव) |
| `calcAriaLabel` | Purchase cost calculator | ख़रीद लागत कैलकुलेटर | ख़रीद का ख़र्च निकालने वाला कैलकुलेटर | majority flags (2 of 3); genuine wording choice, worth a native glance (ख़र्च vs कीमत for 'cost') |
| `methAccuracyDrift` | Accuracy drift | सटीकता में बदलाव | सही रहने में बदलाव | majority flags (2 of 3); genuine wording choice, worth a native glance (technical-page label) |
| `driverPartRupeeTookOff` | a stronger rupee took off about ₹{inr} | मज़बूत रुपये ने करीब ₹{inr} घटाए | मज़बूत रुपये से करीब ₹{inr} घटे | majority flags (2 of 3); same as driverPartGoldTookOff |
| `driverPartGoldTookOff` | global gold prices took off about ₹{gold} | वैश्विक सोने की कीमतों ने करीब ₹{gold} घटाए | दुनिया के बाज़ार में सोने के भाव से करीब ₹{gold} घटे | majority flags (2 of 3); judge claimed the verb should be बढ़े; it is घटे on purpose ('took off' = reduced), so the flag is wrong |
| `methRangeSub` | Right {times} so far: ₹{low} – ₹{high} | अब तक लगभग 10 में से {n} बार सही: ₹{low} – ₹{high} | अब तक लगभग 10 में से {n} बार सही: ₹{low} – ₹{high} | majority flags (2 of 3); 'लगभग 10 में से N बार' is the fixed form used site-wide for this claim |
| `todaysReadEyebrow` | Today's read | आज का सार | आज का हाल | majority flags (2 of 3); genuine wording choice, worth a native glance ('Today's read' -> आज का हाल) |
| `pv2RangeOddsClause` |  A range like this has held {frac} in the past. |  ऐसा दायरा पहले {frac} सही साबित हुआ है। |  ऐसी रेंज पहले {frac} सही साबित हुई है। | majority flags (2 of 3); genuine wording choice, worth a native glance (flagged OFF page_v2 only) |
| `appTitle` | Gold Tracker | Gold Tracker | Gold Tracker | majority flags (2 of 3); brand name, intentionally unchanged (same in English) |
