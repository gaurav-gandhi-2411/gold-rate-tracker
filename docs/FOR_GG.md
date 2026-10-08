# For GG: things only your account or decision can clear

Kept current by CC. Nothing here is urgent unless marked URGENT. Last updated 2026-10-08, afternoon (CC). The weekly one-screen model note is `docs/MODEL_STATUS.md`.
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
| 2 | Encryption (ADR 060): the one-time migration of the files already public | CC split #2075 and merged everything that could go in on its own merits: the crypto module (#2544) and the producer wiring (#2548; every added step does nothing until the migration, and the live site's served files were byte-identical after each merge). What is left for you is ONLY the migration workflow (PR #2558), because it is the one piece that changes what is public and the merge gate flags it. The hardening PR #2555 (a missing file can no longer stop the public data from being committed after the migration) is merged. **5-minute procedure, in order:** 1. Make sure your offline copy of `DATA_ENC_KEY` is saved. 2. Merge PR #2558 (it adds only `.github/workflows/encrypt-raw-data-migration.yml`). 3. Pick a moment at least 15 minutes after a check-price run finished and at least 30 minutes before the next Tanishq visit slot (visit times IST: 01:40, 07:30, 10:40, 11:10, 15:35, 19:50; check-price runs are on the Actions page). 4. GitHub > Actions > "Encrypt raw data (one-shot migration / key rotation, ADR 060)" > Run workflow > branch `master`, mode `migrate`, confirm `encrypt`. It takes about 2 minutes and opens a DRAFT pull request. 5. In that draft PR check: 20 `.enc` files and 20 matching `.sha256.json` files under `data/encrypted/` (19 if `premium_nowcast_bars.json` does not exist yet; it is never tracked), the plaintext files shown as deleted, `lint` and `pwa-js` green. 6. Mark it ready and merge it. 7. Within the next three check-price runs open the "Decrypt raw data" step: it must say decrypted; the site must look unchanged (CC checks both and records the run IDs here). **Rollback:** revert the migration PR (the plaintext is still in git history, and the plaintext files come back as tracked). If a producer run fails after the migration, check-price still commits the forecast and then shows red; the other workflows stop at the failing step; nothing is lost either way. Afterwards CC puts the status step after the decrypt step in `weekly-backtest.yml`. Known and accepted: git history keeps every old plaintext version (ADR 060, not rewritten by decision). |
| 2b | Open questions in the same area | (a) Whether `backtest.json`, `drift_metrics.json` and `metrics_history.json` may be rebuilt on scores only (blocked behind #2075: needs the encrypted data in CI). (b) A public, non-invertible source for the page_v2 range card before that flag is turned on. |
| 2c | DONE 2026-10-08 under your delegation (PR #2525): the switch-off test's single-check size is now 4.5% at the nominal 5% (it was 9.1%), thresholds unchanged. For your information, not a decision. Old text: the automatic switch-off rule fired more often than the ADR said (a reviewer's synthetic test) | The independent review found that, if the live model had NO real skill over repeating the last rate, the error rule alone would switch it off within 121 daily checks about 55% of the time (ADR 068 quotes 4.3% for a model that does have skill). A switch-off only falls back to repeating the last rate and sends you an alert, so it is the safe direction, but it could hide a model that is actually fine. The rule's settings are yours (40-day window, 3 checks in a row). CC has not changed them. Options: keep; or ask CC to re-run `scripts/simulate_demotion.py` under a no-skill assumption and propose a stricter window. |
| 2d | DONE 2026-10-08 under your delegation: promotion rule v3 (PRs #2545 and #2547). The horizon is now 180 DECISION days (days with an official rate), not calendar days. A control variate was tested and rejected. | Realistic chance that a challenger that is truly 10% better is promoted within the horizon: the ensemble 11% to 31%, p3_roll60 28% to 48%, p3_monday 100% (two of the three are under 50%). 20% better: found reliably, usually in 20 to 60 days. Wrongly promoting a model that is no better than the live one: 0.0%. Options if you want more power (not done; each is one number and a new hash): 270 decision days (ensemble 27% to 49%, roll60 40% to 59%) or 360 (49% to 65%, 53% to 69%); the cost is a later retirement and a longer period with the autocorrelation caveat (ADR 072 Amendment 2). CC will not loosen the 0.05 error rate or the 5% minimum gain. |
| 3 | Guardrails | Correction from you (2026-10-08): CC never edits, waives or asks for permission to edit `merge_gate.py` or any hook; anything the gate blocks comes here. CC splits oversized PRs to fit the gate instead. |

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

## 5. The scheduler laptop (about 5 minutes; why Tanishq visits were missed)

CC attributed every missed visit from the laptop's own records (PR #2549, `data/laptop_attribution.json`): of the 11 missed visits since 2026-10-05, 4 were before the timed visits were set up and **7 happened while the laptop was shut down** (Start menu power off or restart). The laptop recorded no sleep at all in that time, and the dispatcher skipped nothing. Wake timers are already switched on, and each task is set to wake the laptop and to run late if a slot was missed, so nothing more can be fixed in software: a laptop that is shut down cannot run a visit.

| # | Item | Exact steps |
|---|---|---|
| 1 | Use Sleep, not Shut down, when you leave (and keep it plugged in if you can) | Visits then still fire: the tasks wake the laptop. Hibernate is not enabled on this laptop; `powercfg /hibernate on` from an administrator window enables it if you prefer it to Sleep. |
| 2 | Turn on Task Scheduler history (optional, needs an administrator window) | `wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true`. CC tried and got "Access is denied". Without it the report cannot tell "the task never fired" from "it fired and the dispatcher died before logging". |
| 3 | Stop three runaway processes that CC's own commands started | Three `python.exe -` processes (PIDs 18252, 28616, 20088) from this session have used a CPU core each for about 5 hours (a shell idiom CC used hung them). The auto-mode classifier refused CC's request to stop them. In a normal window: `taskkill /PID 18252 /PID 28616 /PID 20088 /F`. Check first with `Get-Process -Id 18252,28616,20088` that they are `python` with a very high CPU total. Two of them also keep the empty folders `grt-wt-pa` and `grt-wt-leak` locked; after you stop them CC deletes those folders. (CC removed `grt-wt-vfix` itself: its holder had gone.) |
