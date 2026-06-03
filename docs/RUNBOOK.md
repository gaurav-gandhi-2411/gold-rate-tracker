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
9. [Known constraints](#known-constraints)
10. [Frontend PR device-check (required)](#frontend-pr-device-check-required)
11. [Honesty-ADR audit: user-facing copy paths (required)](#honesty-adr-audit-user-facing-copy-paths-required)

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

> **Local training infra retired (ADR 009/010).** `ml/requirements-train.txt`,
> `scripts/win/setup-train.ps1`, and the `make setup-train` target were removed.
> LightGBM, TFT, N-BEATS, and MLflow are no longer part of this repo.

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

```bash
# 1. Find the commit before the bad promotion
git log --oneline models/production/

# 2. Revert that commit (creates a revert commit, does not rewrite history)
git revert <bad-commit-sha>

# 3. Push — CI uses the reverted model on the next cron tick
git push
```

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
5. Retrain: `python -m ml.training` — MLflow logs the new feature count in run params.
6. If the new model wins the champion/challenger comparison (2% MAE improvement gate), it auto-promotes to `models/production/`.

---

## How to investigate a CI failure

1. Go to **Actions** tab: `https://github.com/gaurav-gandhi-2411/gold-rate-tracker/actions`
2. Find the failed run; click in.
3. Common failure points:
   - **Scraper step:** Tanishq changed HTML structure. Look for `=== PAGE TEXT ===` in stderr; update the selector in `scraper/scrape.js`.
   - **Macro step:** yfinance rate-limited or ticker changed. Check `ml/macro.py` ticker list and `data/macro_cache.parquet` freshness.
   - **Forecast step:** ONNX schema mismatch after a bad model promotion. Roll back `models/production/` (see above).
   - **Commentary step:** `GROQ_API_KEY` secret expired or Groq rate limit hit. Step is `continue-on-error: true` so it won't block scraping.

---

## Staleness alerts — interpretation and response

You may receive ntfy.sh alerts with the following titles. Here is what each means and what to do.

| Alert title | Meaning | Action |
|---|---|---|
| `Gold Tracker: data stale (Xh)` | `prices.json` last reading is >8h old | Check the scraper step in the last CI run |
| `Gold Tracker: forecast stale (Xh)` | `forecast.json` predicted_at is >18h old | Check the forecast/macro steps in the last CI run |
| `Gold Tracker: Scraper Down` | Scraper step exited non-zero | Check DOM structure on Tanishq site; see [Manual scraper re-run](#manual-scraper-re-run) |
| `Gold Tracker: Scraper DOM broken` | Weekly canary failed against live page | DOM may have changed; update selector in `scraper/scrape.js` |
| `Gold Tracker: data stale (Xh)` from Staleness guard | prices.json age >8h after full workflow | Usually same root cause as scraper failure |

**Macro cache stale (CI fails with age >14d):**
The "Check macro cache age" CI step will fail the run (not `continue-on-error`) if `data/macro_status.json`
reports `cache_age_days > 14`. This blocks the forecast commit. To recover:

```bash
# Manually trigger the macro fetch
python ml/macro.py --full
git add data/macro_cache.parquet   # not committed normally; trigger via workflow instead
# Or: trigger check-price.yml manually from the Actions tab — it will re-fetch macro data
```

**Forecast amber banner visible on site:**
The banner in `index.html` shows when `forecast.json`'s `predicted_at` field is >18h old.
It hides automatically once CI successfully updates `forecast.json`. No manual action needed
unless the banner persists for >24h (then check CI).

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

- MLflow runs locally only (port 5001). CI inference is CPU-only via ONNX; no tracking in CI.
- Training requires CUDA 12.4; RTX 3070 Laptop has 8 GB VRAM — sufficient for TFT (hidden=32) and N-BEATS (128 wide).
- GitHub Actions inference venv has no PyTorch or MLflow — only `onnxruntime` for neural model inference.
- Gold prices exhibit near-random-walk behaviour; the model may not beat the naive baseline on MAE. This is documented honestly in the README and in `models/production/*-meta.json`.
- `models/local/` is gitignored — PyTorch `.pt` checkpoints and Optuna DBs stay on your machine.
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
| 3 | `ml/commentary.py` | `SYSTEM_PROMPT` constant | Groq LLM instruction block — governs factual claims about accuracy, direction framing, forward-lean language | Φ9A |
| 4 | `app.js` | `computeVerdict()` | Verdict card headline + reason: "Trending down/up this week" / "Roughly flat this week" + reason template | Φ9A (INV-2) |
| 5 | `app.js` | `computeGoodPriceSignals()` | Good-price verdict text ("Prices have been lower/higher/around usual levels lately"), supporting lines ("Cheaper/Pricier/Around the middle of the past month."), divergence note | Φ11-2 |
| 6 | `app.js` | `renderModelSignal()` vol-context block | 4 regime-conditional strings: "Gold has been more/calmer volatile than usual lately — about ±₹X over 5 days." / "Gold has been moving about ±₹X over 5 days lately." / "Gold's price typically moves about ±₹X over 5 days." | Φ10B |
| 7 | `app.js` | `renderModelSignal()` methodology accordion | "How accurate is this forecast?" panel: flat-hold framing, 56%/63% vs ~70% base rate, no-directional-edge claim; PI range framing ("Covers typical 5-day moves X% of the time"); direction-signal note ("Current price-move alerts use 7-day momentum — not the AI direction model") | Φ8C' |
| 8 | `app.js` | `renderDriverContext()` | Attribution headline (7d, only when attribution_valid=True): "Gold is up/down ~Rs.{total} over the past week — about Rs.{x} from a weaker/stronger rupee and Rs.{y} from global gold prices." Driver-state (30d, 3-branch): B1 (driver >2%): mechanism sentences; B2 (premium-dominated, both drivers <2%): "Indian gold has moved more than global prices or the rupee explain this month — local factors such as import costs or seasonal demand are driving the difference."; B3 (all flat): "Gold has been stable this month; no major driver moved much." — PAST-TENSE ONLY, no forecast, no buy/sell. | Φ14-2 (2026-06-03) |
| 9 | `index.html` | Static section headings, `<summary>` accordion text, `aria-label` attributes | Section `<h2>` labels ("Past estimate checks"), track-record section aria-label, methodology accordion summary ("How this works · how good is this? · historical checks"), canvas aria-labels, comparison card labels ("30-day floor") | Φ15 (2026-06-03) |
| 10 | `app.js` | Hardcoded strings inside `renderMethodology()` template literals and `renderComparisons()` `textContent` assignments | Methodology card heading/stat strings ("5-day range", "Assume no change", "How accurate is this?", "Estimate accuracy — last 7 days"); Chart.js dataset label ("Flat-hold estimate"); verdict-rule body ("estimate or 30-day average"); floor card sub-text ("above this month's lowest") | Φ15 (2026-06-03) |
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
