# Retailer takedown runbook

**Purpose:** Remove a named retailer's data from the live site within hours of a request. The site then falls back cleanly to IBJA × calibration. **Decision record:** ADR 059 (G1). **Switch:** `config/retailers.json`.

Retailers covered: `tanishq` (live enrichment + price history), `grt`, `malabar`, `kalyan` (fusion tier + shadow research data).

**Roles.** **GG** is the owner and the only person who can merge to `master` or edit repo settings. **CC** is a Claude Code session: it prepares branches and PRs and never merges. "GG or CC" means CC prepares and GG merges.

**Expected time.**
- A fusion retailer (GRT, Malabar, Kalyan): about 30 minutes to live.
- Tanishq: 1 to 3 hours to live. Most of that is the PR, CI and the Pages deploy. Tanishq is the hardest case because `data/prices.json`, the site's whole price history, is Tanishq data.

---

## 0. Before you start (5 min, GG)

1. Record the request: who sent it, the date and time, which retailer, and exactly what they asked for (stop fetching, stop displaying, or delete history). Keep the email.
2. If the request makes a legal claim, stop all fetching first (steps 1 and 2). Then get legal advice before replying beyond an acknowledgement. The binding rule in ADR 059 applies.

## 1. Flip the switch (5 min, GG or CC)

1. Branch `fix/takedown-<retailer>` from `origin/master`.
2. In `config/retailers.json`, set `"<retailer>": { "enabled": false }`. Change nothing else. A malformed file fails every reader loudly by design (`ml/retailers.py`, `scraper/retailer-enabled.mjs`).
3. Run `python -c "from ml.retailers import disabled_retailers; print(disabled_retailers())"`. It should print only that retailer.

What the flag does once merged (all tested, see §6):

| Reader | Effect when disabled |
|---|---|
| `scrape-tanishq-selfhosted.yml`, `scraper-canary.yml` (switch step) | No request to Tanishq. The step logs a notice. |
| `scraper/update-and-notify.js` | Refuses to append any Tanishq reading (defence in depth) |
| `ml/inference.py` tier 1 | Tanishq is never shown as the confirmed price |
| `ml/inference.py` tier 3 | A disabled fusion retailer is never fetched and never named in `fusion_sources` or the banner |
| `ml/inference.py` tier 4 | With Tanishq off, the "last Tanishq price" fallback is removed. If IBJA and fusion both fail, inference raises and `forecast.json` is left as it was. |
| `ml/shadow_fusion.py` | Not fetched and not appended to `data/fusion_snapshots.parquet` |
| `ml/calibration.py` | With Tanishq off: no refit and no band-coverage rescoring. The last fitted coefficients are kept. |

## 2. Stop the fetch right now, before the PR merges (2 min, GG)

The PR takes time, so stop traffic first:

- **Tanishq:** GitHub → Actions → *Scrape Tanishq (self-hosted)* → ⋯ → **Disable workflow**. Also disable *Scraper canary*, which fetches live on Mondays. Or run `gh workflow disable scrape-tanishq-selfhosted.yml && gh workflow disable scraper-canary.yml`.
- **GRT, Malabar or Kalyan:** run `gh workflow disable shadow-fusion.yml`. This stops all three; re-enable it after step 1 is merged. The tier-3 fallback in `check-price.yml` only fetches when Tanishq and IBJA both fail, and the merged flag covers it.

## 3. Regenerate the files the site serves (Tanishq only; 10 min, CC)

`data/prices.json` holds only Tanishq readings. It feeds the chart, the history table, the comparisons and the 24K/18K cards. On the same branch:

1. Dry run: `python scripts/build_ibja_derived_prices.py`. It prints the row count and the latest row.
2. Write: `python scripts/build_ibja_derived_prices.py --write`. This replaces `data/prices.json` with one row per IBJA day:
   - 22K = slope × IBJA pm_916/10 + intercept, using the frozen `data/calibration.json`.
   - 24K and 18K come from IBJA's own purity ratios.
   - Every row has `source = "ibja_calibrated_derived"`. `app.js` keys on that tag and then hides every Tanishq label: the hero location line, "Tanishq last confirmed", the long-silent banner clause and the calculator's "Tanishq store rate".
