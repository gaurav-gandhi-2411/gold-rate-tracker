# Model status

Updated 2026-10-07. Computed from real forecast days only; no backtest figure appears here. Rule: ADR 072 (frozen, hash `1bbc5dd3eeae`).

## What is live and how it is doing

The live model has no scored real days yet (counting from 2026-10-07). Nothing is claimed until it has some.

## What is being tested

| Challenger | Real days | Where it stands | Error vs live | Verdict |
|---|---|---|---|---|
| Earlier model (ridge + small neural nets), now running in the background | 0 | first look after 20 days (0 so far), earliest 2026-11-04 | n/a | too early |
| Live model re-fitted on the last 60 days only | 0 | first look after 20 days (0 so far), earliest 2026-11-04 | n/a | too early |
| Live model with its own Monday setting | 0 | first look after 20 days (0 so far), earliest 2026-11-04 | n/a | too early |
| Hourly world-price model (own check on 2026-10-16, then it joins) | 0 | not started | n/a | not started |
| Live model averaged with the hourly model (joins after the hourly model does) | 0 | not started | n/a | not started |

A challenger replaces the live model only if we can be confident it beats it by more than 5% (the safe estimate in the table is the lower bound of its gain, adjusted for looking every day and for three challengers), keeps its range at target and is not worse on up/down. Nothing is judged before 20 real days. A challenger that has not qualified 180 days after its start date is retired, not promoted.

## Were the inputs on time?

- Jeweller price visits: **7 of 18** scheduled visits since the schedule began (2026-10-05) ran within 45 minutes of their time (11 did not; the report cannot tell a laptop that was off from a failed visit).
- Overnight model forecast: published on **2 of 4** nights, typically 194 minutes after the US gold close.

## Dates

- 2026-10-16: hourly world-price check (ADR 066), exactly as written in advance.
- 2026-10-22: morning-rate vs last-reading decision for the weekend estimate (ADR 063).

## Anything that fired

- Automatic fallback has not triggered (last checked 2026-10-07).
- Live model changes: none.
