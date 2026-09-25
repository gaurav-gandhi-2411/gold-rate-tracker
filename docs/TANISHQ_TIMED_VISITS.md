# Tanishq timed visits (GG decision E3)

**Status: STOP for GG.** Nothing here is live until GG merges the PR, and the exact-time trigger
needs GG's own setup on the laptop (section 6).

## 1. Summary

- Tanishq changes its rate mostly in the late morning: an estimated 72% of changes fall between
  10:00 and 11:59 IST (95% CI 62–79%). Most of the rest fall between 14:00 and 19:59 (27%, CI
  18–37%). About 1% fall overnight. No change could be dated to a Sunday.
- The data are too coarse to pin the best exact minutes. Today's captures are a median of
  5.1 h apart, and a typical change is only known to within 260 min. The pre-registered
  "can the data choose?" rule fails (p90 regret 35 min against a 15 min limit).
- Even so, any schedule built this way beats today's by a wide margin, provided the visits
  happen on time. Interim schedule (IST): **01:40, 07:30, 10:40, 11:10, 15:35, 19:50.**
  - Mean staleness: 20 min (95% CI 12–75), versus 439 min (CI 233–680) today.
  - Share of changes captured within 60 min: 90% (CI 67–98%), versus 13% (CI 4–25%) today.
- On-time visits need the laptop trigger. GitHub's scheduler creates only 4.7 of the 8
  scheduled runs a day, 1–3 h late. The same six times sent through GitHub cron would give
  230 min mean staleness and 14% within 60 min.
- The self-hosted runner had no machine available for 38% of the scheduled runs. That, not the
  schedule, is now the biggest cause of stale prices. At that miss rate the interim schedule
  gives 165 min mean staleness and 63% within 60 min.
- Proposed next step (STOP for GG): a bounded measurement window. 15 days (Monday to Saturday)
  of 10 visits a day, with 15-minute probes from 10:00 to 11:15. That narrows morning changes
  from about 420 min to 15 min. Details are in section 4.

## Pre-registration (written before the final analysis run)

Disclosure: before this section was written, one exploratory listing of the
consecutive-capture change intervals in `data/prices.json` was printed (IST
start/end of each change interval). No estimator, schedule optimum,
staleness figure or bootstrap had been computed.

**Data.** Every live Tanishq capture: `data/prices.json` entries whose
`source` is not a history backfill, plus entries present in any earlier
git revision of that file and later removed. Timestamp convention: the
`timestamp` field is the UTC capture instant set by `scraper/scrape.js`
(`new Date()` after a successful parse). A *change* is any difference in
the 22K, 24K or 18K rate between two consecutive captures; Tanishq updated
somewhere in the open-closed interval (previous capture, this capture].
Republishing an unchanged rate is not observable and is not an "update"
for staleness purposes (its staleness is zero by definition).

**Estimator.** Nonparametric Turnbull NPMLE (self-consistency EM) of the
update time-of-day distribution on the 24 h IST circle, 1-minute bins,
uniform initialisation (so mass inside an innermost interval stays
uniform: within-interval placement is not identifiable). Intervals of
24 h or longer carry no time-of-day information and are dropped (counted).
Per day type (weekday / Saturday / Sunday) an interval is used only when
both ends fall on the same day type; otherwise it counts as "mixed" and
enters the pooled estimate only.

**Uncertainty.** Nonparametric bootstrap over change intervals, B = 500,
seed 42, 95% percentile intervals. Monte Carlo staleness with 20,000 draws
per evaluation, common random numbers across schedules.

**Objective.** Choose K in {5, 6} daily IST visit times on a 5-minute grid
minimising mean staleness (time from a real update to the capture that
first sees it), by coordinate descent from 20 seeded starts, under:
(i) at least one visit in 07:00–09:00 IST (fresh price by morning);
(ii) variant A: every circular gap between consecutive visits at most
460 min (the site's 8 h Tanishq freshness gate, `_STALE_THRESHOLD_H` in
`ml/inference.py`, minus a 20 min lateness margin), so the live
price-source gate never flips overnight; variant B: no gap constraint.
Lateness = measured dispatch-to-capture delay of `workflow_dispatch` runs;
a visit fails independently with probability q, evaluated at q = 0 and at
the measured share of scheduled runs that waited more than 30 min for the
runner.

