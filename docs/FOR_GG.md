# For GG: things only your account or decision can clear

Kept current by CC. Nothing here is urgent unless marked URGENT. Last updated 2026-10-07 (CC).
Everything CC could do itself has been done; these are the items CC may not do (accounts, tokens,
settings, a model promotion) or could not do (a guardrail blocked it). Each has the exact steps.

## 1. Settings and accounts (about 15 minutes in total)

| # | Item | Exact steps |
|---|---|---|
| 1 | Make the scraper dependency guard a required check (after PR #2388 is merged) | GitHub > gold-rate-tracker > Settings > Branches > branch protection rule for `master` > "Require status checks to pass" > search `scraper-dependency-guard` > select it > Save. Until then the check reports but does not block. |
| 2 | Turn on visit counting (optional, only if you want usage numbers; the code is merged OFF) | 1. Open https://www.goatcounter.com/signup (free; no plan or payment step is shown). Account name = the site code (lowercase letters, digits, hyphens, e.g. `gold-rate-tracker`); optional site domain `gaurav-gandhi-2411.github.io`; email; password; the human check. Verify the email. Leave any "collect IP / User-Agent / location / screen size" setting off. 2. One small PR: in `analytics.js` set `const ANALYTICS_SITE_CODE = "<your code>";` and in `flags.js` set `analytics: true,`. 3. In the same PR bump `VERSION` in `service-worker.js`, change README ("No accounts, no ads, no analytics." to "No accounts and no ads. It counts visits with GoatCounter, a cookieless counter that keeps no name, cookie or IP address; the page says so in its footer.") and the SECURITY.md PWA row (see `docs/proposals/analytics-5c.md`, "To turn it on"), and update the two tests that pin the shipped-off state. 4. Verify: open the live site, then `https://<code>.goatcounter.com`; one visit and a `lang/en` event within a minute; no cookie set. Switch off: `analytics: false`. CC can do step 2-3 once the account exists: tell CC the site code. |
| 3 | Check the Cloudflare Worker secrets for stale names | In `worker-deadman/` run `npx wrangler secret list`. The Worker's code expects `NTFY_TOPIC`, `TRIGGER_TOKEN`, `GITHUB_PR_HEALTH_PAT`, and (parked, optional) `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Delete any other name from the list. In this repo no secret or workflow references the old `gold-rate-tracker-worker-dispatch` token (CC checked 2026-10-05: repo secrets are `CI_HEALTH_PAT`, `CI_MERGE_PAT`, `DATA_ENC_KEY`, `NTFY_TOPIC`); you already confirmed it is not in your token lists. |
| 4 | Ask IBJA for written permission to publish its rate history | ADR 059 ("IBJA terms findings"): IBJA's own API terms say publishing IBJA rates or history on a website needs written permission; the site's terms do not ban reuse but say reuse beyond fair use needs permission. Send IBJA a short email (the contact is on https://ibjarates.com): who you are, the product (free, no ads, plain-language gold tracker), that the page shows the latest official fix and a next-fix range built from it, and ask in writing whether this and a history chart are allowed and on what terms. Until the answer arrives CC keeps the raw IBJA series out of public files (see PR #2075). |

## 2. Hindi review (about 5 minutes, optional)

All Hindi wording is LLM consensus, not native review. After PR #2397 merges, open
`docs/HINDI_CONTESTED.md`: the 8 strings that most need a native speaker are in the "START HERE"
table at the top, each with the English source and the competing wordings. Reply with the choices
(or "keep") and CC applies them. Direction, track-record and coverage sentences fall back to English
on purpose until reviewed.

## 3. Decisions that are yours

| # | Decision | Context |
|---|---|---|
| 1 | Any model promotion (kept for you) | None is pending today. The next possible one is the ADR 066 hourly world-price check on 2026-10-16 (CC runs it exactly as pre-registered and prepares a PR if it passes; the merge stays yours). |
| 2 | Encryption follow-ups (PR #2075) | (a) Whether `backtest.json`, `drift_metrics.json` and `metrics_history.json` may be rebuilt on scores only (CC is doing this as a separate PR; the visible numbers must not change). (b) A public, non-invertible source for the page_v2 range card before that flag is turned on. |
| 3 | Guardrail blocks CC hit | On 2026-10-07 the auto-mode classifier refused CC reading/editing `merge_gate.py` to add PR-scoped size waivers, even with your written instruction. CC therefore splits oversized PRs to fit the gate instead of using waivers. If you ever want waivers, add the PR-scoped entries yourself or give the classifier an explicit permission rule for `~/.claude/scripts/merge_gate.py` edits. |

## 4. Standing observations (no action unless you want one)

- The self-hosted visit scheduler catches up after the laptop was off: it fires every missed slot at
  once (2 slots on 2026-10-06 10:16, 4 slots on 2026-10-07 17:26). GitHub's concurrency group runs one
  and cancels the rest, so there is no burst of scrapes, but only one reading is stored per catch-up.
  The schedule itself is untouched.
- GitHub's own scheduled check-price runs fire about 4 times a day, not 8; the Tanishq visits dispatch
  the rest.
