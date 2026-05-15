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

---

## Local development setup

**Prerequisites:** Docker Desktop, Python 3.11+, Git, Anaconda (or any Python env manager).

```powershell
# 1. Clone and enter repo
git clone https://github.com/gaurav-gandhi-2411/gold-rate-tracker
cd gold-rate-tracker

# 2. Start MLflow (port 5001 — avoids conflict with other local MLflow instances)
.\scripts\win\mlflow-up.ps1        # or: docker compose up -d
# MLflow UI: http://localhost:5001

# 3. Create training venv with PyTorch CUDA 12.4 (RTX 3070)
.\scripts\win\setup-train.ps1
# Activates venv-train, installs torch+cu124, then ml/requirements-train.txt

# 4. Install pre-commit hooks (one-time)
pre-commit install
```

---

## How to retrain

```powershell
# 1. Ensure MLflow is running
.\scripts\win\mlflow-up.ps1

# 2. Activate training venv
venv-train\Scripts\Activate.ps1

# 3. Train all models (LightGBM + TFT + N-BEATS)
python -m ml.training              # or: .\scripts\win\train-all.ps1

# 4. Inspect run in MLflow UI
# http://localhost:5001  -> experiment "gold-rate-training"

# 5. If a new champion was promoted, commit and push the updated ONNX files
git add models/production/
git commit -m "chore: promote new model champion"
git push
# CI inference picks up the new ONNX on the next 6h cron
```

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

## Known constraints

- MLflow runs locally only (port 5001). CI inference is CPU-only via ONNX; no tracking in CI.
- Training requires CUDA 12.4; RTX 3070 Laptop has 8 GB VRAM — sufficient for TFT (hidden=32) and N-BEATS (128 wide).
- GitHub Actions inference venv has no PyTorch or MLflow — only `onnxruntime` for neural model inference.
- Gold prices exhibit near-random-walk behaviour; the model may not beat the naive baseline on MAE. This is documented honestly in the README and in `models/production/*-meta.json`.
- `models/local/` is gitignored — PyTorch `.pt` checkpoints and Optuna DBs stay on your machine.