**Current-schedule baseline.** Replay of the actual capture instants since
the self-hosted workflow split (2026-09-04), days resampled in the
bootstrap. Metrics for every schedule: mean staleness, share of updates
captured within 30 min and within 60 min.

**Decision rule for (c) "can the data choose?"** Re-optimise in 200 of the
bootstrap replicates. Regret of the point-estimate schedule in replicate b
= its mean staleness under replicate b's distribution minus replicate b's
own optimum. The data resolve the choice if the 90th percentile of regret
is at most 15 min. The non-identifiable within-interval placement is
bounded separately by evaluating the chosen schedule with all mass at the
left and at the right edge of each innermost interval; that range is
reported, not used in the rule. If the rule fails, a bounded measurement
window is proposed (STOP for GG) instead of a schedule.

Pre-registration commit: `5527819a`. sha256 of this section as committed:
`dc3df30393848996c25d2dc609bac32bf1dc991ad85cb647ddc2575eea84f922`.

### Deviations from the pre-registration (stated, not hidden)

1. **Lateness pool.** Only 1 of the 9 successful `workflow_dispatch` runs could be matched to a
   capture, too few to use. The pool used is every self-hosted run whose capture landed
   within 30 min of run creation: runner online, pickup plus scrape. n = 62, median 1.9 min,
   p90 2.9 min, max 26.7 min. That is what a Task Scheduler dispatch adds on top of the
   trigger time. Task Scheduler's own wake-up delay is not measured (INFERRED to be small).
2. **Bootstrap Monte Carlo size.** The point estimates use 20,000 draws, as pre-registered. The
   500 bootstrap replicates and the 200 regret re-optimisations use 4,000 draws each, to keep
   the run at about 27 min.
3. **"Instead of a schedule".** The rule failed, so section 4 proposes a measurement window.
   The PR still carries the point-estimate schedule as an interim configuration, clearly
   labelled, because E3 asks for the schedule change to be prepared. GG chooses (section 4).
4. **Turnbull implementation.** 1-minute bins with identical coverage patterns are collapsed
   before the EM and spread back uniformly. This is mathematically identical to EM on 1-minute
   bins with a uniform start. It was done only for speed.
5. **Added descriptive outputs** (not outcomes): bootstrap CIs of mass in wider IST windows,
   and the censoring-interval widths used to size the measurement window.

## 2. Data and resolution limit (VERIFIED: `scripts/analysis_tanishq_update_times.py`)

- **Captures.** 694 live captures, 2026-05-09 to 2026-09-25. That includes 6 captures that
  appear only in git history (removed from `data/prices.json` in May). History backfill rows
  are excluded.
- **Change intervals.** There are 128 intervals where the rate changed between two captures.
  13 of them are 24 h or longer and carry no time-of-day information, so 115 are used.
  - By IST day type: 96 weekday, 16 Saturday, 0 Sunday, 3 mixed (pooled only).
- **Resolution limit (the capture gap).**
  - Median gap between captures: 3.0 h over all history and 5.1 h since the 2026-09-04
    workflow split. p90 is 7.8 h and the maximum 61.9 h since the split.
  - Width of a used change interval: median 260 min, p10 162 min, p90 767 min.
  - Failed or blocked runs do not appear in `data/prices.json` at all; they show up as longer
    gaps. Since the split, 21 of 97 scheduled runs were cancelled after waiting for a runner,
    and 37 of 97 waited more than 30 min for one.
- **Timestamp convention.** `timestamp` in `data/prices.json` is the UTC capture instant, set
  by `scraper/scrape.js` after a successful parse. Everything here is shown in IST.

