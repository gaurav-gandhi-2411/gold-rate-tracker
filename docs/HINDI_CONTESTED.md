# Hindi strings the judges contested (optional review list for GG)

## START HERE: the 8 strings that most need a native speaker (about 5 minutes)

Chosen by rule: contested by at least 2 of 3 judges AND shown on the main page or the calculator, ranked by
how many people see them, plus any string whose meaning changed between the old and new Hindi. Judge notes
are quoted from `reports/hindi_audit_2026-10/contested.json` (LLM judges, not native speakers; their
suggestions are marked "judge"). Options marked "author" are the PR author's, not a judge's. Pick A, B, C or
write your own; each row is a one-key change in `i18n.js` (`STRINGS.hi`).

| # | Key | Where it appears | English | A: old Hindi | B: new Hindi (in this PR) | C: alternative | Why it needs you |
|---|---|---|---|---|---|---|---|
| 1 | `todaysReadEyebrow` | Main page: small heading above the verdict card (visible to everyone, every visit) | Today's read | आज का सार | आज का हाल | no judge alternative: llama says "common usage", qwen says आज का हाल is "unnatural", gemma says "vague" | Visible on every visit. सार = summary, हाल = state of things; which one reads as the right label for a verdict? Flags: 3 of 3 (all say wording, none give a better word). |
| 2 | `driverPartGoldTookOff`, `driverPartRupeeTookOff` | Main page: the "what moved the price" sentence (when gold or the rupee pulled the price down) | global gold prices took off about ₹X / a stronger rupee took off about ₹X | वैश्विक सोने की कीमतों ने करीब ₹X घटाए / मज़बूत रुपये ने करीब ₹X घटाए | दुनिया के बाज़ार में सोने के भाव से करीब ₹X घटे / मज़बूत रुपये से करीब ₹X घटे | judge (qwen): कम instead of घटे | Meaning check, not just style. "took off" = subtracted, so घटे is intended (one judge wrongly wanted बढ़े). Does the new sentence read as "price fell by about ₹X because of ..."? Flags: 3 of 3. |
| 3 | `dirWordUnchanged` | Main page: the 7-day trend sentence when the price is flat | unchanged | जस की तस रही | जस का तस रहा | none from judges (all three: "too literal"); author: बदली नहीं | Changed form (की/रही to का/रहा). Which gender/tense agrees with the sentence it is inserted into? Flags: 3 of 3. |
| 4 | `calcPresetCustomHint` | Calculator: hint under the "Custom" making-charge option | Know your jeweller's exact rate? Enter it below. | (none: showed English) | अपने ज्वेलर का मेकिंग चार्ज पता है? नीचे डालें। | judge (gemma): add "gold rate"; judge (llama) says the field is not the gold rate. Author: अपने ज्वेलर की सही दर पता है? नीचे डालें। | Meaning changed: English says "exact rate", B says "making charge". The field is the making charge, so B may be more accurate, but is it what the English meant? Flags: 3 of 3. |
| 5 | `calcEmptyState` | Calculator: empty state before any quantity is typed | Enter a quantity to see the cost. | कीमत देखने के लिए मात्रा डालें। | ख़र्च देखने के लिए ग्राम में मात्रा डालें। | judge (qwen): कीमत or भाव instead of ख़र्च. Author: कीमत देखने के लिए ग्राम में मात्रा डालें। | ख़र्च vs कीमत for "cost", and whether ग्राम में is too formal (gemma). Flags: 3 of 3. |
| 6 | `calcRowGoldValue`, `calcMakingModePct`, `calcPresetPlainRange`, `calcPresetIntricateRange`, `calcPresetCoinsRange` | Calculator: the bill lines and the making-charge presets ("% of gold value") | Gold value / % of gold value / 8-12% of gold value ... | सोने की कीमत (calcRowGoldValue); the rest showed English | सोने की कीमत / सोने की कीमत का % / सोने की कीमत का 8–12% ... | judge (all three): सोने का भाव instead of कीमत | The glossary keeps भाव for the live price per gram and कीमत for the bill amount; judges were not told that exception and flagged it. Is कीमत the right word for a bill-line total? Flags: 2 to 3 of 3, five keys, one decision. |
| 7 | `calcPresetCustom` | Calculator: label of the "Custom" making-charge option (radio button) | Custom | (none: showed English) | अपना | judge (gemma): good; judge (qwen): "wrong word". Author: कस्टम, or अपनी पसंद | अपना = "your own". Does it read as a choice label next to the other presets? Flags: 2 of 3. |
| 8 | `rangeAll` | Main page: price chart range button (7d / 30d / 90d / All) | All | सभी | सारे | judge (gemma): good; others: "wrong word, no context". Author: पूरा | One-word button. सभी vs सारे vs पूरा for "all of history". Flags: 2 of 3. |

Not included in the top 8 on purpose: how-we-know-page-only strings (`methAssumeNoChange`, `methRangeSub`,
`methRangeStrFallback`, `methAccuracyDrift`, `ratioWatch`, `ratioRetrainSub`), aria-label-only strings (`trendDirUp/Down`, `bottomNavAriaLabel`,
`karatToggleAriaLabel`, `calcAriaLabel`, `freshnessOkAria`), column headers (`thWhen`, `thDelta`), `noChangeLabel`,
the flagged-off page_v2 string `pv2RangeOddsClause`, and `appTitle` (brand, unchanged). They are still listed below.

Hindi that is deliberately NOT in this PR (falls back to English until reviewed, per #2400): the
direction-signal sentences, the track-record heading and caption, `pv2ConfidenceNote`, `reliabilityCoverage`,
`reliabilityCoverageTested`, `calibrationConfidenceAppend`, `methBandAccuracyText`, `methDirectionOnSub` and the
three `methAccurateP2*` coverage strings, because their previous Hindi presented walk-forward re-runs ("so far",
"checked N times", "right N of the last M") as live calls. These need a reviewed Hindi wording that says
"in a test on past days" before they come back.

---

## Full list (everything the judges contested, in judge-flag order)

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
