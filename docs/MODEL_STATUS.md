# Model status

Updated 2026-10-07. Computed from real forecast days only; no backtest figure appears here. Rule: ADR 072 (frozen, hash `0782301d8890`).

## What is live and how it is doing

The live model has no scored real days yet (counting from 2026-10-07). Nothing is claimed until it has some.

## What is being tested

| Challenger | Real days | Days needed | Earliest decision | Error vs live | Verdict |
|---|---|---|---|---|---|
| Earlier model (ridge + small neural nets), now running in the background | 0 | not yet known | not yet known | n/a | too early |
| Live model re-fitted on the last 60 days only | 0 | not yet known | not yet known | n/a | too early |
| Live model with its own Monday setting | 0 | not yet known | not yet known | n/a | too early |
| Hourly world-price model (own check on 2026-10-16, then it joins) | 0 | not yet known | not yet known | n/a | not started |
| Live model averaged with the hourly model (joins after the hourly model does) | 0 | not yet known | not yet known | n/a | not started |

A challenger replaces the live model only if it beats it by at least 5%, is statistically clear after allowing for several challengers, keeps its range at target and is not worse on up/down. Days needed is sized from how noisy the daily difference actually is. No conclusion before the earliest decision date.

## Were the inputs on time?

- Jeweller price visits: **8 of 19** scheduled visits since the schedule began (2026-10-05) ran within 45 minutes of their time (11 did not). Of the 11 not run on time: 5 never dispatched (laptop off or scheduler idle), 6 ran late (within 6 hours, after the laptop came back). Inferred from GitHub run records only, which does not tell a laptop that was off from a scheduler that did not fire.
- Overnight model forecast: published on **3 of 5** nights, typically 167 minutes after the US gold close.

## Dates

- 2026-10-16: hourly world-price check (ADR 066), exactly as written in advance.
- 2026-10-22: morning-rate vs last-reading decision for the weekend estimate (ADR 063).

## Anything that fired

- Automatic fallback has not triggered (last checked 2026-10-07).
- Live model changes: none.