## 3. When Tanishq changes its rate

Turnbull NPMLE of the time of day, with bootstrap 95% CIs (B = 500, resampling change
intervals, seed 42). File: `reports/tanishq_update_times/update_time_distribution.json`.

| IST window | Share of changes | 95% CI |
|---|---|---|
| 10:00–11:59 | 71.7% | 61.5–79.0% |
| 12:00–13:59 | 0.0% | 0.0–0.0% |
| 14:00–19:59 | 27.3% | 17.6–36.5% |
| 20:00–09:59 | 1.0% | 0.0–8.0% |

- **Weekdays (96 intervals).** Mass sits in narrow innermost intervals around 10:37, 10:41–10:59
  and 11:09, plus about 15% at 15:31. The NPMLE puts mass on the narrowest consistent
  intervals, so these point locations are *not* confidence statements. The hourly CIs are wide:
  10:00–10:59 has 0–76%, and 11:00–11:59 has 0–72%. The data cannot yet say whether the
  morning change is nearer 10:35 or 11:10. Only the 10:00–11:59 total is tight.
- **Saturday (16 intervals).** 90% in 10:02–11:08 and 10% in 14:07–14:53. That matches
  weekdays; too few for its own CIs.
- **Sunday.** 17 Sundays had captures. No change interval lay within a Sunday. One Sunday saw
  a change carried over from Saturday. Rule of three: at most about 3/17 of Sundays (18%)
  have a change, 95% upper bound.
- **Days with a change seen inside a <12 h interval:** 65 of 90 weekdays, 14 of 18 Saturdays,
  and 1 of 17 Sundays.
- "Update" means a change in the rate. If Tanishq republishes the same rate, we cannot see it,
  and it does not matter here: an unchanged price is never stale.

## 4. Choosing visit times

File: `reports/tanishq_update_times/schedule_evaluation.json`. Staleness is the time from a real
change to the capture that first sees it. Updates are drawn from the Monday-to-Saturday
distribution. Proposed schedules assume the laptop trigger, with lateness drawn from the runner-online pool
(section 1, deviation 1). q is the chance a visit does not happen.

| Schedule (IST) | Mean staleness, min | Within 30 min | Within 60 min |
|---|---|---|---|
| **Today**: actual captures since 2026-09-04, replayed (17 Mon–Sat days) | 439 (233–680) | 6.0% (0.7–13.6%) | 12.9% (3.7–24.5%) |
| **A, K=6 (interim, in this PR)**: 01:40, 07:30, 10:40, 11:10, 15:35, 19:50 | 19.7 (12.2–74.9) | 88.5% (63.9–95.4%) | 90.4% (67.0–98.2%) |
| A, K=5: 01:40, 07:00, 11:10, 15:35, 19:50 | 29.1 (17.1–79.6) | 57.6% (10.2–88.5%) | 90.4% (66.3–98.0%) |
| B, K=6 (no 8 h gap limit): 07:00, 10:40, 11:10, 15:35, 17:15, 19:50 | 16.6 (9.6–71.0) | 91.0% (64.0–96.4%) | 93.4% (72.7–99.3%) |
| B, K=5 (no 8 h gap limit): 07:00, 10:40, 11:10, 15:35, 19:50 | 23.3 (15.3–88.2) | 87.8% (63.8–94.9%) | 89.2% (65.9–97.9%) |
| A, K=6 at the measured miss rate q = 0.38 | 165 | 54.9% | 63.2% |
| A, K=6 sent through GitHub cron instead (measured delay, 41.5% of triggers dropped) | 230 | 6.1% | 14.1% |

- Brackets are bootstrap 95% CIs (q = 0).
- **Placement bound (A, K=6).** Mass is moved to the left edge or the right edge of every
  innermost interval. Mean staleness is then 18.4 or 17.6 min, and within-30 is 82% or 90%.
  Within-60 is 90% either way.
