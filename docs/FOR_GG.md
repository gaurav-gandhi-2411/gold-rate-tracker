# For GG: things only your account or decision can clear

Kept current by CC. Last updated 2026-10-19 (CC, visitor line verified end to end). The weekly one-screen model note is `docs/MODEL_STATUS.md`.
Short on purpose: nothing is blocked on GG. Dated token renewals are in section 0; one optional check and a few
optional items follow.

## 0. Dated token renewals (nothing to do until the date shown)

`token-expiry.yml` checks both tokens every day (scripts/check_token_expiry.py) and pages the private ntfy topic
once each at 30, 14 and 7 days before expiry, and at once if GitHub rejects one (HTTP 401). The expiry dates
below are read from GitHub's `github-authentication-token-expiration` header (VERIFIED 2026-10-15). History: the
old `CI_MERGE_PAT` expired on 2026-10-14 and the data sync (and so the page price) stopped for about 24 hours
before anyone knew; that is what this prevents.

| Token (repository secret) | Expires | Becomes active | What it does if it lapses |
|---|---|---|---|
| `CI_HEALTH_PAT` | **2026-12-10** | **2026-11-10** (you will also get the 30-day page) | `ci-health.yml` cannot read branch protection and pages "could not verify master's CI state" every hour. The site and the data sync keep working. |
| `CI_MERGE_PAT` | 2027-10-14 | 2027-09-14 | Every data sync fails with HTTP 401: the price and forecast stop refreshing. |

**Renewing `CI_HEALTH_PAT` (from 2026-11-10; it expires 2026-12-10):**
1. GitHub > Settings > Developer settings > Personal access tokens > Fine-grained tokens. Open the
   `CI_HEALTH_PAT` token (it shows its permissions and expiry) and regenerate it, or make a new one: resource
   owner your account, **only** repository `gold-rate-tracker`, permission **Administration: read** (that is all
   it needs: it reads branch protection) and Metadata read, expiration the longest you are comfortable with.
2. Repository > Settings > Secrets and variables > Actions > `CI_HEALTH_PAT` > Update secret > paste the token.
3. Check: `gh workflow run token-expiry.yml --ref master`; its log shows the new expiry date (or tell CC).

**Renewing `CI_MERGE_PAT` (from 2027-09-14; it expires 2027-10-14):** the same steps with the permissions Contents
read and write, Pull requests read and write, Actions read and write, Metadata read (INFERRED from what the sync
does; copy the token's own list when you open it), the secret `CI_MERGE_PAT`, and the check
`gh workflow run check-price.yml --ref master` (a data PR should merge by itself).

## 1. One check (30 seconds; the analytics fix is live)

**Open https://gold-rate-tracker.goatcounter.com when convenient; each visit should count once, and referrers should
show where visitors came from.** (Direct visits and the installed app have no referrer; installed-app launches
show as a separate path starting `/app`.)
- A browser that already visited needs about **3 reloads** before it runs the new code (the app's service worker).
- Your Edge has "Send Do Not Track requests" on. The site now honours that again by decision, so your own
  Edge will NOT be counted. To test, turn it off under Edge Settings > Privacy > "Send Do Not Track requests"
  or use a browser without it.

## 2. Optional (nothing is blocked on these)

| # | Item | Exact steps |
|---|---|---|
| 1 | Check the Cloudflare Worker secrets for stale names | In `worker-deadman/` run `npx wrangler secret list`. The Worker's code expects `NTFY_TOPIC`, `TRIGGER_TOKEN`, `GITHUB_PR_HEALTH_PAT`, and (parked, optional) `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Delete any other name from the list. |
| 2 | Ask IBJA for written permission to publish its rate history | ADR 059: IBJA's API terms say publishing IBJA rates or history on a website needs written permission. Send IBJA a short email (the contact is on https://ibjarates.com): who you are, the product (free, no ads, plain-language gold tracker), that the page shows the latest official fix and a next-fix range built from it, and ask in writing whether this and a history chart are allowed and on what terms. The raw IBJA series is now encrypted in the repo (the metrics-history migration, #2651, is merged). |
| 3 | The 8 contested Hindi strings | All Hindi wording is LLM consensus, not native review, and it is live. `docs/HINDI_CONTESTED.md`, "START HERE" table: the 8 strings that most need a native speaker. Reply with the choices (or "keep") and CC applies them. |
