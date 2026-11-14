# Closeout plan: from today to maintenance mode

Written 2026-11-14 (CC). Whoever runs the weekly routine (`docs/WEEKLY_ROUTINE.md`) owns this file.
Every number is measured (source named) or marked INFERRED. Update the table when an item moves.

## 1. Definition of done: "maintenance mode"

The project is in maintenance mode when all of these hold and have held for 14 days in a row:

1. **Runs unattended.** `check-price` publishes a fresh forecast at least every 9 h on every day the laptop is on and falls back to the IBJA-calibrated price when it is off; no bot PR stays open more than 2 h without a reason in the ops log.
2. **GG hears only gold-price summaries and real problems.** The phone gets: the daily gold summary, the Monday summary, the site showing a stale or wrong price, token expiry warnings, and the public price alerts. Everything else is in the ops log (`python scripts/read_ops_log.py`, one call).
3. **The live model is up.** `forecast.json` has `next_fix.active = true`, and the live P3 record grows by one forward day per IBJA day (read weekly from `docs/MODEL_STATUS.md`).
4. **Every check that can stop a run is blocking and proven:** each flipped only after 5 clean real runs over at least 24 h (standing rule 2026-10-30).
5. **Dependencies are current:** no open Dependabot PR older than 7 days, except the two the scraper proof-run rule holds (item 8 below).
6. **All dated checks are recorded** (section 2) and `docs/FOR_GG.md` lists only what GG must do.

Maintenance mode does not mean new features. Model changes happen only through ADR 072 (frozen rule) at the dated looks.

## 2. Remaining items

Owner: **CC** (a Claude Code session), **GG**, or **calendar** (nothing to do until the date). Estimate = one session's working time.

| # | Item | Owner | Estimate | Status / date |
|---|---|---|---|---|
| 1 | P0: live next_fix down since 2026-11-12 13:10Z | CC | done | Fixed live 2026-11-13 21:54Z (check-price 43514677420: `next_fix: mode=after_afternoon_rate`, `forecast.json` `next_fix.active=true`): #3780 (positive-or-missing rates), #3784 (stored folds with a 0 price set aside; root cause), #3786 (IBJA ingest: a 0 is no rate). Watch one week |
| 2 | Clock guard flips from report to blocking | CC | 10 min | Landed report-only in #3789 (297 tests read the clock unpinned, 0 time-window dependent, all reviewed). Flip PR after 5 clean real runs over 24 h: about 2026-11-17 |
| 3 | Data-reading tests on the data-only path blocking | CC | done on merge | #3794 (101 clean of 103 data-only runs 2026-11-07..13; the 2 others were correct blocks) |
| 4 | Secret scan + 10 MB check on every PR | CC | flip 20 min | #3791 merged report-only (first real run: `PR-HYGIENE: secret_scan=ok large_files=ok`). Flip after 5 clean runs over 24 h AND an allowlist decision (57 generic-api-key findings in 2 old commits of reports/): about 2026-11-18 |
| 5 | Price-history integrity blocking on gaps and basis errors | CC | done | #3788 (39 clean of 40 check-price runs). Moves and could-not-verify go to the ops log, never block (design note in the PR) |
| 6 | Ops-log reader (weekly read in seconds) | CC | done | #3793; verified live on the real branch (8 entries in 7 days) |
| 7 | Long-term monthly-sample chart | CC | done | #3782, live, SW v105 (flag ON; EN and HI verified in a headed browser) |
| 8 | Dependabot floors, one a day: pillow #3224, pandas #3691, pytest #3492, yfinance #3690, soupsieve #3689, pyarrow. #2789 (actions) and #2471 (scraper npm) need the scraper proof-run rule | CC for the six; proof runs need GG to allow `gh workflow run` (see section 4) | 15 min each | 2026-11-15 to 2026-11-20 |
| 9 | Alert when `next_fix.active=false` (the 32 h outage went unnoticed because `Run inference` is `continue-on-error`); harden `ml/volatility.py::_log_returns`, `save_oos` re-reading `quarantined_invalid`, `ml.ibja.is_rate` on int dtypes, a test for the PDF-parse mask | CC | 1 PR | about 2026-11-17 |
| 10 | ADR 076 look (hourly model), first qualifying Monday on or after 2026-11-16, exactly as frozen, one look | CC; needs GG to allow `gh workflow run` | 20 min | Mon 2026-11-16 if both gates hold; else the next Monday. Counts run first (last counts-only run 2026-11-06: after_morning_rate 11/20 fixes, 18/28 days; after_afternoon_rate 13/20 fixes, 21/28 days) |
| 11 | ADR 077 look, after ADR 076's | CC | 20 min | on or after 2026-12-14 |
| 12 | W2 range read | CC | 20 min | about 2026-12-25 (earliest 2026-12-24 per `MODEL_STATUS.md`) |
| 13 | Delete the branch backup bundle `C:\Users\gaura\backups\gold-rate-tracker-branches-2026-10-18.bundle` unless a branch was restored from it | CC proposes, GG confirms | 5 min | 2027-01-16 |
| 14 | #3306 (draft: key-migration workflow installs cryptography 50.0.2) | GG: merge or close | 5 min | when convenient |
| 15 | Housekeeping: about 70 stale local branches, 3 stashes, merged remote branches, one orphan queued check-price run (43095941464, queued since 2026-11-11 20:25Z; auto mode denied cancelling it) | CC lists, GG confirms the deletes | 30 min | after item 8 |
| 16 | Renew `CI_HEALTH_PAT` | GG | 5 min | expires 2026-12-10; exact steps at the top of `docs/FOR_GG.md` |
| 17 | Renew `CI_MERGE_PAT` | GG | 5 min | active 2027-09-14, expires 2027-10-14 |

