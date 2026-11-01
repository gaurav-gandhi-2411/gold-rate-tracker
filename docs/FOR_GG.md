# For GG: things only your account or decision can clear

Kept current by CC. Last updated 2026-10-29 (CC; autonomy-window handover in `docs/HANDOVER_2026-10-27.md`). The weekly one-screen model note is `docs/MODEL_STATUS.md`.
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
| 4 | Optional: scheduled Claude shifts while you are away (autonomy spec section 1.2) | On 2026-10-22 the auto-mode classifier refused writing the wrapper script that the `GRT-Autonomy-Shift` task would run (a headless `claude -p ... --permission-mode auto` shift three times a day). Per the spec this was recorded and not worked around: no task exists, and the dated items run from GitHub Actions instead (daily digest, Monday push, T19, the look workflows). Nothing is needed. Treat the scheduled self-shift as not available: plan any future absence on GitHub Actions, proven before you leave (`docs/HANDOVER_2026-10-27.md`, "Lessons for the next absence"). |
| 5 | Optional: merge claude-config #74 (the trailer-strip hook fix) | https://github.com/gaurav-gandhi-2411/claude-config/pull/74. The hook amended local master when a commit and a branch switch ran in one command (three times on 2026-10-22/23). It now amends only a commit that command just made on the same branch. Selftest 538/538 with the fix; the old hook fails the new bug case. CC does not merge claude-config PRs. |
| 6 | Dependabot PRs | #2790 (setup-python) is closed: it would have downgraded 23 pins from v7.0.0 to v6.3.0. #2789 changes the self-hosted Tanishq scraper workflow (`setup-node`), and #2471 is a `/scraper` dependency; both need the scraper proof-run rule, so CC leaves them open. For the five `/ml` floor bumps (#2426 cryptography, #3222 setuptools, #2788 pyarrow, #3223 soupsieve, #3224 pillow), CC opens one replacement PR each that raises the floor and regenerates `ml/requirements-inference.lock`, and closes the Dependabot PR once its replacement merges. One a day at most, so the rest finish by about 2026-11-02. Nothing to do. |
| 7 | Optional: merge claude-config #75 (merge-gate gate 1b) | https://github.com/gaurav-gandhi-2411/claude-config/pull/75. #2790 passed every merge gate, because its title said "5 to 6" (an upgrade) and the downgrade showed only in the diff (`# v7.0.0` to `# v6.3.0`). `classify_semver_bump` already refuses a downgrade in a title, and gate 3b never ran (0 generated lines). The new gate 1b reads the version direction from a Dependabot PR's diff and fails if any pin goes down; it also fails if the diff cannot be fetched. Selftest 546/546; on the real patches it finds 23 downgrades in #2790 and none in #2791 or #2789. CC does not merge claude-config PRs. |
| 8 | Optional: merge claude-config #78 (let CC delete its own scratch) | https://github.com/gaurav-gandhi-2411/claude-config/pull/78 (draft). On 2026-10-30 auto mode denied CC's deletes of its own scratch (about 1 GB of throwaway test clones and verifier copies stayed on disk). A plain allow rule is unsafe: `rm -rf <scratch>/*` would also match extra arguments outside it. The PR adds a hook that allows a delete only when it is one plain command and every path resolves strictly inside the current session's own scratchpad (`%TEMP%\claude\<project>\<session>\scratchpad\`, taken from the hook's own session_id and transcript_path). It never allows another session's or another project's files. Anything else gets no opinion, so the existing guards and deny rules still decide; a hook allow cannot override a deny. Selftest 615/615, including the cases that must stay unallowed: another session's scratchpad, another project, the session folder outside `scratchpad`, paths outside, `..`, globs, chained commands, wrappers, and junctions pointing out. Subagents likely get no allow (safe; their transcript path has a different shape). It is 480 reviewable lines, over the ~400 limit, so it needs a human merge anyway. Unverified until merged: whether a hook allow also skips auto mode's own judgment. Try one scratch delete after merging. CC does not merge claude-config PRs. |
| 9 | Decide: give the data-sync merge more time (CC recommends 15 to 30 minutes) | On 2026-11-01 check-price run 41287213214 failed at "Sync data via bot PR" and ci-health paged you ("check-price stopped"). Cause: bot PR #3423's checks finished by 08:11 UTC, but the job that posts the required commit statuses (`post-required-check-status`, the Bug #4 fix) waited 8.5 minutes for a runner, because CC was merging several PRs at the same time. The statuses landed about 51 s after `scripts/bot_pr_merge.sh` stopped waiting (`WAIT_MINUTES`, default 15). Nothing was broken: a re-dispatched check-price (run 41291358476) synced the data. Of the last 100 bot-PR lint runs, the median took 7 minutes, 6 took 12 minutes or more, and the longest took 37. So 15 minutes leaves little slack. The wait is a production setting, so CC did not change it. If you agree, reply "raise the sync wait to 30" and CC will open the one-line PR. Related, also not changed: the script's "Required contexts green" line also counts check runs, but branch protection accepts only the forwarded commit statuses, so it can report green a few minutes early. CC now also avoids running its own PR CI while a data sync is waiting. |