3. Run `python -m ml.inference`. Check that `data/forecast.json` has `"price_source": "ibja_calibrated"`.
4. **Static text naming Tanishq** is not data-driven. Edit it in the same PR, EN and HI, and send the HI text for native review:
   - `index.html`: meta, og and twitter descriptions, `firstVisitText`, the footer link
   - `manifest.webmanifest` `description`
   - `i18n.js`: `pageDescription`, `firstVisitText`, `footerBody`, `heroLocation`, `calcRateUsedTanishq`, `bannerTanishqLongSilent`, `heroLastConfirmed`

   Use the neutral replacements listed in ADR 059 §"Proposed wording".
5. **Research files that hold Tanishq values:**
   - `data/feature_store/snapshots.parquet` column `tanishq_22k`
   - `data/tanishq_scrape_outcomes.jsonl`, which holds outcomes only and no prices
   - `archive/history_seed_v1_uniform_premium.json`

   Stop appending to them: the feature store reads `prices.json`, so after step 3 it records derived values. Do not rewrite history in this PR. Deletion from history is step 7.

For a fusion retailer, step 3 is not needed. The live site shows fusion data only on tier 3, which is computed live each cycle. `data/shadow_fusion_output.json` is regenerated by the next shadow cycle without the retailer.

## 4. Ship it (30–90 min, GG)

1. Bump `VERSION` in `service-worker.js` if any shell file changed in step 3.4. Without the bump, installed PWAs keep the old HTML and JS until the next bump. Data files are network-first and refresh on the next load.
2. Open the PR and wait for CI (`lint`, `pwa-js`, `pwa-headless`). `tests/test_retailer_takedown_headless.js` is the render check for exactly this state.
3. GG merges. GitHub Pages deploys from `master` in about 1–10 minutes.
4. Re-enable `shadow-fusion.yml` if you disabled it in step 2 for a fusion retailer. It now skips the disabled one. Leave the Tanishq workflows disabled: they are no-ops now, but disabled is clearer.

## 5. Verify on the live site (10 min, GG or CC)

1. Load `https://gaurav-gandhi-2411.github.io/gold-rate-tracker/` in a private window, so no service-worker cache applies. Also hard-reload the installed PWA.
2. Check the hero. It shows `≈ ₹…` with "Estimated shop price from IBJA · pan-India". There is no "Tanishq last confirmed" line, and the calculator says "IBJA-based estimate".
3. Run `curl -s …/data/prices.json | grep -ci tanishq`. It should print `0`. Also run `curl -s …/data/forecast.json | grep price_source`.
4. Run `curl -s …/ | grep -ci "<retailer>"`. It should print `0`, provided step 3.4 was done. For a fusion retailer, check that `fusion_sources` in `forecast.json` never lists it. It only appears on tier 3.

## 6. Proof this works (already in CI)

`tests/test_retailer_takedown.py` runs the real pipeline (`build_ibja_derived_prices` → `ml.inference.main`) with Tanishq and Kalyan disabled. It asserts:
- a valid `ibja_calibrated` `forecast.json`
- no fetch of a disabled source
- a loud failure instead of a Tanishq fallback
- that derived rows never size the band

It also drift-checks `tests/fixtures/retailer_takedown/`. `tests/test_retailer_takedown_headless.js` renders that pipeline output with the real `app.js` (desktop EN, phone EN, phone HI). It asserts no Tanishq-attributed figure or label appears in the price surfaces. Screenshots are in `reports/screenshots/retailer-takedown/`.

## 7. Purge from future commits and, if asked, from history (GG decision)

- **Future commits:** the flag plus the disabled workflows stop new rows. `check-price.yml` and `shadow-fusion.yml` commit only the files they regenerate.
- **Past commits:** the data stays in git history and in any fork. Removing it needs `git filter-repo` plus a force-push and a GitHub support request to purge cached views. That is an irreversible history rewrite (CLAUDE.md rules 37/55/57). Do it only on explicit GG instruction, after a backup. Say plainly to the requester what can and cannot be removed.

## 8. What to tell the requester (GG)

Keep it short and factual. For example: "On <date> we stopped collecting <retailer>'s published rates and removed them from <site> by <time, IST>. The site now shows an estimate based on the IBJA benchmark. Historical copies remain in the project's version history [and will / will not be removed — see below]. Contact: <email>." Do not argue the merits. Do not state legal conclusions.
