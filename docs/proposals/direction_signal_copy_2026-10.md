# Proposed wording: the up/down signal (item 2i). READY for GG, nothing applied

**Status:** prepared 2026-10-05, not applied. User-facing text: GG decides.
**Target:** `i18n.js` keys `directionUp`, `directionDown`, `directionUnclear`, `directionTrackRecord`
(EN lines 275-278, HI lines 664-667), rendered by `app.js` around line 1740.

## What is wrong with today's wording (VERIFIED by reading `i18n.js` and `app.js`)

1. "More likely to go up than down **next**": next what? The model forecasts the next **official
   (IBJA) rate**, not the shop price and not gold itself. A reader will assume the shop price.
2. No reason is given, so the signal reads as a market call. The reason it can be right is narrow
   and checkable: India's official rate is set at midday and follows world gold and the rupee
   with a delay (ADR 064: correlation 0.364 between the next fix's move and the world move over the
   fix day, 203 day pairs).
3. "Our call has been right 92 of the last 143 times": those 143 are a walk-forward re-run on past
   days (`nextfix_oos.json`), not calls made live. Live calls began 2026-10-01 and none had resolved
   at the time of writing. "Has been right" implies live results.
4. The model is about 64% right, with an honest interval of 57-71% (`forecast.json`
   `next_fix.track_record.direction_accuracy_ci95`, n 143). The page should not make 64% sound like
   certainty.

## Proposed English

| Key | Text |
|---|---|
| `directionUp` | `World gold prices have risen since India's last official rate was set, so the next official rate is more likely to be <strong>higher</strong> (about ${pct}% chance).` |
| `directionDown` | `World gold prices have fallen since India's last official rate was set, so the next official rate is more likely to be <strong>lower</strong> (about ${pct}% chance).` |
| `directionUnclear` | `World gold prices have not moved enough since India's last official rate to say whether the next one will be higher or lower.` |
| `directionTrackRecord` (back-test) | `Tested on ${n} past days, this call was right ${right} times. These are past results, not live ones.` |
| `directionTrackRecordLive` (new; shown only once live n >= 30) | `Since 1 October, ${right} of ${n} live calls were right.` |
| `directionWhy` (new, one line under the signal, or in "how we know") | `India's official gold rate follows world gold prices and the rupee with a delay, so the overnight move already tells us part of tomorrow's rate. We do not predict gold prices themselves.` |

Notes:
- "up" vs "has risen" must follow the model's own side, which is the sign of the world move after
  the fix. If the model's probability disagrees with the sign of the world move on a given day,
  the sentence would be false; so `directionUp`/`directionDown` should be chosen from the model's
  side only when it also matches the sign of the move, else use `directionUnclear`. This needs a
  `world_move_since_fix` field in `forecast.json` `next_fix` (not present today).
  A weaker alternative that needs no new field: drop "have risen / fallen" and keep
  "World gold prices have moved since India's last official rate was set, so ...".
- Banned-jargon and retailer-language checks (`scripts/check_plain_language.py`,
  `scripts/check_retailer_language.py`) must be run when applied; not run here. UNVERIFIED.
- Hindi: do not hand-translate here. Strings follow `docs/HINDI_GLOSSARY.md` (item 3, separate PR).
- The 14-day rule for claims: the back-test line is a fixed historical count, refreshed every run;
  hide it if `track_record` is older than 14 days.

## What this does and does not change

- Changes: wording only (plus an optional new field for the sign check). No model, gate or
  threshold changes.
- A UI-path PR needs before/after screenshots (rule 15c): produce them when applied.
