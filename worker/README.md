# Cloudflare Worker — clean-IP Tanishq scrape

This Worker (`gold-rate-tanishq-worker`) fetches the Tanishq gold-rate page from a clean
Cloudflare edge IP every 3 hours, validates the rates, and fires a GitHub
`repository_dispatch` event that triggers `check-price.yml`. It exists because Tanishq's
CDN blocks GitHub Actions runner IPs, so the in-CI scrape misses often; the Worker's IP is
not blocked.

- `index.js` — `scheduled()` handler: fetch → CF-challenge check → parse → validate → POST
  `/repos/<owner>/<repo>/dispatches`. Never throws (a CF cron must not throw).
- `wrangler.toml` — cron (`30 */3 * * *`) + non-secret vars (`GITHUB_OWNER`, `GITHUB_REPO`).
- `test_worker.mjs` — unit tests (mock fetch, no live network).

## ⏰ TIME-BOMB: the GitHub token expires 2026-09-11

The Worker authenticates to GitHub with a **fine-grained Personal Access Token** stored as
the Worker secret `GITHUB_TOKEN`. **It expires 2026-09-11.** When it expires the Worker keeps
running but every dispatch POST returns `401 Bad credentials` (visible in `wrangler tail`),
so the clean-IP path silently stops. **The app does NOT break** (see "If the Worker dies"
below) — but the scrape gap-rate rises until the token is renewed.

### The single renewal step (do this before 2026-09-11)

1. **Mint/extend the token.** GitHub → *Settings → Developer settings → Fine-grained
   personal access tokens*. Either **Regenerate** the existing `gold-rate-tanishq-worker`
   token with a new expiration, or create a new one with:
   - **Repository access:** Only select repositories → `gold-rate-tracker`
   - **Permissions → Repository permissions → Contents: Read and write**
     (this is what `repository_dispatch` requires; Metadata: Read is added automatically)
   - **Expiration:** the longest your org allows (or set a calendar reminder to repeat this).
2. **Copy** the new token (shown once).
3. **Re-put the Worker secret.** From this `worker/` directory:
   ```bash
   npx wrangler secret put GITHUB_TOKEN
   # paste the new token when prompted
   ```
   (Or: Cloudflare dashboard → Workers & Pages → `gold-rate-tanishq-worker` → Settings →
   Variables and Secrets → `GITHUB_TOKEN` → Edit.)
4. **Verify delivery (not just the deploy).** Run `npx wrangler tail` and wait for the next
   `:30` cron, or trigger manually; a healthy run logs `[worker] dispatched: 22k=<price>`.
   Then confirm a fresh **`repository_dispatch`** run shows up under the repo's Actions tab,
   and that `data/prices.json`'s newest entry is tagged `"source": "repository_dispatch"`.

That's the whole maintenance task. Nothing else about the Worker needs touching.

## Deploy / redeploy the Worker code

```bash
cd worker
npx wrangler deploy        # publishes index.js + wrangler.toml
npx wrangler tail          # live logs
node --test test_worker.mjs  # run unit tests
```

The three secrets/vars the Worker needs: `GITHUB_TOKEN` (secret, above), and
`GITHUB_OWNER` / `GITHUB_REPO` (non-secret, in `wrangler.toml`).

## If the Worker dies (expired token, deleted, CF outage) — degraded, NOT broken

The Worker is a *latency/coverage optimisation*, not a single point of failure. With it gone:

1. **Scheduled CI still scrapes.** `check-price.yml` runs every 3h on its own cron; when the
   event is not a dispatch it runs the in-CI scraper (`scrape.js`: plain `fetch` → Playwright
   fallback). Playwright succeeds a good fraction of the time even on runner IPs.
2. **Stale → estimate floor.** If a scrape misses and `prices.json` goes >8h stale, inference
   serves an **IBJA-calibrated estimate** (`price_source=ibja_calibrated`, shown as an
   "approximate / from IBJA benchmark" banner) when a recent IBJA rate exists.
3. **Worst case is honest, not dead.** If even that isn't available, the PWA shows the last
   confirmed price with a "live update unavailable — showing last confirmed price from …"
   banner, and **T9** fires a one-per-day "data stale" ntfy alert.

So a dead Worker means a higher scrape gap-rate (roughly the pre-Worker ~1-in-3), never a
wrong or dead price. Renew the token to restore full coverage.

## SYNC CONTRACT

`parseGoldRates`, `isCFChallengeHtml`, `validate`, and the four validation constants
(`RANGE_MIN`, `RANGE_MAX`, the two ratio bounds) are **copied verbatim** across
`worker/index.js`, `scraper/scrape.js`, and `scraper/dispatch-validate.js` to keep the
Worker a build-step-free single file. If you change a threshold or the parse logic, update
**all three** together.
