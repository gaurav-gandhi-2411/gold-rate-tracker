# Closeout plan: from today to maintenance mode

Written 2026-11-14 (CC); status updated 2026-11-16. Whoever runs the weekly routine (`docs/WEEKLY_ROUTINE.md`) owns this file.
Every number is measured (source named) or marked INFERRED. Update the table when an item moves.

## 1. Definition of done: "maintenance mode"

The project is in maintenance mode when all of these hold and have held for 14 days in a row:

1. **Runs unattended.** `check-price` publishes a fresh forecast at least every 9 h on every day the laptop is on and falls back to the IBJA-calibrated price when it is off; no bot PR stays open more than 2 h without a reason in the ops log.
2. **GG hears only gold-price summaries and real problems.** The phone gets: the daily gold summary, the Monday summary, the site showing a stale or wrong price, token expiry warnings, and the public price alerts. Everything else is in the ops log (`python scripts/read_ops_log.py`, one call).
3. **The live model is up.** `forecast.json` has `next_fix.active = true`, and the live P3 record grows by one forward day per IBJA day (read weekly from `docs/MODEL_STATUS.md`).
4. **Every check that can stop a run is blocking and proven:** each flipped only after 5 clean real runs over at least 24 h (standing rule 2026-10-30).
5. **Dependencies are current:** no open Dependabot PR older than 7 days.
6. **All dated checks are recorded** (section 2) and `docs/FOR_GG.md` lists only what GG must do.

Maintenance mode does not mean new features. Model changes happen only through ADR 072 (frozen rule) at the dated looks.

## 2. Remaining items

Owner: **CC** (a Claude Code session), **GG**, or **calendar** (nothing to do until the date). Estimate = one session's working time.

