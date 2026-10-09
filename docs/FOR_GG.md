# For GG: things only your account or decision can clear

Kept current by CC. Last updated 2026-10-09 (CC). The weekly one-screen model note is `docs/MODEL_STATUS.md`.
CC does everything it is allowed to do itself. What is left here is the one click below, plus a few
optional items that need your account or your judgement.

## 0. URGENT (found 2026-10-09 09:30 UTC): data sync is blocked

**https://github.com/gaurav-gandhi-2411/gold-rate-tracker/pull/2588** : open it, click **Squash and merge**, confirm.

Why: since `scraper-dependency-guard` became a required check, every bot data PR stops at "the base branch policy prohibits the merge" (the guard only runs on human PRs, never on the bot branches). The site's data has not updated since about 2026-10-08 23:55 UTC. PR #2588 makes the bot post that check for its own data-only PRs; it needs your click because it edits `.github/actions/`.

Faster alternative, if you prefer: GitHub > Settings > Branches > master > untick `scraper-dependency-guard` for now; the waiting bot PRs merge on the next sync run; re-tick it after #2588 merges. CC does not post the status by hand for the waiting PRs (that would be a manual bypass of a required check).

After either, CC confirms the next sync run says "Merged." and removes this section.

## 1. The one click (about 1 minute)

**Encryption, last step (ADR 060): https://github.com/gaurav-gandhi-2411/gold-rate-tracker/pull/2558**

Open it, click **Squash and merge**, confirm. That is all. It only adds a manual workflow; nothing changes
until CC runs it. Everything after the merge is CC's: it starts the one-time encryption at a safe time
(at least 15 minutes after a price run and 30 minutes before a Tanishq visit), checks the draft PR the
workflow opens (20 encrypted files with their checksums, plaintext removed, checks green), merges it if
the merge gate allows (otherwise it becomes your second one-click item here), then confirms the next three
price runs say "decrypted" and the live site is unchanged.

Rollback, if ever needed: revert the encryption PR; the plaintext is still in git history, so the files
come back as tracked. (Git history keeps every earlier plaintext version by decision; it is not rewritten.)

Status of the button: CC re-checks it before every report and removes this section when it is done. If
this section is here, #2558 was ready when CC last looked (see the report that pointed you here).

## 2. Done (for your information; nothing to do)

| Item | State |
|---|---|
| `scraper-dependency-guard` as a required check | Done by you, 2026-10-08. |
| GoatCounter visit counting | **Live since 2026-10-09 (PR #2581).** Verified in a real browser on the live site: two count requests (page and language), both answered 200, no cookie, no console error, the footer note shows. GoatCounter's numbers are not public (403/401 without your login), so the one-step check is yours, 30 seconds: open https://gold-rate-tracker.goatcounter.com, you should see the test visit from 2026-10-09 (path `/gold-rate-tracker/` and the event `lang/en`); and under Settings > Data collection confirm individual pageviews, User-Agent, screen size and location are all OFF (the footer note claims exactly that). |
| The three runaway python processes | Stopped by you, 2026-10-08. |
| Sleep instead of Shut down | Done by you. CC checks every visit slot with `scripts/laptop_attribution.py` and reports any miss while the laptop was on or asleep. |
| Promotion horizon | Decided: **180 decision days** (rule v3, ADR 072 Amendment 2, hash `2499a124…846c73`). CC will not loosen the 0.05 error rate or the 5% minimum gain. |
| #2075 (encryption) | Closed and superseded: crypto module #2544, producer wiring #2548, hardening #2555 are merged; only #2558 is left (section 1). |
| Merge gate | CC never edits, waives or asks permission to edit `merge_gate.py` or any hook. Anything the gate blocks is listed here. |

## 3. Optional (about 15 minutes in total; nothing is blocked on these)

| # | Item | Exact steps |
|---|---|---|
| 1 | Check the Cloudflare Worker secrets for stale names | In `worker-deadman/` run `npx wrangler secret list`. The Worker's code expects `NTFY_TOPIC`, `TRIGGER_TOKEN`, `GITHUB_PR_HEALTH_PAT`, and (parked, optional) `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Delete any other name from the list. In this repo the secrets are `CI_HEALTH_PAT`, `CI_MERGE_PAT`, `DATA_ENC_KEY`, `NTFY_TOPIC` (checked 2026-10-05). |
| 2 | Ask IBJA for written permission to publish its rate history | ADR 059: IBJA's API terms say publishing IBJA rates or history on a website needs written permission. Send IBJA a short email (the contact is on https://ibjarates.com): who you are, the product (free, no ads, plain-language gold tracker), that the page shows the latest official fix and a next-fix range built from it, and ask in writing whether this and a history chart are allowed and on what terms. Until the answer arrives the raw IBJA series stays out of public files (the encryption above). |
| 3 | The 8 contested Hindi strings | All Hindi wording is LLM consensus, not native review, and it is live. `docs/HINDI_CONTESTED.md`, "START HERE" table: the 8 strings that most need a native speaker, with the English source and the competing wordings. Reply with the choices (or "keep") and CC applies them. |
| 4 | Task Scheduler history (needs an administrator window) | `wevtutil sl Microsoft-Windows-TaskScheduler/Operational /e:true`. Without it the report cannot tell "the task never fired" from "it fired and the dispatcher died before logging". Today's attribution does not need it: every miss so far was a shut-down laptop or before the schedule existed. |

## 4. Standing observations (no action unless you want one)

- "Real days" for the live model means decision days on or after 2026-10-07. The first one (decision day
  2026-10-07, resolved by the 2026-10-08 PM fix) is scored in `docs/MODEL_STATUS.md`. The 40/40/60-day
  switch-off windows are filled mostly by re-run history until about December.
- The visit scheduler catches up after the laptop was off: it fires every missed slot at once. GitHub's
  concurrency group runs one and cancels the rest, so only one reading is stored per catch-up.
- GitHub's own scheduled check-price runs fire about 4 times a day, not 8; the Tanishq visits dispatch the rest.
