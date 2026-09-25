# ADR 062 — Pre-registration: the Kalman nowcast's forward shadow, scored only when Tanishq is not fresh

**Status:** Accepted 2026-09-25 (GG decision, task 4e). Pre-registration only. This text,
`scripts/run_kalman_shadow.py` and `tests/test_kalman_shadow_v2.py` are frozen by the commit that
adds this file. **No shadow entry has been scored.** The pre-registration hash is the sha256 of
this file's text above a `## Results` heading. At freeze time that is the whole file.

Research and forward shadow only. Nothing a user sees changes. Promotion would be GG's call, in a
separate PR.

**Replaces:** ADR 055's "Forward shadow" section (read at n ≥ 60, scored against the day's last
Tanishq reading). ADR 055's model, its retrospective results and its leak-audit addendum stay as
written.

**Number.** This takes 062, not 061. At write time (2026-09-25) no branch had an ADR 061 or
later. 061 is left free for the known_at leak-guard ADR that another agent is writing on
`feat/known-at-leak-guard`.

## Context

ADR 055's leak audit (PR #2070, addendum in ADR 055) re-scored the Kalman nowcast with every input
restricted to what was known strictly before the target reading. The Kalman model still beat
IBJA × fixed markup and fusion. But on every stratum it lost to Tanishq's own last reading before
the target: ₹13.9/g for that reading against ₹35.1/g for the model, all 90 set-A days. Most of those
days had a Tanishq reading from a few hours earlier, and the board had usually not changed since.

So the model has no job while Tanishq is fresh. Its only possible job is the moment the site
actually needs an estimate: when the Tanishq scrape has failed and the latest reading is old. GG's
decision is to re-register the forward shadow to test exactly that moment, against exactly what
the site would otherwise have: Tanishq's last reading.

**What has been seen before this registration:**
- all of ADR 055's results and the leak audit;
- the timing of past gaps in `data/prices.json`, to size the sample (below). No price value or
  error at a stale moment was computed.

The only run of the new code on real data was one smoke run into a temp file, never committed. It
checked that the script runs, and it printed only the freshness flag and the reading's age. Its
target reading was not looked up. Nothing was scored.

## The frozen test

**Run moment R.** The UTC instant a shadow run starts. The script only runs at "now". There is no
back-dating option, so no entry can be made for a past moment.

**Inputs: everything known strictly before R, and nothing else.** The rule is the leak audit's:
an input is used only if known_at < R. This is `is_available`, `ibja_known_at` and
`comex_known_at` in `scripts/audit_kalman_leak.py`.

| input | known_at |
|---|---|
| Tanishq reading | its timestamp in `data/prices.json` |
| IBJA AM / PM | value date 06:30 UTC / 11:30 UTC (assumed publication times; the data has value dates only) |
| GRT, Malabar national snapshot | `capture_utc` |
| COMEX × USD/INR close dated c | c + 1 day, 00:00 UTC |

**Swap-in.** The known_at guard on `feat/known-at-leak-guard` has not merged yet. When it does,
it replaces those three helpers, and the rule (known_at strictly before R) stays the same. The
script asserts that no input known at or after R reaches the filter.

**The nowcast.** ADR 055's model, unchanged: state, transition, observation equations, parameters,
bounds, estimation and COMEX conversion. Per run:
- The 10 noise parameters are fitted by MLE on the UTC days strictly before R's date.
- The filter runs through every input known before R. That includes any Tanishq reading earlier
  on R's own UTC date.
- The nowcast is the filtered level after the last known input, `exp(E[s])`.
- The band is `exp(E[s] ± 1.2816 × sd)` with `sd² = Var[s] + r_tanishq`, as in ADR 055.

**"Not fresh".** The site's rule, evaluated at R:
- Fresh means R − (timestamp of the last Tanishq reading before R) ≤ 8 hours. This is
  `STALE_THRESHOLD_H = 8` in `app.js` and `_STALE_THRESHOLD_H = 8` in `ml/inference.py`, the same
  threshold the site uses to leave its Tanishq tier.
- **Only entries with an age over 8 hours are scored.** Fresh entries are logged and never scored.

**Target Y.** The **first Tanishq 22K reading with a timestamp strictly after R**. This is the
price the site shows once Tanishq is fresh again. It cannot exist at R.
- If that reading comes more than 72 hours after R, the entry is not scored (logged as
  `no_target_72h`).
- A reading committed after R but stamped before R is never a target. It is also not the
  baseline, which is fixed at run time by timestamp.

**Why the next reading.** ADR 055's level is a random walk. Its point forecast is the same for
any later moment, so the first reading after R measures the right thing.

**Baselines, all fixed at R:**
- **B1 (primary): Tanishq's last reading before R.** This is what the site shows, labelled with
  its age, when nothing newer exists.
- **B2 (secondary): IBJA × fixed markup.** The latest IBJA 916 PM published before R (÷ 10,
  ₹/g) × 1.0138681104363907. That is the scorecard's median Tanishq/IBJA-PM ratio over the first
  30 same-day pairs, as recorded in ADR 055's `results.json`, and it is frozen here.

