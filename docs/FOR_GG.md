# For GG: things only your account or decision can clear

Kept current by CC. Last updated 2026-10-09 (CC). The weekly one-screen model note is `docs/MODEL_STATUS.md`.
Short on purpose: two small items, one check, and a few optional items.

## 1. One check (30 seconds; the analytics fix is live)

**Open https://gold-rate-tracker.goatcounter.com tomorrow; each visit should count once, and referrers should
show where visitors came from.** (Direct visits and the installed app have no referrer; installed-app launches
show as a separate path starting `/app`.)
- A browser that already visited needs about **3 reloads** before it runs the new code (the app's service worker).
- Your Edge has "Send Do Not Track requests" on. The site now honours that again by decision, so your own
  Edge will NOT be counted. To test, turn it off under Edge Settings > Privacy > "Send Do Not Track requests"
  or use a browser without it.

## 2. Two small items (about 2 minutes, nothing is blocked on them)

1. **Re-add the required check `scraper-dependency-guard`** (GitHub > Settings > Branches > master > required
   status checks > add `scraper-dependency-guard`). CC proved both halves first: on a real bot PR the dispatched
   guard posts a success status on the PR's head commit (PR #2579, 13:19 UTC today), and a normal PR that
   changes `scraper/package.json` without a proof run still fails it (PR #2471). A new test
   (`tests/test_required_checks_skippable.py`) now fails CI if any required check can be skipped by paths or
   by a skip-ci commit.
2. **Encrypt one more file (optional, when you have a minute): https://github.com/gaurav-gandhi-2411/gold-rate-tracker/pull/2610**
   Registers `data/metrics_history.json` (it holds the Tanishq shop reading per day; the page never reads it).
   Click **Squash and merge**. Then tell CC; CC runs the migration workflow and opens the second PR, which is the
   one other click (same as before: rollback = revert the PR, the plaintext stays in git history).

## 3. Optional (nothing is blocked on these)

| # | Item | Exact steps |
|---|---|---|
| 1 | Check the Cloudflare Worker secrets for stale names | In `worker-deadman/` run `npx wrangler secret list`. The Worker's code expects `NTFY_TOPIC`, `TRIGGER_TOKEN`, `GITHUB_PR_HEALTH_PAT`, and (parked, optional) `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Delete any other name from the list. |
| 2 | Ask IBJA for written permission to publish its rate history | ADR 059: IBJA's API terms say publishing IBJA rates or history on a website needs written permission. Send IBJA a short email (the contact is on https://ibjarates.com): who you are, the product (free, no ads, plain-language gold tracker), that the page shows the latest official fix and a next-fix range built from it, and ask in writing whether this and a history chart are allowed and on what terms. The raw IBJA series is now encrypted in the repo (after click 2). |
| 3 | The 8 contested Hindi strings | All Hindi wording is LLM consensus, not native review, and it is live. `docs/HINDI_CONTESTED.md`, "START HERE" table: the 8 strings that most need a native speaker. Reply with the choices (or "keep") and CC applies them. |
