# Runbook

Operational procedures for gold-rate-tracker.

## Table of contents

1. [Local development setup](#local-development-setup)
2. [How to retrain](#how-to-retrain)
3. [How to roll back a bad production model](#how-to-roll-back-a-bad-production-model)
4. [How to roll back a bad forecast.json commit](#how-to-roll-back-a-bad-forecastjson-commit)
5. [How to add a new feature](#how-to-add-a-new-feature)
6. [How to investigate a CI failure](#how-to-investigate-a-ci-failure)
7. [Staleness alerts — interpretation and response](#staleness-alerts--interpretation-and-response)
8. [Manual scraper re-run](#manual-scraper-re-run)
9. [Bug #4 — bot-sync PRs don't auto-merge (FIXED, PR #183)](#bug-4--bot-sync-prs-dont-auto-merge-fixed-2026-07-17-pr-183)
10. [Known constraints](#known-constraints)
11. [Frontend PR device-check (required)](#frontend-pr-device-check-required)
12. [Honesty-ADR audit: user-facing copy paths (required)](#honesty-adr-audit-user-facing-copy-paths-required)

---

## Local development setup

**Prerequisites:** Docker Desktop, Python 3.11+, Git, Anaconda (or any Python env manager).

```powershell
# 1. Clone and enter repo
git clone https://github.com/gaurav-gandhi-2411/gold-rate-tracker
cd gold-rate-tracker

# 2. Install pre-commit hooks (one-time)
pre-commit install
```

> **Local training infra retired (ADR 009/010/014/024).** `ml/requirements-train.txt`,
> `scripts/win/setup-train.ps1`, and the `make setup-train` target were removed.
> LightGBM, TFT, N-BEATS, MLflow, and the Hydra config loader (`ml/config.py`,
> `configs/`) are no longer part of this repo (see
> [ARCHITECTURE.md](ARCHITECTURE.md#retired-components)).

---

## IBJA 30-day PDF backfill (one-time setup and monthly refresh)

`data/ibja_rates.parquet` accumulates IBJA daily AM/PM rates via two paths:

1. **Live daily scrape** (`check-price.yml`, every 6h): `python -m ml.ibja append`
   appends today's rates from `ibjarates.com/` if not already present.

2. **30-day PDF backfill** (`monthly-ibja-backfill.yml`, 1st of each month):
   `python -m ml.ibja backfill` fetches the live ibjarates.com HTML, extracts
   the dynamic PDF URL (`UploadedFiles/30DaysPdf/Pdf_XXXX_timestamp.pdf`),
   downloads and parses with pdfplumber, and appends any rows not already in
   the parquet. Idempotent — re-running appends nothing if already current.

To run the backfill manually from a clean clone:

```bash
# Run once after first checkout to seed recent 30 days
python -m ml.ibja backfill

# Or trigger from the Actions tab:
# Actions → Monthly IBJA PDF Backfill → Run workflow
```

The monthly workflow also commits the updated parquet automatically. No manual
commit required for scheduled runs.

**Tier 3 deep historical backfill (deferred):** Coverage beyond 30 days requires
either the Wayback Machine PDF extraction path (103 archived ibjarates.com
snapshots 2022–2026, each with a 30-day PDF link) or a paid IBJA API subscription
(`indiagoldratesapi.com`). Decision deferred to post-PR E based on Chronos
performance. See §3.7 risks in PROGRESS.md.

---

## Regenerating the inference dependency lockfile

`ml/requirements-inference.lock` pins every transitive dependency used in CI. Regenerate it
whenever `ml/requirements.txt` changes:

```bash
pip install uv
uv pip compile ml/requirements.txt \
  --extra-index-url https://download.pytorch.org/whl/cpu \
  --index-strategy unsafe-best-match \
  --output-file ml/requirements-inference.lock \
  --python-version 3.12
```

Commit the updated `.lock` file. The lockfile is the source of truth for `check-price.yml`;
`ml/requirements.txt` remains the human-edited input.

---

## How to retrain

> **Retired (ADR 009/010).** Local training infra retired per ADR 009/010; `requirements-train.txt` removed.
> Production forecast is naive flat-hold (ADR 012) — no retraining loop applies.
> Chronos-Bolt-Tiny is zero-shot; it is not trained locally.

---

## How to roll back a bad production model

> **Retired (ADR 009).** ONNX model promotion was retired with the neural models (ADR 009). No model artifacts exist in `models/production/`; the production forecast is naive flat-hold (ADR 012). To recover from a bad forecast output, see [How to roll back a bad forecast.json commit](#how-to-roll-back-a-bad-forecastjson-commit).

---

## How to roll back a bad forecast.json commit

`data/forecast.json` is committed by the `gold-rate-bot` CI user every 6 hours. If a forecast
commit contains bad data (e.g. NaN price, wrong karatage), roll it back without re-running CI:

```bash
# 1. Find the bad bot commit
git log --oneline data/forecast.json | head -5

# 2. Revert it (non-destructive — creates a new revert commit)
git revert <bad-commit-sha> --no-edit

# 3. Push — the live site picks up the reverted forecast.json within minutes
git push
```

If the forecast is stuck stale (>18h old), the amber banner on the site will appear automatically.
The next successful CI run will replace `forecast.json` and hide the banner.

---

## How to add a new feature

1. Add feature logic to `ml/features.py` (follow the existing pattern in `build_feature_matrix()`).
2. Add the feature name to `FEATURE_COLS` or `MACRO_FEATURE_COLS` as appropriate.
3. If the feature requires a config flag, add it to `configs/data/default.yaml`.
4. Write a unit test in `tests/test_features.py`.

> **Steps 5–6 retired (ADR 009/010).** `python -m ml.training` and the ONNX auto-promotion pipeline were retired with the neural models. The production forecast is naive flat-hold (ADR 012) — no retraining or model-promotion step applies.

---

## How to investigate a CI failure

1. Go to **Actions** tab: `https://github.com/gaurav-gandhi-2411/gold-rate-tracker/actions`
2. Find the failed run; click in.
3. Common failure points:
   - **Scraper step:** usually Tanishq's Cloudflare block (`continue-on-error: true`, logged as a `::warning` run annotation, not a hard failure — expected per ADR 025). Only look for `=== PAGE TEXT ===` in stderr and update the selector in `scraper/scrape.js` if the failure message is a selector/DOM error, not a `Cloudflare challenge page` message.
   - **Macro step:** yfinance rate-limited or ticker changed. Check `ml/macro.py` ticker list and `data/macro_cache.parquet` freshness.
   - **Run inference** (`ml.inference`): `continue-on-error: true`. Missing `data/prices.json` or fewer than 30 backtest folds → `forecast.json` written with `model_status: "insufficient_backtest_history"`. A hard crash here isn't independently alerted (T9/T9_ESCALATE watch IBJA freshness, not inference success) — check the Actions run status directly.
   - **Refit calibration** (`ml.calibration`): `continue-on-error: true`. Missing `data/ibja_rates.parquet` or fewer than 30 IBJA-Tanishq overlap pairs → refit silently skipped; stale `calibration.json` persists until data accumulates.
   - **Run Chronos probe** (`ml.chronos_forecast --probe`): `continue-on-error: true`. HuggingFace download timeout or torch error → `chronos_probe.json` absent or `status: "failed"` → next run's inference falls back to flat-hold, no directional signal. Check the Actions cache for `chronos-bolt-tiny-*`.
   - **Commentary step:** `GROQ_API_KEY` secret expired or Groq rate limit hit. Step is `continue-on-error: true` so it won't block scraping.

---

## Staleness alerts — interpretation and response

Per **ADR 025** (IBJA is the primary price source, Tanishq an opportunistic
enrichment), Tanishq's scrape failing is the *expected* steady state under its
sustained Cloudflare block and does **not** raise an alert on its own — only IBJA
itself (the primary source) failing does. You may receive ntfy.sh alerts with the
following titles. Here is what each means and what to do.

| Alert title | Meaning | Action |
|---|---|---|
| `Gold Tracker: IBJA data stale (Nd)` (T9) | No valid IBJA reading in >= 2 business days (weekends never count) | Check `ibjarates.com` reachability and the "Append IBJA rates" step in the last CI run |
| `Gold Tracker: SUSTAINED IBJA outage (Nd)` (T9_ESCALATE) | Same gap >= 4 business days — a sustained, not transient, IBJA outage | Same as above, treat as urgent; the site has nothing fresher than a multi-day-old estimate |
| `Gold Tracker: Scraper DOM broken` | Weekly canary failed against the live Tanishq page | DOM may have changed *or* Tanishq's runner-IP block is active — check `data/prices.json` for a run of missed entries before assuming the selector broke (see [Manual scraper re-run](#manual-scraper-re-run)) |

A Tanishq-only scrape miss shows up as a `::warning` run annotation in the
"Flag scrape miss" CI step (visible in the Actions run summary) — informational
only, never a paging alert.

**Macro cache stale (CI fails with age >14d):**
The "Check macro cache age" CI step will fail the run (not `continue-on-error`) if `data/macro_status.json`
reports `cache_age_days > 14`. This blocks the forecast commit. To recover:

```bash
# Manually trigger the macro fetch
python ml/macro.py --full
git add data/macro_cache.parquet   # not committed normally; trigger via workflow instead
# Or: trigger check-price.yml manually from the Actions tab — it will re-fetch macro data
```

**Stale/estimate banner visible on site:**
`app.js::renderStaleBanner` and `renderFreshness` read `forecast.price_source` directly —
"Estimated retail price — calibrated from IBJA..." is the expected default state
whenever Tanishq hasn't confirmed within 8h (not a problem to fix). It only shows the
"Live price update unavailable" wording when IBJA itself is beyond its 14-day display
backstop — that state should be rare and, if it persists, will have already fired T9.

---

## Manual scraper re-run

To re-scrape outside the 6-hour schedule (e.g. after fixing a broken selector):

1. Go to **Actions** → **Check Gold Price** → **Run workflow** (top-right button).
2. This triggers the full pipeline: scrape → macro → forecast → commentary → commit.

To test the scraper locally:

```bash
cd scraper
npm ci
npx playwright install chromium
node scrape.js          # prints JSON to stdout; non-zero exit = failure
node --test test_scrape.js   # fixture-based DOM tests
```

If the selector is broken, open the Tanishq gold rate page, inspect
`span.goldpurity-rate[data-goldrate22kt]`, and update the selector in `scraper/scrape.js`.
Then update `tests/fixtures/tanishq_sample.html` with the new page structure and update the
expected values in `scraper/test_scrape.js`.

> **Scraper DOM canary issues (#113/#114-style):** the weekly `scraper-canary.yml` runs the
> live scrape on a GitHub runner IP, which Tanishq's CDN frequently blocks — so a canary
> failure usually means "runner IP blocked", NOT "DOM changed". Before assuming the selector
> broke, check whether `data/prices.json` has a run of missed entries around the same time
> (an IP block affects every run, not one DOM change) — if recent readings are otherwise
> intact and only the canary run failed, the DOM is fine and the canary issue can be closed.

---

## Cloudflare Worker (retired 2026-07-16)

A Cloudflare Worker (`gold-rate-tanishq-worker`) ran 2026-06-13–2026-06-25 as a clean-IP
fetch path, dispatching the pipeline via `repository_dispatch`. It went silent on
2026-06-25 when Tanishq extended its Cloudflare bot-protection challenge to Workers
egress (confirmed via `wrangler dev --remote` reproduction: `403` + "Just a moment..."
challenge on the exact production fetch). Cron, deploy, and the GitHub PAT were all
healthy — the block was on Tanishq's side and not fixable client-side. The Worker, its
GitHub PAT (`gold-rate-tracker-worker-dispatch`), the `repository_dispatch` CI trigger,
and the in-CI dispatch-payload validator were all removed rather than left running in a
permanently degraded state. The scheduled in-CI Playwright scrape + IBJA estimate floor
are now the sole ingestion path (see [docs/CLEAN_IP_FETCH.md](CLEAN_IP_FETCH.md) for the
retired setup, kept for historical reference).

---

## Bug #4 — bot-sync PRs don't auto-merge (FIXED 2026-07-17, PR #183)

**Status: fixed. `lint.yml` now forwards its real result as a Commit Status
(`post-required-check-status` job) in addition to the normal Check Run, sidestepping the
PR-linkage question entirely — Commit Statuses are SHA-scoped and branch protection
matches required contexts by name across both APIs. Proven 3/3 on live `check-price.yml`
cycles (00:15, 00:21, 00:26 UTC 2026-07-17): every merge succeeded on bot-pr-sync's FIRST
attempt, ~3 seconds after checks went green, zero `"policy prohibits"` rejections. `#169`'s
ntfy alert is untouched and remains the permanent backstop regardless.**

The section below is kept as the full diagnostic timeline — three different theories were
tried before landing on the actual fix, and the history is worth more than a clean summary
would be if this ever regresses. Read it before assuming a new bypass mechanism is needed;
the working fix (Commit Status forwarding) is a completely different lever from either of
the two abandoned bypass attempts.

Every `bot-pr-sync` merge (`bot/data-sync`, `bot/og-image`, `bot/direction-eval-sync`,
`bot/ibja-backfill-sync`, `bot/backtest-sync`) had been failing with `"Pull request #N is
not mergeable: the base branch policy prohibits the merge"` on effectively every cycle for
weeks, even with both required checks (`lint`, `pwa-js`) `success` on the head SHA.
`#169`'s ntfy alert exists specifically to catch this; the owner had been manually
clicking merge on each stuck PR since PR #160 first (unsuccessfully) tried to fix it.

**First-pass root-cause theory (since disproven as stated):** classic branch protection
was blocking scoped tokens (PAT, App installation token) even with `enforce_admins:
false`, because check-runs dispatched via explicit `workflow_dispatch` (not a native
`pull_request` event) weren't linking to the PR for merge-eligibility purposes. Two bypass
mechanisms were tried against this theory and both failed identically:

1. **User PAT (`CI_MERGE_PAT`, fine-grained, PR #160).**
2. **GitHub App with a Ruleset `bypass_actor`** (created, tested, then fully reverted same
   session — ruleset deleted, App deleted, secrets removed, all 5 workflows confirmed
   byte-identical to pre-experiment). Ruleset `bypass_actors` only exempt an actor from
   that ruleset's *own* rules, never from a separately-configured classic protection rule
   — irrelevant here since classic protection was the actual blocker either way.

**Re-diagnosis, same session, after both bypasses failed:** re-reading `lint.yml`'s
trigger verbatim showed `pull_request: branches: [main, master]` with **no `paths:`
filter at all** — nothing should exclude bot PRs from the native trigger. Checking actual
run history (not `gh pr checks`, which was reporting stale/empty results) confirmed a
native **`pull_request`-triggered** check-suite genuinely fires on `CI_MERGE_PAT`-authored
pushes, alongside the redundant `workflow_dispatch` one bot-pr-sync explicitly requests.
Once that native check-suite existed, `gh pr checks` correctly linked the checks and
`gh pr view` reported `mergeable: MERGEABLE, mergeStateStatus: CLEAN` — durable across
repeated checks, including after master advanced further. **This directly contradicts an
earlier SHA from the same session** (~17:38 UTC, during the App-token test's rapid
successive force-pushes) which shows *only* a `workflow_dispatch` run, no paired
`pull_request` one.

**What this means:** the checks-not-linking theory, as originally stated ("workflow_dispatch
checks structurally can't satisfy required_status_checks"), is **wrong** — native
`pull_request` checks clearly can and do satisfy it. What's genuinely uncertain is *why*
the native trigger doesn't always fire reliably. Leading unconfirmed hypothesis: GitHub's
`pull_request` synchronize-webhook delivery may be unreliable under rapid repeated
force-pushes to the same branch — exactly what bot-pr-sync's own resync loop and this
session's intensive testing were doing. Not confirmed; `gh api`'s raw REST endpoint was
intermittently returning malformed responses during the investigation, blocking one
follow-up check (`required_status_checks.strict`) that would have ruled out a "branch
behind" explanation cleanly.

**A short observation window followed** (natural scheduled cycles, no code changes) to
distinguish "webhook flakiness, needs a different mitigation" from "reliably broken in
steady state." Superseded before completing — GG requested the deterministic fix directly
rather than continuing to measure trigger reliability. In hindsight the webhook-flakiness
question was a red herring for the actual FIX (though it may still explain the original
*symptom*): the real lever was never "make `pull_request` fire reliably," it was "give
branch protection a satisfying status through a channel that doesn't depend on `pull_request`
firing at all."

**The actual fix (PR #183, 2026-07-17):** added `post-required-check-status` to
`lint.yml` — `needs: [lint, pwa-js]`, gated on their real result (`if:
needs.lint.result == 'success' && needs['pwa-js'].result == 'success'`, never posts on
failure), running with `GITHUB_TOKEN` under job-level `permissions: statuses: write` (repo
default is read-only; `CI_MERGE_PAT`'s scope untouched). Posts two Commit Statuses
(`POST /repos/{owner}/{repo}/statuses/{sha}`, `context: "lint"` / `"pwa-js"` — exact
required-context names, confirmed via `gh api .../branches/master/protection`) to
`github.event.pull_request.head.sha` (pull_request-triggered runs) or `github.sha`
(workflow_dispatch/push). Branch protection's `required_status_checks.contexts` matches by
name across *both* Check Runs and Commit Statuses — a Commit Status has no check-suite or
PR-linkage concept to fail, regardless of which event triggered the workflow. No branch
protection setting was touched (`required_status_checks: [lint, pwa-js]`, `strict: true`
unchanged); `strict: true` needed no separate handling since `bot-pr-sync` already
rebases-before-push and resyncs on `BEHIND` (redispatching `lint.yml`, which re-posts fresh
statuses against the new SHA automatically).

**Proof:** 3 consecutive manual `check-price.yml` dispatches, each merging its
`bot/data-sync` PR (#184, #185, #186) on bot-pr-sync's first merge attempt:

| Cycle | PR | Checks-green → merge attempt | Result |
|---|---|---|---|
| 1 | #184 | 00:15:34 → 00:15:37 UTC (3s) | Merged, no rejection |
| 2 | #185 | 00:21:17 → 00:21:20 UTC (3s) | Merged, no rejection |
| 3 | #186 | 00:26:51 → 00:26:54 UTC (3s) | Merged, no rejection |

Compare to the pre-fix pattern: every historical cycle retried the merge call every ~12s
for the full 15-minute window and failed every single time with `"the base branch policy
prohibits the merge"`.

---

## Custom domain (deferred)

No domain has been purchased. Steps to add one when ready:

1. **Buy the domain on Cloudflare** (~₹800/yr for a `.in` domain; this is the only paid component of the whole stack). Cloudflare is preferred over other registrars because DNS propagation is fast and the management UI is clean.

2. **Add a CNAME record in Cloudflare DNS:**
   - Type: `CNAME`
   - Name: `@` (or `www`, depending on preference)
   - Target: `gaurav-gandhi-2411.github.io`
   - Proxy status: **DNS only** (grey cloud) — GitHub Pages requires direct CNAME, not Cloudflare proxy

3. **Add a `CNAME` file to the repo root** containing only the bare domain, e.g.:
   ```
   goldrate.in
   ```
   Commit and push. GitHub Pages reads this file to configure the custom domain.

4. **Enable HTTPS in GitHub Pages settings:**
   - Settings → Pages → Custom domain → enter domain → Save
   - Wait ~10 minutes for DNS propagation
   - Tick **Enforce HTTPS** — GitHub provisions a free Let's Encrypt certificate automatically

5. **Update the OG image URL** in `index.html` from the GitHub Pages URL to the custom domain once it's live:
   ```html
   <meta property="og:image" content="https://goldrate.in/og.png" />
   ```

Nothing to configure in this repo until the domain is purchased.

---

## Known constraints

- ~~MLflow runs locally only (port 5001). CI inference is CPU-only via ONNX; no tracking in CI.~~ **Retired (ADR 009).** MLflow and ONNX runtime removed with the neural models.
- ~~Training requires CUDA 12.4; RTX 3070 Laptop has 8 GB VRAM — sufficient for TFT (hidden=32) and N-BEATS (128 wide).~~ **Retired (ADR 009).** Local GPU training retired; TFT and N-BEATS removed.
- ~~GitHub Actions inference venv has no PyTorch or MLflow — only `onnxruntime` for neural model inference.~~ **Retired (ADR 009).** Neural model inference path removed; CI runs Chronos-Bolt-Tiny (zero-shot) or naive flat-hold.
- ~~Gold prices exhibit near-random-walk behaviour; the model may not beat the naive baseline on MAE. Documented in `models/production/*-meta.json`.~~ **Retired (ADR 009/012).** No trained model competes against naive; the production forecast IS naive flat-hold. Near-random-walk behaviour is still true and documented in the README.
- ~~`models/local/` is gitignored — PyTorch `.pt` checkpoints and Optuna DBs stay on your machine.~~ **Retired (ADR 009).** No local training artifacts; `models/local/` no longer used.
- WANDB is **not wired** in this project. The `.env` file previously contained `WANDB_API_KEY`, `WANDB_ENTITY`, and `WANDB_PROJECT` stubs which have been removed (Phase 1 audit confirmed no `wandb` imports anywhere in the codebase). Do not re-add these without a tracking ADR.

---

## Frontend PR device-check (required)

**Trigger:** any PR that touches `app.js`, `index.html`, `style.css`, or `service-worker.js`.

After the PR merges and the live site updates (Pages rebuilds within ~1 min of the master push):

1. Open the live site on a real iOS device (Safari).
2. Force a fresh service worker: open App Switcher (swipe up, hold), swipe the app away, reopen from Home Screen. This evicts the cached shell and forces the new SW to install. See also the "iOS standalone PWA" note in CURRENT_STATE.md for full SW lifecycle context.
3. Verify the specific UI change from the PR on the live site — not just "no blank screen."
4. If a regression is found, open a follow-on fix PR immediately (do not revert unless blocking).

**Background (Φ13, 2026-06-03):** WI-5 (info-panel) and Φ9B-3 (two-click nav) were both verified code-review-only and discovered post-merge on device. The root cause was this check being skipped, not a missing preview URL. A PR preview deploy was evaluated and rejected — the viable no-dependency option (GH Pages subpath) would add a new production failure mode to `check-price.yml`; third-party options (Netlify/Cloudflare) break the Rs.0/no-external-dependency discipline. See PROGRESS.md Decision Log §Φ13.

---

## Honesty-ADR audit: user-facing copy paths (required)

**Trigger:** any PR that implements or updates an ADR that changes what the system can *claim* — accuracy framing, probability language, direction-signal scope, forecast vs. description framing, buy-timing advice.

**Why this exists:** 7 copy-path misses to date. 3 of them were pure copy-layer misses — the fix landed in one consumer, and a sibling consumer generating independent copy was not in scope. The fix scope has been the primary consumer; the pattern is the sibling. Structural guard: when an honesty ADR lands, iterate this list before closing the PR.

**Complete list of user-facing copy-generating paths:**

| # | File | Function / constant | What copy it generates | Last audited |
|---|------|---------------------|------------------------|--------------|
| 1 | `ml/notifications.py` | `_build_t8_content()` | T8 morning/evening digest directional hint: "Prices may edge up/ease a little." | PR #84 (Φ9A gap) |
| 2 | `ml/notifications.py` | `_check_t1()` … `_check_t7()` bodies | T1–T7 notification titles and bodies; T7 lean_hint strings | Φ9A |
| 3 | `ml/commentary.py` | `SYSTEM_PROMPT` constant | Groq LLM instruction block — governs factual claims about accuracy, direction framing, forward-lean language | Φ18B (2026-06-05) |
| 4 | `app.js` | `computeVerdict()` | Verdict card headline + reason: "Trending down/up this week" / "Roughly flat this week" + reason template | Φ9A (INV-2) |
| 5 | `app.js` | `computeGoodPriceSignals()` | Good-price verdict lead (4-tier: "Today's price is low for the past month" / "on the lower side" / "around usual levels" / "on the higher side this month"), proof line ("Cheaper than X% / More expensive than X% of the N days in the past month"), data-sufficiency degrade note (when <30 days), supporting lines, divergence note | Φ18A (2026-06-05) |
| 6 | `app.js` | `renderModelSignal()` vol-context block | 4 regime-conditional strings: "Gold has been more/calmer volatile than usual lately — about ±₹X over 5 days." / "Gold has been moving about ±₹X over 5 days lately." / "Gold's price typically moves about ±₹X over 5 days." | Φ10B |
| 7 | `app.js` | `renderModelSignal()` methodology accordion | "How accurate is this forecast?" panel: flat-hold framing, 56%/63% vs ~70% base rate, no-directional-edge claim; PI range framing ("Covers typical 5-day moves X% of the time"); direction-signal note ("Current price-move alerts use 7-day momentum — not the AI direction model") | Φ8C' |
| 8 | `app.js` | `renderDriverContext()` | Attribution headline (7d, only when attribution_valid=True): "Gold is up/down ~Rs.{total} over the past week — about Rs.{x} from a weaker/stronger rupee and Rs.{y} from global gold prices." Driver-state (30d, 3-branch): B1 (driver >2%): mechanism sentences; B2 (premium-dominated, both drivers <2%): "Indian gold has moved more than global prices or the rupee explain this month — local factors such as import costs or seasonal demand are driving the difference."; B3 (all flat): "Gold has been stable this month; no major driver moved much." — PAST-TENSE ONLY, no forecast, no buy/sell. | Φ14-2 (2026-06-03) |
| 9 | `index.html` | Static section headings, `<summary>` accordion text, `aria-label` attributes | Section `<h2>` labels ("How past estimates have held up"), track-record section aria-label, methodology accordion summary ("How this works · how good is this? · historical checks"), canvas aria-labels, comparison card labels ("30-day floor") | Φ18C (2026-06-05) |
| 10 | `app.js` | Hardcoded strings inside `renderMethodology()` template literals and `renderComparisons()` `textContent` assignments | Methodology card heading/stat strings ("5-day range", "Assume no change", "How accurate is this?", "Estimate accuracy — last 7 days"); Chart.js dataset label ("Flat-hold estimate"); verdict-rule body ("estimate or 30-day average"); floor card sub-text ("above this month's lowest"); dynamic accuracy clause in direction-signal meth-note (bt.dir_acc_5d_chronos) | Φ18C (2026-06-05) |
| 11 | `app.js` | `refreshData()` catch block; `init()` load-error catch block | State-honesty error copy: "Couldn't refresh — showing last update from {rel_time}" (refresh fail, `#stale-banner`); "Price unavailable" (hero on load fail); "Couldn't load the latest price. Check your connection and try again." (commentary on load fail) | Φ16 (2026-06-03) |
| 12 | `app.js` | `updateOfflineBanner()` | Offline copy: "Offline · showing last loaded data from {rel_time}" and "Offline · no data loaded yet" — shown in `#offline-banner` when `navigator.onLine === false` | Φ16 (2026-06-03) |

**Grep to find all paths before closing a honesty PR:**

```bash
# Generated-copy paths (Python):
grep -rn "SYSTEM_PROMPT\|lean_hint\|_build_t8_content\|body +=" ml/
# Generated-copy paths (JS functions):
grep -n "computeVerdict\|computeGoodPriceSignals\|volNote\|renderDriverContext\|renderMethodology\|renderComparisons" app.js
# Static HTML labels and aria — these were the §11 blind spot (Φ15):
grep -in "forecast\|predict\|expected" index.html
# Hardcoded strings inside template literals — also missed pre-Φ15:
grep -n "meth-heading\|meth-stat-value\|cmp-label\|dataset\.label\|textContent\s*=" app.js | grep -v "^\s*//"
```

**Instruction:** When an honesty decision (ADR) changes what we can claim, audit every row in this table before marking the PR ready. These generate user-facing copy and are easy to miss in a fix's scope — 8 misses to date. Update the "Last audited" column when you confirm a path is still compliant.

**§11 blind-spot note (Φ15):** Rows 9 and 10 were added because the original §11 only listed JS *functions* generating copy — it missed static HTML labels (`<h2>`, `aria-label`, `<summary>`) and hardcoded strings inside template literals in render functions. The `grep -in "forecast\|predict\|expected" index.html` pattern is the structural guard that catches future label regressions.
