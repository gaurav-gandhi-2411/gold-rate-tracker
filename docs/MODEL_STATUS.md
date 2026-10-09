# Model status

Updated 2026-10-09. Computed from real forecast days only; no backtest figure appears here. Rule: ADR 072 (frozen, hash `e4efabec2193`).

## What is live and how it is doing

Real days scored since 2026-10-07: **2** (latest 2026-10-08).

- Average miss on the next official rate: **Rs.81** per gram vs **Rs.87** if we had just repeated the last rate (-6.8%).
- Right about up or down on **100%** of 2 days the rate moved.
- The stated range held the actual rate on **50%** of 2 days (target 80%).

Only 2 days so far: too few to say whether this is good or bad.

## What is being tested

| Challenger | Real days | Where it stands | Error vs live | Verdict |
|---|---|---|---|---|
| Earlier model (ridge + small neural nets), now running in the background | 2 | first look after 20 days (2 so far), earliest 2026-11-03 | n/a | too early |
| Live model re-fitted on the last 60 days only | 2 | first look after 20 days (2 so far), earliest 2026-11-03 | n/a | too early |
| Live model with its own Monday setting | 2 | first look after 20 days (2 so far), earliest 2026-11-03 | n/a | too early |
| Hourly world-price model (own check on 2026-10-16, then it joins) | 0 | not started | n/a | not started |
| Live model averaged with the hourly model (joins after the hourly model does) | 0 | not started | n/a | not started |

A challenger replaces the live model only if we can be confident it beats it by more than 5% (the safe estimate in the table is the lower bound of its gain, adjusted for looking every day and for three challengers), keeps its range at target and is not worse on up/down. Nothing is judged before 20 real days. A challenger that has not qualified 180 decision days (days with an official rate) after its start date is retired, not promoted.

## Were the inputs on time?

- Jeweller price visits: **14 of 30** scheduled visits since the schedule began (2026-10-05) ran within 45 minutes of their time (16 did not). Of the 16 not run on time: 6 never dispatched (laptop off or scheduler idle), 1 dispatched then cancelled in a catch-up burst, 9 ran late (within 6 hours, after the laptop came back). Inferred from GitHub run records only, which does not tell a laptop that was off from a scheduler that did not fire.
- Overnight model forecast: published on **4 of 4** nights, typically 167 minutes after the US gold close.
- Why those 16 visits were missed (from the laptop's own records, checked 2026-10-09): 4 were before the timed visits were set up on 2026-10-05; 11 happened while the laptop was shut down; 1 were requested on time but the run was cancelled before it finished. A laptop that is shut down cannot run a visit; the dispatcher also catches up once when the laptop is back, which records one late reading, not the missed ones.

## Dates

- 2026-10-16: hourly world-price check (ADR 066), exactly as written in advance.
- 2026-10-22: morning-rate vs last-reading decision for the weekend estimate (ADR 063).

## Anything that fired

- Automatic fallback has not triggered (last checked 2026-10-09).
- Live model changes: none.