**Unit of analysis: the episode.** All scored entries that share the same target reading form one
episode. Runs inside one Tanishq outage share their target and their B1, so they are not
independent. For each predictor, the episode's loss is the mean absolute error, in ₹/g, over its
entries. Episodes are ordered by target timestamp.

**Metric.** MAE in ₹/g, with a Newey–West (lag 1) 95% CI.

**Tests.** One-sided paired HAC Diebold–Mariano on episode losses, lag 1
(`diebold_mariano_test`, horizon 2), as ADR 055. H1 of each test: the Kalman model's loss is
lower.
- **H1: Kalman vs B1** (primary), all episodes.
- **H2: Kalman vs B2**, on the episodes where B2 exists.
- Family H1–H2. Bonferroni at α = 0.05/2 = 0.025 and Benjamini–Hochberg at q = 0.05. Both are
  reported.

**Success gate.** PASS only if both hold:
1. n ≥ 30 episodes;
2. H1 is significant under Bonferroni (one-sided p ≤ 0.025).

Anything else is not a pass:
- H2 alone, or H1 under BH only, is **NEGATIVE**;
- fewer than 30 episodes at the hard read date is **INCONCLUSIVE**.

A PASS licenses a promotion *proposal* for the stale-Tanishq tier. It changes nothing on its own.

**Descriptive only, no gate:**
- MAE by day type (weekday/weekend, by the run's UTC date);
- the share of episodes whose target falls inside the 80% band (Wilson 95% CI);
- counts of fresh, pending, `no_target_72h` and baseline-missing entries.

**When it is read.**
- At the first `--score` run on which n ≥ 30 episodes are scored.
- If that has not happened by **2027-03-31**, the read is on that date with whatever n exists, and
  it is labelled INCONCLUSIVE if n < 30.
- Earlier `--score` runs print `NOT YET` and are not reads.

**Sample size (INFERRED, not measured).** Gap timing only, no values:
- Live readings in `data/prices.json` (2026-05-09 to 2026-09-25) show 57 gaps over 8 h, about 11
  a month, and 39 over 11 h.
- At a 3-hourly cadence, a gap of about 11 h or more reliably contains a run past the 8-h mark.
  That gives about 8 episodes a month, so n = 30 lands roughly 3.5–4 months after wiring (about
  mid-January 2027 if wired in early October).
- If timed Tanishq visits (#2078) make gaps rarer, n accrues more slowly. The hard date then
  applies.

**Power (INFERRED).** No estimate of the Kalman model's error at stale moments exists. The audit's
₹13.9 baseline was measured at mostly fresh moments. B1's error at an 8-plus-hour-old reading is
expected to be larger, but it has not been computed. With about 30 episodes, only a sizeable gap
between the two MAEs will be detectable. A non-significant H1 at n = 30 is inconclusive, not
evidence of no effect.

## The shadow log (E1)

Each run appends one entry to `reports/kalman_nowcast/shadow_v2.json`. The entries hold **no
absolute price**, so the file needs no encryption.

**Stored per entry:**
- the run moment, day type, fresh flag and the 8-h threshold;
- the last reading's **timestamp** and its age in hours;
- Kalman − B1 and B2 − B1, in ₹/g;
- the IBJA PM value date and known_at behind B2;
- the band's log sd and z;
- the input provenance:
  - per source, the count and latest known_at of inputs inside the filter's window;
  - every input known on R's own date, with its known_at;
  - the latest known_at overall;
- the fitted parameters, the fit status, this ADR's sha256 and the code commit.

**Scoring** recovers B1 and Y from `data/prices.json` by timestamp. The Kalman nowcast is B1 plus
its stored difference, and B2 is B1 plus its difference. `prices.json`'s git history is never
rewritten, so the levels stay recoverable. A test asserts that no Rs/g level appears in an entry.

**Not wired into any workflow.** The orchestrator does that after E1 (#2075).

## Deviations from ADR 055 (fixed here)

- **The nowcast now uses earlier same-day Tanishq readings.** ADR 055's nowcast never saw its own
  day's Tanishq, because its target was that day. Here the target is a later reading, and any
  reading before R is fair information.
- **The nowcast is taken at R, not per UTC day.** The information set is "known before R", not
  "the day's last capture".
- **Episodes replace days as the unit.**
- **The weekday/weekend T-family and the band-coverage gate are dropped.** There are too few stale
  episodes to split, and the question is now only whether the model beats the reading the site
  would otherwise show.

## What would change what

- **PASS.** A promotion PR can propose using the Kalman nowcast, labelled as an estimate, when
  Tanishq is over 8 h old. It needs GG's go and its own band check.
- **NEGATIVE or INCONCLUSIVE.** The stale tier keeps its current behaviour. The Kalman model is
  retired or kept in shadow, and the record says which.

## Alternatives considered

- **Target = the day's last Tanishq reading (ADR 055).** Rejected: at a stale moment that reading
  can be the very one the site is missing, and on fresh days it rewards carry-forward.
- **Every entry as a unit.** Rejected: runs inside one outage share their target and baseline,
  which would inflate n.
- **Encrypting the log instead (#2075).** Not needed, because the log stores differences and
  timestamps only.
