# ADR 060: Commit raw third-party data only as ciphertext

**Status:** Proposed, 2026-09-25. Records GG decision **E1**: encrypt the data rather than create a new repository. Pending GG review of this PR and of the migration PR it enables.

**Related:**
- ADR 059: retailer data risk and the IBJA terms findings. Its option "private repo (G3)" is replaced by encryption.
- ADR 054 / PR #2038: no raw third-party market series. This ADR supersedes two parts of #2038, listed under "Relation to #2038".
- ADR 044 / ADR 052: the frozen Yahoo snapshots.
- #2053: takedown switch and IBJA-derived history.
- #2069: E2 hero.
- #2037: page_v2, flagged off.

## Context

This repository is public. It commits raw third-party data that it has no right to republish:
- IBJA's full rate history. IBJA's API terms forbid publishing its rates or its history without written permission (ADR 059).
- Retailer rates from GRT, Malabar, Kalyan and Tanishq. Their terms prohibit scraping or public reproduction (ADR 059).
- Yahoo Finance series. Yahoo's terms do not cover redistribution (#2038).

The pipeline still needs the raw data to run: calibration, fusion, the shadows, and the research reruns.

GG added a repository secret, `DATA_ENC_KEY`. Only CI can read it.

## Decision

Every file in `REGISTRY` (`scripts/data_crypt.py`) is committed only as ciphertext:
- `data/encrypted/<path>.enc` holds the ciphertext.
- `data/encrypted/<path>.sha256.json` is the public manifest entry: plaintext SHA-256 and size, ciphertext SHA-256, and key id.

The plaintext paths are gitignored. CI decrypts into the runner workspace, runs the pipeline, and re-encrypts before commit.

Public files keep only derived numbers. A number counts as derived only if it cannot be inverted back to a raw price using public data (ADR 059). For example, a markup over the public IBJA rate is raw.

### Format and cryptography (`scripts/data_crypt.py`)

