# ADR 061: A `known_at` contract and a leak guard that refuses to score

**Status:** Proposed 2026-09-25 (branch `feat/known-at-leak-guard` and the two PRs stacked on
it). This is evaluation infrastructure. No live-pipeline file changes: nothing here is imported by
`ml/inference.py` or run by `check-price.yml`.

## Context

Review caught three real timing leaks in this repo. No test caught any of them.

| # | Leak | Introduced | Where (file:line at the leaky commit) | Fixed |
|---|---|---|---|---|
| 1 | **Missing embargo.** Walk-forward fold *i* trained on every earlier row. At h2, row *i*-1's label is IBJA's PM two trading days later, so it was not yet published on the test day. The persistence baseline copied that label. | `d49c9a96` (#126, the live evaluator); `e4a271af` (#1903, `config_sweep`, ADR 034's config J selection) | `ml/direction/evaluate.py:393` `train_df = dataset.iloc[:i]` and `:427` `prev_lbl = int(dataset.iloc[i - 1][label_col])` (at `a29a98dd^`); `ml/direction/config_sweep.py:113` `train_df = ds.iloc[:i]` (at `1a0c55c7^`) | `a29a98dd` (#1930), `1a0c55c7` (#1925), `1003e46d` (#1933, ADR 038 A1). ADR 042 v2 kept the same embargo |
| 2 | **Same-day India VIX in the proxy arm.** The proxy label is day *t*'s own move, and the feature was *t*'s VIX close (15:30 IST on *t*). | `8b487292` (#1892) | `ml/direction/preregistration.py:226` `out = augment_with_m1_drivers(out)`, and `ml/direction/config_sweep.py:70-71`, which looks up the close at `as_of_ts` itself (at `b461cf2f^`) | `b461cf2f` (#1949, ADR 038 A2): prior trading day's close |
| 3 | **Kalman nowcast inputs.** IBJA AM/PM are assimilated on their value date and retailer readings on their capture's UTC date. The target is the last Tanishq reading of that date. So GRT/Malabar captures made after the target were used, and IBJA PM was used before 17:00 IST. | `70d5474c` (#2050, unmerged, branch `feat/kalman-nowcast-shadow`) | `ml/kalman_nowcast.py:307` `put(d, Observation(src, ...))` (IBJA on value date, age 0) and `:322` `put(day, ...)` (retail on capture day) | Found by the independent audit #2070 (`scripts/audit_kalman_leak.py`, `reports/kalman_leak_audit/` at `ae70692d`). Strict re-score only; #2050 is unmerged |

In each case the value's **date** was compared, not the **instant it became known**. #2057 took
the first step (`<col>_asof_date` for macro series). ADR 058 (#2051, unmerged) measured the
clocks.

## Decision

1. **`ml/known_at.py` is the one place that states when each source became known.** It returns
   tz-aware UTC instants: IBJA AM/PM at 12:00/17:00 IST on the value date (ASSUMED, the repo-wide
   convention), or the row's `fetched_at` when that is later. Retailer readings use their capture
   time. GC=F daily bars use the 13:30 ET settlement (VERIFIED in ADR 058). INR=X daily uses 23:59
   UTC (conservative). Hourly bars use start + 1 h. Other macro dailies use the exchange close
   (ASSUMED). Feature-store rows have a live vs backfill rule. The module docstring lists every
   convention and marks it VERIFIED or ASSUMED. `Clock` and `at_utc` match ADR 058's
   `ml/timing_alignment.py` signatures, so that module can replace them by import when #2051
   lands. A test asserts the shared clocks agree whenever it is importable. No helper was copied
   from #2051.
2. **`ml/leak_guard.py`.** An input is allowed at prediction moment *t* only if
   `known_at < t`, **strictly**. `assert_known_before` raises `TimingLeakError`, a typed error, not
   a warning. `filter_known_before` returns the usable inputs. `LeakGuard(mode=...)` checks a
   whole run:
   - `"raise"` refuses to score.
   - `"report"` records violations without changing the run. It is used **only** where a registered
     or published number would otherwise change. There the report is the finding, and a test
     documents it.
   Naive timestamps, None, NaT and unparseable values raise `NaiveTimestampError`. This is fail
   closed: there is no "could not check, allow" path.
3. **Prediction moments are declared per harness**, as the latest instant the published claim
   allows:
   - The direction walk-forwards use `ist_day_end(as_of_date)`. `build_dataset` keeps only rows
     whose IBJA PM is dated `as_of_date`, so every forecast is issued after 17:00 IST and before
     IST midnight. The date embargo (`label_date < as_of_date`) is stricter by one day at h1. The
     guard is a necessary condition, not a replacement for the embargo.
   - The proxy arm uses `ist_day_start(t)`, because the prediction must come before day *t*'s move.
   - The backtest uses the first actual's publication.
   - The nowcast uses the target reading's timestamp.

## Wiring (PR 2 and PR 3 of the stack)

| Path | Guard | Mode | Registered numbers |
|---|---|---|---|
| `ml/direction/evaluate.run_walk_forward` (eval-direction.yml, README numbers) | training labels | raise | unchanged: h1/h2 output identical except the new `leak_guard` key (VERIFIED, same data before and after) |
| same | test-row features | **report** (finding F1) | unchanged |
| `ml/direction/config_sweep.run_config_sweep`, with embargo (ADR 038/042 live arm) | training labels | raise | unchanged |
| same, without embargo (ADR 034 reproduction path) | training labels | **report** (this is leak 1) | unchanged; `analysis_prereg_reference.py --check` reproduces v1 and v2 |
| `preregistration.run_proxy_arm` (weekly shadow) | India VIX | raise | unchanged (n 329, p 0.789 on the same data before and after) |
| `ml/backtest.run_backtest` / `yield_folds` (weekly-backtest.yml) | IBJA context | raise | unchanged: context is strictly earlier by construction |
| `scripts/run_nowcast_shadow.py` (G3 shadow) | same-day IBJA vs target reading | **report** (finding F2) | unchanged: M0/M3 predictions are not altered |

Not wired: the #2015 scorecard and the Kalman runner (#2050) are unmerged. When they land, they
should take the same guard. Their leak is replayed in the tests below.

## Replays (`tests/test_leak_guard_replays.py`)

Each leak is rebuilt in the exact input configuration from the commit above. Each one is
**blocked**, and its fixed configuration passes.

- **Leak 1:**
  - `test_pre_1930_training_window_is_blocked`: the `iloc[:i]` window. Row *i*-1's h2 label is
    published 17 h after the fold moment.
  - `test_no_embargo_sweep_is_refused_in_raise_mode`: the real `run_config_sweep` without embargo.
  - `test_no_embargo_sweep_reports_every_fold_by_default`
  - `test_embargoed_sweep_passes_in_raise_mode`
  - `test_real_data_fold_2026_09_18`: ADR 038's last selection fold, on committed data.
- **Leak 2:**
  - `test_same_day_vix_is_blocked_by_the_proxy_arm`: the real `run_proxy_arm`. The close is
    15.5 h late.
  - `test_prior_day_vix_passes`
  - `test_monday_uses_fridays_close`
- **Leak 3:**
  - `test_grt_malabar_after_target_and_pm_before_1700_ist_are_blocked` (2026-07-21: target
    08:22:07Z, GRT and Malabar captured 19:48:27Z, PM 11:30Z).
  - `test_strict_rescore_inputs_pass`: #2070's strict set, from `filter_known_before`.
  - `test_pm_blocked_am_allowed_73_seconds_before` (2026-08-28).
  - `test_am_fix_before_1200_ist_is_blocked` (2026-09-09).
  - `test_repo_fetch_time_also_blocks_the_am_fix`.
  - `test_replay_timestamps_match_the_committed_data`: cross-checks the audit's timestamps against
    `data/`.

The unit tests are in `tests/test_leak_guard.py`. They cover:
- known_at equal to *t* is blocked, and one second earlier is allowed;
- an IBJA PM used at 16:59 IST is blocked;
- a GC=F bar dated D is blocked before 13:30 ET on D;
- naive timestamps are rejected, in every mode.

## Findings (new leaks found by wiring the guard in)

Every number here was VERIFIED by `python scripts/analysis_leak_guard_findings.py` at `18fbd132`.
The output is `reports/leak_guard/findings.json`. No registered or published number was changed.

**F1: direction walk-forward, backfilled test rows (report mode).**
- Which folds: 82 of 154 h1 test folds (77 of 148 at h2) have test-row features published after
  the fold's prediction moment (the end of IST day `as_of_date`). They are exactly the backfill
  test folds. The dataset has 103 `backfill_yfinance` and 72 `live_pit` rows. No live fold is
  flagged, and 0 training-label violations were found.
- Which features: US daily closes dated D. `usd_inr`, `dxy`, `vix` and `us_10y_yield` are flagged
  on 81 folds each, `crude_wti` and `tips` on 80, and `gold_usd` on 21 (winter only, because the
  13:30 ET settlement falls at 18:30 UTC = IST midnight). There are 505 input violations in total.
  The latest is 5.5 h after IST midnight (INR=X at 23:59 UTC).
- This mechanically re-finds ADR 058 A5.
- It is **not an outcome leak**. Every flagged value is known at least 11.5 h before the h1
  label (the next IBJA PM, 11:30 UTC on D+1 or later).
- It **is** a leak against the harness's own claim ("as of IST day D"), and train/serve skew: a
  live row at the same moment holds an intraday quote.
- Refusing these folds would change the published direction numbers (on this data: h1 logistic 49.35% vs
  always-up 50.65%; h2 54.73% vs 58.11%). So they stay in report mode. The fix is ADR 058
  proposal #7 (backfill with values known by 11:30 UTC on D). That needs its own decision.

**F2: the R2 same-day nowcast and the G3 shadow built on it (report mode).**
- What leaks: `scripts/analysis_nowcast.py` pairs each day's last Tanishq reading with that
  day's IBJA AM/PM (`merge_asof` on the UTC date). This is the same pattern as Kalman leak 3.
- How often: on 6 of 62 scored same-day days, IBJA's PM was published after the reading it
  estimates (2026-07-21, 08-18, 08-28, 09-01, 09-02, 09-09). On 09-09 the AM was also published
  after it. With this repo's own `fetched_at`, the same 6 days leak on both fixes.
- Effect (exploratory, not a new registration), one-sided HAC-DM, lag 1, M3 better than M0:
  - all 62 days: M0 MAE Rs 45.48/g, M3 Rs 34.79/g, effective n 50.02, p 0.00196;
  - excluding the 6 days: M0 Rs 41.73/g, M3 Rs 32.93/g, effective n 42.44, p 0.00527.
- R2's direction and significance survive. Its edge shrinks from Rs 10.7 to Rs 8.8/g.
- The G3 shadow (`run_nowcast_shadow.py`) now records `inputs_known_after_target` on every
  logged day, so the forward window shows how many days are affected. Its protocol is unchanged.

**F3: provenance of 38 live snapshot rows.**
- What: 38 `live_pit` rows captured between 2026-06-07 and 2026-08-03 (37 for the AM field) hold
  an IBJA PM that was published up to 16.98 h after their `capture_utc`. #621 repaired these rows
  by replacing the IBJA values and keeping the original capture time. `ibja_rates.parquet`
  `fetched_at` confirms that the repo only had those values later.
- Effect on scoring: none. The direction harness's moment (the end of IST day D) is after the
  PM's publication. But `capture_utc` is not the known_at of those rows' IBJA fields.
- Fix: `ml/known_at.snapshot_field_known_at` therefore uses max(capture, publication) for IBJA
  fields on live rows.

## Consequences

- A new source cannot enter an evaluation without a `known_at` rule. A missing macro clock
  fails a test. A missing timestamp fails closed. See `docs/KNOWN_AT.md`.
- Assumed clocks (IBJA publish times, non-COMEX exchange closes, INR=X) set the precision of the
  guard. They are chosen late on purpose: the guard can over-block, never under-block, if a
  clock is wrong in the conservative direction. Measuring them (ADR 058 follow-ups) tightens the
  guard without changing any code that calls it.
- Report mode keeps a known leak visible without moving a registered number. Removing a
  report-mode guard, or switching it to raise, is a registered-number change and needs its own
  decision.

## Alternatives

- **A warning instead of an error:** rejected. Warnings in CI logs are how leaks 1-3 survived.
- **Per-harness ad-hoc date checks** (what #1930/#1949 did): each one is correct, but a new
  harness starts without one. The shared contract is the point of this ADR.
- **Depending on #2051's `ml/timing_alignment.py` now:** it is unmerged. The signatures match
  instead, so it can be swapped in with one import.

## Amendment A1 (2026-09-26): F1 fixed. Late inputs are excluded from the direction evaluation

**Decision.** GG decision F1, pre-approved in the 2026-09-26 brief: *"exclude every input
published after the prediction moment from the direction evaluation, even though it is before
the outcome. Let the published direction numbers move; report before/after."*

**Method (fixed before any run on the data; this section is committed before the before/after
run).**
- `ml.direction.leak_checks.mask_late_features` runs inside `ml.direction.evaluate.run_walk_forward`,
  on **every row, training and test**, so both follow one convention.
- For each timed feature (`TIMED_FEATURES`) whose `snapshot_field_known_at` is after the row's
  prediction moment (`ist_day_end(as_of_date)`), the value is replaced by the most recent
  earlier row's value that was known by that moment. The donor must have the same `source`.
  The row's `<col>_asof_date` becomes the donor's. With no donor the value becomes NaN, which the
  harness already imputes with training means.
- The test-row feature guard moves from `report` to `raise`. Any late input left over is now an
  error, not a note.
- **Not changed:**
  - `ml.direction.config_sweep` and the pre-registered ADR 038/042 arms. Their registered
    numbers need their own decision.
  - The label embargo.
  - The feature set, the models, `min_train_size`, the seeds.

**Why substitute rather than drop folds.** Dropping the 82 flagged h1 folds would evaluate only
the live period and change the population being scored. Substitution keeps every fold and
gives each one exactly what a forecast issued at that moment could have seen. That is the
convention of ADR 058 proposal 7.

**Reported (before = master's harness, after = this amendment; same data, same commit of
`data/`):**
- For h1 and h2: n test folds, logistic accuracy, LightGBM accuracy, always-up accuracy, and
  the logistic one-sided p-value vs always-up, exactly as `evaluate.py` computes them. Also the
  per-column count of replaced inputs.
- No other test is run on these data for this amendment.
- The ship gate (`ml/direction/gate.py`) is unchanged. A number that moves is reported as it
  is. It is not re-gated here.
