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
| `data/wait_or_buy_today.json` | yes: IBJA `price_t` (dropped by the producer since 2026-09-25) | page_v2 only (flagged off) | **Done on master (GG 4c).** See "GG decisions of 2026-10-05" below. |
| `data/weekly_range_shadow_log.json` (created by the first weekly run) | yes: `ibja_pm_916`, `path_low`/`path_high` | page_v2 only (flagged off) | **STOP (page_v2).** Superseded by the 2026-10-05 update below: the shipped file holds only forecast ranges (no `ibja_pm_916`/`path_*` fields), so it is classed derived and left public pending GG. |
| `archive/history_seed_v1_uniform_premium.json`, `data/history_seed_inr22k_*.parquet` | derived (products of Yahoo series × constants, #2038's reasoning) | no | Keep. |
| `data/chronos_probe.json`, `calibration*.json`, `coverage/cadence` metrics, `preregistered_h2_shadow_results.json`, other `reports/*` | derived (forecasts, fitted constants, aggregates) | some | Keep. |

**Data files added to master after this ADR was first written.** Resolved by the 2026-10-05 sweep below: `markup_today.json`, `markup_reversion/shadow.json` and `fhs_ranges/shadow.json` are now registered; `stale_day_shadow.json` was checked and is derived; `premium_nowcast_bars.json` (gitignored) is registered.

### Inventory update, 2026-10-05 (sweep of every tracked file under `data/`, `reports/`, `archive/` on master `53597600` plus the merge)

Sweep scope (rule 85b): every tracked `.json`/`.jsonl` (and the parquet/CSV files already listed above); numeric values in the per-gram band (9,000-20,000) and per-10 g band (60,000-200,000) under any key; key names matching markup/price/rate/tanishq/ibja/malabar/grt/kalyan/22k/24k/18k/premium/retail/actual/truth/level/close/usd/comex. Not covered: values stored under other scales or as text, and `reports/screenshots/`. The sweep found each file by pattern, so a raw field in an unusual unit could still be missed.

| Path | Source | Raw or derived | Decision | Why |
|---|---|---|---|---|
| `data/markup_today.json` | Tanishq/GRT/Malabar markup over public IBJA | raw (inverts) | **Register** | `markup_pct` x public IBJA = retailer price (ADR 059 rule). page_v2's reader wants a top-level `markup_pct`, so against this schema it already renders nothing: no visible change. |
| `reports/markup_reversion/shadow.json` | same, `markup_pct`/`markup_rs` | raw (inverts) | **Register** | Same inversion. No site reader. |
| `reports/fhs_ranges/shadow.json` | Tanishq `current_22k`/`actual_next_22k`, 63 rows | raw | **Register** | Tanishq daily prices. No site reader. |
| `data/premium_nowcast_bars.json` (gitignored) | Yahoo 1-hour bars | raw | **Register** | Already gitignored (ADR 046 option A); registration gives it an encrypted home. Never tracked, so the migration skips it. |
| `data/stale_day_shadow.json` | own-model MAE/coverage aggregates | derived | Keep public | Checked in full: only dates, counts, MAE, coverage intervals. No price level. (ADR's "raw until shown otherwise" is now shown.) |
| `data/weekly_range_shadow_log.json` | own-model 1d/week ranges (`lo`/`hi` on the IBJA per-10 g scale) | **raw: the IBJA rate is exactly recoverable** | **Registered** (GG decision 2, 2026-10-05) | Field-by-field verdict and the 10/10 recovery test are in "GG decisions of 2026-10-05" below. page_v2 is flagged off, so no visible change. Supersedes the "recommend keep public" text this row had. |
| `data/wait_or_buy_today.json` | rupee moves only; `price_t`, `lo`, `hi` already dropped by the producer (GG 4c, 2026-09-25) | derived | Keep public | Nothing left to drop; asserted by `tests/test_public_price_surfaces.py`. See below. |
| `data/nextfix_oos.json` | `pm0`/`pm1` = IBJA `pm_916` / 10 (143 of 143 folds) | raw IBJA series | **Registered** (GG decision 1, 2026-10-05) | Verification table below. `forecast.json` `next_fix` stays the public output. |
| `data/nextfix_intraday_shadow.json` | `base`/`target` = IBJA `pm_916`/`am_916` / 10 (15 of 15, 8 of 8) | raw IBJA values | **Registered** (GG decision 1) | Same. `nextfix_intraday_backtest.json` was checked and stays public (aggregates only). |
| `data/nextfix_p3_oos.json`, `data/nextfix_p3_variants_oos.json` | `pm0`/`pm1` = IBJA `pm_916` / 10 (145 of 145 folds each; 145 of 145 for both `p3_roll60` and `p3_monday`, 290/290 values each) | raw IBJA series | **Registered** (2026-10-08 update, same rule as `nextfix_oos.json`) | Verified against `data/ibja_rates.parquet` (275 rows) on the 2026-10-08 merge of master. Both are written by `ml.inference` in `check-price.yml`, whose `encrypt` step now names them. Details in "2026-10-08 update" below. |
| `data/model_demotion_state.json` | own demotion state | derived | Keep public | Booleans, reasons and timestamps; 180 bytes, no rate or price. Checked on disk 2026-10-08. |
| `data/model_scorecard_weekly.json` | own scorecard (`models[]`: per-model n, MAE, intervals, p-values, verdict text) | derived | Keep public | Checked on disk 2026-10-08: no `pm0`/`pm1`/per-day series; its retrospective text carries aggregate MAE only. It is the input of the public `docs/MODEL_SCORECARD.md`. |
| `data/model_status_weekly.json` (feat/model-status-weekly, not yet on master) | counts, dates, p-values, status strings | derived | Keep public | Checked on that branch's committed copy 2026-10-08: no rate and no price level. Not on this branch, so no workflow here touches it. |
| `data/prices.json`, `backtest.json`, `drift_metrics.json`, `metrics_history.json`, `commentary.json` | Tanishq series | raw | **GG decision (STOP, live-read)** | Unchanged from "Live-read files" below. Recommendation unchanged: ADR 059 option B. |
| `data/forecast.json`, `chronos_probe.json` | own forecast; one latest `base_ibja`/`ibja_last_value`/`current_22k` | derived (single latest value) | Keep public | E2's displayed current price. A single latest value is the displayed product, not a history. |
| `data/ibja_derived_prices.json`, `archive/history_seed_v1_uniform_premium.json`, `data/history_seed_inr22k_*.parquet` | IBJA/Yahoo x public constants | derived | Keep public | ADR 059 option B series; unchanged. |
| `data/calibration*.json`, `coverage_metrics`, `cadence_metrics`, `direction_*`, `preregistered_h2_shadow_results`, `event_watch_today`, `events_calendar`, `duty_cbic`, `tanishq_scrape_*`, `run_cadence_log`, `catchup_dispatch_log`, `experiments/*` | fitted constants, aggregates, public calendars, run logs | derived | Keep public | No price-level numbers in the swept bands or keys (`duty_cbic`/`events_calendar` are public government/event data). |
| `reports/*` other than those above (`kalman_*`, `leak_guard`, `timing_audit`, `model_audit_2026-10`, `r1`-`r4`, `*_run_*.json`, `plain-language-u4`, `tanishq_update_times`, `lighthouse`, `model_scorecard`, `markup_analysis`, `markup_reversion/{historical_test,step1_persistence}`, `premium_nowcast_exploratory`, `fhs_ranges/results`, `wait_or_buy_results`, `weekly_range_results`, `dow/vol_regime results`, `event_watch_results`) | statistics, p-values, counts, timings, ratios | derived | Keep public | No price-level numbers. Known residual: `reports/drivers_timing/before_after.json` carries a 168-value daily IBJA percent-change series; with one public anchor price it chains back to levels. ADR 060 never treated return series as raw; flagged for GG, not decided here. |

**Kalyan.** Kalyan is a retired source, but `data/fusion_snapshots.parquet` (1,827 rows of GRT/Malabar/Kalyan readings) and `data/shadow_fusion_output.json` (Kalyan values) are its retained history. ADR 060 treats retailer rates as raw whether or not the source is live (Context, third bullet), so the right treatment is: stay registered and encrypted, keep the history rather than delete it. The retirement PR must therefore not remove those two REGISTRY entries, not re-add plaintext, and not delete `data/encrypted/data/fusion_snapshots.parquet.*` after migration. If it stops the producer (`shadow-fusion.yml`), the ciphertext simply stops changing.

### GG decisions of 2026-10-05, applied (items 1, 2, 3, 4, 5)

**1. Own-model files that carry the raw IBJA PM-fix series: registered.** Verified against `data/ibja_rates.parquet` as on master `6953306a` (273 rows), not assumed:

| Path | What was checked | Result |
|---|---|---|
| `data/nextfix_oos.json` | 143 folds; `pm0` and `pm1` against IBJA `pm_916` / 10 on `d0` and `d1` | 143/143 equal on both (within 0.06). It is the raw IBJA PM fix, one row per day. **Registered.** |
| `data/nextfix_intraday_shadow.json` | 15 entries; `base` against IBJA `am_916`/`pm_916` / 10 on `base_date`; the 8 filled `target`s on `target_date` | 15/15 and 8/8 equal. `g_fix`/`g_now` are Yahoo COMEX in INR. **Registered.** |
| `data/nextfix_intraday_backtest.json` | every key and value range listed | Only counts, MAE aggregates (23.8 .. 110.5), change percentages, intervals, p-values, hit rates and timestamps. No rate and no price level (no value in the 9,000-20,000 or 60,000-200,000 band; the `n_decisions` counts reach 1,132 and are counts). **Stays public.** |

The public output stays `forecast.json` `next_fix` (the latest forecast and the numbers derived from it). `app.js`, `how-we-know.js` and `service-worker.js` name none of the three files; only `ml/nextfix.py`, `ml/nextfix_intraday.py`, `check-price.yml`, analysis scripts and tests do. `check-price.yml` already decrypts every registered path before `ml.inference`; its `encrypt` step now also names the two files, so they are re-sealed before the commit step.

Consequences to know: `tests/test_nextfix.py::test_committed_track_record_passes_the_direction_gate_as_recorded` reads the real OOS file and now skips when it is absent (the lint job has no key). `docs/ADR066_CHECK_HOWTO.md` step 1 now starts with a decrypt, because the 2026-10-16 check reads the shadow log.

**2026-10-08 update: P3 files registered.** #2411 and #2418 are on master, so the follow-up the previous text deferred is applied here: `data/nextfix_p3_oos.json` and `data/nextfix_p3_variants_oos.json` are in `REGISTRY` (`scripts/data_crypt.py`), in `.gitignore` (CRLF file) and in the `encrypt` line of the `check-price.yml` step that follows `ml.inference`. The migration workflow takes its list from `REGISTRY`, so it needs no edit. `data/model_demotion_state.json` (derived) and `data/model_scorecard_weekly.json` (aggregates) stay public. Consumers: `ml/nextfix.py` (check-price, decrypted by the top-of-job step), `scripts/build_model_scorecard.py` (weekly-backtest, which decrypts at the top; `docs-refresh.yml` runs `--render-only` from the public JSON only), `scripts/check_adr066_promotion.py` and the analysis scripts (see `docs/ADR066_CHECK_HOWTO.md`). Migration must run after this PR merges.

**2. `data/weekly_range_shadow_log.json`: raw IBJA rate recoverable, so registered.** Field by field over the committed file and both git revisions of it (`da884b6c`, `8964e759`), 10 entries:

| Key | Type | Range | Could it be a rate or level? |
|---|---|---|---|
| `shadow_after`, `entries[].as_of`, `window_end`, `issued_at_utc`, `result.scored_at_utc`, `result.scored_days[]`, `runs[].run_at_utc` | date/time strings | 2026-09-24 .. 2026-10-05 | No |
| `entries[].horizon` | `1d` / `week` | | No |
| `entries[].git_sha`, `runs[].git_sha` | commit ids | | No |
| `entries[].scale` | float | 1.0094 .. 1.0720 | No (public conformal scale) |
| `entries[].n_cal`, `runs[].issued`/`scored`, `summary.*` counts, `coverage`, `wilson_95`, `times_out_of_10` | int/float | counts 0..197, probabilities 0.30..0.95 | No |
| `entries[].result.inside` | bool | | No |
| **`entries[].lo`** | float | **129,327 .. 136,630** (per 10 g) | **Yes: inside the 60,000-200,000 band** |
| **`entries[].hi`** | float | **138,215 .. 147,340** (per 10 g) | **Yes** |

`lo` and `hi` are `price x exp(scale x q)` with `price` = the IBJA `pm_916` rate of `as_of` (`scripts/run_weekly_range_shadow.py` lines 59-74) and `q` from `ml.weekly_range.base_range` over the public proxy series. Recomputing `lo / exp(scale x q_lo)` with the repo's own code and public proxy gave the IBJA rate with **0.000% error for 10 of 10 entries** (and the same from `hi`), e.g. 2026-09-25 -> 139,336 (IBJA `pm_916`). Even without the proxy, `lo/price` takes only a handful of values (1d: 0.98058-0.98059; week: 0.95612-0.95653), so one known day anchors the rest. Verdict: a raw IBJA rate series is recoverable, so **registered**. Cost: page_v2's job-3 reader (`pickRangeShadowEntry`, flag `page_v2` is `false` in `flags.js`) fetches this file and treats a missing file as "no range" (`loadJSON(...).catch(() => null)`), so nothing visible changes today. **Before page_v2 is switched on,** its 1-day/7-day range needs a public source; this ADR's earlier plan (a separate public `{as_of, horizon, window_end, lo, hi}` file) would publish the same recoverable levels, so it needs a delta form (rupee moves, like `wait_or_buy_today.json`) or GG's decision. Open question for GG, recorded in the PR body.

**3. `data/prices.json`: NOT done (STOP).** See "Live-read files" below: the page side is ready (verified), but trimming only this file leaves the same Tanishq series public in four other files and needs a pipeline redesign that cannot be rehearsed without the key.

**4. `data/wait_or_buy_today.json`: already done on master (GG decision 4c, 2026-09-25).** `scripts/run_wait_or_buy_shadow.py` `public_today_entry` drops `as_of`, `n`, `price_t` and `range.lo`/`range.hi`; `tests/test_public_price_surfaces.py::test_committed_wait_or_buy_today_has_no_ibja_level` asserts no key `price_t` and no number in 60,000-200,000 anywhere in the committed file (re-run in this PR: passes). The remaining numbers are rupee moves (`lo_rs`/`hi_rs`/`x_rs`, -5,898 .. 7,718) that cannot be turned into a level without the dropped `lo`/`hi`. The page's reader takes only `horizons.<N>.sentence`. Nothing to implement.

**5. `data/fusion_snapshots.parquet` was already registered** (first table, since the first version of this ADR) and its producer `shadow-fusion.yml` already decrypts, encrypts and commits it; the readers (`ml.markup`, `ml.fusion_snapshot_store`, `ml.stale_day_estimate` via `scripts/run_stale_day_shadow.py`, analysis scripts) get the plaintext because `decrypt-data` runs `decrypt --all` and encryption is byte-level (a parquet and a JSON file take the same path; `test_cli_full_round_trip_every_registered_path` covers every registered path, including this parquet). **`reports/tanishq_update_times/kalyan_city_identity.json` is registered** as GG decided. It holds no rate (counts of cycles, two UTC timestamps, city names: verified key by key), so this is precautionary; ADR 059's addendum cites it as evidence, and after the migration that evidence is readable only with the key. It has no CI producer (`scripts/analysis_tanishq_update_times.py` is run by hand, then `data_crypt.py encrypt` seals it).

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

**Status, 2026-10-05 (GG: trim the public `prices.json` to today's readings plus the last earlier one, history in an encrypted file; implement only if nothing visible depends on the full public history).**

What reads the public `data/prices.json` history, sweep: `app.js`, `how-we-know.js`, `og.html`, `service-worker.js`, `worker-deadman/`, `ml/`, `scripts/`, `scraper/`, `.github/workflows/`:

- **The page (`app.js`, `loadJSON(DATA_URL)` into `allReadings`).** Latest reading: the hero, the freshness label, the stale banner, the calculator. Today's readings plus the one before: `computeTodayChange`. Every multi-day reader (hero verdict and 7-day sparkline, week/month comparison cards, history table, the 22K/24K/18K switch, good-price signals, 90-day band position, support distance, "today's read", model signal) goes through `historyRows` -> `chartSeries`, which since GG 3d (2026-09-26) takes `data/ibja_derived_prices.json` (265 rows, 2022-01-19 .. 2026-10-05, public) when it has 2 or more valid rows. `tests/test_history_series_headless.js` ("short-prices": prices.json holds only today's two readings) was written for exactly this trim. Conclusion for the page: **nothing visible depends on the full public history while `ibja_derived_prices.json` loads.** The one dependency left is the fallback: if that file is missing or unreadable, the multi-day cards fall back to Tanishq rows and would shrink with a trimmed file (the "no-derived" test state).
- **Not the page:** `how-we-know.js` does not fetch `prices.json`; `og.html` is a hand-run helper that reads fields (`price_22k`) the file does not have; `worker-deadman/` only mentions it in comments; `service-worker.js` caches every `/data/*.json` network-first by pattern, not by name.
- **Pipeline (not visible, but they need the full file):** `ml.inference`, `ml.calibration` (refit and band coverage), `ml.drift`, `ml.metrics`, `ml.drivers`, `ml.feature_store`, `ml.volatility`, `ml.markup`, `ml.notifications`, `scripts/run_*_shadow.py`, `analysis_*` scripts, and the scraper itself (`scraper/update-and-notify.js` reads the file and appends to it).

Why this is not implemented here (a STOP, with the exact question):

1. **It would not remove the Tanishq history from the public repo.** `data/backtest.json` (`folds[].actuals`/`naive`), `data/drift_metrics.json` (`actual_22k`), `data/metrics_history.json` (`current_22k`, `actual_next_22k`) and `data/commentary.json` carry the same series (ADR 059 lists them as following the `prices.json` decision). The page reads `backtest.json` (forecast-vs-actual chart, the MAE/direction lines of how-we-know) and `drift_metrics.json` (the "recent vs usual error" line); `metrics_history.json` has a URL constant in `app.js` that nothing fetches, and `commentary.json` is no longer read by the page (`composeTodaysRead` replaced it), so those two can be encrypted without a visible change. Encrypting or rebuilding those on derived actuals **does change what is rendered** (the forecast-vs-actual chart's actual line becomes the IBJA-based estimate; accuracy figures change value), so it needs GG's approval of the new numbers.
2. **The write path needs a redesign that cannot be rehearsed without the key and races with a live flow.** The self-hosted scrape job appends to `prices.json` and opens its own bot PR, with no Python and no key on that host. A workable design: the scrape flow stays as is; `check-price` (has the key) merges any public rows into an encrypted `data/prices_history.json`, runs the pipeline on the merged file, then writes the trimmed public file. Two bot PRs would then edit `prices.json` (a textual conflict leaves a PR open, which the existing "bot PR open too long" alert reports; no row is lost because the next run absorbs whatever is public). It also needs a one-time seed step in `data_crypt.py migrate`, and the lint pytest job (no key) reads the real file in several tests.

Question for GG: (a) approve the four follower files being rebuilt on derived actuals or encrypted (and which visible numbers may change), then (b) approve the write-path design above as a separate PR. The preview in `reports/screenshots/e1-encrypt/` already shows the chart change.

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
