# For GG: things only your account or decision can clear

Kept current by CC. Last updated 2026-10-09 (CC). The weekly one-screen model note is `docs/MODEL_STATUS.md`.
Short on purpose: two clicks, one check, and a few optional items.

## 1. Two clicks (about 2 minutes), in this order

Both are green and ready; both need you only because the merge gate refuses to let CC merge workflow files
or a 10,000-line data move. Open each, click **Squash and merge**, confirm.

1. **Restore data sync: https://github.com/gaurav-gandhi-2411/gold-rate-tracker/pull/2593**
   Data sync has been blocked since about 07:58 UTC (the new required check never reports on the bot's
   branches; #2588 was not enough, CC's mistake). This one makes the bot start that check and forward its
   result the way it already does for `lint`. Until it merges the site's prices and forecast do not
   update. CC confirms right after: the waiting bot PRs merge and the next price run succeeds.
2. **Encrypt the raw data: https://github.com/gaurav-gandhi-2411/gold-rate-tracker/pull/2596**
   The one-time encryption CC ran today (19 files, every one verified). Rollback if ever needed: revert
   the PR; the plaintext is in git history. After the click CC checks that three price runs say
   "decrypted" and that the live site is unchanged.

If you would rather unblock sync at once: untick `scraper-dependency-guard` under Settings > Branches >
master for now and re-tick it after #2593 merges.

## 2. One check (30 seconds)

**Open https://gold-rate-tracker.goatcounter.com and confirm a visit appears.**

What CC found: your Edge has "Send Do Not Track requests" switched on, and the page used to send nothing in
that case. That is fixed and live (PR #2591). Two things to know before you look:
- The page is cached by the app's service worker: a browser that already visited needs about **3 reloads**
  before it runs the new code. Reload the live site 3 times, then check the dashboard.
- CC's own test visits from this laptop (same address and browser name as yours) were counted around
  10:50 to 11:20 UTC today. So look for the total page views to go UP after your reloads, not for "a" visit.
Also confirm under Settings > Data collection that individual pageviews, User-Agent, screen size and
location are OFF (the footer note says exactly that).

## 3. Optional (nothing is blocked on these)

| # | Item | Exact steps |
|---|---|---|
| 1 | Check the Cloudflare Worker secrets for stale names | In `worker-deadman/` run `npx wrangler secret list`. The Worker's code expects `NTFY_TOPIC`, `TRIGGER_TOKEN`, `GITHUB_PR_HEALTH_PAT`, and (parked, optional) `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Delete any other name from the list. |
| 2 | Ask IBJA for written permission to publish its rate history | ADR 059: IBJA's API terms say publishing IBJA rates or history on a website needs written permission. Send IBJA a short email (the contact is on https://ibjarates.com): who you are, the product (free, no ads, plain-language gold tracker), that the page shows the latest official fix and a next-fix range built from it, and ask in writing whether this and a history chart are allowed and on what terms. The raw IBJA series is now encrypted in the repo (after click 2). |
| 3 | The 8 contested Hindi strings | All Hindi wording is LLM consensus, not native review, and it is live. `docs/HINDI_CONTESTED.md`, "START HERE" table: the 8 strings that most need a native speaker. Reply with the choices (or "keep") and CC applies them. |