- **Why variant A is in the PR, not B.** Variant A keeps every gap between visits at or under
  350 min. The site switches from the Tanishq price to the IBJA-based estimate when the last
  capture is over 8 h old (`_STALE_THRESHOLD_H` in `ml/inference.py`, shared with `app.js`).
  - Variant B leaves a 670-min overnight gap. That would flip the live display to the estimate
    every night even when nothing is wrong: a live behaviour change nobody has decided.
  - A costs one extra visit, at 01:40. That visit also gives a price confirmed on the new IST
    day before anyone wakes up.
- **Why 6 not 5.** The morning change straddles 10:40 and 11:10. Dropping one of the two morning
  visits cuts the within-30 share from 89% to 58%.
- **Can the data choose? Pre-registered rule: no.** Over 200 re-optimised bootstrap replicates,
  regret is median 16.9 min, p90 35.3 min and max 96.1 min, against a 15 min limit.
  - The unstable slot is the fourth visit: its bootstrap 5–95% range runs from 11:00 to 16:30.
  - The morning visit is stable at 10:35–11:15.
  - This is uncertainty about *which* timed schedule is best. Every one of them beats today's
    captures, and the CIs do not overlap.

### Proposed measurement window (STOP for GG; not started)

Censoring-interval widths are simulated under the current estimate:

| Design | Visits/day | Days (Mon–Sat) | Total visits | Median width, morning change | Median width, afternoon change |
|---|---|---|---|---|---|
| Today's captures (replay) | ~4.7 delivered | — | — | 421 min | 381 min |
| **M1**: 01:40, 07:30, every 15 min 10:00–11:15, 15:35, 19:50 | 10 | 15 | 150 | 15 min | 260 min |
| M2: M1 plus every 30 min 14:00–18:00 (replacing 15:35) | 18 | 10 | 180 | 15 min | 30 min |

- **Recommendation: M1.**
  - It is 10 visits a day: 4 more than the interim schedule, and 2 more than today's
    nominal 8, for 15 days only.
  - Over 15 days it should add about 8 morning changes resolved to 15 min. That is 15 days
    times 73% of Monday-to-Saturday days with a change, times the 72% morning share. That resolves the morning slot, which carries 72% of
    the mass.
  - The afternoon (27%) stays coarse. M2 resolves it too, at nearly twice the load.
- **After the window:** re-run `scripts/analysis_tanishq_update_times.py`, then fix the six
  times in `scraper/visit_schedule.json` and the crons.
- M1 needs the laptop trigger (section 6), with `visit_schedule.json` temporarily listing the
  10 times. GitHub cron cannot deliver 15-minute probes.

## 5. Scheduler lateness (VERIFIED from `gh run list`, fetched 2026-09-25)

File: `reports/tanishq_update_times/scheduler_lateness.json`. Raw run metadata:
`reports/tanishq_update_times/gh_runs/`.

- **`scrape-tanishq-selfhosted.yml`** (cron `7 */3 * * *`, 2026-09-04 to 2026-09-25):
  - Runs created: 97, which is 4.68 a day against 8 nominal.
  - Creation delay against the latest nominal slot: p10 31 min, median 102 min, p90 156 min,
    max 179 min. This is a *lower bound*: a run cannot be tied to its slot, and dropped
    triggers are not counted.
  - Queue plus run time: median 6.8 min, p90 458 min, max 1,589 min. The long waits are the
    laptop being unavailable.
  - Run creation to capture, runner online: median 1.9 min, p90 2.9 min (n = 62).
- **`check-price.yml`** (cron `37 1-22/3 * * *`, from 2026-09-11; it does not visit Tanishq):
  - Runs created: 4.97 a day against 8 nominal.
  - Creation delay: median 120 min, p90 150 min.
- **Current IST visit times.** The nominal times are 02:37, 05:37, 08:37, 11:37, 14:37,
  17:37, 20:37 and 23:37. In practice runs are created at about 10:00–10:20, 16:35–17:10,
  21:45–22:35, 02:00–03:00 and 04:25–04:50 IST, and 38% of those find no runner online.

