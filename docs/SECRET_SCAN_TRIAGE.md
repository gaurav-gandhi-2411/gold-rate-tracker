# Secret scan triage

Triage of a full-history gitleaks scan of this public repo (2026-11-14). The matched values are
never written down here: only file, commit, rule, line, length and character class.

## Method

- Tool: gitleaks v8.30.1, windows x64 (`gitleaks_8.30.1_windows_x64.zip`), downloaded from
  `https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/` and checked against the
  release's `gitleaks_8.30.1_checksums.txt`. SHA-256 of the zip:
  `d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e` (matched the checksums file).
- Command: `gitleaks git --no-banner --redact --report-format json --report-path <out> --log-opts="--all" <repo>`.
  No `.gitleaks.toml` existed on master before this change, so the first run used the built-in
  default rules only (no config could hide a finding).
- Result: 4243 commits scanned, 113 findings, 4 distinct (file, rule) pairs, all rule
  `generic-api-key`.
- Each finding was classified by reading the matched span from `git show <commit>:<file>` in memory
  and computing features only (length, charset, digest shapes, provider prefixes, whether the value
  appears in the master tree and in which files). No finding had a provider prefix, a digest shape,
  a private key block, a URL with credentials or a UUID shape.

## Verdicts

| Verdict | Findings |
| --- | --- |
| FIXTURE | 0 |
| DIGEST | 0 |
| PUBLIC_ID | 113 |
| UNCLEAR | 0 |
| POSSIBLE_CREDENTIAL | 0 |

| File | Rule | Findings |
| --- | --- | --- |
| `reports/hindi_audit_2026-10/inventory_after.json` | generic-api-key | 30 |
| `reports/hindi_audit_2026-10/inventory_before.json` | generic-api-key | 30 |
| `reports/hindi_audit_2026-10/items.json` | generic-api-key | 52 |
| `scripts/analysis_tanishq_update_times.py` | generic-api-key | 1 |

### Hindi audit i18n keys

The three `reports/hindi_audit_2026-10/` files are audit inventories. The flagged text is the value of
a JSON field named `key`, whose generic-api-key keyword match fired on the field name. The values are
13 to 27 characters of camelCase plus digits and are i18n string keys: every value also occurs in the
served app source (`app.js`, `i18n.js`, `index.html`, `how-we-know*.js`), which is public by design.

### Tanishq analysis label

One finding in `scripts/analysis_tanishq_update_times.py`: a literal assigned to a variable named
`rec_key`. The literal is 18 characters in five underscore-separated segments (lengths 1, 3, 2, 6, 2),
four of which are words used elsewhere in the same script, and it appears both as a key and as a
value in `reports/tanishq_update_times/schedule_evaluation.json`. It is a label, not random.

## Findings (no values)

| File | Commit | Rule | Line | Length | Class | Verdict | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `reports/hindi_audit_2026-10/inventory_after.json` | 912fcb5096 | generic-api-key | 679 | 13 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | 912fcb5096 | generic-api-key | 7579 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | 912fcb5096 | generic-api-key | 7695 | 19 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | 912fcb5096 | generic-api-key | 7724 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | 912fcb5096 | generic-api-key | 7811 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | 912fcb5096 | generic-api-key | 7956 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | 912fcb5096 | generic-api-key | 7985 | 17 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | 912fcb5096 | generic-api-key | 8014 | 17 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | 912fcb5096 | generic-api-key | 8159 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | 912fcb5096 | generic-api-key | 8333 | 19 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | 912fcb5096 | generic-api-key | 9473 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | 912fcb5096 | generic-api-key | 9531 | 27 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | 912fcb5096 | generic-api-key | 9589 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | 912fcb5096 | generic-api-key | 9705 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | 912fcb5096 | generic-api-key | 9763 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | e3039dfdc4 | generic-api-key | 679 | 13 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | e3039dfdc4 | generic-api-key | 7579 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | e3039dfdc4 | generic-api-key | 7695 | 19 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | e3039dfdc4 | generic-api-key | 7724 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | e3039dfdc4 | generic-api-key | 7811 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | e3039dfdc4 | generic-api-key | 7956 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | e3039dfdc4 | generic-api-key | 7985 | 17 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | e3039dfdc4 | generic-api-key | 8014 | 17 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | e3039dfdc4 | generic-api-key | 8159 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | e3039dfdc4 | generic-api-key | 8333 | 19 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | e3039dfdc4 | generic-api-key | 9473 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | e3039dfdc4 | generic-api-key | 9531 | 27 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | e3039dfdc4 | generic-api-key | 9589 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | e3039dfdc4 | generic-api-key | 9705 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_after.json` | e3039dfdc4 | generic-api-key | 9763 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | 912fcb5096 | generic-api-key | 679 | 13 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | 912fcb5096 | generic-api-key | 7406 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | 912fcb5096 | generic-api-key | 7522 | 19 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | 912fcb5096 | generic-api-key | 7551 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | 912fcb5096 | generic-api-key | 7638 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | 912fcb5096 | generic-api-key | 7783 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | 912fcb5096 | generic-api-key | 7812 | 17 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | 912fcb5096 | generic-api-key | 7841 | 17 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | 912fcb5096 | generic-api-key | 7986 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | 912fcb5096 | generic-api-key | 8160 | 19 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | 912fcb5096 | generic-api-key | 9244 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | 912fcb5096 | generic-api-key | 9302 | 27 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | 912fcb5096 | generic-api-key | 9360 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | 912fcb5096 | generic-api-key | 9476 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | 912fcb5096 | generic-api-key | 9534 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | e3039dfdc4 | generic-api-key | 679 | 13 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | e3039dfdc4 | generic-api-key | 7406 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | e3039dfdc4 | generic-api-key | 7522 | 19 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | e3039dfdc4 | generic-api-key | 7551 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | e3039dfdc4 | generic-api-key | 7638 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | e3039dfdc4 | generic-api-key | 7783 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | e3039dfdc4 | generic-api-key | 7812 | 17 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | e3039dfdc4 | generic-api-key | 7841 | 17 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | e3039dfdc4 | generic-api-key | 7986 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | e3039dfdc4 | generic-api-key | 8160 | 19 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | e3039dfdc4 | generic-api-key | 9244 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | e3039dfdc4 | generic-api-key | 9302 | 27 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | e3039dfdc4 | generic-api-key | 9360 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | e3039dfdc4 | generic-api-key | 9476 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/inventory_before.json` | e3039dfdc4 | generic-api-key | 9534 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 13 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 103 | 19 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 133 | 17 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 313 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 433 | 19 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 723 | 17 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 783 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 833 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 953 | 27 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 1093 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 1163 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 1173 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 1333 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 1443 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 1633 | 13 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 2483 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 2583 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 3023 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 3213 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 3323 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 3613 | 13 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 3903 | 19 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 4123 | 19 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 5053 | 27 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 5623 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | 912fcb5096 | generic-api-key | 5823 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 13 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 103 | 19 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 133 | 17 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 313 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 433 | 19 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 723 | 17 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 783 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 833 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 953 | 27 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 1093 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 1163 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 1173 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 1333 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 1443 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 1633 | 13 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 2483 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 2583 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 3023 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 3213 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 3323 | 14 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 3613 | 13 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 3903 | 19 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 4123 | 19 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 5053 | 27 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 5623 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `reports/hindi_audit_2026-10/items.json` | e3039dfdc4 | generic-api-key | 5823 | 20 | alnum | PUBLIC_ID | i18n string key, also present in app.js/i18n.js/index.html |
| `scripts/analysis_tanishq_update_times.py` | bb38f8d5fa | generic-api-key | 791 | 18 | alnum+underscore | PUBLIC_ID | structured schedule label (5 underscore segments), also a key in the committed report |