- **Cipher:** AES-256-GCM, from `cryptography`. It is already a pinned dependency: `cryptography>=50.0.1` in `ml/requirements.txt`, and `==50.0.1` in `ml/requirements-inference.lock`. No dependency was added. `shadow-fusion.yml` installs its own minimal set, so the same pin was added there.
- **File layout:** `b"GRTENC" | version 0x01 | uint32 header length | header JSON | ciphertext+tag`.
- **Header contents:** the logical path, schema version, algorithm, KDF parameters, a random 16-byte salt, a random 96-bit nonce and the key id.
- **Associated data:** the whole prefix, from the magic bytes through the header. The path and schema version are therefore authenticated: a ciphertext copied to another path, or any edited header byte, fails authentication.
- **Key derivation:** `scrypt(DATA_ENC_KEY, salt, n=2^15, r=8, p=1)`, with a fresh salt for every encryption. scrypt was chosen over HKDF because this code cannot know how much entropy the secret has.
- **Key id:** the first 8 bytes of `scrypt(DATA_ENC_KEY, fixed salt)`. It tells "wrong key" apart from "tampered file" and names the key during rotation. Attacking a key through its id costs the same scrypt call as attacking any file.
- **Fail-closed checks:**
  - Decryption checks the ciphertext hash against the manifest, then the header path, then the key id, then the GCM tag, then the plaintext SHA-256 against the manifest.
  - Plaintext is written to a temporary file and moved into place only after every check passes. A failure therefore leaves no partial plaintext and never overwrites an existing file.
  - `encrypt` refuses to replace an existing ciphertext unless this workspace decrypted that path first. This guards against a pipeline step that runs after a failed decrypt, rebuilds a file from nothing (for example today's IBJA row alone), and would otherwise wipe the encrypted history.
  - Unchanged plaintext is not re-encrypted, so a bot PR does not carry a new ciphertext on every run.
  - A frozen snapshot must match its pinned SHA-256 on every encrypt and decrypt.
- **The key never leaves the environment variable:**
  - The script has no command-line option for it.
  - No message prints it. Unexpected errors are printed without a traceback, with every key form redacted.
  - CI masks the key and its base64 and hex forms (`mask-key`) before any other step.
  - Every producer runs `scan-key --changed` before it commits, which fails the commit if the key in any of those forms is in any file about to be committed.
  - `tests/test_data_crypt.py` runs every subcommand with a known test key and asserts the key appears in no output and no written file.

### Inventory (sweep scope, rule 85b)

What was swept:
- every tracked file under `data/`, `reports/` and `archive/`;
- JSON/JSONL numbers in the per-gram band (9,000–20,000) and the per-10 g band (60,000–200,000), under any key;
- every parquet column;
- the `*_URL` constants and `fetch()` calls in `app.js` and `how-we-know.js` on master and on `feat/page-v2-flagged`, plus `og.html`, `service-worker.js` and `_config.yml`;
- the data files that open PRs add under `data/` and `reports/`.

| File | Raw? | Read by the live app? | Action |
|---|---|---|---|
| `reports/vol_regime_prereg_gcf_2000_2012.csv` | yes: Yahoo GC=F closes | no | **Encrypted.** Frozen at `2db071aa…`. This equals the blob at `1c4a372e` and ADR 044's record. |
| `reports/vol_regime_prereg_gld_2004_2012.csv` | yes: Yahoo GLD closes | no | **Encrypted.** Frozen at `9aa4143f…`. |
| `data/ibja_rates.parquet` | yes: IBJA, every purity, AM/PM, 268 days | no (excluded from Pages) | **Encrypted.** Written by `check-price` and `monthly-ibja-backfill`. |
| `data/fusion_snapshots.parquet` | yes: GRT/Malabar/Kalyan, 1,827 rows | no | **Encrypted.** |
| `data/shadow_fusion_output.json` | yes: Kalyan values, and markups that invert to raw prices | no | **Encrypted.** |
| `data/feature_store/snapshots.parquet` | yes: `tanishq_22k`, `ibja_*`, Yahoo levels | no | **Encrypted.** Supersedes #2038's z-score rewrite. |
| `data/next_day_range_shadow.json` | yes: `current_22k`/`actual_next_22k` (Tanishq) | page_v2 reads it, but only top-level `lo`/`hi`, which this file never has (finding: that reader is dead against the real schema) | **Encrypted.** No visible change, even with page_v2 on. |
| `data/wait_or_buy_shadow.json` | yes: IBJA `price_t`/`price_target` per entry | no | **Encrypted.** |
| `data/nowcast_shadow_log.json` (created by the first weekly run) | yes: `truth_rs_per_g` (Tanishq) | no | **Encrypted from creation.** |
| `reports/derived_premium.json` | yes: IBJA `pm_999` (216 values) plus Yahoo COMEX/USD-INR | no | **Encrypted.** Supersedes #2038's column drop, which left `pm_999` in. |
| `data/prices.json` | yes: 713 Tanishq readings | **yes** (chart, history, cards, og.png, dead-man Worker) | **STOP for GG.** See "Live-read files". |
| `data/backtest.json`, `drift_metrics.json`, `metrics_history.json`, `commentary.json` | yes: Tanishq series and values | **yes** | Follow the `prices.json` decision. |
| `data/forecast.json` | one latest Tanishq value on tier 1 | **yes** | Keep. This is E2's "current price". |
| `data/wait_or_buy_today.json` | yes: IBJA `price_t` and IBJA-scale range per horizon | page_v2 only (flagged off) | **STOP (page_v2).** Plan below. |
| `data/weekly_range_shadow_log.json` (created by the first weekly run) | yes: `ibja_pm_916`, `path_low`/`path_high` | page_v2 only (flagged off) | **STOP (page_v2).** Plan below. Left unregistered so page_v2 is not silently broken. **Its first run (Sunday 2026-09-27) will publish raw IBJA rows unless GG decides first.** |
| `archive/history_seed_v1_uniform_premium.json`, `data/history_seed_inr22k_*.parquet` | derived (products of Yahoo series × constants, #2038's reasoning) | no | Keep. |
| `data/chronos_probe.json`, `calibration*.json`, `coverage/cadence` metrics, `preregistered_h2_shadow_results.json`, other `reports/*` | derived (forecasts, fitted constants, aggregates) | some | Keep. |

**Data files added by open PRs, not on master.** Each must adopt `data_crypt.py`, via a `REGISTRY` entry plus the workflow encrypt step, before it merges:
- `data/markup_today.json` (#2022/#2061): `markup_pct` inverts to the retailer price, and page_v2 reads it.
- `reports/markup_reversion/shadow.json` (#2061): `markup_pct`/`markup_rs`.
- `reports/fhs_ranges/shadow.json` (#2052): `current_22k`/`actual_next_22k`.
- `data/stale_day_shadow.json` (#2024): not yet checked row by row; treat it as raw until shown otherwise.

### CI wiring

- **Decrypting.** `.github/actions/decrypt-data` masks the key and runs `decrypt --all`. These jobs use it:
  - `check-price.yml`
  - `weekly-backtest.yml`
  - `shadow-fusion.yml`
  - `monthly-ibja-backfill.yml`
  - `analysis.yml` (shard and aggregate)
  - `eval-direction.yml`
- **Producers.** They run `encrypt <their paths>`, then `git add data/encrypted/`, then `scan-key --changed`, then commit. The `git add` stays literal, so `check_bot_pr_sync_allowlist.py` still sees and checks it (it passes: `data/…`).
- **`check-price` failures.** In `check-price`, a decrypt or encrypt failure does not stop the forecast being committed: tier 2 needs only today's IBJA rate and the committed `calibration.json`. The run then fails at its last step, so the failure is visible, and the encrypted history stays untouched.
- **Other producers.** They fail at the failing step.
- **Pre-migration.** Before the migration PR, every path is still tracked plaintext. `encrypt` and `decrypt` are then no-ops that need no key, and the pipeline behaves exactly as today. The one exception is `nowcast_shadow_log.json`: it is new and untracked, so it is encrypted from its first write.
- **Lint.** `lint` runs `data_crypt.py guard`, which needs no key. It fails if:
  - a registered path is tracked while it has ciphertext;
  - a registered path is not gitignored;
  - a ciphertext does not match its manifest entry;
  - a stray file sits under `data/encrypted/`.
- **Production ciphertext.** It comes only from `encrypt-raw-data-migration.yml` (`workflow_dispatch`, `mode=migrate`), because only CI can read `DATA_ENC_KEY`. The workflow encrypts, verifies each round trip, runs `git rm --cached` on the plaintext, and opens a **draft** PR. It never merges.

## Threat model

**What this protects:** new publication of the raw data from now on. That covers:
- the repository tree;
- GitHub Pages (the `data/encrypted/` path is also excluded from the Pages build);
- clones and forks made after the migration;
- anyone browsing, scraping or mirroring the public repo.

**What this does not protect:**
- **Git history.** Every plaintext version committed before the migration stays in the history of this public repository and in every existing clone and fork. **History is not rewritten, by decision.** Removing it would take a `git filter-repo` rewrite plus a force-push, would break every fork and open PR, and would still not recall existing copies. That would be a separate GG decision.
- **Key compromise.** Anyone holding `DATA_ENC_KEY` can read every ciphertext, past and future. That includes a malicious workflow change merged to `master`, or a compromised maintainer account. Secrets are not exposed to pull requests from forks.
- **CI logs and artifacts.** Actions logs and artifacts of a public repository are public. A script that prints raw rates, or an `analysis.yml` shard that writes raw rows into its uploaded artifact, publishes them regardless of this ADR. This was not audited here. It is listed as a follow-up.
- **Derived numbers that can be inverted.** For example, the IBJA-calibrated estimate, together with the public `calibration.json` slope and intercept, gives back IBJA's 22K rate. That is the displayed product, and the IBJA display question stays open (ADR 059).
- **Metadata.** File sizes and commit timing reveal row counts and update cadence.

## Key management

**Recommended key:**
- 32 random bytes, for example `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- The script refuses keys shorter than 16 characters.

**Offline copy: do this before running the migration.** GitHub secrets are write-only: if nobody saved the value of the existing `DATA_ENC_KEY`, it cannot be recovered. In that case:
1. Generate a new key locally with the command above.
2. Store it in two offline places: a password manager, plus a printed or USB copy kept separately.
3. Set it as the secret with `gh secret set DATA_ENC_KEY --repo gaurav-gandhi-2411/gold-rate-tracker`. Paste the value at the prompt; never put it on the command line.
4. After the migration PR merges, check the offline copy. Set `DATA_ENC_KEY` in a local shell and run `python scripts/data_crypt.py key-id`. The output must equal the `key_id` in any `data/encrypted/*.sha256.json`.

**Rotation:** after a suspected leak, or when a maintainer with access leaves.
1. Generate a new key and store it offline, as above.
2. Set the secret `DATA_ENC_KEY_OLD` to the current key, and `DATA_ENC_KEY` to the new one.
3. Run `encrypt-raw-data-migration.yml` with `mode=rotate`. It decrypts every migrated file with the old key, checks it against the manifest, re-encrypts it under the new key, and opens a draft PR.
4. Review and merge that PR, then delete `DATA_ENC_KEY_OLD`.

Scheduled runs between step 2 and the merge fail loudly with "wrong key". The replace guard means they cannot damage the ciphertext, and `check-price` keeps publishing the forecast. Do the rotation in a quiet window, between `check-price` runs.

Rotation does not protect data an attacker has already decrypted. Old ciphertexts in git history stay readable with the old key.

**If the key is lost:**
- Everything committed **before** the migration is still in git history as plaintext, because history is not rewritten. The frozen research snapshots can always be recovered with `git show 1c4a372e:reports/vol_regime_prereg_gcf_2000_2012.csv`; the GLD snapshot likewise. Both hashes are pinned. **Correction to the brief:** the brief said the frozen research snapshots would be unrecoverable. That holds only if history is ever rewritten.
- What would be lost is data accrued **after** the migration:
  - new IBJA rows (re-fetchable from IBJA's monthly PDFs, `ml.ibja backfill`);
  - new fusion snapshots and feature-store rows (retailer rows are not re-fetchable);
  - new shadow-log entries (the weekly scores can be recomputed only where their inputs survive).
- Recovery:
  1. Generate a new key.
  2. Restore plaintext from the last pre-migration commit (`git show <sha>:<path>`), or from any local decrypted copy.
  3. Delete the stale `data/encrypted/` entries in a human PR.
  4. Re-run `mode=migrate`.

## Live-read files: prepared, NOT done (STOP for GG)

Moving `data/prices.json` to ciphertext changes what every user sees. E2 (#2069) already covers the hero: Tanishq's current rate when it is fresh, otherwise our estimate, labelled as ours.

The proposed public replacement for the history is ADR 059's option B:
- The chart, history table, week/month comparisons and 7-day sparkline are built from the IBJA-derived series (`scripts/build_ibja_derived_prices.py`, #2053). That series is a pure function of IBJA and two public constants, and contains no Tanishq observation.
- Tanishq appears only as the current reading (E2), plus the previous reading if "today's change" is kept.
- `prices.json` joins `REGISTRY`. `backtest.json`, `drift_metrics.json` and `metrics_history.json` are rebuilt on derived actuals, or encrypted where they are research-only. `commentary.json` quotes derived values only.

Preview:
- Setup: the `feat/hero-tanishq-live-price` branch (#2069 stacked on #2053), with today's data, and `prices.json` = 259 derived rows plus the latest Tanishq reading.
- Screenshots: `reports/screenshots/e1-encrypt/` (before = master, after = the preview) at 1280 px and 390 px, in EN and HI.

What the preview shows GG must decide before this ships:
1. **The chart changes shape.** It is now IBJA's daily fix scaled to Tanishq's level. The 30-day minimum moves from ₹14,040 (Tanishq) to ₹14,016 (derived). The price trend and history become an **estimate** and need that label; #2053's `isDerivedReading` is the hook.
2. **Mixing the two series gives false changes.** The hero badge showed "+₹24 since last", and the "30-day low" card "+₹24". Both compare Tanishq's 14,040 with the derived 14,016, which are two different series. So:
   - "today's change" must compare two Tanishq readings, which needs the previous reading to stay public (a 2-row `tanishq_latest.json`);
   - otherwise it must be dropped;
   - the cards must use only the derived series.
3. **`og.png` and the dead-man Worker read `prices.json`.** They need the same switch.
4. **page_v2 files.**
   - `wait_or_buy_today.json`: drop `price_t` and publish the range as a rupee move (`lo_rs`/`hi_rs`, already in the file).
   - `weekly_range_shadow_log.json`: encrypt the log, and have `run_weekly_range_shadow.py` also write a public `{as_of, horizon, window_end, lo, hi}` file for page_v2. Otherwise raw IBJA rows start being published on 2026-09-27.

## Relation to #2038 (supersede, don't build on it)

This PR starts from `master`, not from #2038's branch. It supersedes two of #2038's items:
- **2a, the feature-store z-score (ADR 054).** Encryption keeps full-fidelity raw levels for research, and it avoids ADR 054's hazard of mixing units across `schema_version` 4 and 5.
- **2c, deleting the frozen CSVs and fetching live at run time.** That path cannot reproduce ADR 044/052 any more, because Yahoo's GC=F history has drifted (`41f0343a…` ≠ `2db071aa…`). The frozen bytes are now kept, encrypted, and `--check` reproduces both registered results exactly.

It also supersedes **2b** (`derived_premium.json` column drop): that drop left IBJA `pm_999` in the file, whereas this PR encrypts the whole file.

Still standing from #2038, if GG wants them:
- item 4: plain-language source credits on How We Know (user-visible);
- the item 3 finding: a macro forward-fill makes a carried-forward value look fresh.

**Recommendation:** close #2038, or trim it to item 4.

## Consequences

- **Researchers.** Anyone without the key can check a decrypted copy (`verify --hash-only`), but cannot rerun the research.
- **Local development.** Needs the key for any script that reads a registered file: `decrypt --all`. Tests use fixtures and do not need it (the one real-data test skips when the file is absent).
- **Scripts after migration.** `analysis_vol_regime_prereg.py` and `analysis_dow_prereg.py` refuse to run on a missing or altered snapshot. They never re-download it.
- **CI cost.** One scrypt per file per run. Measured at about 0.7 s each on the Windows dev machine; not yet measured on Ubuntu runners.
- **Failure behaviour.** A failed decrypt in CI never damages the committed ciphertext.

## Alternatives considered

- **A private repository (ADR 059, G3).** Rejected by GG (E1): it adds a second repository, a cross-repo token and a sync job.
- **`age` passphrase mode.** Not needed: `cryptography` is already pinned, and `age` is not installed on the runners.
- **One `MANIFEST.json` for all files.** Rejected. Producers on different schedules open separate bot PRs, and a shared file would make them conflict. `data_crypt.py manifest` prints the combined view.
- **HKDF.** Rejected in favour of scrypt: fine for a random 32-byte key, but weak if the secret is a passphrase.
- **Deleting the frozen snapshots (#2038 2c).** Rejected: ADR 044/052 could then be reproduced only by checking out an old commit.
