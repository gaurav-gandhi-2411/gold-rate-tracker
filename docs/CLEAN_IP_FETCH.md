# Φ25 Clean-IP Tanishq Fetch — Owner Setup Guide (RETIRED 2026-07-16)

> **Retired.** The Worker ran 2026-06-13–2026-06-25, then went silent: Tanishq extended
> its Cloudflare bot-protection challenge to Workers egress (confirmed via direct
> reproduction — `403` + "Just a moment..." challenge on the exact production fetch from
> Cloudflare's edge). Cron, deploy, and the PAT were all healthy; the block was on
> Tanishq's side and not fixable client-side. The Worker, its PAT, the
> `repository_dispatch` CI trigger, and `scraper/dispatch-validate.js` were removed. See
> [docs/RUNBOOK.md](RUNBOOK.md) for the current (CI-Playwright-only) architecture. This
> file is kept as a historical record of the setup steps, not a live procedure.

A Cloudflare Worker running from a non-Indian edge IP fetches the Tanishq gold-rate page
every ~3h and triggers the existing GitHub Actions pipeline via `repository_dispatch`.

## Why this exists

GitHub Actions runner IPs (and Indian IPs) get hard-403'd by Tanishq's Cloudflare WAF.
The in-CI Playwright scrape carries 55–87% of successful runs. A CF Worker probe from a
Singapore PoP (2026-06-11) returned HTTP 200 with valid prices in ~400ms.

The Worker restores near-real-time price capture without changing any CI logic:
the entire existing pipeline (IBJA, inference, drop-alert, commit, feature store) is
unchanged. The in-CI Playwright scrape is kept as a fallback for any cycle the Worker
misses.

## Cron timing and the double-fire question

| Trigger | Cron | UTC fire times |
|---------|------|----------------|
| CI schedule | `0 */3 * * *` | 0:00, 3:00, 6:00 … 21:00 |
| Worker | `30 */3 * * *` | 0:30, 3:30, 6:30 … 21:30 |

The 30-minute offset prevents concurrent git pushes. Both triggers use the same
`check-price` GitHub Actions concurrency group (`cancel-in-progress: false`), so if they
do overlap, the second run queues and starts after the first commits.

**Double-fire / drop-alert spam risk:** None. The second run (whichever arrives later)
reads `prices.json` with the first run's newly written price as the `lastEntry`. The
`delta` against that price is 0 (same price, same cycle) so no drop alert fires. The
`update-and-notify.js` append + compare logic is idempotent across close-together runs.

## Step 1 — Create a fine-grained GitHub PAT

> **Owner action required.** Do not store the token in the repo.

1. Go to **GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Set **Token name**: `gold-rate-tracker-worker-dispatch`
3. Set **Expiration**: 1 year (set a calendar reminder to rotate before expiry).
4. Set **Resource owner**: your account (`gaurav-gandhi-2411`).
5. Set **Repository access**: Only selected repositories → `gold-rate-tracker`.
6. Set **Permissions**:
   - **Actions** → Access: **Read and write**
   - All other permissions: **No access**
7. Click **Generate token** and copy the value immediately (shown only once).

This is the minimum scope required for `POST /repos/{owner}/{repo}/dispatches`.

## Step 2 — Set the token as a wrangler secret

Run from the repo root (you must be logged in with `wrangler login` first):

```sh
cd worker
wrangler secret put GITHUB_TOKEN
# Paste the PAT value when prompted. It is stored encrypted in Cloudflare and
# never written to disk or the repo.
```

## Step 3 — Deploy the Worker

```sh
cd worker
wrangler deploy
```

Expected output includes:
- `Uploaded gold-rate-tanishq-worker`
- `Published gold-rate-tanishq-worker`
- `schedule: 30 */3 * * *`

The Worker is now live. Cloudflare will run it at :30 past every 3h mark (UTC).

## Step 4 — Verify a live dispatch lands

After the next scheduled Worker fire (at most 30 min after deploy):

1. Go to **GitHub → Actions → Check Gold Price** and confirm a new run was triggered
   with **Event: repository_dispatch**.
2. Expand the **Scrape Tanishq and update prices.json** step and confirm it shows
   `[dispatch] 22k=... 24k=... 18k=... validated OK — piping to update-and-notify`.
3. After the run completes, confirm `data/prices.json` has a new entry with the current
   prices and `"source": "tanishq-price-dispatch"` (or equivalent).
4. Check `fetch_method=` in the logs — the dispatch branch does not call the scraper,
   so you should NOT see a `fetch_method=requests` or `fetch_method=playwright` line.

## Step 5 — Rotation

When the PAT approaches expiry:
1. Generate a new fine-grained PAT with identical scope (Step 1).
2. Run `wrangler secret put GITHUB_TOKEN` again with the new value.
3. The old token is overwritten; no Worker redeployment needed.

## Non-owner notes

- `worker/index.js` — the Worker source; parse/validate logic is a copy of
  `scraper/scrape.js` constants and functions (sync contract: keep identical on both sides).
- `worker/wrangler.toml` — cron config + non-secret vars; safe to commit.
- `worker/test_worker.mjs` — 11 unit tests; run with `node --test test_worker.mjs`.
- No secret values are stored anywhere in the repository.
