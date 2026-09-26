# Tanishq timed visits (GG decision E3)

**Status: STOP for GG.** Nothing here is live until GG merges the PR, and the exact-time trigger
needs GG's own setup on the laptop (section 6). The schedule is GG decision 3a (2026-09-26,
section 10), which replaces decision 4a (section 9).

## 1. Summary

**GG decision 3a (2026-09-26), in force in this PR; replaces decision 4a.** Six visits a day
across morning and afternoon: the section 4 variant A schedule. Section 10 has the details.

- **Visit times (IST): 01:40, 07:30, 10:40, 11:10, 15:35, 19:50.**
- **Why.** Decision 4a's 10:00–12:30 cluster left every afternoon change (27% of changes) stale
  until the next morning. Measured with the same data, bootstrap and Monte Carlo (section 9
  results table, q = 0, every visit on time):

  | | Mixed (3a) | 10:00–12:30 cluster (4a) |
  |---|---|---|
  | Mean staleness, all changes | **19.7 min** (95% CI 11.9–73.0) | 304 min (215–415) |
  | Morning changes | 8.4 min (3.4–59.1) | 21.0 min (12.6–24.3) |
  | Afternoon changes | 49.7 min (15.4–174.4) | 1,046 min (998–1,112) |
  | Caught within 30 min | 88.5% (63.9–95.3%) | 68.5% (58.7–76.9%) |
  | Caught within 60 min | 90.4% (66.6–98.7%) | 71.9% (61.4–79.6%) |
  | Hours a day the page shows "not fresh" | **0 h** | 13.5 h |

- **At the measured miss rate** (q = 0.38: 38% of visits find no runner), mean staleness is
  164.6 min against U30's 351.
- **01:40 is kept for the freshness gate, not for updates.** Section 9 found no evidence of
  updates near 01:40: 1.0%, CI 0–3.1%. The visit is there because without it the overnight gap
  (19:50 → 07:30) is 11.7 h, longer than the site's 8 h gate. The page would then show the
  estimate every night even when nothing is wrong.
- **T14 ("Tanishq has not updated") stays at 30 h, re-derived for this schedule** (section 10).
- **Forward refinement.** `scripts/tanishq_schedule_refine.py weekly` now runs in mixed mode. It
  re-optimises under variant A's constraints and proposes a change to GG only under the rule in
  section 10. It never applies one.
- Exact-time visits still need the laptop trigger (section 6). GitHub's scheduler creates only
  4.7 of 8 scheduled runs a day, 1–3 h late; its crons stay only as the fallback.

Sections 2–8 below are the earlier E3 analysis. They still hold as evidence (the update-time
distribution and scheduler lateness), but its interim schedule and measurement window were
replaced by decision 4a.

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

