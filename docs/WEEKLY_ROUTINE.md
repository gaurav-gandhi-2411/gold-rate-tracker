# Weekly routine for a CC session (no brief needed)

Run this once a week, and at the start of any session after a gap. It is a checklist, not a
report: do each step, write down what you saw (VERIFIED) and what you only reasoned (INFERRED), and
report in the `.claude/CLAUDE.md` format. Rules that always apply: never read, print or set a
secret; CC cannot read `DATA_ENC_KEY`, so the model records are read only from aggregates the
workflows publish; never upload decrypted data as an artifact or log (the repo is public); forward
days only; one merge per command, with the merge gate and
`scripts/check_required_checks_positive.py`; scripts go in files and run under `timeout` (see
`.claude/CLAUDE.md`, "Process safety"). Times below are UTC unless marked IST (IST = UTC + 5:30).

Commands are for bash; replace `$REPO` with `gaurav-gandhi-2411/gold-rate-tracker`.

## 0. Start

1. `date -u`: check today's date against the brief; never trust an old note about "today".
2. `git fetch` then `git status -sb`: main checkout is on `master`, clean. Read the newest
   `memory/project_resume_*.md` and `docs/FOR_GG.md`.
3. `git worktree list` and `gh pr list --state open`: know what is yours and what is waiting on GG.

## 1. Sync health and stale periods

Data on master (public, no decrypting): 
- `data/cadence_metrics.json`: `price_checks.longest_gap_hours`, `open_gap_hours`,
  `median_gap_hours` over the last 7 days. The page shows "not fresh" past 8 h
  (`docs/TANISHQ_TIMED_VISITS.md`), the "Tanishq has not updated" alert (T14) is at 30 h.
  Flag: `open_gap_hours` above 8, or `longest_gap_hours` above 12.
- `data/tanishq_scrape_success_rate.json`: `success_rate` and its Wilson interval (7 days).
- `data/forecast.json`: `predicted_at` and `scraped_at` should be hours, not days, old.
- `gh run list --workflow check-price.yml --limit 15` and `--workflow ci-health.yml --limit 3`:
  the last success is recent. The GitHub cron (`37 1-22/3 * * *`) runs late or skips; a timed visit
  or a push/dispatch run covers it. A gap of several hours with no run at all is the finding.
- Stale periods this week: compare the longest gap above with the previous week's note.

## 2. Timed visits and attribution

Slots (IST): 01:40, 07:30, 10:40, 11:10, 15:35, 19:50 = 20:10, 02:00, 05:10, 05:40, 10:05, 14:20 UTC.
1. `gh run list --workflow scrape-tanishq-selfhosted.yml --limit 40 --json event,status,conclusion,createdAt`
   For each slot since your last check: a `workflow_dispatch` run created within about 2 minutes
   of the slot and `success` is on time. Anything else: `cancelled`, `failure`, a late dispatch or no
   dispatch. List every slot, none skipped.
2. `data/laptop_attribution.json` (counts by class: `laptop_off`, `dispatched_run_cancelled`, ...)
   is rebuilt only on the laptop from its event log (`scripts/laptop_attribution.py`); CC cannot
   refresh it. Report its `generated_at` and say it is stale if older than the slots you list.
3. A cancelled run: read its job steps (`gh run view <id> --json jobs`) for the cause before saying
   anything (the 2026-10-09 case was the bot-PR sync step waiting out the 25-minute timeout).

## 3. Required checks and bot PRs

