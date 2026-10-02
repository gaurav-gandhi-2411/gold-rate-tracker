# ADR 063: the G3 nowcast-shadow decision counts only days whose IBJA inputs were on time (F2)

**Status:** Accepted 2026-09-26 (GG decision F2, pre-approved in the 2026-09-26 brief). It was
set before the shadow logged any day: `data/nowcast_shadow_log.json` does not exist on any
branch as of this commit.

## Context

- G3 (#1983) runs the R2 morning-fix nowcast in forward shadow. M3 (AM + PM) is compared with
  M0 (PM only) on same-day days after 2026-09-24. A promotion PR is prepared after 2026-10-22,
  if M3 holds.
- ADR 061 finding F2 showed the pattern R2 was chosen on: a day's last Tanishq reading is paired
  with that day's IBJA fixes, even when IBJA published after the reading. That happened on 6 of
  R2's 62 same-day days (2026-07-21, 08-18, 08-28, 09-01, 09-02 and 09-09).
- Those 6 days fall **before** the shadow window, so they are not in the 2026-10-22 decision's
  data. The same pattern can recur inside the window, though. #2090 made the runner record it,
  as `inputs_known_after_target`, but in report mode only.

## Decision

- The decision numbers in the shadow summary (`n_same_day`, the MAEs, the one-sided HAC-DM p,
  effective n) use **certified** same-day days only. A certified day is one where no IBJA input
  was known at or after the reading it estimates.
- `all_same_day` keeps every same-day day in, for comparison. `n_same_day_excluded_late_ibja`
  counts the excluded days.
- Predictions are still logged for every day, unchanged. The log stays append-only.
- A day logged without the field is certified at summary time from the same persisted
  timestamps. That happens when it was logged by master's pre-#2090 runner, for example on
  Sunday 2026-09-27 if #2119 has not merged by then. The certification uses
  `data/prices.json`'s target timestamp and `ibja_rates.parquet`'s `fetched_at`. Logged
  predictions are never rewritten. With no certifier, such a day does not count.
- For the promotion PR's context, R2's historical edge with the 6 days excluded is already
  recorded in ADR 061 F2 (exploratory):
  - M0 Rs 41.73/g, M3 Rs 32.93/g, effective n 42.44, p 0.00527;
  - all 62 days: Rs 45.48 vs 34.79, p 0.00196.

## Consequences

- **Fewer days.** The window may yield fewer than R2's 62 days. A day with a late PM is
  excluded, not scored. The count is visible in every summary.
- **Protocol unchanged otherwise.** Nothing else in G3 changes: the arms, the test and the
  window.
