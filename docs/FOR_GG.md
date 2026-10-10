# For GG: things only your account or decision can clear

Kept current by CC. Last updated 2026-10-10 (CC). The weekly one-screen model note is `docs/MODEL_STATUS.md`.
Short on purpose: one check, one timed click, and a few optional items.

## 1. One check (30 seconds; the analytics fix is live)

**Open https://gold-rate-tracker.goatcounter.com tomorrow; each visit should count once, and referrers should
show where visitors came from.** (Direct visits and the installed app have no referrer; installed-app launches
show as a separate path starting `/app`.)
- A browser that already visited needs about **3 reloads** before it runs the new code (the app's service worker).
- Your Edge has "Send Do Not Track requests" on. The site now honours that again by decision, so your own
  Edge will NOT be counted. To test, turn it off under Edge Settings > Privacy > "Send Do Not Track requests"
  or use a browser without it.

## 2. One timed click (about 1 minute; please merge by 17:30 IST on 2026-10-10)

**Encrypt one more file: https://github.com/gaurav-gandhi-2411/gold-rate-tracker/pull/2651**
It is the metrics-history file (the Tanishq shop reading per day; the page never reads it). CC ran the
migration at 06:14 UTC on 2026-10-10 and checked the PR: one encrypted file plus its checksum, the plaintext
removed, `verify --all` green, all required checks green. It is a draft, so click **Ready for review**, then
**Squash and merge**. It must be merged soon because a producer run that changes the file first makes it
conflict: the file is rewritten about once a day at 01:40-02:15 UTC, and 1 gap in 10 is under 6 hours. If it
does show a conflict, ignore it; CC redoes it once more tomorrow right after the morning write. Rollback if
ever needed: revert the PR (the plaintext stays in git history, the producers re-encrypt on their next run).
When this is merged, delete this section (CC does it after checking the next producer run).

*Done, no action:* the required check `scraper-dependency-guard` is set (read from the branch-protection API
on 2026-10-10) and five real bot PRs (#2645 to #2649) merged on their own under it. If a bot PR ever blocks,
remove the check under GitHub > Settings > Branches > master > Edit > required status checks, and tell CC.

## 3. Optional (nothing is blocked on these)

| # | Item | Exact steps |
|---|---|---|
| 1 | Check the Cloudflare Worker secrets for stale names | In `worker-deadman/` run `npx wrangler secret list`. The Worker's code expects `NTFY_TOPIC`, `TRIGGER_TOKEN`, `GITHUB_PR_HEALTH_PAT`, and (parked, optional) `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Delete any other name from the list. |
| 2 | Ask IBJA for written permission to publish its rate history | ADR 059: IBJA's API terms say publishing IBJA rates or history on a website needs written permission. Send IBJA a short email (the contact is on https://ibjarates.com): who you are, the product (free, no ads, plain-language gold tracker), that the page shows the latest official fix and a next-fix range built from it, and ask in writing whether this and a history chart are allowed and on what terms. The raw IBJA series is now encrypted in the repo (after the click in section 2). |
| 3 | The 8 contested Hindi strings | All Hindi wording is LLM consensus, not native review, and it is live. `docs/HINDI_CONTESTED.md`, "START HERE" table: the 8 strings that most need a native speaker. Reply with the choices (or "keep") and CC applies them. |
