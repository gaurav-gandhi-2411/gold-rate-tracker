# Hindi glossary (hi) for the Gold Tracker site

Status: DRAFT for GG review. Written 2026-10-05, before any string was rewritten, so every
Hindi string in `i18n.js` and `how-we-know-strings.js` is worded to this table. Not reviewed by
a native speaker; see `reports/hindi_audit_2026-10/summary_stats.json` and the PR body for the LLM-consensus evidence
(labelled "LLM consensus, not native review").

Register: everyday spoken Hindi, as a buyer talks to a jeweller or a family member. Common English
loanwords that people actually say (ट्रेंड, चार्ट, ऐप, रीफ़्रेश, ऑफ़लाइन) are kept. Textbook words
(वैश्विक, सूचीबद्ध, संकेतात्मक, सामान्य) are replaced where an everyday word exists.

Rules that apply everywhere:

1. One concept, one wording (table below). A string that needs a different word needs a glossary change first.
2. Numbers, rupee amounts, dates and `${...}` placeholders are never reworded or reordered into a different meaning.
3. Spelling: nukta is written consistently (ज़्यादा, ख़रीद, फ़र्क़, आख़िरी, ताज़ा, हफ़्ता, ज़रूरत). Danda `।` ends full sentences.
4. Neutral near retailer names (`scripts/check_retailer_language.py`): next to Tanishq/GRT/Malabar/Kalyan use only factual words (भाव, ज़्यादा, कम). Never महंगा, लूट, धोखा, सावधान.
5. Plain-language banned jargon (`scripts/check_plain_language.py`) stays out of the main page. मॉडल, कैलिब्रेशन, कवरेज are tolerated only on how-we-know.html (the designated technical page), and even there are avoided when an everyday word works.

## Core terms