## 3. Honest estimate

- **Engineering to maintenance mode: 2 more working sessions plus one a day for the Dependabot floors** (items 2, 4, 8, 9 are about 12 small PRs; CI is serial at about 10 min each, 8 PRs went through on 2026-11-13/14).
- **Calendar: earliest 2026-11-20, realistic 2026-11-20 to 2026-11-27**, because the two flips (items 2 and 4) need 24 h of clean runs after they landed and item 8 is one PR a day.
- **Then 14 clean days** of the definition: **declared done about 2026-12-04 to 2026-12-11** (INFERRED; assumes no new P0).
- **Dated tail**, one short session each, none needs engineering unless a look surprises: 2026-11-16 (ADR 076), 2026-12-14 (ADR 077), about 2026-12-25 (W2), 2027-01-16 (bundle deletion).
- The biggest risk is another silent outage like 2026-11-12: the model was down about 32 h before anyone saw it (item 9 closes that gap).

## 4. What GG does periodically, and what happens if nothing is done

| What | When | If GG does nothing |
|---|---|---|
| Renew `CI_HEALTH_PAT` | by 2026-12-10 (pushes at 30, 14, 7 days) | `ci-health` cannot read branch protection; it writes "could not verify master's CI state" to the ops log hourly. Site and data keep working. |
| Renew `CI_MERGE_PAT` | by 2027-10-14 | Every data sync fails with HTTP 401: price and forecast stop; the stale-price push reaches the phone. The one that matters. |
| Allow CC to run `gh workflow run` (one narrow Bash permission rule) | before 2026-11-16 | Auto mode denied `gh workflow run adr076-look.yml` and `gh run cancel` on 2026-11-13. Without it CC cannot run the ADR 076 look, post-merge proof dispatches, or the scraper proof runs; the dated looks wait for you to run the command yourself. |
| Keep the laptop on (or accept gaps) | daily; six timed Tanishq visits (01:40, 07:30, 10:40, 11:10, 15:35, 19:50 IST) | The shop price goes stale after 8 h; the page switches to the IBJA-calibrated estimate and says so; at 30 h the "Tanishq has not updated" alert goes to the ops log. The laptop was off 2026-11-07 12:03 IST to 11-10 11:28 IST (72 h); the site stayed up. |
| Read the Monday summary | weekly | Nothing breaks. |
| Click merge on a PR that fails a merge gate | rare | It waits; nothing else depends on it. |

## 5. One-page incident runbook

Look in this order: **phone** (what was pushed), **ops log** (`python scripts/read_ops_log.py --days 2`), **Actions** (`gh run list --limit 15`), **the live page** (price, freshness label, console).

| Symptom | First check | Likely cause and fix |
|---|---|---|
| "Site showing a stale price" push | `scraped_at` / `predicted_at` in `data/forecast.json` on master vs the page | (a) laptop off: nothing to fix, the IBJA fallback covers it; (b) `check-price` failing: `gh run view <id> --log-failed`; (c) the sync step 401: renew `CI_MERGE_PAT`; (d) Pages deploy: `gh run list --workflow pages-deploy.yml` |
| `check-price` red at "Sync data via bot PR" | `INVALID:` lines from `scripts/validate_data.py` in the run log | The guard blocked bad data (a Rs.0 forecast on 2026-11-11). Fix the cause; the next run republishes. Never merge the bot PR by hand. |
| `check-price` red at "Price-history integrity" | the step's printed `(ii)` / `(iii)` line | A gap over 7 days in the chart file or a systematic basis error: repair the history through the encrypted-data workflow; if the check is wrong, revert #3788 (back to report-only). |
| Plain "hold" headline, `next_fix.active=false` | `forecast.json` `next_fix.reason`; the check-price log line `next_fix: failed` now carries a traceback | An impossible price in a stored record or input. 2026-11-12: a stored P3 fold with pm0 = 0 (#3784); look for `set aside` lines. |
| Master `lint` red | `gh run list --branch master --workflow lint.yml`, then the failed step | A test reading the real clock: pin it (`tests/clock_guard.py` reports them); an install step: check the lock; `pwa-headless` apt failures are external, re-run. |
| Bot PR open more than 2 h | `gh pr list --state open` | A required check is not green; `python scripts/check_required_checks_positive.py --pr N` names it. |
| Model demoted or champion changed | `data/model_demotion_state.json`, `forecast.json` `next_fix.champion` | Read ADR 068 / 072; never change the frozen rule file. |
| A push carries something ops-related | `tests/test_ntfy_keep_list.py` | A sender slipped past the keep list; remove it. |
| Anything touching tokens or encrypted data | stop | GG's account only; CC cannot read `DATA_ENC_KEY`. |

Rollback for any bad merge: `git revert <sha>` on a `fix/` branch, PR, merge when green. Production deploys happen from CI on push to master, never from a laptop.