## 6. Exact-time trigger from the laptop (design, and GG's setup steps)

**Design.**
- `scripts/win/tanishq_dispatch.ps1` runs from Windows Task Scheduler at each visit time.
  - It waits up to 5 min for the network after a wake.
  - It skips if a run of the workflow was created in the last 45 min, or is still queued or
    in progress. A late or duplicate trigger therefore never doubles a visit.
  - Otherwise it calls `gh workflow run scrape-tanishq-selfhosted.yml -f slot_ist=HH:MM`.
- The existing workflow then does everything else: scrape, jump guard, health record,
  outcomes log and bot-PR sync. Nothing new touches `data/prices.json`.
- **Capture time and IST day.** The reading's timestamp is the real capture instant, set in
  `scrape.js`, so a late visit is recorded at its real time and belongs to the IST day it
  happened.
- **A missed slot is never a wrong price.** If the laptop is off, asleep without wake timers,
  or offline, no visit happens and nothing is written. The last capture ages, and past 8 h the
  site shows the existing "not fresh" path, the one E2 is redesigning. A reading is only
  written after a successful parse, validation and the 5% jump guard.
- `Run task as soon as possible after a scheduled start is missed` means a laptop that was off
  at 10:40 visits when it comes back. The visit is recorded at its real time, and the 45 min
  spacing guard stops a burst.
- **Switching off the GitHub cron.** The job now carries
  `if: github.event_name != 'schedule' || vars.TANISHQ_TRIGGER != 'task_scheduler'`.
  - Setting the repository variable makes GitHub-cron runs skip without touching Tanishq, so
    visits never double.
  - Deleting the variable brings the cron back as the fallback.
- **Forward measurement.** The outcomes log now records `trigger`, `slot_ist` and `blocked`.
  `blocked` is set when the scraper's stderr shows a Cloudflare challenge or a 429/Retry-After.

**GG's setup steps** (do not run until the PR is merged; the laptop's time zone must be
India Standard Time):

1. Pull master on the laptop, in the clone you will run the script from:
   `git -C C:\Users\gaura\ml-projects\gold-rate-tracker pull`.
2. Check that `gh` is signed in with a token that can start workflows: `gh auth status`. It
   must list `workflow` among the scopes. If not, run `gh auth refresh -s workflow`.
3. Turn on wake timers, for both mains and battery. In an admin PowerShell:
   - `powercfg /SETACVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1`
   - `powercfg /SETDCVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1`
   - `powercfg /SETACTIVE SCHEME_CURRENT`

   Or use Control Panel > Power Options > Change plan settings > Change advanced power
   settings > Sleep > Allow wake timers > Enable (both).
4. Check what this laptop can wake from: `powercfg /a`. If it lists "Standby (S0 Low Power
   Idle)" (Modern Standby), wake timers may be ignored on battery with the lid closed. Keep it
   plugged in, or accept that those slots will be missed and fall back to "not fresh".
5. Preview the tasks: `.\scripts\win\register_tanishq_tasks.ps1 -DryRun`. It should list 6
   tasks, `\GoldRateTracker\Tanishq-0140` … `Tanishq-1950`.
6. Create them: `.\scripts\win\register_tanishq_tasks.ps1`. Each task is set to:
   - daily at its IST time;
   - **Wake the computer to run this task**;
   - **Run task as soon as possible after a scheduled start is missed**;
   - run on battery, and keep running if the laptop switches to battery;
   - never a second copy at once, and a 10 min time limit;
   - run as GG, **only when signed in** (a locked screen is fine).

   A task set to run when signed out cannot read the `gh` token from Windows Credential
   Manager without storing your password in the task, so that is not done. After a restart,
   sign in once; until then slots are missed and the site shows "not fresh".
7. Check that the runner service is running: `Get-Service actions.runner.*` should show
   `Running`.