### Proposed measurement window (not adopted: GG decision 4a chose no measurement window)

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
  - It skips if a run of the workflow was created in the last 10 min, or is still queued or
    in progress. A late or duplicate trigger therefore never doubles a visit. (It was 45 min
    before decision 4a; visits are now 30 min apart, so it must stay under 30.)
  - It skips every visit for 3 h after a run that Tanishq answered with a rate limit (429)
    or a challenge (the outcomes log's `blocked` flag). If it cannot read that log, it skips
    (fails closed).
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
  at 10:30 visits when it comes back. The visit is recorded at its real time. If several slots
  were missed, Task Scheduler starts them together; the first dispatches, and the others skip
  because a run is already queued or in progress, so there is no burst.
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
   tasks: `\GoldRateTracker\Tanishq-0140`, `Tanishq-0730`, `Tanishq-1040`, `Tanishq-1110`,
   `Tanishq-1535` and `Tanishq-1950`. If you registered the 4a tasks (`Tanishq-1000` …
   `Tanishq-1230`) earlier, first run `.\scripts\win\register_tanishq_tasks.ps1 -Unregister`.
   It removes every `\GoldRateTracker\Tanishq-*` task.
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
12. Set the switch time, so the weekly scripts count only the new schedule: put the UTC time
    of step 11 into `effective_from_utc` in `scraper/visit_schedule.json` (a one-line PR).
13. Weekly (Mondays):
    - `python scripts/tanishq_visit_metrics.py --days 7` (section 7);
    - `python scripts/tanishq_schedule_refine.py weekly` (section 9). Act only on `PROPOSE` or
      `ESCALATE`.

**How to check a run worked** (any day): `Get-Content $env:LOCALAPPDATA\gold-rate-tracker\tanishq_dispatch.log -Tail 6` shows one line per slot: `DISPATCHED`, or `SKIP` with the reason
(recent run, cool-off, no network). `gh run list --workflow scrape-tanishq-selfhosted.yml -L 6`
shows the matching runs. In Task Scheduler, each task's *Last Run Result* should be `0x0`.

**If the laptop is asleep or off.**
- Asleep with wake timers on: the task wakes it, dispatches, and the runner service picks the
  job up. That takes about 2 min plus network reconnect.
- Asleep with wake timers blocked, or off: the slot is missed. The task runs when the laptop
  comes back, if that is before the next day's slot. The site shows "not fresh" meanwhile.
  Nothing wrong is ever shown.

**Undo** (any step, any order):
1. `gh variable delete TANISHQ_TRIGGER --repo gaurav-gandhi-2411/gold-rate-tracker`. The GitHub
   cron takes over again at the same six times (as a fallback: 1–3 h late, some dropped).
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
  ran within `tolerance_min` (10 min; it was 45 before decision 4a) after the slot: laptop off
  or asleep, runner offline, or no trigger. For captured slots,
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

Pre-registration commit: `11d7dbba`. sha256 of this section as committed (from its heading to
the end of the file at that commit):
`7c47974debcec2ebe92e93cd5634bcda23e11bac850fdca11a2f75fa797bbbfc`.

### Deviations from the 4a pre-registration (stated, not hidden)

1. **Minimum 15 min between visits, added to O.** The optimum as registered (no minimum spacing)
   is 10:00, 10:10, 10:40, 10:45, 10:55, 11:10. Visits 5-10 min apart cannot both happen: a run
   holds the self-hosted runner from dispatch to the bot-PR merge (created to completed: median
   6.0 min, p95 12.6 min, n = 75 successful runs that finished within 45 min;
   `reports/tanishq_update_times/gh_runs/runs_sh.json`), and the dispatcher skips a slot while
   a run is in progress. So the rule was applied to O with every gap in 15-30 min. The
   as-registered O is reported (point estimate only) and was not bootstrapped, because it
   cannot run.
2. **Pool wording.** The pre-registration says "112 Monday-to-Saturday" intervals. The pool
   actually used is 115: 96 weekday, 16 Saturday and 3 mixed, exactly the section 4 pool (only
   Sunday-only intervals excluded, and there are none). "The same data as sections 2-4" was the
   intent; 112 was a counting slip.
3. **Operational settings that 30-min visits force (not statistics).** The dispatcher's
   duplicate guard went from 45 to 10 min, and the metrics slot tolerance from 45 to 10 min;
   both must stay under the shortest visit gap (a test enforces it). A 3 h cool-off after a
   rate-limited or challenged run was added (see "Reconciliation with PR #2048" below).

### Results (VERIFIED: `scripts/tanishq_schedule_refine.py evaluate`)

File: `reports/tanishq_update_times/morning_schedule_evaluation.json`. B = 500, seed 42, 4,000
Monte Carlo draws per replicate, 20,000 for point estimates; lateness from the runner-online pool
(median 1.9 min). Sanity check: the section 4 interim schedule reproduces its published
19.7 min mean staleness exactly.

**Question 1: keep 01:40? No.**
- Mass in 00:40-02:40 IST: 1.0%, 95% CI 0-3.1%. Only 58% of bootstrap replicates put any
  mass there. The CI includes zero, so the rule drops 01:40.
- Mass in 20:00-09:59: 1.0%, CI 0-8.0%. All of it is the one interval below.
- Change intervals containing 01:40: 11 of 115. Only 1 lies entirely inside 20:00-09:59, so
  only 1 forces an overnight change: Fri 2026-07-24, between 00:44 and 03:32 IST. The other 10
  also cover daytime hours, where the rest of the evidence puts the change.
- Trade-off: without 01:40 (and with no visit after 12:30), the 8 h gate shows "not fresh" from
  about 20:30 to 10:00 IST. That is acceptable under E2's "not fresh" display: the estimate,
  never a wrong price. Keeping 01:40 would have shortened this to about 20:30-01:40 and
  09:40-10:00, but the data give no reason to visit then.

**Question 2: which six times? U30.**
- O (15-30 min gaps): 10:00, 10:15, 10:40, 10:55, 11:10, 11:25. It lowers mean staleness by
  11.3 min against U30, but the one-sided 95% lower bound of that improvement is -46.7 min. The
  rule needs the bound above zero, so U30 stays. O's own morning staleness CI (3.2-133.4 min)
  shows why: it bets on the NPMLE's point locations, and if the change is after 11:25 it waits
  until the next day.

| Schedule (IST) | Mean staleness, min | Morning changes, min | Afternoon changes, min | Within 30 min | Within 60 min |
|---|---|---|---|---|---|
| Today's captures (replayed) | 440 (227–695) | 484 (258–761) | 324 (138–548) | 5.8% (0.6–14.0%) | 12.8% (4.3–27.1%) |
| **U30 (chosen)**: 10:00, 10:30, 11:00, 11:30, 12:00, 12:30 | 304 (215–415) | 21.0 (12.6–24.3) | 1,046 (998–1,112) | 68.5% (58.7–76.9%) | 71.9% (61.4–79.6%) |
| O: 10:00, 10:15, 10:40, 10:55, 11:10, 11:25 | 292 (208–429) | 5.3 (3.2–133.4) | 1,046 (998–1,112) | 71.9% (59.7–79.6%) | 71.9% (60.0–79.6%) |
| Section 4 interim: 01:40, 07:30, 10:40, 11:10, 15:35, 19:50 | 19.7 (11.9–73.0) | 8.4 (3.4–59.1) | 49.7 (15.4–174.4) | 88.5% (63.9–95.3%) | 90.4% (66.6–98.7%) |

- Brackets: bootstrap 95% CIs, q = 0 (every visit happens). Morning = changes dated 10:00-11:59
  (72% of changes); afternoon = 14:00-19:59 (27%).
- **At the measured miss rate** (q = 0.38, 38% of visits find no runner): U30 gives 351 min
  mean staleness, 43.1% within 30 min and 60.9% within 60 min. Morning changes 79 min.
- **Placement bound.** With all mass on the left or right edge of each innermost interval, U30
  gives 306 or 301 min, and 68.1% or 68.9% within 30 min. The choice does not depend on where
  inside those intervals the changes fall.
- **Consequence of GG's choice, plainly.** U30 is better than today for morning changes (21 vs
  484 min) and far better at catching changes quickly (68.5% vs 5.8% within 30 min). It is worse
  than today for afternoon changes (1,046 vs 324 min): every afternoon change now waits for the
  next morning. Against the section 4 interim schedule, U30 gives up 20 points of within-30
  share and about 280 min of mean staleness, and adds 13.5 h a day of "not fresh".

