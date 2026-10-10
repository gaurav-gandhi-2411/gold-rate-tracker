# For GG: things only your account or decision can clear

Kept current by CC. Last updated 2026-10-10 (CC, after the #2651 merge). The weekly one-screen model note is `docs/MODEL_STATUS.md`.
Short on purpose: one optional check and a few optional items. Nothing is blocked on GG.

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
| 4 | Visitor numbers in the weekly note | In GoatCounter: Settings > API, create a token with **read statistics permission only**, and add it as the repo secret `GOATCOUNTER_TOKEN` (Repo Settings > Secrets and variables > Actions). From then on `visitors-weekly.yml` sends you one private line each Monday: the 7-day visitor total and the top 3 sources by host name (sources seen fewer than 3 times are left out). Nothing is printed in the public run log or committed. Until the secret exists it does nothing and says so in one line. |
