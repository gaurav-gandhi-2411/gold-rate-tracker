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
| `index.html:525-528` (static pre-hydration footer fallback) | "We use IBJA's official gold benchmark and calibrate it to match real shop prices..." | calibrate; IBJA unexplained | "We use IBJA (the India Bullion and Jewellers Association's daily benchmark rate) and adjust it to match real shop prices..." |
| `i18n.js` `pageDescription` (EN) | same as index.html meta description | calibrated | same fix, kept in sync |
| `i18n.js` `footerBody` (EN) | "...calibrate it to match real shop prices..." + unexplained IBJA | calibrate; IBJA unexplained | "...adjust it to match real shop prices..." + IBJA explained inline (the one canonical explanation the rest of the page relies on) |
| `i18n.js` `firstVisitText` (EN+HI) | "...(worst case recently ~Xh; n=41, as of DATE)..." | n= | "...(worst case recently ~Xh, as of DATE)..." — n= clause dropped, hours/as-of kept |
| `i18n.js` `footerBody` (EN+HI) | "...(worst case recently ~Xh; n=41, as of DATE)" | n= | same fix as `firstVisitText` |
| `i18n.js` `volNoteElevated` (EN) | "Gold has been more volatile than usual lately — about ±₹X over 5 days." | volatility/volatile | "Gold has been swinging more than usual lately — about ±₹X over 5 days." |
| `i18n.js` `volNoteCalm` (EN) | "Gold has been calmer than usual lately..." | volatility/volatile (key concept, adjacent to the elevated/calm pair) | "Gold has been steadier than usual lately..." |
| `i18n.js` `reliabilityCoverage` (EN) | "Our estimated range has been right {pct}% of the time (checked {n} times)." | matches the `"coverage X% (n=Y)"` pattern | "Our estimated range has usually been right — about N times out of 10 so far." (floored, never overstates — see `fractionOutOf10Phrase`) |
| `i18n.js` `calibrationConfidenceAppend` (EN) | "...about {coverage}% of the time so far (n={n} weeks measured)." | n= | "...about N times out of 10 so far." (n dropped; exact % and n preserved on how-we-know.html's new "Band accuracy" section) |
| `i18n.js` `errCouldntLoadMethodology` (EN) | "Couldn't load model details — check your connection and reload." | model | "Couldn't load this section — check your connection and reload." |
| `i18n.js` `errCouldntLoadMethodology` (HI) | "मॉडल की जानकारी लोड नहीं हो पाई..." | model (मॉडल, loanword) + EN meaning changed | Removed, flagged HI-needs-native-review (falls back to reworded English) |
| `i18n.js` 37× `meth*` keys (EN+HI) | full methodology breakdown (verdict rule, next-day-range p-value, direction-signal internals, drift stats) | model, p-value, MAE, coverage, percentile-adjacent framing | Relocated verbatim to `how-we-know-strings.js` (its own exempt catalogue) — same wording, same numbers, just off the main page. Main page's accordion body now renders 2-3 short plain sentences (`accSummaryIntro`/`accSummaryDirectionOff`, reusing the already-plain `reliabilityDriftOnTrack/Watch/Retrain`) plus a link |
| `README.md` "How fresh the data is" bullet | "...typical gap between updates was 4.6 (n=41, as of DATE) hours." | n= | "...typical gap between updates was 4.6 (as of DATE) hours." — `\|n=n` modifier dropped from the METRIC marker |
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
