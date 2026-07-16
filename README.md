# Gold Rate Tracker

[![Check Gold Price](https://github.com/gaurav-gandhi-2411/gold-rate-tracker/actions/workflows/check-price.yml/badge.svg)](https://github.com/gaurav-gandhi-2411/gold-rate-tracker/actions/workflows/check-price.yml)
[![Lint](https://github.com/gaurav-gandhi-2411/gold-rate-tracker/actions/workflows/lint.yml/badge.svg)](https://github.com/gaurav-gandhi-2411/gold-rate-tracker/actions/workflows/lint.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A free, $0/month gold-price tracker for Indian retail buyers (Tanishq 22K). It scrapes the live retail rate every 3 hours, shows today's price with an honest range, sends phone alerts when prices move, and **deliberately does not pretend to predict tomorrow's price** — because, measured honestly, no model we've tested beats "today's price, held flat."

**Live site:** https://gaurav-gandhi-2411.github.io/gold-rate-tracker/

**Stack:** GitHub Actions (3h cron + ML pipeline) · GitHub Pages (PWA) · Chronos-Bolt-Tiny · Groq LLM · ntfy.sh — **₹0 / month**

## What it actually claims (and what it doesn't)

This project is built around **honest-baseline reporting** ([ADR 005](docs/adr/005-honest-baseline-reporting.md)):

- ✅ **Today's price**, scraped live, with a freshness label and a clearly-marked fallback estimate when the live scrape is briefly unavailable.
- ✅ A **5-day range** ("prices have typically swung ±X over 5 days") — an illustrative band, not a forecast.
- ✅ A plain-language **"is it a good time to buy?"** read based on the *recent 7-day trend* (a description of what already happened, never a prediction).
- ❌ **No price prediction. No "% chance up". No buy/sell call.** We test next-day and 2-day direction models every week; none beats the base rate ("gold rises most days") by a statistically significant margin, so the directional signal stays **off**. See [docs/DIRECTION_SIGNAL_STATUS.md](docs/DIRECTION_SIGNAL_STATUS.md) for the live numbers and [ADR 012](docs/adr/012-naive-headline-chronos-companion.md) / [ADR 019](docs/adr/019-direction-signal-below-base-rate.md) for why.

The headline forecast is a **naive flat-hold** (predict = today's price). The app reports the model's accuracy *next to* the naive baseline's, every time — it never cherry-picks.

## How it works

```
GitHub Actions (check-price.yml, every 3h)
   └─ scrape in-CI (plain fetch → Playwright fallback)
                          ▼
   prices.json → inference (naive flat-hold + IBJA-calibrated
                 fallback floor + Chronos directional companion,
                 kept DARK) → forecast.json
                          ▼
   ntfy alerts (price moves, weekly digest, staleness)
                          ▼
   commit data/*.json → GitHub Pages PWA renders it
```

- **Two-layer scrape resilience.** The scheduled CI run scrapes in-process (plain `fetch` → Playwright fallback). If that misses, the page shows an IBJA-calibrated estimate (and, failing that, the last confirmed price with a clear "may be outdated" banner) — **the user never sees a dead price.** (A Cloudflare Worker clean-IP fetch path ran 2026-06-13–2026-06-25 but was retired after Tanishq extended its bot-blocking to Workers egress; see [docs/RUNBOOK.md](docs/RUNBOOK.md).)
- **Static PWA.** `index.html` + `app.js` fetch `data/*.json` straight from the repo and render price, verdict, sparkline, and chart — no server.

## Notifications (bring your own ntfy topic)

Alerts are delivered via [ntfy.sh](https://ntfy.sh) — free, no account. **Pick your own topic and keep it private:** anyone who knows a topic name can publish to it, so treat it like a password (a long random string, e.g. `gold-<yourname>-<16 random chars>`). Set it as the `NTFY_TOPIC` GitHub Actions secret and subscribe to it in the ntfy app.

Alert types: a price-move alert (describes the recent trend), a twice-daily digest, and a data-staleness warning if scraping stalls. All copy is plain-language and ASCII-safe.

## Setup (~15 minutes)

1. Fork / create a public repo and upload all files.
2. **Settings → Secrets and variables → Actions → New repository secret:**
   - `NTFY_TOPIC` — your OWN ntfy.sh topic (treat like a password; long & random).
   - `GROQ_API_KEY` — free tier from [console.groq.com](https://console.groq.com) (optional; without it the plain-language commentary is skipped, nothing else breaks).
3. **Actions → Check Gold Price → Run workflow** (manual trigger; wait ~2 min).
4. **Settings → Pages → Deploy from branch → `master` → `/` (root).**
5. Install the PWA: iOS Safari → Share → Add to Home Screen · Android Chrome → Install app.
6. Subscribe to alerts: install the ntfy app → **+** → enter your topic.

## Honesty & methodology

- Headline = **naive flat-hold**; on the walk-forward backtest no model beats it on magnitude (it's ~14% *worse*, p≈0.003), so the baseline *is* the production forecast ([ADR 012](docs/adr/012-naive-headline-chronos-companion.md)).
- Direction signal is **off** at every horizon — see the auto-measured [DIRECTION_SIGNAL_STATUS.md](docs/DIRECTION_SIGNAL_STATUS.md) (re-run weekly).
- **Not financial advice. Rates are indicative.**

## Repo layout

| Path | What |
|------|------|
| `index.html`, `app.js`, `service-worker.js` | The PWA (what users see) |
| `scraper/` | Tanishq scrape (Node) |
| `ml/` | Inference, calibration, notifications, the direction-eval harness |
| `data/` | Committed price/forecast/eval JSON the PWA reads |
| `.github/workflows/` | `check-price.yml` (3h loop), `lint.yml`, `eval-direction.yml`, canary |
| `docs/` | RUNBOOK, ADRs, CURRENT_STATE, DIRECTION_SIGNAL_STATUS |

## Troubleshooting

- **Prices look stale:** the page banner will say so. Check the latest **Check Gold Price** run in Actions; the scrape step is `continue-on-error`, so a miss is logged as a run annotation, not a hard failure.
- **No notifications:** confirm `NTFY_TOPIC` has no URL prefix, you subscribed to the *exact* topic, and a price move actually occurred.
- **Commentary missing:** set `GROQ_API_KEY` (optional).
- **Scraper DOM canary issue opened:** Tanishq's runner-IP block can fail the live canary even when the page is fine — check recent `prices.json` entries first (a stretch of missed scrapes points at an IP block, not a DOM change) before assuming the selector changed. See [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Design decisions (ADRs)

- [ADR 005](docs/adr/005-honest-baseline-reporting.md) — always report when the model loses to naive
- [ADR 012](docs/adr/012-naive-headline-chronos-companion.md) — naive flat-hold headline, Chronos as a (dark) companion
- [ADR 019](docs/adr/019-direction-signal-below-base-rate.md) — the direction signal doesn't beat the base rate; ship nothing
- [docs/RUNBOOK.md](docs/RUNBOOK.md) — rollback, CI debugging, staleness

## License

[MIT](LICENSE)
