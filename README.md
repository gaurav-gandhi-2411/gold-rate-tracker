# Gold Rate Tracker

[![Check Gold Price](https://github.com/gaurav-gandhi-2411/gold-rate-tracker/actions/workflows/check-price.yml/badge.svg)](https://github.com/gaurav-gandhi-2411/gold-rate-tracker/actions/workflows/check-price.yml)
[![Lint](https://github.com/gaurav-gandhi-2411/gold-rate-tracker/actions/workflows/lint.yml/badge.svg)](https://github.com/gaurav-gandhi-2411/gold-rate-tracker/actions/workflows/lint.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A free, zero-infrastructure app that scrapes the Tanishq gold rate page every 6 hours, answers "Should I buy today?" with a buyer-facing verdict, pushes a phone notification when the 22K rate drops by ₹100 or more, and forecasts the next reading with a LightGBM ensemble.

**Live site:** https://gaurav-gandhi-2411.github.io/gold-rate-tracker/

**Stack:** GitHub Actions (cron scraper + ML pipeline) · GitHub Pages (PWA) · LightGBM · Groq LLM · ntfy.sh · ₹0/month

## How it works

- **Scrape → forecast → commit:** GitHub Actions runs every 6h — Playwright scrapes Tanishq, LightGBM retrains on all data, Groq writes a 2-sentence commentary, results committed to `data/*.json`.
- **Static PWA:** `index.html` + `app.js` fetch those JSON files directly from the repo and render price, verdict, sparkline, and chart — no server needed.
- **Verdict logic:** 7-day slope ±₹100 threshold confirmed by a second signal (forecast direction or 30-day mean deviation) → three buckets: down / flat / up.

## Honesty

Model currently matches the naive baseline on MAE (LightGBM 1.010× naive at 65 real readings). The naive-blend safety net means the live forecast hedges toward "no change" until the model earns its weight. Verdict logic combines price slope, model direction, and 30-day average — it does not rely on the point-estimate alone. **Not financial advice.**

Quarterly model check scheduled at ~200 real readings (~2026-07-15). See [ADR 005](docs/adr/005-honest-baseline-reporting.md).

## Monitoring

Configured via [UptimeRobot](https://uptimerobot.com) free tier (create monitors yourself — no API key needed in this repo).

| URL | Monitor type | What it catches |
|-----|-------------|-----------------|
| `https://gaurav-gandhi-2411.github.io/gold-rate-tracker/` | HTTP(S) · 5 min interval | Site down / Pages outage |
| `https://gaurav-gandhi-2411.github.io/gold-rate-tracker/data/forecast.json` | Keyword · check for `predicted_22k` | Forecast pipeline silently broken (site up, data missing) |
| `https://gaurav-gandhi-2411.github.io/gold-rate-tracker/data/prices.json` | Keyword · check for `price_22k` | Scraper broken (prices file empty or malformed) |

**Alert channel:** use the same ntfy.sh topic as the in-pipeline alerts (`NTFY_TOPIC` secret). Set UptimeRobot alert contacts to send a webhook to `https://ntfy.sh/<your-topic>` with `Content-Type: text/plain`.

> UptimeRobot free tier does not support "response older than N hours" checks natively. The in-pipeline staleness monitors (check-price.yml steps "Check data staleness" and "Forecast staleness monitor") cover the age dimension; UptimeRobot covers availability and schema.

## Error tracking

Sentry browser SDK is wired in `index.html` (CDN, v7). It captures JS exceptions and failed JSON fetches with context (which URL failed, HTTP status).

**To activate:** create a free Sentry project at [sentry.io](https://sentry.io) → **Settings → Client Keys → DSN**. Replace the placeholder in `index.html`:

```html
<!-- Find this line in index.html and replace the DSN value: -->
dsn: "https://PLACEHOLDER@o000000.ingest.sentry.io/0000000",
```

Until you replace the DSN, errors are logged to the browser console only (Sentry init fails silently when the DSN is invalid, and all Sentry calls are guarded by `typeof Sentry !== 'undefined'`).

## Setup (~15 minutes)

1. Fork / create a public repo. Upload all files.
2. **Settings → Secrets → Actions → New secret:**
   - `NTFY_TOPIC` — your OWN ntfy.sh topic. Treat it like a password: pick a long random string nobody can guess (e.g. `gold-<yourname>-<random-suffix>`). Anyone who knows the topic can publish notifications to it.
   - `GROQ_API_KEY` — from [console.groq.com](https://console.groq.com) (free tier)
3. **Actions → Check Gold Price → Run workflow** (manual trigger, wait ~2 min).
4. **Settings → Pages → Deploy from branch → master → / (root).**
5. Install the PWA: iOS Safari → Share → Add to Home Screen · Android Chrome → Install app.
6. Subscribe ntfy: install the ntfy app → **+** → enter your topic.

### Seed historical data (first time only)

```bash
pip install -r ml/requirements.txt
python ml/seed_history.py
git add data/history_seed.json && git commit -m "chore: add seed data"
```

## Dev setup

```bash
# MLflow (local, port 5001)
docker compose up -d
# http://localhost:5001 — experiment "gold-rate-training"

# Training venv (RTX 3070 / CUDA 12.4)
.\scripts\win\setup-train.ps1
venv-train\Scripts\Activate.ps1
python -m ml.training
```

See [docs/RUNBOOK.md](docs/RUNBOOK.md) for rollback, CI debugging, and staleness alert procedures.

## Tweaking

| What | Where |
|------|-------|
| Cron schedule | `.github/workflows/check-price.yml` → `cron:` |
| Drop threshold | same file → `DROP_THRESHOLD` env var |
| Chart default range | `app.js` → `renderChart(allReadings, "7")` |
| Model hyperparams | `configs/model/lightgbm.yaml` |

## Troubleshooting

- **Scraper fails:** Tanishq changed HTML. Find the new selector in the `=== PAGE TEXT ===` dump and update `scraper/scrape.js`.
- **No notifications:** check `NTFY_TOPIC` has no URL prefix, you subscribed to the exact topic, and price actually dropped ≥₹100.
- **Forecast missing:** check "Run forecast" step in Actions logs. It's `continue-on-error: true` so scraping still works.
- **Commentary missing:** ensure `GROQ_API_KEY` secret is set.
- **OG image stale:** share via [Twitter Card Validator](https://cards-dev.twitter.com/validator) or [Facebook Sharing Debugger](https://developers.facebook.com/tools/debug/) to force a re-fetch.

## Design decisions

- [ADR 001](docs/adr/001-local-train-ci-inference.md) — Train locally, serve via ONNX in CI
- [ADR 002](docs/adr/002-no-dagster.md) — No Dagster: Python scripts + Makefile
- [ADR 005](docs/adr/005-honest-baseline-reporting.md) — Always report when model loses to naive

## License

[MIT](LICENSE)