| English term | Preferred Hindi | Rejected variants | Why |
|---|---|---|---|
| gold rate / price of gold today (per gram) | सोने का भाव | सोने की कीमत, स्वर्ण मूल्य, सोने की दर | A jewellery buyer says "भाव" for the day's rate. कीमत is kept only for what you pay in total or a non-rate amount. |
| price (what you pay; generic) | कीमत / दाम | मूल्य | मूल्य is textbook. |
| rate (published by a body, e.g. IBJA) | भाव (e.g. "IBJA का रोज़ का भाव") | आधिकारिक दर, मूल्य | Keeps one word (भाव) for the thing a buyer compares. |
| India's official daily gold rate (IBJA) | भारत का आधिकारिक रोज़ का भाव | राष्ट्रीय दैनिक स्वर्ण दर | आधिकारिक is the one formal word kept: there is no everyday word for "official" that is not misleading. |
| listed rate (Tanishq's website rate) | Tanishq की साइट पर दिया भाव | सूचीबद्ध दर, सूचीबद्ध भाव | सूचीबद्ध ("listed on an exchange") reads as stock-market Hindi. |
| estimate (noun) / estimated | अनुमान / अनुमानित | अंदाज़ा (for the noun), आकलन, प्राक्कलन | One word for "our estimate" everywhere. अंदाज़ा is reserved for "guessing the future" (see below). |
| rough indication ("treat as indicative") | इसे मोटा अंदाज़ा ही समझें | संकेत के तौर पर लें, संकेतात्मक | "Indicative" has no everyday Hindi; the spoken phrase is "मोटा अंदाज़ा". |
| to guess / forecast ("we don't guess") | अंदाज़ा लगाना | पूर्वानुमान, भविष्यवाणी | Spoken; पूर्वानुमान sounds like a weather bulletin and overstates. |
| range (price band) | रेंज | दायरा, सीमा, परास | Everyone says "रेंज" for a price range. दायरा is kept only where it means "scope". |
| making charge | मेकिंग चार्ज | निर्माण शुल्क, बनवाई | The exact term on every jeweller's bill. |
| GST | GST | जीएसटी (Latin kept in labels) | Same as the bill. |
| HUID / hallmarking | HUID / हॉलमार्किंग | | As on the hallmark. |
| wastage | वेस्टेज | क्षय, घटत | The term jewellers use. |
| stones | नग | पत्थर, रत्न | "नग" is what jewellers call set stones. |
| gold value (in the cost breakup) | सोने की कीमत | स्वर्ण मूल्य | A bill line, so कीमत (not भाव). |
| 22K / 24K / 18K | 22 कैरेट / 24 कैरेट / 18 कैरेट | 22K, 22 KT, 22 कैरट | People say "बाईस कैरेट". Written with the number and कैरेट everywhere in Hindi. |
| purity | शुद्धता | | |
| per gram | प्रति ग्राम (₹/ग्राम in compact labels) | हर ग्राम (in labels) | Rate cards read "प्रति ग्राम". |
| jeweller | ज्वेलर | जौहरी, स्वर्णकार | ज्वेलर is what buyers say today; जौहरी is older/formal. |
| shop | दुकान | स्टोर, प्रतिष्ठान | |
| other jewellers' rates | दूसरे ज्वेलर्स के भाव | अन्य जौहरियों की दरें | |
| today / yesterday | आज / कल | | कल means yesterday and tomorrow; yesterday strings add "बीता कल" only if ambiguous (see `whenYesterday`). |
| this week / this month | इस हफ़्ते / इस महीने | इस सप्ताह, इस माह | |
| cheaper / pricier | सस्ता / महंगा | किफ़ायती, मूल्यवान | महंगा is fine in general copy; never next to a retailer name (rule 4). |
| usual / normal | आम (आम भाव, आम तौर पर) | सामान्य | आम is the spoken word. |
| low / high (price) | कम / ज़्यादा | न्यूनतम / अधिकतम (except in the fixed "सबसे कम भाव") | |
| lowest price in 3 months | 3 महीने का सबसे कम भाव | 3-महीने का न्यूनतम | |
| reading (a recorded price) | दर्ज किया भाव / भाव | रीडिंग | "रीडिंग" is meter-reading language; the site records a price. |
| update / refresh | अपडेट / रीफ़्रेश | | Both are everyday loanwords. |
| trend | ट्रेंड | रुझान, प्रवृत्ति | |
| chart | चार्ट | आलेख | |
| history (nav / section) | पुराने भाव | इतिहास | Says what the section holds. |
| range toggle "All" | सारे | सभी | |
| confidence ("how sure are we") | भरोसा | विश्वास-स्तर | |
| accurate / accuracy | सही / कितना सही | सटीक, सटीकता | सटीक is textbook; "सही" is what people say. |
| track record | अब तक का रिकॉर्ड | ट्रैक रिकॉर्ड, पिछला प्रदर्शन | |
| rise / fall | बढ़ना / घटना; nouns बढ़त / गिरावट | उछाल, वृद्धि | |
| up or down | ऊपर या नीचे | | |
| global gold price | दुनिया के बाज़ार में सोने का भाव | वैश्विक सोने की कीमतें | वैश्विक is textbook. |
| rupee weaker / stronger | रुपया कमज़ोर / मज़बूत | | |
| imported gold | बाहर से आया सोना | आयातित सोना | आयात is understood; the longer phrase is what is said. |
| festival demand | त्योहारों की मांग | | |
| not financial advice | यह निवेश की सलाह नहीं है | वित्तीय सलाह | |
| AI model (how-we-know page only) | AI मॉडल | | Technical page only. |
| test runs / test windows (how-we-know only) | टेस्ट | | |
| average | औसत | | |
| sample (small sample) | कम नमूने | सैंपल | how-we-know page only. |
| Share / Refresh / Dismiss | शेयर करें / रीफ़्रेश करें / बंद करें | | |
| Home Screen (iOS) | होम स्क्रीन | मुख्य स्क्रीन | The iPhone setting is named exactly this. UI words the user must find on screen (Share, Add to Home Screen) stay in English as the phone shows them. |
| offline | ऑफ़लाइन | | |
| next trading day | अगला कारोबारी दिन | अगला ट्रेडिंग दिवस | |

## Context rules

- Buttons and labels: shortest natural form, no full stop, no sentence verb ("शेयर करें", "बंद करें").
- Sentences: full sentence with danda, spoken order (subject, object, verb), no literal English word order.
- Notifications: none exist in Hindi today (see `docs/HINDI_INVENTORY.md`); if added they follow the sentence rule and the same glossary.
- Aria labels: a short noun phrase that reads well aloud by a screen reader.
