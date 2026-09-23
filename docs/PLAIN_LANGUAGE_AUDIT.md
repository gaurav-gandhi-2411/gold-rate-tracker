# Plain-Language Audit — Gold Rate Today

Audit date: 2026-09-23
Scope: every user-visible string in index.html, app.js, i18n.js (EN+HI), the
Python notification templates, and README.md's first screen (U1/U2/U3 spec).
Enforced going forward by `scripts/check_plain_language.py` (wired into
`.github/workflows/lint.yml`'s `lint` job) — see that script's own module
docstring for the exact scanning rules per file type.

## Method

1. Read every user-visible string in index.html, i18n.js (both languages, in
   full), app.js's rendered `.textContent`/`.innerHTML` assignments, and the
   five Python files the task named (`ml/notifications.py`,
   `ml/public_copy.py`, `ml/cadence_digest.py`, `ml/notification_routing.py`,
   `ml/inference.py`).
2. Flagged any occurrence of: Brier, calibration/calibrated, coverage,
   confidence interval/CI, n=, p=/p-value, model, baseline, regime, embargo,
   walk-forward, shadow, residual, percentile, conformal, ECE, MAE,
   volatility/σ, z-score, and unexplained acronyms.
3. Fixed every real violation in place; grep-verified against the full banned
   list before treating any surface as clean (rule 85b — a sweep is only as
   good as the shapes it checks for, so the automated checker re-runs the
   same list mechanically on every PR going forward, not just this one pass).
4. Built `scripts/check_plain_language.py` to keep it that way. Proof run
   (banned term injected into i18n.js, checker fails, term removed, checker
   passes again) is in the session's PR description / final report, not
   duplicated here.

## Summary — flagged strings by file and term

| File | Term(s) found | Count | Disposition |
|---|---|---|---|
| `index.html` | calibrated/calibration, IBJA (unexplained) | 4 occurrences (meta description ×3, static footer fallback ×1) | Fixed |
| `i18n.js` (EN) | calibrated/calibration, n=, volatility/volatile, model, IBJA (unexplained) | 9 strings (`pageDescription`, `footerBody`, `firstVisitText`, `calibrationConfidenceAppend`, `reliabilityCoverage`\*, `volNoteElevated/Calm/Normal/Fallback` ×4, `errCouldntLoadMethodology`) | Fixed |
| `i18n.js` (EN) | model, p-value, MAE, percentile-adjacent framing, coverage, n= | 37 keys (the full `meth*` methodology catalogue) | Moved to `how-we-know-strings.js` (exempt technical page), not deleted — same numbers, unrounded, still live |
| `i18n.js` (HI) | n=, calibrated/calibration (loanword), model | 6 strings (`firstVisitText`, `footerBody`\*\*, `reliabilityCoverage`, `calibrationConfidenceAppend`, `errCouldntLoadMethodology`) | 3 fixed directly (n= removed, mechanical subtraction); 3 removed + flagged **HI needs native review** (see below), falling back to the reworded English per the file's existing convention |
| `i18n.js` (HI) | (technical, unchanged) | 37 keys (`meth*`) | Moved verbatim to `how-we-know-strings.js`'s hi block — technical page, no plain-language constraint applies there |
| `app.js` | (none — all user text routes through i18n.js's `t()`) | 0 real violations | n/a — one CSS-class-name false positive (`outlook-volatility`) allowlisted in the checker |
| `README.md` (first screen) | n=, 95% CI | 2 bullets (`How fresh the data is`, `A likely range, not just one number`) | Fixed — new `frac10` metric format added to `scripts/inject_metrics.py` so the replacement stays computed, not hand-typed |
| `ml/public_copy.py` | none | 0 | Already compliant — has its own enforced plain-language contract (module docstring + `tests/test_notification_routing.py`) |
| `ml/notifications.py`, `ml/cadence_digest.py`, `ml/notification_routing.py`, `ml/inference.py` | many (calibration, CI, model, coverage, walk-forward, ...) | not counted | **Out of scope by design** — see "Notification files" below |

\* `reliabilityCoverage` itself contains no literal banned term but matches
the exact `"coverage 73% (n=63, 95% CI…)"` shape the task's own worked
example calls out; rewritten to the same floored-fraction phrasing as
`calibrationConfidenceAppend` for consistency.
\*\* `footerBody`'s Hindi text is left in place (not blanked to fall back to
English) — see "HI needs native review" below for why this one case differs
from the other three.

**Total: 6 real strings fixed directly outside the methodology catalogue,
2 README bullets fixed, 37+37 methodology keys relocated (not deleted), 4
HI strings flagged for native review.**

## Detailed findings and fixes

| File:line (original) | Original text | Flagged term(s) | Replacement |
|---|---|---|---|
| `index.html:55,59,65` (meta/og/twitter description) | "22K gold rate — an IBJA-calibrated estimate, confirmed against live Tanishq retail when reachable..." | calibrated | "22K gold rate — closely matched to real shop prices, confirmed against live Tanishq retail when reachable..." |
| `index.html:525-528` (static pre-hydration footer fallback) | "We use IBJA's official gold benchmark and calibrate it to match real shop prices..." | calibrate; IBJA unexplained | "We use IBJA (the India Bullion and Jewellers Association, which publishes an official gold price every working day) and adjust it to match real shop prices..." — U4 pass (01202f86): "daily benchmark rate" itself read as jargon; reworded to say plainly what IBJA actually does |
| `i18n.js` `pageDescription` (EN) | same as index.html meta description | calibrated | same fix, kept in sync |
| `i18n.js` `footerBody` (EN) | "...calibrate it to match real shop prices..." + unexplained IBJA | calibrate; IBJA unexplained | "...adjust it to match real shop prices..." + "IBJA (the India Bullion and Jewellers Association, which publishes an official gold price every working day)" — U4 pass (01202f86) reworded the IBJA gloss itself (see index.html row above, same fix) |
| `i18n.js` `firstVisitText` (EN+HI) | "22K gold retail price, checked about every Xh (worst case recently ~Xh; n=41, as of DATE)..." | n=; U4 pass: "retail price" (both models) | "The price of 22K gold in shops, checked about every Xh (worst case recently ~Xh, as of DATE)..." — n= dropped (2026-09-23 first pass), "retail price" → "price ... in shops" (U4 pass, 01202f86) |
| `i18n.js` `footerBody` (EN+HI) | "...(worst case recently ~Xh; n=41, as of DATE)" | n= | same fix as `firstVisitText` (n= dropped) |
| `i18n.js` `volNoteElevated` (EN) | "Gold has been more volatile than usual lately — about ±₹X over 5 days." | volatility/volatile | "Gold has been swinging more than usual lately — about ±₹X over 5 days." |
| `i18n.js` `volNoteCalm` (EN) | "Gold has been calmer than usual lately..." | volatility/volatile (key concept, adjacent to the elevated/calm pair) | "Gold has been steadier than usual lately..." |
| `i18n.js` `reliabilityCoverage` (EN) | "Our estimated range has been right {pct}% of the time (checked {n} times)." | matches the `"coverage X% (n=Y)"` pattern; U4 pass: "estimated range" (both models) | "The real price has stayed inside the range we show about N times out of 10 so far." — floored fraction (first pass), "estimated range" → "the range we show" (U4 pass, 01202f86) |
| `i18n.js` `calibrationConfidenceAppend` (EN) | "...about {coverage}% of the time so far (n={n} weeks measured)." | n= | "...about N times out of 10 so far." (n dropped; exact % and n preserved on how-we-know.html's new "Band accuracy" section). U4 pass: "estimate" was flagged by both models here too, but kept deliberately — see the U4 section below for why |
| `i18n.js` `errCouldntLoadMethodology` (EN) | "Couldn't load model details — check your connection and reload." | model; U4 pass: scored 1/5 by both models even after the U1 fix | "We couldn't show this part right now. Please check your internet and refresh the page." (U4 pass, 01202f86) |
| `i18n.js` `errCouldntLoadMethodology` (HI) | "मॉडल की जानकारी लोड नहीं हो पाई..." | model (मॉडल, loanword) + EN meaning changed | Removed, flagged HI-needs-native-review (falls back to reworded English) |
| `i18n.js` `accSummaryDirectionOff` (EN) | "We don't try to guess whether prices will rise or fall next — nothing we've tested beats simply assuming they'll stay about the same, so that's what we go with." | (honesty fix, not a banned term) | "We don't try to guess whether prices will rise or fall next — none of the methods we've tested could do it reliably, so we don't show a guess." — the original wording implied "assume no change" was itself a tested, winning method; the real baseline the direction gate compares against is "always guess up" (ADR 019), not "assume no change" (that's the separate price-range method). Same correction class as the two rows below: don't let a rounding/summarizing rewrite assert something the underlying data doesn't support. |
| `i18n.js` 37× `meth*` keys (EN+HI) | full methodology breakdown (verdict rule, next-day-range p-value, direction-signal internals, drift stats) | model, p-value, MAE, coverage, percentile-adjacent framing | Relocated verbatim to `how-we-know-strings.js` (its own exempt catalogue) — same wording, same numbers, just off the main page. Main page's accordion body now renders 2-3 short plain sentences (`accSummaryIntro`/`accSummaryDirectionOff`, reusing the already-plain `reliabilityDriftOnTrack/Watch/Retrain`) plus a link |
| `how-we-know-strings.js` `hwkIntro` (EN) | "This page has the full technical detail behind the plain-language summary on the main page — the same numbers, unrounded, plus how we test them." | U4 pass: "technical detail", "plain-language summary", "unrounded" (both models) | "This page shows the full detail behind the short summary on the main page — the exact numbers, and how we check them." (U4 pass, 01202f86) |
| `how-we-know-strings.js` `hwkError` (EN) | "Couldn't load this page's data. Check your connection and reload." | U4 pass: scored 1/5 by both models (same class as `errCouldntLoadMethodology`) | "We couldn't show these numbers right now. Please check your internet and refresh the page." (U4 pass, 01202f86) |
| `README.md` "How fresh the data is" bullet | "...the typical gap between updates was 4.6 (n=41, as of DATE) hours." | n=; U4 pass: "typical gap", "updates" (both models) | "...a new price usually came in every 4.6 (as of DATE) hours." — `\|n=n` modifier dropped (first pass), "typical gap between updates was" → "a new price usually came in every" (U4 pass, 01202f86) |
| `README.md` "A likely range, not just one number" bullet | "...it has actually contained the real rate 70.9% (n=86, 95% CI [60.6%, 79.5%], as of DATE) of the time..." | n=, CI | "...it has actually landed inside the range about 7 times out of 10 so far..." via the new `frac10` METRIC format, plus a link to How we know for the exact numbers |

## "HI needs native review" — not machine-translated

Per the task's instruction ("do not machine-translate new/changed meaning"),
four Hindi strings were **removed rather than hand-translated** when their
English counterpart's *shape* changed (raw percentage+n → a floored fraction
phrase, or methodology jargon → a generic plain sentence). `t()`'s existing
fallback (`STRINGS[currentLang]?.[key] ?? STRINGS.en[key]`) means Hindi
readers see the reworded English until a native speaker adds the Hindi
version — the same convention the file already used for the 2026-09
calculator strings (see i18n.js's own comment block near `calcPresetsLegend`).

- `reliabilityCoverage` (HI) — was `"हमारी अनुमानित रेंज अब तक {pct}% बार सही रही है ({n} बार जांची गई)।"`. Removed; needs a Hindi floored-fraction phrase matching `fractionOutOf10Phrase`'s English wording.
- `calibrationConfidenceAppend` (HI) — was `"...लगभग {coverage}% बार...（n={n} हफ़्तों का मापन）।"`. Removed; same reason.
- `errCouldntLoadMethodology` (HI) — was `"मॉडल की जानकारी लोड नहीं हो पाई..."`. Removed; both the loanword and the underlying English meaning changed.
- `accSummaryIntro` / `accSummaryDirectionOff` / `accSummaryLinkText` — brand new keys, no prior Hindi to preserve. No Hindi entry added.

One further case is flagged but **left as-is** rather than removed, because
only the *English* sentence gained new content — the existing Hindi sentence
is still an accurate (if less detailed) translation of the old English:

- `footerBody` (HI) — contains `"...कैलिब्रेट करते हैं..."`, a transliterated loanword for "calibrate", and does not yet have the inline IBJA gloss the English version now has. Left unchanged (still correct, just less detailed) rather than deleted (deleting would regress every Hindi visitor to full English footer text for a change that's additive, not a shape change). Needs a native-speaker pass to (a) replace the loanword and (b) add the IBJA gloss.

## Notification files — scope decision

The task named five Python files. Audited all five; only one needed a
plain-language contract:

- **`ml/public_copy.py`** — the only file that builds text for the PUBLIC
  ntfy topic (ordinary gold buyers). Already has its own enforced
  plain-language contract in its module docstring ("no jargon (no 'IBJA',
  'CI', 'run', 'workflow', 'model', 'calibration', 'trigger')") and a
  dedicated test (`tests/test_notification_routing.py`). Zero violations
  found. This is the only Python file `scripts/check_plain_language.py`
  scans.
- **`ml/notifications.py`** — verified by reading every `_make_alert(...)`
  call site: the six triggers that can reach the public topic (`T1`, `T2`,
  `T3`, `T4`, `T8_MORNING`, `T8_EVENING` — `ml/notification_routing.py`'s
  own `PUBLIC_ALLOWLIST`) build their title/body **exclusively** via
  `public_copy.weekly_trend()`/`price_move()`/`weekly_summary()`/
  `daily_digest()` — no hardcoded text of its own on that path. Every other
  trigger id (`T5`-`T13`) is routed to the OPS topic
  (`ml.notification_routing.KNOWN_OPS`), i.e. the project owner, not a
  general buyer — legitimately technical, out of scope.
- **`ml/cadence_digest.py`** — posts only to the OPS topic. Confirmed via
  `.github/workflows/weekly-backtest.yml`'s "Post weekly cadence digest"
  step: `NTFY_TOPIC` (the OPS secret), never `NTFY_TOPIC_PUBLIC`. Its own
  module docstring says as much ("weekly, non-paging summary... nothing GG
  can act on" — this is the owner's operational digest). Out of scope.
- **`ml/notification_routing.py`** — pure routing logic (which topic a
  trigger id goes to). No user-facing strings at all.
- **`ml/inference.py`** — checked for any long, sentence-shaped string
  literal (`grep -noE '"[A-Z][a-zA-Z0-9 ,.\x27-]{15,}"'`); found none. This
  file writes numeric fields to `forecast.json` that app.js/i18n.js format
  into plain language separately — it emits no text of its own.

## IBJA — handling policy

IBJA is **not** in `scripts/check_plain_language.py`'s automated banned-term
list. Whether an IBJA mention is a problem depends on context (has it been
explained nearby, or not?) — a fixed regex can't make that judgement, so this
stayed a manual audit item (U1: "explained once, or avoided"). Resolution:
`i18n.js`'s `footerBody` — the single most prominent explanatory sentence on
the page, present in the footer of every screen — now explains it once:
*"IBJA (the India Bullion and Jewellers Association's daily benchmark
rate)"*. Every other mention (`bannerIbjaToday`, `calcRateUsedIbja`, the
README's "What you'll see" bullet, etc.) is a short label that relies on that
one canonical explanation rather than repeating it — the same pattern the
README already used successfully before this audit (its own "What you'll
see" section explains IBJA inline once and never re-explains it).

## Other acronyms reviewed, accepted as-is

- **GST** (`calcRowGst: "GST ({pct}%)"`) — India's Goods and Services Tax, a
  term as widely understood by Indian consumers as "tax" itself (it appears
  on every retail bill in the country). Not reworded.
- **HUID** (`calcDisclaimer`: "...hallmarking (HUID) fees...") — the
  primary word ("hallmarking") is already plain; HUID is bracketed
  supplementary detail matching the exact term a buyer will see printed on
  their real jeweller's invoice, which is useful recognition value rather
  than jargon. Not reworded.

## Numbers — still computed, never hardcoded

Per U2, no plain-language rewrite hardcodes a number that used to be
computed. `fractionOutOf10Phrase(pct)` (i18n.js) and `frac10` (the new
`scripts/inject_metrics.py` format) both take the SAME live percentage the
old code read and convert it to a plain phrase at render/build time — the
underlying data source, and the floor-never-round-up policy, are unchanged.
Both independently implement the identical rounding rule (floor to the
nearest 10%, e.g. 78% → "7 times out of 10", never "8") since one runs in a
browser and the other in a docs-build script with no runtime in common; kept
in sync by policy (this document), not shared code.

## Follow-up correction (2026-09-23, same day) — plain wording can assert too much, not just too little

A second pass caught two ways the plain-language rewrite itself had drifted
from what the data actually supports — the opposite failure mode from
jargon, but still dishonest:

- `reliabilityCoverage` and `calibrationConfidenceAppend` said the range/band
  has "**usually** been right — about N times out of 10". Below a 6/10
  fraction, "usually" directly contradicts the number sitting right next to
  it (a range that's right 4 times out of 10 is not "usually" right). Fixed
  by dropping "usually" — the fraction is left to speak for itself,
  correct at every value from 0 to 10.
- `accSummaryDirectionOff` said "nothing we've tested beats **simply
  assuming they'll stay about the same**" — but the direction gate's actual
  comparison baseline is "always guess up" (ADR 019), not "assume no
  change" (that's the separate next-day-range method, a different part of
  the page). The plain summary had quietly swapped in the wrong baseline
  while simplifying. Fixed to say "none of the methods we've tested could do
  it reliably" — true regardless of which specific baseline is meant, and
  doesn't claim a comparison that wasn't actually made.

Screenshots `06-accordion-opened-375-after.png`, `08-model-signal-375-after.png`,
and `10-banner-mocked-375-after.png` were re-captured after this fix.

## U4 readability check (LLM-consensus, not user testing)

**Provenance note:** this section documents a rating pass run and relayed by
GG/the orchestrating session, not reproduced independently by this executor
session (no Ollama access in this environment, and no raw ratings artifact
— e.g. a per-string JSON/log — was committed to the repo alongside
`01202f86` for this session to inspect directly). The method and the
specific findings below are recorded exactly as reported. Anything not
explicitly reported (the individual numeric score for each of the 10
strings that were NOT flagged, for instance) is left unstated here rather
than invented — see the "What's not in this record" note at the end of
this section.

**Method:** every rewritten user-facing string from the U1/U2 pass was
rated blind (the model sees only the sentence, not which key it came from
or what it replaced) by two independently-run local model families via
Ollama — `gemma2:9b` and `qwen3:30b-a3b` — at `temperature=0, seed=42` for
determinism. A string is flagged if either model scores it ≤2/5 or labels
it "misleading", plus a separate signal: a word is logged as a **consensus
hard word** when both models name the identical word as hard-to-understand,
independent of the pass/fail score. The rating scale itself was calibrated
first against a GG-authored reference sentence (expected to pass) before
being run on the real strings, so a flag means the string reads harder than
that reference bar, not just "the model didn't love the wording."

**This is a readability proxy, not user testing** — two LLMs agreeing a
sentence is easy to read is evidence, not proof that an actual gold buyer
finds it so. Recorded as a check against the same two "cheap
resource-order" model families used elsewhere in this repo (rule: prefer
free/open models before paid ones), not as a replacement for real user
feedback.

### First pass — results as reported

16 rewritten strings rated. Flag agreement between the two models: 1.0
(Cohen's κ = 1.0 — perfect agreement on which strings to flag, this pass).

Reported outcomes, by string:

| Key | Result | Consensus hard word(s) | Action |
|---|---|---|---|
| `errCouldntLoadMethodology` (EN) | scored 1/5 by both models | — | Rewritten (01202f86) |
| `hwkError` (EN) | scored 1/5 by both models | — | Rewritten (01202f86) |
| `footerBody` (EN) | flagged | IBJA, "benchmark rate" | Rewritten (01202f86) |
| `firstVisitText` (EN) | flagged | "retail price" | Rewritten (01202f86) |
| `reliabilityCoverage` (EN) | flagged | "estimated range" | Rewritten (01202f86) |
| `calibrationConfidenceAppend` (EN) | flagged | "estimate" | **Kept as-is** — both models also flag "estimate" in GG's own reference sentence (the calibration baseline for the whole scale), so this flag reads as the scale's floor, not a real readability defect; "estimate" is also the one honest word for what the number actually is (not a guarantee) |
| README "How fresh the data is" bullet | flagged | "typical gap", "updates" | Rewritten (01202f86) |
| `hwkIntro` (EN) | flagged | "technical detail", "plain-language summary", "unrounded" | Rewritten (01202f86) |
| (remaining 8 of 16 strings) | not flagged | — | No change |

**What's not in this record:** the individual 1-5 score for each of the 8
non-flagged strings, and the exact score (vs. just "≤2 or flagged") for the
6 hard-word strings above, were not included in what was relayed to this
session — only the flag/no-flag outcome and, for the two error messages,
the specific "1/5" figure. The identities of the 8 non-flagged strings
were also not enumerated. If the full 16-row table (every string, both
raw scores, pass/fail) is needed for the record, it should be pulled from
the rating harness's own output artifact, not reconstructed here from a
partial summary.

### What was changed and why

`01202f86` rewrote the 7 strings the first pass flagged (6 consensus-hard-word
strings plus README's freshness bullet, which is the same string as one of
the 6 — see the updated "Detailed findings and fixes" table above for
old/new text per key). `calibrationConfidenceAppend`'s "estimate" was left
untouched — see the table entry above for why keeping a flagged word can be
the correct call rather than automatically rewriting away every flag.

### Second pass

<to be filled>

## Follow-up correction 2 (2026-09-23) — screenshots and this audit table

`06-accordion-opened-375-after.png`/`08-model-signal-375-after.png` were
already current (their underlying keys, `accSummaryDirectionOff`/
`reliabilityCoverage`, were touched by the earlier "usually" fix, not by
01202f86's wording, except `reliabilityCoverage`'s "estimated range" →
"the range we show" change, which DOES affect `08-model-signal-375-after.png`
again). `02-main-page-375-after.png`, `04-main-page-1280-after.png`,
`08-model-signal-375-after.png`, and `11-how-we-know-375.png` were
re-captured after `01202f86` (first-visit panel, footer, model-signal card,
and how-we-know.html's intro all changed visible text).
