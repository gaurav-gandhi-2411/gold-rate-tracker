# Model status

Updated 2026-11-15. Computed from real forecast days only; no backtest figure appears here. Rule: ADR 072 (frozen, hash `e4efabec2193`).

## What is live and how it is doing

Real days scored since 2026-10-07: **22** (latest 2026-11-12).

- Average miss on the next official rate: **Rs.111** per gram vs **Rs.131** if we had just repeated the last rate (-15.3%).
- Right about up or down on **77%** of 22 days the rate moved.
- The stated range held the actual rate on **68%** of 22 days (target 80%).

## What is being tested

| Challenger | Real days | Where it stands | Error vs live | Verdict |
|---|---|---|---|---|
| Earlier model (ridge + small neural nets), now running in the background | 22 | safe estimate of the gain -20.0% (needs more than +5%); 159 decision days left | -4.0% | not yet |
| Live model re-fitted on the last 60 days only | 22 | safe estimate of the gain -22.9% (needs more than +5%); 159 decision days left | +3.2% | not yet |
| Live model with its own Monday setting | 22 | safe estimate of the gain -7.4% (needs more than +5%); 159 decision days left | +1.3% | not yet |
| Hourly world-price model (2026-10-16 check: too early; a fresh check is fixed in ADR 076) | 0 | not started | n/a | not started |
| Live model averaged with the hourly model (joins after the hourly model does) | 0 | not started | n/a | not started |

A challenger replaces the live model only if we can be confident it beats it by more than 5% (the safe estimate in the table is the lower bound of its gain, adjusted for looking every day and for three challengers), keeps its range at target and is not worse on up/down. Nothing is judged before 20 real days. A challenger that has not qualified 180 decision days (days with an official rate) after its start date is retired, not promoted.

Pattern check, Earlier model (ridge + small neural nets), now running in the background (22 days): the daily differences repeat from one day to the next by +0.01 (1 day) to -0.11 (4 days).

Pattern check, Live model re-fitted on the last 60 days only (22 days): the daily differences repeat from one day to the next by -0.26 (1 day) to -0.40 (4 days). That is high: the promotion rule's safety margin is not reliable until a new written rule says otherwise.

Pattern check, Live model with its own Monday setting (22 days): the daily differences repeat from one day to the next by -0.17 (1 day) to -0.20 (4 days).

Range test W2 (ADR 074; background only, it never changes the live range): the live model's forecast with a range sized by how big the forecast move is. Real days: **7** since 2026-10-30. Its range is judged only after 40 real days, earliest 2026-12-29; nothing is read before that.

## Were the inputs on time?

- Jeweller price visits: **16 of 46** scheduled visits since the schedule began (2026-10-05) ran within 45 minutes of their time (30 did not). Of the 30 not run on time: 23 never dispatched (laptop off or scheduler idle), 7 ran late (within 6 hours, after the laptop came back). Inferred from GitHub run records only, which does not tell a laptop that was off from a scheduler that did not fire.
- Overnight model forecast: published on **2 of 4** nights, typically 368 minutes after the US gold close.
- Why those 16 visits were missed (from the laptop's own records, checked 2026-10-09): 4 were before the timed visits were set up on 2026-10-05; 11 happened while the laptop was shut down; 1 were requested on time but the run was cancelled before it finished. A laptop that is shut down cannot run a visit; the dispatcher also catches up once when the laptop is back, which records one late reading, not the missed ones.

## Dates

- 2026-10-16: hourly world-price check (ADR 066) ran once, as written: too early. Final; the next check is fixed in ADR 076 (first look 2026-11-16 or later).
- 2026-10-22: morning-rate vs last-reading decision for the weekend estimate (ADR 063).

## Anything that fired

- Automatic fallback has not triggered (last checked 2026-11-15).
- Live model changes: none.
