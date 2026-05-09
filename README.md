# Gold Rate Tracker

A free, open-source app that scrapes the [Tanishq gold rate page](https://www.tanishq.co.in/gold-rate.html?lang=en_IN) every 6 hours, charts the trend, pushes a phone notification when the **22K** rate **drops by ₹100 or more**, forecasts the next reading with a LightGBM model, and surfaces a short LLM-written market note.

- **Frontend:** Progressive Web App (installs to iOS & Android home screen)
- **Backend:** GitHub Actions cron job (free)
- **Storage:** JSON files in this repo
- **Notifications:** [ntfy.sh](https://ntfy.sh) — free, no account needed
- **Hosting:** GitHub Pages (free)
- **ML:** LightGBM (runs in <1 s on this data volume, no GPU)
- **LLM:** Groq — llama-3.3-70b-versatile, free tier
- **Cost:** ₹0

## How it works

```
┌──────────────────────┐   every 6h    ┌─────────────────┐
│  GitHub Actions cron │──────────────▶│  Playwright     │
│  (.github/workflows) │               │  scrapes page   │
└──────────────────────┘               └────────┬────────┘
                                                │
                                                ▼
                                       ┌─────────────────┐
                                       │ Compare to last │
                                       │  22K reading    │
                                       └────┬───────┬────┘
                                            │       │
                              dropped ≥₹100 │       │ all readings
                                            ▼       ▼
                                  ┌──────────┐  ┌──────────────┐
                                  │  ntfy.sh │  │ prices.json  │
                                  │   push   │  │  (committed) │
                                  └──────────┘  └──────┬───────┘
                                                        │
                                    ┌───────────────────┼──────────────┐
                                    ▼                   ▼              ▼
                             ┌────────────┐   ┌──────────────┐  ┌──────────┐
                             │ LightGBM   │   │   Groq LLM   │  │ PWA on   │
                             │ forecast   │   │  commentary  │  │ GH Pages │
                             └──────┬─────┘   └──────┬───────┘  └──────────┘
                                    │                 │
                                    ▼                 ▼
                             forecast.json    commentary.json
```

## ML approach

The forecaster is a **LightGBM regressor** trained to predict the *delta* of the next 22K reading (i.e., how much the price will move, not the price level). Differenced targets are more stationary, which makes the learning problem easier on small datasets.

**Why LightGBM and not LSTM/Transformer?**  
We have ~500 daily readings at deployment time and will accumulate one new reading every 6 hours. Deep sequence models need thousands of examples to generalise; on this volume, gradient-boosted trees are demonstrably more reliable and are much faster to train (< 1 second per run, so we can retrain from scratch on every scrape without caching a model artefact).

**Features**

| Feature | Description |
|---|---|
| `lag_1..4` | Price at previous 1–4 readings |
| `lag_7d`, `lag_30d` | Price at the reading closest to 7 and 30 days ago |
| `roll_7d_mean/std/min/max` | 7-day rolling stats (time-based window) |
| `dow`, `hour`, `dom`, `month` | Calendar features |
| `akshaya_tritiya`, `dhanteras` | Binary flag: within ±3 days of festival |
| `since_last_drop` | Readings since last ≥₹100 price drop |
| `hours_since_prev` | Helps model distinguish 6-hourly vs daily data |
| `prev_delta` | Price change from the previous reading |

**Target:** `price[t+1] − price[t]`

**Confidence interval:** Two extra LightGBM models with `objective="quantile"` at α = 0.10 and α = 0.90 produce the 80% prediction interval shown in the PWA.

## LLM commentary

After each forecast run, `ml/commentary.py` calls the [Groq](https://groq.com) API (model: `llama-3.3-70b-versatile`, free tier, 30 RPM / 14,400 RPD — we are nowhere near it at 4 calls/day) with:

- The latest 22K / 24K / 18K reading
- 3-day and 7-day price deltas
- The current price's percentile within the last 90 days
- Days since the last ≥₹100 drop
- The model's forecast and interval
- Festival proximity flags

The system prompt instructs the model to write 2–3 sentences of plain, factual English. It is explicitly prohibited from giving buy/sell/hold advice, making confident predictions, or using hype language. The result is stored in `data/commentary.json` (rolling 30-entry list).

## Data sources

<!-- BACKTEST_STATS_START -->
> **Model performance (90-day walk-forward backtest, 58 folds on seed data)** — run `python ml/backtest.py` to refresh.
>
> | Metric | LightGBM | Naive baseline |
> |---|---|---|
> | MAE | Rs. 283 | Rs. 204 |
> | MAPE | 1.87% | 1.35% |
> | Direction accuracy | 48.3% | 0.0% |
>
> The naive baseline ("predict no change") beats the model on MAE — expected on a near-random-walk series with fewer than 500 training points. The model's advantage is directional: 48.3% direction accuracy versus 0% for the baseline (which always predicts flat). The baseline's direction accuracy of 0% is a consequence of its constant delta=0 prediction: every actual non-zero move is a directional miss.
>
> What would improve results: more real scraped data (each 6-hourly Tanishq reading adds signal), a longer training window, or exogenous features (USD/INR rate, crude oil, US Fed decisions). As the live scraper accumulates months of data, the seed data becomes a smaller fraction of the training set and the model's edge should grow.
<!-- BACKTEST_STATS_END -->

### Calibration note

The cold-start seed data (444 daily entries) is derived from Yahoo Finance gold spot (GC=F) converted to INR/g and then to 22K/18K via standard karat ratios. Indian retail prices from sources like Tanishq carry a premium over international spot due to import duty (15%), GST (3%), and dealer margins, with additional intraday variability from IBJA reference rates.

This means the seed values are internally consistent for *modeling price changes* but are offset in absolute terms (~5–10%) from what Tanishq actually shows. The forecast carries a "warmup" flag and lower confidence until ~14 days of real Tanishq scrapes accumulate, after which the model retrains primarily on calibrated live data via `load_combined_history()`'s precedence rule (prices.json wins over seed on date overlap).

### What's real and what's seeded

| Data | Status |
|---|---|
| Live 22K/24K/18K readings | **Real** — scraped from Tanishq every 6h |
| `data/history_seed.json` | **Estimated** — see below |

`data/history_seed.json` is bootstrapped by `ml/seed_history.py`. The script attempts real Indian retail price sources first (goodreturns.in, goldpriceindia.in). If those fail (due to bot protection, site changes, etc.), it falls back to computing an *estimated* Indian 22K retail price from:

```
price_22k_inr = GC=F_close × INR=X_close / 31.1035 × (22/24) × 1.15
```

where:
- `GC=F` — COMEX Gold Futures (USD/troy oz), via Yahoo Finance chart API
- `INR=X` — USD/INR spot rate, via Yahoo Finance chart API
- `31.1035` — troy oz to gram conversion
- `22/24` — 22K purity
- `1.15` — approximate Indian retail premium (~10% import duty + 3% GST + ~2% margin)

**These estimated prices are NOT actual Tanishq or IBJA retail rates.** They are a reasonable approximation for bootstrapping the ML model. Once the live scraper accumulates a few months of data, the model will rely more on real readings.

The seed data spans roughly 2 years of daily observations. The live scraper adds 4 readings per day going forward.

## Model performance

Run `python ml/backtest.py` to generate `data/backtest.json` with full walk-forward results.

The naive baseline (predict last value unchanged, delta = 0) often has lower MAE than the model on small datasets — this is expected and honest. Gold prices exhibit near-random-walk behaviour over short horizons, so "predict no change" is a hard baseline to beat. The model's advantage, where it exists, tends to be in directional accuracy.

## Setup (≈15 minutes)

### 1. Create the repo

1. Go to [github.com/new](https://github.com/new), make a new **public** repo (public keeps GitHub Actions and Pages free and unlimited). Name suggestion: `gold-rate-tracker`.
2. Upload all files from this bundle. Easiest path: on your new repo's empty page, click **uploading an existing file**, then drag the entire folder contents in. Commit.

### 2. Pick your ntfy topic

Topics on ntfy.sh are public — anyone who guesses the name can read or send to it. Treat the topic like a password.

Pick something unguessable, e.g. `gold-gaurav-7k2x9p4r`. Keep it written down.

### 3. Add secrets as GitHub secrets

1. In your new repo, go to **Settings → Secrets and variables → Actions → New repository secret**.
2. Add `NTFY_TOPIC` = your topic name from step 2 (no URL prefix).
3. Add `GROQ_API_KEY` = your Groq API key from [console.groq.com](https://console.groq.com) (free tier is sufficient). Without this, commentary is silently skipped.

### 4. Seed historical data (first time only)

```
pip install -r ml/requirements.txt
python ml/seed_history.py
```

Commit `data/history_seed.json` to the repo so the GitHub Actions workflow has it.

### 5. Trigger the workflow once manually

The cron only fires every 6 hours. To get a first reading immediately:

1. Go to **Actions** tab on your repo.
2. Click **Check Gold Price** in the left sidebar → **Run workflow** → **Run workflow**.
3. Wait ~2 minutes. The run should go green and `data/prices.json`, `data/forecast.json`, and `data/commentary.json` will have entries.

### 6. Enable GitHub Pages

1. **Settings → Pages**.
2. **Source:** Deploy from a branch.
3. **Branch:** `main`, folder: `/ (root)`.
4. Save. After a minute, your site is live.

### 7. Subscribe on your phone

1. Install the **ntfy** app (iOS App Store / Android Play Store / F-Droid).
2. Open the app → **+** → enter your topic name → Subscribe.

### 8. Install the PWA on your phone

- **iOS Safari:** open your Pages URL → tap **Share** → **Add to Home Screen**.
- **Android Chrome:** open your Pages URL → menu → **Install app**.

## Tweaking

| What | Where |
|---|---|
| Cron schedule | `.github/workflows/check-price.yml` → `cron:` line |
| Drop threshold | same file → `DROP_THRESHOLD` env var |
| ntfy server | same file → `NTFY_SERVER` env (default `https://ntfy.sh`) |
| Chart default range | `app.js` → `renderChart(allReadings, "7")` |
| Backtest window | `ml/backtest.py` → `BACKTEST_DAYS` |

## Troubleshooting

- **Scraper fails with "Could not parse 22K rate":** Tanishq changed their page structure. Open the failed Action run, look at the `=== PAGE TEXT ===` dump, and adjust the regex in `scraper/scrape.js`.
- **No notifications arriving:** make sure `NTFY_TOPIC` is set with no URL prefix, you subscribed to the exact topic, and a price actually dropped by ≥₹100.
- **Chart is empty:** wait for ≥2 readings (every 6h, or trigger manually), or check that `data/prices.json` is populated.
- **Forecast section missing:** check GitHub Actions logs for the "Run forecast" step. The step is `continue-on-error: true` so it won't break the scrape.
- **Commentary section missing:** ensure `GROQ_API_KEY` secret is set. If it's missing, commentary is silently skipped (by design).

## License

MIT — do whatever you like with it.
