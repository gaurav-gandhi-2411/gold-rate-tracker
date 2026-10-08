# For GG: things only your account or decision can clear

Kept current by CC. Nothing here is urgent unless marked URGENT. Last updated 2026-10-08 (CC). The weekly one-screen model note is `docs/MODEL_STATUS.md`.
Everything CC could do itself has been done; these are the items CC may not do (accounts, tokens,
settings, a model promotion) or could not do (a guardrail blocked it). Each has the exact steps.

## 1. Settings and accounts (about 15 minutes in total)

| # | Item | Exact steps |
|---|---|---|
| 1 | Make the scraper dependency guard a required check (PR #2388 was split and its parts are merged: the guard runs on every PR; this step only makes it blocking) | GitHub > gold-rate-tracker > Settings > Branches > branch protection rule for `master` > "Require status checks to pass" > search `scraper-dependency-guard` > select it > Save. Until then the check reports but does not block. |
| 2 | Turn on visit counting (optional, only if you want usage numbers; the code is merged OFF) | 1. Open https://www.goatcounter.com/signup (free; no plan or payment step is shown). Account name = the site code (lowercase letters, digits, hyphens, e.g. `gold-rate-tracker`); optional site domain `gaurav-gandhi-2411.github.io`; email; password; the human check. Verify the email. Leave any "collect IP / User-Agent / location / screen size" setting off. 2. One small PR: in `analytics.js` set `const ANALYTICS_SITE_CODE = "<your code>";` and in `flags.js` set `analytics: true,`. 3. In the same PR bump `VERSION` in `service-worker.js`, change README ("No accounts, no ads, no analytics." to "No accounts and no ads. It counts visits with GoatCounter, a cookieless counter that keeps no name, cookie or IP address; the page says so in its footer.") and the SECURITY.md PWA row (see `docs/proposals/analytics-5c.md`, "To turn it on"), and update the two tests that pin the shipped-off state. 4. Verify: open the live site, then `https://<code>.goatcounter.com`; one visit and a `lang/en` event within a minute; no cookie set. Switch off: `analytics: false`. CC can do step 2-3 once the account exists: tell CC the site code. |
| 3 | Check the Cloudflare Worker secrets for stale names | In `worker-deadman/` run `npx wrangler secret list`. The Worker's code expects `NTFY_TOPIC`, `TRIGGER_TOKEN`, `GITHUB_PR_HEALTH_PAT`, and (parked, optional) `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Delete any other name from the list. In this repo no secret or workflow references the old `gold-rate-tracker-worker-dispatch` token (CC checked 2026-10-05: repo secrets are `CI_HEALTH_PAT`, `CI_MERGE_PAT`, `DATA_ENC_KEY`, `NTFY_TOPIC`); you already confirmed it is not in your token lists. |
| 4 | Ask IBJA for written permission to publish its rate history | ADR 059 ("IBJA terms findings"): IBJA's own API terms say publishing IBJA rates or history on a website needs written permission; the site's terms do not ban reuse but say reuse beyond fair use needs permission. Send IBJA a short email (the contact is on https://ibjarates.com): who you are, the product (free, no ads, plain-language gold tracker), that the page shows the latest official fix and a next-fix range built from it, and ask in writing whether this and a history chart are allowed and on what terms. Until the answer arrives CC keeps the raw IBJA series out of public files (see PR #2075). |

## 2. Hindi review (about 5 minutes, optional)

All Hindi wording is LLM consensus, not native review. The wording is live (PRs #2467, #2468, #2469;
the three sentences about recent accuracy and the "estimate accuracy" heading are left in English
on purpose). Open `docs/HINDI_CONTESTED.md`: the 8 strings that most need a native speaker are in the "START HERE"
table at the top, each with the English source and the competing wordings. Reply with the choices
(or "keep") and CC applies them. Direction, track-record and coverage sentences fall back to English
on purpose until reviewed.

## 3. Decisions that are yours

| # | Decision | Context |
|---|---|---|
| 1 | The ADR 066 hourly promotion (kept for you) | None is pending today. The check is on 2026-10-16 (CC runs it exactly as pre-registered and prepares a PR if it passes; the merge stays yours). Promotions between the live model and its challengers follow ADR 072 and need no decision from you once the switch is wired. |
| 2 | Encryption (PR #2075, ADR 060): merge it and run the migration | CC could NOT do this under your 2026-10-08 delegation: the delegation asked for gate-3 and gate-4 waivers recorded in `~/.claude/scripts/merge_gate.py`, and the auto-mode classifier refused CC reading or editing that file on 2026-10-07 and again on 2026-10-08 (reason: self-modification of its own guardrails). CC did not work around it. #2075 is 1,700+ lines and changes a migration workflow (gates 3 and 4). To unblock: either add a permission rule allowing CC to edit `~/.claude/scripts/merge_gate.py` (then CC records the PR-scoped waivers and runs the sequence itself), or merge #2075 yourself. Note: `feat/encrypt-raw-data` currently conflicts with master (`check-price.yml`, `weekly-backtest.yml`); CC re-merges master into it as soon as you decide, since the conflicts keep changing. It is up to date with master and now also covers the P3 live record and the ADR 071 variants (VERIFIED: 20 registered paths, 19/19 round-trip byte-identical in the proof). Steps, in order: 1. Make sure your offline copy of `DATA_ENC_KEY` is saved. 2. Merge #2075. 3. Wait at least 15 minutes after a check-price run and at least 30 minutes before the next Tanishq visit. 4. Actions > "encrypt-raw-data-migration" > Run workflow > mode `migrate`, confirm `encrypt`. 5. Review and merge the PR it opens (it should contain 17 or more `.enc` pairs). 6. Confirm the next three check-price runs log "decrypted". Rollback: revert the migration PR; the plaintext is in git history. Afterwards CC puts the status step after the decrypt step in `weekly-backtest.yml` (the status JSON can stay plain). |
| 2b | Open questions in the same area | (a) Whether `backtest.json`, `drift_metrics.json` and `metrics_history.json` may be rebuilt on scores only (blocked behind #2075: needs the encrypted data in CI). (b) A public, non-invertible source for the page_v2 range card before that flag is turned on. |
| 2c | DONE 2026-10-08 under your delegation (PR #2525): the switch-off test's single-check size is now 4.5% at the nominal 5% (it was 9.1%), thresholds unchanged. For your information, not a decision. Old text: the automatic switch-off rule fired more often than the ADR said (a reviewer's synthetic test) | The independent review found that, if the live model had NO real skill over repeating the last rate, the error rule alone would switch it off within 121 daily checks about 55% of the time (ADR 068 quotes 4.3% for a model that does have skill). A switch-off only falls back to repeating the last rate and sends you an alert, so it is the safe direction, but it could hide a model that is actually fine. The rule's settings are yours (40-day window, 3 checks in a row). CC has not changed them. Options: keep; or ask CC to re-run `scripts/simulate_demotion.py` under a no-skill assumption and propose a stricter window. |
| 2d | DONE 2026-10-08 under your delegation (PRs #2531-#2533): the promotion rule is now a sequential test, first look after 20 real days, retired after 180 calendar days. One judgement call for you: 180 CALENDAR days is about 128 decision days; if you meant 180 decision days it is one number in the rule and a new hash. The rule is stricter than the old one for true gains of 5-10% (mostly retired), much faster for 20% or more. |
| 3 | Guardrail blocks CC hit | On 2026-10-07 the auto-mode classifier refused CC reading/editing `merge_gate.py` to add PR-scoped size waivers, even with your written instruction. CC therefore splits oversized PRs to fit the gate instead of using waivers. If you ever want waivers, add the PR-scoped entries yourself or give the classifier an explicit permission rule for `~/.claude/scripts/merge_gate.py` edits. |

## 4. Standing observations (no action unless you want one)

- "Real days" for the live model means decision days on or after 2026-10-07, rebuilt after the
  fix publishes. A day when the page was held or the data cache was stale can still count; the
  numbers matched the live forecast in the reviewer's test, but no log records that a forecast was
  actually shown on a given day. The 40/40/60-day switch-off windows are filled mostly by re-run
  history until about December.

- The self-hosted visit scheduler catches up after the laptop was off: it fires every missed slot at
  once (2 slots on 2026-10-06 10:16, 4 slots on 2026-10-07 17:26). GitHub's concurrency group runs one
  and cancels the rest, so there is no burst of scrapes, but only one reading is stored per catch-up.
  The schedule itself is untouched.
- GitHub's own scheduled check-price runs fire about 4 times a day, not 8; the Tanishq visits dispatch
  the rest.