1. `gh api repos/$REPO/branches/master/protection/required_status_checks --jq .contexts`
   must list `lint`, `pwa-js`, `scraper-dependency-guard`. If one is missing, say so first
   (GG's saves have failed to stick before) and put the steps in `docs/FOR_GG.md`.
2. The last 10 merged bot PRs (`gh pr list --state merged --limit 10`): each head commit has
   `scraper-dependency-guard=success` (`gh api repos/$REPO/commits/<sha>/status`) and merged
   without a person. An open bot PR older than 2 hours is blocked: find which check, tell GG at once
   with the steps to remove the required check, then fix.
3. For every PR you merge: merge current master in, wait for CI on the NEW head, then
   `python scripts/check_required_checks_positive.py --pr N` and
   `python ~/.claude/scripts/merge_gate.py -PrNumber N -Repo $REPO`, then `gh pr merge N --squash`.
   Data deletion or encryption migration PRs are GG's (gate 4); everything the gate blocks goes to
   `docs/FOR_GG.md` as a READY item.

## 4. Forward scoring and MODEL_STATUS

1. `docs/MODEL_STATUS.md` ("Updated ..." line, real days scored, latest day) is rebuilt by the
   weekly-backtest workflow. If its date is older than 7 days, or a decision day since is not
   scored, dispatch `weekly-backtest.yml`:
   - at least 15 minutes after the last check-price run finished and at least 30 minutes before
     the next Tanishq slot (both workflows write `data/metrics_history.json`; two writers race);
   - never while a migration PR is open.
2. Read results from the run log and the bot PR it opens, never from decrypted files. The
   nowcast line is `nowcast shadow [G3 ...]: {...}` in the step "Score the morning-fix nowcast shadow".
3. Report P3's forward record (n, mean miss vs holding the last rate, direction, range hit) and every
   challenger's with its n. No conclusion before a challenger's first look under rule v7
   (20 forward days, `ml/promotion.py`). With fewer days say "too few to say".
4. After a `data/backtest.json` refresh, the page's "test on past days" chart should show 30 points
   (`p3_past_errors.n`).

## 5. Demotion and champion state

- `data/model_demotion_state.json` (P3) and `data/model_demotion_state__<version>.json` (any other
  model that has been live): `demoted` must be false; if true, find why (ADR 068) and say so.
- `forecast.json` -> `next_fix.champion`: `id` is `p3` unless a promotion happened;
  `data/champion_state.json` exists only after one. A `fallback` or `unreadable` flag is a finding.
- The hourly model (ADR 066) is a shadow with its own record; it is not in the pool
  (`ml.promotion.LIVE_CAPABLE`). It joins only through an ADR 072 amendment.

## 6. Open PRs, worktrees, processes

1. Close or finish every PR of yours; leave Dependabot PRs alone unless a brief says otherwise.
2. `git worktree remove` each merged worktree (check `git status` in it first). Main stays on `master`.
3. List background tasks you started (the harness lists them) and stop any still running, by id or
   PID, never by name.

## 7. Analytics glance

GoatCounter needs GG's login (checked 2026-10-10: the dashboard shows a login form, `/counter/*.json`
returns 403), so CC cannot read it. Say "not readable without GG" and keep the optional glance in
`docs/FOR_GG.md`. Do not substitute a guess.

## 8. Dated checks coming up

Announce each outcome to GG in one plain sentence with `gh workflow run notify-gg.yml -f title="..."
-f message="..."` (a public input: no secrets, no prices).

| Date | Check | How |
|---|---|---|
| 2026-10-16 | ADR 066 hourly check, exactly as pre-registered | `gh workflow run adr066-check.yml`, then read the console report of the run (aggregates only). Nothing else decides it. If a window is promotable, the next step is an ADR 072 amendment (GG), see the ADR 066 addendum. |
| 2026-10-22 | ADR 063 nowcast decision, the 6 late-IBJA days excluded | Dispatch `weekly-backtest.yml` (timing rule in step 4), read the `nowcast shadow [G3 ...]` line: certified `n_same_day`, `mae_m0` vs `mae_m3`, the one-sided HAC p. The summary already excludes late-IBJA days. Apply G3's "holds" (INFERRED reading: M3 lower and p below 0.05 on certified days, n stated); if n is small, say so and prepare nothing. |
| about 2026-11-03 | First look at the ensemble, roll60 and monday challengers under rule v7 | `docs/MODEL_STATUS.md` after a weekly run: each needs 20 forward days before the first look. |

Add the next dated check here whenever one is created, and remove it once its outcome is reported.

## 9. Close

Update the memory resume note and `docs/FOR_GG.md` (only READY items and the optional standing
ones). Report: SUMMARY, per item in full, SURPRISES AND MISTAKES, WAITING ON GG.
