# For GG: things only your account or decision can clear

Kept current by CC. Last updated 2026-10-09 (CC). The weekly one-screen model note is `docs/MODEL_STATUS.md`.
Short on purpose: one check, two small items, and a few optional items.

## 1. One check (30 seconds; the analytics fix is live)

**Open https://gold-rate-tracker.goatcounter.com tomorrow; each visit should count once, and referrers should
show where visitors came from.** (Direct visits and the installed app have no referrer; installed-app launches
show as a separate path starting `/app`.)
- A browser that already visited needs about **3 reloads** before it runs the new code (the app's service worker).
- Your Edge has "Send Do Not Track requests" on. The site now honours that again by decision, so your own
  Edge will NOT be counted. To test, turn it off under Edge Settings > Privacy > "Send Do Not Track requests"
  or use a browser without it.

## 2. Two small items (about 2 minutes, nothing is blocked on them)

1. **Encrypt one more file (optional): https://github.com/gaurav-gandhi-2411/gold-rate-tracker/pull/2628**
   It is the metrics-history file (the Tanishq shop reading per day; the page never reads it). CC ran the
   migration and checked the PR: one encrypted file plus its checksum, the plaintext removed, `verify --all`
   green. It is a draft, so click **Ready for review**, then **Squash and merge**. Rollback if ever needed:
   revert the PR (the plaintext stays in git history, the producers re-encrypt on their next run).
2. **Required check `scraper-dependency-guard`: it is not set yet.** At 19:00 UTC on 2026-10-09 master's required
   checks were only `lint` and `pwa-js` (read from the GitHub branch-protection API), so the re-add did not save.
   GitHub > Settings > Branches > master > Edit > required status checks > add `scraper-dependency-guard` > Save.
   CC proved it first: on real bot PRs (#2625, #2626) the dispatched guard posts a success status on the head
   commit and they merged on their own; a normal PR touching `scraper/package.json` without a proof run still
   fails it (PR #2471). If a bot PR ever blocks, remove the check again the same way and tell CC.

## 3. Optional (nothing is blocked on these)

| # | Item | Exact steps |
|---|---|---|
| 1 | Check the Cloudflare Worker secrets for stale names | In `worker-deadman/` run `npx wrangler secret list`. The Worker's code expects `NTFY_TOPIC`, `TRIGGER_TOKEN`, `GITHUB_PR_HEALTH_PAT`, and (parked, optional) `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Delete any other name from the list. |
| 2 | Ask IBJA for written permission to publish its rate history | ADR 059: IBJA's API terms say publishing IBJA rates or history on a website needs written permission. Send IBJA a short email (the contact is on https://ibjarates.com): who you are, the product (free, no ads, plain-language gold tracker), that the page shows the latest official fix and a next-fix range built from it, and ask in writing whether this and a history chart are allowed and on what terms. The raw IBJA series is now encrypted in the repo (after click 2). |
| 3 | The 8 contested Hindi strings | All Hindi wording is LLM consensus, not native review, and it is live. `docs/HINDI_CONTESTED.md`, "START HERE" table: the 8 strings that most need a native speaker. Reply with the choices (or "keep") and CC applies them. |