### Reconciliation with PR #2048 (`feat/retailer-risk-mitigations`)

Checked against #2048's head `5f0f2f62` (VERIFIED by reading its `scraper/scrape.js` diff):
- **Probe once per UTC day.** #2048's `shouldTryRequestsPath` tries the plain-GET probe only when
  the last outcome in `data/tanishq_scrape_outcomes.jsonl` is from an earlier UTC day. All six
  visits are 04:30-07:00 UTC, the same UTC day, so the probe runs at the 10:00 IST visit and not
  at the other five. This needs each run's outcome line on master before the next visit: bot-PR
  merges took median 3.6 min, max 10.3 min over the last 60 (`gh pr list`), inside the 30 min
  gap. If a merge ever lags, the worst case is one extra plain GET at the next visit.
- **Backoff never pushes a visit past the next slot.** #2048's retries are at most 3 attempts,
  with about 2.5-5 s then 5-10 s of backoff. The job's `timeout-minutes: 25` bounds a run below
  the 30 min gap, and a run in progress makes the next dispatch skip, never overlap.
- **429 handling.** #2048 ends a rate-limited run with no Playwright escalation and "backs off
  until the next scheduled run". With visits 30 min apart that would mean another visit 30 min
  later. So the dispatcher now skips every visit for 3 h after a run whose outcome is `blocked`
  (429, Retry-After or a challenge). After a 429 at 10:00, the rest of that morning is skipped;
  the next visit is the next day's 10:00. That is at least as long as the old 3 h cadence. The
  GitHub-cron fallback has no cool-off, but it only runs when the laptop trigger is off.
- **No file conflicts.** `git merge-tree` of the two branch heads is clean; the only shared file
  is `tests/test_count_baseline.json`, which merges automatically. #2048 needed no change.

## 10. GG decision 3a: mixed morning-and-afternoon schedule (2026-09-26)