8. Check wake timers: `powercfg /waketimers` should list the next `Tanishq-` task.
9. Test one run now: `Start-ScheduledTask -TaskPath \GoldRateTracker\ -TaskName Tanishq-1110`.
   Then check each of these:
   - `gh run list --workflow scrape-tanishq-selfhosted.yml -L 1` shows a `workflow_dispatch`
     run.
   - `Get-Content $env:LOCALAPPDATA\gold-rate-tracker\tanishq_dispatch.log -Tail 3` shows
     `DISPATCHED`.
   - After the bot PR merges, the last line of `data/tanishq_scrape_outcomes.jsonl` has
     `"trigger": "workflow_dispatch", "slot_ist": "11:10"`.
10. Test sleep once: put the laptop to sleep about 5 min before a slot and confirm that the run
    appears at the slot time.
11. Only after steps 9–10 work, stop the GitHub cron visits:
    `gh variable set TANISHQ_TRIGGER --body task_scheduler --repo gaurav-gandhi-2411/gold-rate-tracker`.
12. Weekly: `python scripts/tanishq_visit_metrics.py --days 7` (section 7).

**If the laptop is asleep or off.**
- Asleep with wake timers on: the task wakes it, dispatches, and the runner service picks the
  job up. That takes about 2 min plus network reconnect.
- Asleep with wake timers blocked, or off: the slot is missed. The task runs when the laptop
  comes back, if that is before the next day's slot. The site shows "not fresh" meanwhile.
  Nothing wrong is ever shown.

**Undo** (any step, any order):
1. `gh variable delete TANISHQ_TRIGGER --repo gaurav-gandhi-2411/gold-rate-tracker`. The GitHub
   cron takes over again at the same six times.
2. `.\scripts\win\register_tanishq_tasks.ps1 -Unregister`. This removes every
   `\GoldRateTracker\Tanishq-*` task.
3. Optionally reset wake timers: re-run step 3 with `0` instead of `1`.
4. To go back to the old 3 h cron, revert this PR's change to
   `.github/workflows/scrape-tanishq-selfhosted.yml`.

## 7. Forward measurement (after switching)

`python scripts/tanishq_visit_metrics.py --days 7 [--end YYYY-MM-DD] [--out FILE]` reads only
`data/prices.json`, `data/tanishq_scrape_outcomes.jsonl` and `scraper/visit_schedule.json`. It is
not wired to anything user-facing. It reports:

- **Per change:** the IST day and the staleness *bounds*, from 0 to the interval width. The
  exact update instant is never observable. The share of changes whose width is ≤30 or ≤60 min
  is a lower bound on the share captured within 30 or 60 min.
- **Per slot:** captured, attempted with no capture, or missed. A missed slot means nothing
  ran within 45 min: laptop off or asleep, runner offline, or no trigger. For captured slots,
  it gives the lateness.
- **Per run:** successes, failures, and runs flagged `blocked` (challenge or 429).

Set `effective_from_utc` in `scraper/visit_schedule.json` to the switch time so earlier slots are
not counted as missed.

## 8. Kalyan: 1 city instead of 4

- Re-verified on the full snapshot store (`reports/tanishq_update_times/kalyan_city_identity.json`):
  259 of 259 multi-city cycles, 2026-07-19 to 2026-09-24, had the same 22K rate in all four
  cities.
- `ml/shadow_fusion.py` now fetches `SHADOW_KALYAN_CITIES = ("Bangalore",)`. That is 4 requests a
  day instead of 16. Bangalore is the same city as the tier-3 live fallback.
- The `KALYAN_CITIES` registry is unchanged, so re-adding a city is a one-line change.
- The weekly live canary (`scraper-canary.yml`) is kept unchanged, per E3.

## 9. GG decision 4a: six morning visits

### Pre-registration (written before any 4a schedule was evaluated)