## Allowlist rationale

`.gitleaks.toml` extends the default rules and adds two `[[allowlists]]` entries, each scoped to a path
regex plus `targetRules = ["generic-api-key"]`. No value, and no pattern of a value, is in the file.
Scoping by path+rule keeps every other rule active on those files and every rule active everywhere
else. `tests/test_secret_scan_allowlist.py` rejects an entry with no description, a bare `.*` path, an
entry that looks like a secret value, or an entry without paths/commits and a rule. With the config,
the same full-history scan reports 0 findings over the same 4243 commits.

## Limits

- Only refs present in this clone were scanned (89 local branches, 160 remote-tracking refs, a stash and 3
  tmp refs at scan time). Pull-request refs (`refs/pull/*`) and forks were not scanned.
- Binary files are not scanned (text scan). Before ADR 060 the raw-data parquet files were committed
  in plaintext; they hold market rates, not credentials, and nothing here reads inside them.
- Commits not scanned, accounted for (re-measured 2026-11-14 on a clone that had grown to 5357
  non-merge commits; gitleaks then reported 4261 scanned). gitleaks reads `git log -p -U0` and scans
  ADDED lines only, so a merge commit (no patch), a binary-only commit and a deletion-only commit give it
  nothing to read (its own debug note: "smaller than expected due to commits with no additions"). Count
  of the 5357 non-merge commits (script `no_addition_commits.py`, hunks and added lines parsed from the
  patch stream): 4258 have at least one added line, 3 are empty commits (gitleaks counts them as
  scanned: 4258 + 3 = 4261), 1085 change only binary files (encrypted `*.enc` and parquet: ciphertext by
  design, and a secret cannot be read from a binary blob by a text scan), and **11 are deletion-only**
  (text hunks, zero added lines, so they cannot introduce a secret): `14a6ac8da9`, `a1df67bfc1`
  (2026-10-30), `d690af26c5` (2026-10-08), `d4bdae4cb0`, `90d0c3b67f` (2026-10-07), `89ffa2cc47`
  (2026-10-05), `b7307886fa`, `6c72cfec36` (2026-09-26), `f8658c9a06` (2026-09-25), `3c03c8fb9f`
  (2026-05-14), `1b15472b15` (2026-05-11). 4261 + 1085 + 11 = 5357. The earlier "9 unaccounted" (4252
  text-change commits against 4243 scanned, 2026-11-14) was these deletion-only commits, counted as text
  changes by `git log --numstat` but with nothing added. The 372 merge commits carry no patch of their
  own (`git rev-list --all --merges --count`).
- This is a public repo: anything that was ever pushed is already published, and rewriting history
  does not un-publish it. A real credential would need rotation, not deletion.
- `--redact` was used, so the report holds no values; classification re-read the values in memory.

## Re-run

```
gitleaks git --no-banner --redact --log-opts="--all" --config .gitleaks.toml .
```

Expected: `no leaks found`. To check the allowlist is not hiding anything, run it without `--config`
and compare with the table above (gitleaks then finds 113 in the files listed).