| # | Item | Owner | Estimate | Status / date |
|---|---|---|---|---|
| 1 | P0: live next_fix down since 2026-11-12 13:10Z | CC | done | Fixed live 2026-11-13 21:54Z (check-price 43514677420: `next_fix: mode=after_afternoon_rate`, `forecast.json` `next_fix.active=true`): #3780, #3784 (stored folds with a 0 price set aside; root cause), #3786, then #3801/#3813/#3821/#3822 (hardening, unpublished days, forecast-down alert, IBJA per-cell parse). Watch one week |
| 2 | Clock guard flips from report to blocking | CC | done | #3868 (2026-11-15): 51 clean full-suite runs over 25.9 h after the last finding; a planted unpinned read made pytest exit 1. Three earlier runs did report 2 unpinned tests (43584578215, 43584580189, 43585282906, 2026-11-14), so the clean span starts after them |
| 3 | Data-reading tests on the data-only path blocking | CC | done | #3794 (101 clean of 103 data-only runs 2026-11-07..13; the 2 others were correct blocks) |
| 4 | Secret scan + 10 MB check on every PR | CC | done | #3812 allowlist (GG), #3862 flip (2026-11-15): 65 clean runs, first clean 2026-11-14 01:31Z. The step now runs master's checker, checksum and allowlist, so a PR cannot weaken the scan that judges it |
| 5 | Price-history integrity blocking on gaps and basis errors | CC | done | #3788 (39 clean of 40 check-price runs). Moves and could-not-verify go to the ops log, never block |
| 6 | Ops-log reader (weekly read in seconds) | CC | done | #3793; verified live (8 entries in 7 days) |
| 6b | Ops-log gap 2026-11-12..16 and a daily heartbeat | CC | done | No entry after 2026-11-12 03:18Z. All 104 sender runs since then (check-price 46, ci-health 36, bot-pr-sweep 17, scraper-canary 4, visitors-weekly 1) were scanned: 0 `ops-alert` annotations, 0 write-failure lines, and all 25 check-price runs after #3801 report `next-fix health: OK`; the forecast outage predates #3801. The quiet was real, but quiet and a broken writer looked the same. #3904 (heartbeat + 48 h silence note in the digest job summary, never ntfy) and #3905 (the sweep logs a merge). Writer proven live: the sweep's ops-log commit 3d1e8bc8 (2026-11-16 12:21Z). The first heartbeat line appears on the first digest run after #3904 merged. Not proven by a new write: the scraper-canary, visitors-weekly and Windows-runner (`ops_log_fallback.sh`) senders |
| 7 | Long-term monthly-sample chart | CC | done | #3782, live, SW v105 (flag ON; EN and HI verified in a headed browser; screenshots in `reports/screenshots/long-history-on/`) |
| 8 | Dependabot floors, one a day | CC | 15 min each | Done: pillow (#3832, replaces #3224), actions #2789, scraper npm #2471 (needed #3824 and #3848: the guard's gh retry used a misspelt flag), pandas 3.1.1 (#3884, 2026-11-16, replaces #3691; 5 check-price runs after it succeeded), pytest 9.2.0 (#3908, 2026-11-16, replaces #3492). Left: yfinance #3690 on 2026-11-17. `gh pr list --state open --author app/dependabot` on 2026-11-16 lists only #3690: soupsieve #3689 and a pyarrow PR are no longer open |
| 9 | Alert when `next_fix.active=false`, one page per incident, model-paused sentence in the daily summary | CC | done | #3821 and #3831 (2026-11-15; replay of 46 real ci-health runs: 18 old pushes vs 8 new). Verify live on the first real incident |
| 10 | ADR 076 look (hourly model), first Monday both gates hold, exactly as frozen, one look | CC | 20 min | Counts 2026-11-14 (run 43567000172): after_morning_rate 14/20 fixes, 26/28 days; after_afternoon_rate 17/20, 29/28. Mon 2026-11-16 (run 43777935024): unchanged, 14/20 and 17/20, no look taken; first look likely Mon 2026-11-23 (INFERRED) |
| 11 | ADR 077 look, after ADR 076's | CC | 20 min | on or after 2026-12-14 |
| 12 | W2 range read | CC | 20 min | about 2026-12-25 (earliest 2026-12-24 per `MODEL_STATUS.md`) |
| 13 | Delete the branch backup bundle `C:\Users\gaura\backups\gold-rate-tracker-branches-2026-10-18.bundle` unless a branch was restored from it | CC proposes, GG confirms | 5 min | 2027-01-16 |
| 14 | #3306 (key-migration workflow installs cryptography 50.0.2) | GG | done | merged by GG |
| 15 | Housekeeping: stale branches and stashes | CC | done | 4 stashes dropped and 15 local branches deleted on GG's decision 2026-11-14 (kept in the verified bundle / saved patches); the orphan queued check-price run completed |
| 16 | Renew `CI_HEALTH_PAT` | GG | 5 min | by 2026-12-09 (expires 2026-12-10); exact steps at the top of `docs/FOR_GG.md` |
| 17 | Renew `CI_MERGE_PAT` | GG | 5 min | active 2027-09-14, expires 2027-10-14 |
| 18 | IBJA zero cells | CC | done | #3822: backfill cleaned 5 stored zero cells (run 43696244586, 2026-11-15); a second run found nothing more. The 10-Nov row is `-` in IBJA's own PDF, so it stays missing rather than zero; where the first zero came from is NOT established (public logs inconclusive). Confirmed on the stored data 2026-11-16 (#3875, run 43777837656, counts only): 300 rows, 0 zero cells, 9 null cells in each `pm_*` column and none in `am_*`, all five `pm_*` cells of 2026-11-10 null. Standing rule "zero cells == 0" runs weekly (`ibja-store-counts.yml`, listed in `docs/WEEKLY_ROUTINE.md` section 1) |
| 19 | Real-tool contract test: every gh/git/jq flag our scripts use exists in the installed tool | CC | done | #3877 (CI run 43748167854: gh, git, jq parametrizations passed). 0 defects beyond #3824's in the enumerated surface; commands built from variables are not seen (stated in the test) |
| 20 | Bot-PR sweep switches from report-only to real merging | CC | done when #3911 merges | Natural input too rare (1 stranded PR in 34 scheduled runs). Staged proof, run 43865342183 (2026-11-16): merged staged PR #3906 (merge commit f1a08870, `--match-head-commit`, ops-log line 3d1e8bc8), refused not-green #3907. The 5-clean-runs rule was met by that staged proof. A fork-named PR refusal is proven by stub tests only |

## 3. Honest estimate

- **Engineering left (2026-11-16): the yfinance floor #3690 on 2026-11-17 (15 minutes) and the merge of #3911.** Every other flip and fix above has landed.
- **Calendar: engineering closed 2026-11-17 if the yfinance floor lands then** (earlier estimate was 11-20; only one floor was left once soupsieve and pyarrow had no open PR). Slips a day for each day it does not.
- **Then 14 clean days** of the definition: the count starts 2026-11-18, so **declared done about 2026-12-02** (INFERRED; assumes no new P0 and no new Dependabot PR older than 7 days).
- **Dated tail**, one short session each: 2026-11-16 (ADR 076 counts: did not qualify), about 2026-11-23 (ADR 076 look), 2026-12-14 (ADR 077), about 2026-12-25 (W2), 2027-01-16 (bundle deletion).
- The biggest risk is another silent outage like 2026-11-12: item 9 now pages once per incident.

## 4. What GG does periodically, and what happens if nothing is done

| What | When | If GG does nothing |
|---|---|---|
| Renew `CI_HEALTH_PAT` | by 2026-12-09 (pushes at 30, 14, 7 days) | `ci-health` cannot read branch protection; it writes "could not verify master's CI state" to the ops log hourly. Site and data keep working. |
| Renew `CI_MERGE_PAT` | by 2027-10-14 | Every data sync fails with HTTP 401: price and forecast stop; the stale-price push reaches the phone. The one that matters. |
| Keep the laptop on (or accept gaps) | daily; six timed Tanishq visits (01:40, 07:30, 10:40, 11:10, 15:35, 19:50 IST) | The shop price goes stale after 8 h; the page switches to the IBJA-calibrated estimate and says so; at 30 h the "Tanishq has not updated" alert goes to the ops log. See section 6. |
| Read the Monday summary | weekly | Nothing breaks. |
| Click merge on a PR that fails a merge gate | rare | It waits; nothing else depends on it. |

(`gh workflow run` is allowed since 2026-11-14, so CC runs the ADR looks and proof dispatches itself; `gh run cancel` is still denied.)

## 5. One-page incident runbook

Look in this order: **phone** (what was pushed), **ops log** (`python scripts/read_ops_log.py --days 2`), **Actions** (`gh run list --limit 15`), **the live page** (price, freshness label, console).

| Symptom | First check | Likely cause and fix |
|---|---|---|
| "Site showing a stale price" push | `scraped_at` / `predicted_at` in `data/forecast.json` on master vs the page | (a) laptop off: nothing to fix, the IBJA fallback covers it; (b) `check-price` failing: `gh run view <id> --log-failed`; (c) the sync step 401: renew `CI_MERGE_PAT`; (d) Pages deploy: `gh run list --workflow pages-deploy.yml` |
| `check-price` red at "Sync data via bot PR" | `INVALID:` lines from `scripts/validate_data.py` in the run log | The guard blocked bad data (a Rs.0 forecast on 2026-11-11). Fix the cause; the next run republishes. Never merge the bot PR by hand. |
| `check-price` red at "Price-history integrity" | the step's printed `(ii)` / `(iii)` line | A gap over 7 days in the chart file or a systematic basis error: repair the history through the encrypted-data workflow; if the check is wrong, revert #3788 (back to report-only). |
| Plain "hold" headline, `next_fix.active=false` | `forecast.json` `next_fix.reason`; the check-price log line `next_fix: failed` carries a traceback | An impossible price in a stored record or input. 2026-11-12: a stored P3 fold with pm0 = 0 (#3784); look for `set aside` lines (kept under `quarantined_invalid`). |
| Master `lint` red | `gh run list --branch master --workflow lint.yml`, then the failed step | A test reading the real clock (now blocking): pin it, or add a reviewed entry to `tests/clock_guard_allowlist.txt`; a hygiene finding: read the step's `PR-HYGIENE` line; an install step: check the lock; `pwa-headless` apt failures are external, re-run. |
| Bot PR open more than 2 h | `gh pr list --state open` | A required check is not green; `python scripts/check_required_checks_positive.py --pr N` names it. |
| Model demoted or champion changed | `data/model_demotion_state.json`, `forecast.json` `next_fix.champion` | Read ADR 068 / 072; never change the frozen rule file. |
| A push carries something ops-related | `tests/test_ntfy_keep_list.py` | A sender slipped past the keep list; remove it. |
| Anything touching tokens or encrypted data | stop | GG's account only; CC cannot read `DATA_ENC_KEY`. |

Rollback for any bad merge: `git revert <sha>` on a `fix/` branch, PR, merge when green. Production deploys happen from CI on push to master, never from a laptop.

## 6. What a multi-day laptop gap looks like to a visitor (verified on real data)

The laptop was off 2026-11-07 12:03 IST to 2026-11-10 11:28 IST (72 h). To see what a visitor gets after such a gap, the real data of 2026-11-10 04:00Z (the last good state before the gap closed) was loaded with its clock shifted to the present, and the live page was driven in a headed browser (VERIFIED). The page showed: the headline "about Rs.13,151, OUR ESTIMATE FOR TODAY, FROM INDIA'S OFFICIAL GOLD RATE" (not the stale shop price), the usual range, and a calm banner saying this is an estimate and the last successful shop check was 3 days ago. It did not show a stale shop price as if current, and nothing was marked as an error. Not measured here: how far the estimate sits from the shop's own price after a long gap (INFERRED to be wider than after a short one); it is labelled as an estimate for that reason. Screenshots of the long-history chart in the same session: `reports/screenshots/long-history-on/`.
