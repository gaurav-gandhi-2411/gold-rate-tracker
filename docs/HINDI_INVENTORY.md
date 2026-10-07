# Hindi string inventory (2026-10-05)

Full row-per-string inventory: `reports/hindi_audit_2026-10/inventory.csv` (key, variant, file, screen,
section, kind, where it appears, English, Hindi before, Hindi after, character length before/after,
figure-placeholder flag, changed flag). Raw machine-readable copies: `inventory_before.json` (master
at the start of this pass) and `inventory_after.json` (this branch). Regenerate with
`node scripts/hindi_audit_extract.js <out.json>` (set `HINDI_AUDIT_ROOT` to a checkout of master for
the "before" copy) and then `python scripts/hindi_audit_csv.py`.

## Where Hindi lives in the repo

| Surface | File | Hindi strings | Notes |
|---|---|---|---|
| Main page (cards, banners, calculator, chart labels, freshness pill, page_v2) | `i18n.js` `STRINGS.hi` | 264 of 288 keys before, 288 of 288 after | Rendered through `t(key, params)`. The 24 keys without Hindi silently showed English. |
| how-we-know.html (technical page) | `how-we-know-strings.js` `STRINGS_HWK.hi` | 49 of 60 keys before, 60 of 60 after | Rendered through `tHwk()`. The 11 missing keys showed English. |
| Language toggle label | `index.html:144`, `how-we-know.html:73`, `app.js:3346,3361`, `how-we-know.js:333` | 1 string, "हिं" | Static label, not in a catalogue. Unchanged. |
| Weekday, date and time words | `app.js` via `Intl` (CLDR) | not catalogue strings | Produced by the browser, out of scope. |
| Notifications (ntfy: `ml/notifications.py`, `ml/notification_routing.py`, `ml/public_copy.py`, `worker-deadman/`, `scraper/`) | none | 0 | No Devanagari in any of these files. Notifications are English only today. |
| Static HTML text | `index.html`, `how-we-know.html` | 0 besides the toggle | Page text is injected from the catalogues. |
| Tests, scripts, docs | various | n/a | Contain Hindi only as assertions or word lists; not user-facing. |

Sweep scope (rule 85b): every file from `git ls-files` except `reports/`, `data/`, `archive/`,
`notebooks/` and binaries, matched against the Unicode range U+0900 to U+097F.

## Counts (VERIFIED by `scripts/hindi_audit_extract.js`, rows in `inventory.csv`)

- Catalogue keys with a Hindi entry: 313 of 348 before, 348 of 348 after (35 keys added).
- Rendered Hindi variants (a function-valued string is counted once per conditional branch that produces
  sensible text): 317 before, 353 after.
- Of the 353 after: 237 reworded, 80 identical to before (brand names, digit-only labels, and strings that
  already matched the glossary such as "कुल", "होम", "भाषा बदलें"), 36 new.
- Variants carrying a computed-figure placeholder (a rupee amount, percentage, day count, date or relative time): 118.
- By kind: 176 sentences, 72 short labels, 44 labels, 26 aria-labels, 23 headings, 12 banners/messages.
- Longest Hindi string: `methNextFixP`, 403 characters (how-we-know page).
