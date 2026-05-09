# Gold Rate Tracker

A free, open-source app that scrapes the [Tanishq gold rate page](https://www.tanishq.co.in/gold-rate.html?lang=en_IN) every 6 hours, charts the trend, and pushes a phone notification when the **22K** rate **drops by ₹100 or more**. Increases stay silent.

- **Frontend:** Progressive Web App (installs to iOS & Android home screen)
- **Backend:** GitHub Actions cron job (free)
- **Storage:** A JSON file in this repo
- **Notifications:** [ntfy.sh](https://ntfy.sh) — free, no account needed
- **Hosting:** GitHub Pages (free)
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
                                  └─────┬────┘  └──────┬───────┘
                                        │              │
                                        ▼              ▼
                                  ┌──────────┐  ┌──────────────┐
                                  │ Phone    │  │ PWA on       │
                                  │ (ntfy app│  │ GitHub Pages │
                                  └──────────┘  └──────────────┘
```

## Setup (≈15 minutes)

### 1. Create the repo

1. Go to [github.com/new](https://github.com/new), make a new **public** repo (public keeps GitHub Actions and Pages free and unlimited). Name suggestion: `gold-rate-tracker`.
2. Upload all files from this bundle. Easiest path: on your new repo's empty page, click **uploading an existing file**, then drag the entire folder contents in. Commit.

### 2. Pick your ntfy topic

Topics on ntfy.sh are public — anyone who guesses the name can read or send to it. Treat the topic like a password.

Pick something unguessable, e.g. `gold-gaurav-7k2x9p4r`. Keep it written down.

### 3. Add the topic as a GitHub secret

1. In your new repo, go to **Settings → Secrets and variables → Actions → New repository secret**.
2. Name: `NTFY_TOPIC`
3. Value: your topic name from step 2 (just the topic name, no URL prefix)
4. Save.

### 4. Trigger the workflow once manually

The cron only fires every 6 hours. To get a first reading immediately:

1. Go to **Actions** tab on your repo.
2. If GitHub asks "I understand my workflows, go ahead and enable them" — click it.
3. Click **Check Gold Price** in the left sidebar → **Run workflow** → **Run workflow**.
4. Wait ~1 minute. The run should go green and `data/prices.json` will have one entry.

If the run fails, open it and check the logs — the scraper dumps page text on failure so we can fix the parser.

### 5. Enable GitHub Pages

1. **Settings → Pages**.
2. **Source:** Deploy from a branch.
3. **Branch:** `main`, folder: `/ (root)`.
4. Save. After a minute, your site is live at `https://gaurav-gandhi-2411.github.io/gold-rate-tracker/`.

### 6. Subscribe on your phone

1. Install the **ntfy** app:
   - iOS: [App Store](https://apps.apple.com/us/app/ntfy/id1625396347)
   - Android: [Play Store](https://play.google.com/store/apps/details?id=io.heckel.ntfy) or [F-Droid](https://f-droid.org/en/packages/io.heckel.ntfy/)
2. Open the app → **+** (add subscription) → enter your topic name from step 2 → Subscribe.
3. Test it: from your phone or any browser, hit `https://ntfy.sh/<your-topic>` with this curl-like POST, or just use the workflow's "Run workflow" button when there is no price drop — you can also test by editing `data/prices.json` to fake a higher previous value.

### 7. Install the PWA on your phone

- **iOS Safari:** open your Pages URL → tap the **Share** icon → **Add to Home Screen**.
- **Android Chrome:** open your Pages URL → menu → **Install app** (or **Add to Home Screen**).

You now have a Gold app icon on your home screen that opens fullscreen.

## Tweaking

| What | Where |
|---|---|
| Cron schedule | `.github/workflows/check-price.yml` → `cron:` line |
| Drop threshold | same file → `DROP_THRESHOLD` env var |
| ntfy server (use your own) | same file → set `NTFY_SERVER` env (default `https://ntfy.sh`) |
| Notify on rises too | `scraper/update-and-notify.js` → add a branch for `delta > 0` |
| Chart default range | `app.js` → bottom of file, `renderChart(allReadings, "7")` |
| City | scraper grabs whatever the page renders. To force a city, edit `scraper/scrape.js` to interact with the city dropdown before extracting text. |

## Sharing

Anyone can fork this repo, follow steps 2–7, and have their own private notifications with their own topic. The PWA is self-serve — point friends at your Pages URL and they can install it (they'll get the same chart, but for notifications they need their own fork because the topic is per-user).

## Troubleshooting

- **Scraper fails with "Could not parse 22K rate":** Tanishq changed their page structure. Open the failed Action run, look at the `=== PAGE TEXT ===` dump, and adjust the regex in `scraper/scrape.js`.
- **No notifications arriving:** make sure (a) the `NTFY_TOPIC` secret is set with no URL prefix, (b) you subscribed to that exact topic name on the ntfy app, (c) a price actually dropped by ≥₹100.
- **Chart is empty:** wait for ≥2 readings (every 6h, or trigger manually), or check that `data/prices.json` is populated.

## License

MIT — do whatever you like with it.