**Decision (pre-approved by GG in the 2026-09-26 brief, STOP for GG to merge).** Switch from
4a's 10:00–12:30 cluster to the mixed morning-and-afternoon schedule, keeping 5–6 visits.

**Which mixed schedule, and why this one.** The schedule is the section 4 variant A, K = 6
point estimate: **01:40, 07:30, 10:40, 11:10, 15:35, 19:50 IST**. No new schedule was fitted,
and no new analysis was run to choose it. It is the only mixed candidate already evaluated
under both pre-registrations (sections 4 and 9), in the same paired bootstrap as U30, so its
numbers are directly comparable (section 1 table).
- Variant A's gap limit (≤ 460 min) is what makes "not fresh" 0 h a day.
- Variant B, K = 6 (07:00, 10:40, 11:10, 15:35, 17:15, 19:50) scores 16.6 min (9.6–71.0) but
  has an 11.2 h overnight gap. The page would then show the estimate for about 3 h every night.
- The 5-visit options are worse: A, K = 5 is 29.1 min and B, K = 5 is 23.3 min.
- The section 4 regret rule said the data cannot pin down the exact best schedule. Every
  candidate above beats both today's captures and U30.

**What changed in this PR for 3a.**
- `scraper/visit_schedule.json`: the six times and `"mode": "mixed"`.
- The workflow's fallback crons (UTC 20:10, 02:00, 05:10, 05:40, 10:05, 14:20).
- Tests: the schedule equals `INTERIM`, is `mixed_feasible`, and shows 0 "not fresh" hours.
- Mixed mode in `scripts/tanishq_schedule_refine.py weekly`.
- The T14 derivation (below).
- The dispatcher's 10 min duplicate guard, the 10 min slot tolerance and the 3 h cool-off are
  unchanged. The shortest gap is still 30 min (10:40 → 11:10), so the existing test that both
  stay under the shortest gap still holds.

**Reconciliation with #2048, re-checked for the new times.**
- **Probe once per UTC day.** In UTC the visits are 20:10 (previous UTC day), 02:00, 05:10,
  05:40, 10:05 and 14:20. The first visit of each UTC day is 02:00 UTC (07:30 IST), so the
  plain-GET probe runs there once a day.
- **429 cool-off.** A block at 10:40 skips 11:10 and resumes at 15:35 (3 h later), not the next
  day as under 4a.

**T14 threshold, re-derived: stays 30 h.**
- **Method.** 20,000 simulated weeks, seed 42. Each visit is missed independently with the
  measured q = 0.38. Silence is counted in non-Sunday IST hours, as T14 does. Independence is
  an assumption: real misses (laptop off) are correlated, which makes long gaps more likely
  than simulated.

  | Threshold | Mixed (3a): one false alert per | 10:00–12:30 cluster (4a): one false alert per |
  |---|---|---|
  | 24 h | 7 weeks | 7 weeks |
  | 26 h | 19 weeks | 52 weeks |
  | 30 h | **65 weeks** | **54 weeks** |
  | 36 h | 222 weeks | 74 weeks |

- **With every visit on time**, the longest gap is 5.8 h, so a healthy week never alerts.
- **Why 30 h holds.** It keeps 4b's false-alert rate and worst-case detection time (30
  non-Sunday hours after the last successful visit).
- **Why not 4b's rule applied literally.** "First visit of one day to the last visit of the
  next" is 42.2 h here, which would give about 46 h: slower detection for no gain.
- **Why not lower.** Going below 30 h trades detection speed for more false alerts, and alert
  thresholds are GG's decision.
- These figures come from a simulation (INFERRED), not from observed alerts.

**Weekly refinement in mixed mode (set before any mixed-mode weekly run).** The rules in
section 9 hold, with two changes:
- The candidate is re-optimised over the whole day under `mixed_feasible`: variant A's
  constraints plus the 15 min minimum spacing. It starts from the current schedule plus up
  to 19 random starts (random draws that are not feasible are skipped).
- Rule 3 (the morning-window escalation) does not apply, and the script reports it as not
  applicable.

Everything else is unchanged: at least 20 changes bracketed to ≤ 30 min since
`effective_from_utc`, and improvement ≥ 5 min with a one-sided 95% lower bound above 0
(B = 200, seed 42) before it prints PROPOSE. At most one proposal per 4 weeks. The script
never edits the schedule.