Disclosure: nothing about the 4a schedules below had been computed when this was written. The
Monday-to-Saturday update-time distribution, its bootstrap and the replay of today's captures are
the ones in sections 2-4 (already published). No staleness figure for any 10:00-12:30 schedule
had been run.

**GG's decision (fixed, not tested).** Six visits a day, all between 10:00 and 12:30 IST. No
measurement window. The 01:40 visit stays only if the data show updates at that time.

**Data.** The same captures as sections 2-4: `data/prices.json` at commit `bb38f8d5` plus the
captures removed in earlier git revisions (694 captures, 115 usable change intervals, 112
Monday-to-Saturday). Timestamps are UTC capture instants (`scraper/scrape.js`), shown in IST.

**Question 1: keep 01:40?** "The data show updates at that time" means: the Monday-to-Saturday
Turnbull NPMLE has mass in 00:40-02:40 IST (01:40 plus or minus 60 min) whose bootstrap 95%
interval excludes zero (B = 500, seed 42, resampling change intervals). Reported alongside: the
mass and CI for 20:00-09:59, and the number of change intervals that contain 01:40 and lie
entirely inside 20:00-09:59 (only those force an overnight update). If the rule is not met, 01:40
is dropped.

**Question 2: which six times?** Two candidates, both entirely inside 10:00-12:30 IST:
- **U30**: 10:00, 10:30, 11:00, 11:30, 12:00, 12:30. Every change inside the window is bracketed
  to 30 min or less, which is what the forward refinement (below) needs.
- **O**: the six times on the 5-minute grid inside 10:00-12:30 that minimise mean staleness,
  subject to every gap between consecutive visits being 30 min or less (coordinate descent, 20
  seeded starts, the same Monte Carlo and lateness pool as section 4).

U30 is the default. O replaces it only if O lowers mean staleness by at least 5 min against U30
and the one-sided 95% paired-bootstrap lower bound of that improvement is above zero. Reason for
the default: section 4 showed the data cannot place mass inside the innermost intervals (the
regret rule failed), so an optimum that sits on those intervals is not trusted without margin.

**Reported for the chosen schedule, with bootstrap 95% CIs (B = 500, seed 42, same Monte Carlo
settings as section 4, q = 0):** mean staleness over all changes; mean staleness for changes
dated 10:00-11:59 ("morning") and 14:00-19:59 ("afternoon"); share of all changes captured within
30 and within 60 min. The same metrics, from the same bootstrap draws, for today's captures
(replayed) and for the section 4 interim schedule, so comparisons are paired. Point estimates
also at the measured miss rate q = 0.38. Also the daily hours during which the site's 8 h
freshness gate shows "not fresh" when every visit happens.

**Forward refinement rule (run weekly, never applied automatically).**
`scripts/tanishq_schedule_refine.py weekly` refits the same Turnbull NPMLE on every capture up to
now (history plus the new, denser captures) and prints a recommended schedule. It proposes a
change to GG only when all of these hold:
1. At least 20 changes seen since `effective_from_utc` (the switch to the 4a schedule) with a
   bracket of 30 min or less. (About 3 morning changes a week are expected, so this is about 6
   weeks.)
2. **Retime inside the window.** The in-window optimum (as O above) beats the current schedule by
   at least 5 min of mean staleness, and the one-sided 95% paired-bootstrap lower bound of the
   improvement (B = 200, seed 42) is above zero. Then it prints "PROPOSE" with the new times.
3. **Window check.** Separately, it counts forward changes first seen at the first visit of an IST
   day (so they happened outside the visit window, between the previous day's last visit and
   this visit). If the Wilson 95% lower bound of that share exceeds 38% (the upper CI of the
   historical outside-window share, 27% + 1%), it prints "ESCALATE: the window misses more
   changes than expected", for GG to reconsider afternoon coverage.
4. At most one proposal per 4 weeks. After GG adopts a new schedule, `effective_from_utc` is reset
   and the count in (1) starts again.

A schedule change is GG's. The script never edits `scraper/visit_schedule.json` or the crons.
