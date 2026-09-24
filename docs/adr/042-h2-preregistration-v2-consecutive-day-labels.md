# ADR 042 — Pre-Registration v2: the h2 "Config J" Test on Consecutive-Day IBJA Labels

**Status:** Accepted 2026-09-24 (GG decision G2). **Supersedes ADR 038 (v1, as amended by A1 and
A2) before any post-registration day was scored.** Pre-registration only: no promotion and no
change to the gate or to anything a user sees. The live arm runs from `weekly-backtest.yml` and
writes only to `data/preregistered_h2_shadow_results.json`.

**Extends:** ADR 038 (the v1 registration), ADR 034 (config J).

---

## Context

### The labels bridged holes in the IBJA record

`ml.direction.dataset.build_dataset` labelled each day with "the next IBJA row" (h1) and "the row
after that" (h2). `data/ibja_rates.parquet` has 266 rows from 2022-01-20 to 2026-09-23. Before
2025-Q2 it is not daily. After that it still has holes of 14, 20, 24, 27, 95 and 101 days. So "the
next row" was sometimes months away.

Measured on the live dataset at `d0f18e67` (2026-09-24):

| label | rows | spanning more than a normal step | longest span |
|---|---|---|---|
| h2 ("2 trading days") | 182 | 13 span more than 6 calendar days | 122 days |
| h1 ("next trading day") | 184 | 10 span more than 5 calendar days | 101 days |

The h1 problem was not in the brief. It is the same defect.

Those rows went into every live-IBJA direction number: the published walk-forward
(`data/direction_baseline.json`), ADR 034's config J selection, ADR 038's reference figures, and
ADR 040's live-IBJA diagnosis.

### What "consecutive" means here

IBJA publishes on weekdays, except on market holidays. A step between two IBJA rows counts as
consecutive when **at most one weekday between them has no publication** (`np.busday_count(prev,
next) <= 2`). That allows one holiday and treats anything longer as a hole. In the dense data since
2025-04, every step with exactly one missing weekday falls on a real holiday:

- Good Friday, 2025-04-18
- Maharashtra Day, 2025-05-01 and 2026-05-01 (IBJA is Mumbai-based)
- Muharram, around 2026-06-26
- Ganesh Chaturthi, 2026-09-14

The IBJA holiday calendar for each year is not in the repo, so this rule is a judgement. The
strict alternative (no missing weekday at all) was considered under "Alternatives". A label is
built only when **every** step from the capture day to the label day is consecutive:

- If the h1 step crosses a hole, the row is dropped.
- If a later step crosses a hole, the h2/hN label is left empty.

After the fix: 174 rows and 169 h2 labels. The longest h2 span is 5 calendar days (Thursday to
Tuesday across a Friday holiday), and the longest h1 span is 4.

### What the clean labels do to the reference figures

All four runs below use config J on the selection folds (as_of ≤ 2026-09-18), in the pinned
environment (`ml/requirements-inference.lock`: scikit-learn 1.9.0, LightGBM 4.7.0, pandas 3.0.6,
numpy 2.4.6, scipy 1.17.1). The test is the one-sided HAC Diebold-Mariano test on 0/1 loss against
always-up, with lag 1.

| labels | embargo | n | effective n | config J vs always-up | one-sided p | n for 80% power (v1 construction) |
|---|---|---|---|---|---|---|
| old (bridging) | no — **v1 reference (A2)** | 161 | 112.52 | 65.84% vs 59.01% | 0.0043 | 144.17 |
| old (bridging) | yes (A1 finding) | 159 | 159.20 | 60.38% vs 59.75% | 0.327 | 4,903 |
| **consecutive** | no | 148 | 121.42 | 58.78% vs 59.46% | 0.575 | — (model is worse) |
| **consecutive** | yes — **v2 reference** | 146 | 111.31 | 62.33% vs 58.90% | 0.157 | **891.70** |

Two things follow from this table:

- **On clean labels, the original selection result disappears.** Without the embargo, config J is
  0.7 points *below* always-up. The 6.8-point edge that selected it (ADR 034) depended on the
  leak (A1) and on the bridged labels.
- **With the embargo on clean labels, the edge is 3.4 points and not significant (p = 0.157).**
  This is what v2 tests. Honestly powering a 3.4-point edge takes far more data than 144 effective
  folds.

### The v1 reference discrepancy has a cause: library versions

ADR 038 A2b could not reproduce the first freeze (p 0.00340, effective n 119.38, long-run variance
0.10260). It suggested an uncommitted scratch computation. **That was wrong.** The first freeze
reproduces exactly from the committed code in this machine's conda base environment:

- scikit-learn 1.7.2
- LightGBM 4.6.0
- pandas 2.2.3
- numpy 2.4.4
- scipy 1.15.3

On the same data, that environment gives n 161, effective n 119.379, long-run variance 0.10260,
p 0.00340 and n-for-power 135.89. A2b compared only nearby versions (scikit-learn 1.9.0 against
1.9.1), which agree. The older stack moves individual fold probabilities across 0.5, so the same
win/loss totals land on different days.

The effect is not negligible. On clean labels with the embargo, the conda stack gives 63.01% vs
58.90% at p = 0.139, against 62.33% at p = 0.157 in the pinned stack. **The reference figures are a
property of code, data and library versions together.** v2 therefore makes the library versions
part of the frozen record.

## Decision

Register v2 now and mark v1 superseded. No v1 or v2 post-registration day has been scored yet:
`data/preregistered_h2_shadow_results.json` does not exist, and the step has never run.

**Frozen, unchanged from v1:**
- Model configuration (`PREREGISTERED_CONFIG`, ADR 034's config J).
- The test: one-sided HAC Diebold-Mariano on 0/1 loss against always-up, lag = h − 1 = 1.
- α = 0.05.
- The embargo: a training row is used only if its `label_date_h2` is before the test day.

**Changed in v2:**
1. **Labels.** Only consecutive IBJA publication days are used
   (`build_dataset(require_consecutive=True)`, the new default for every caller).
2. **Registration date.** `CONFIRMATORY_AFTER_AS_OF = 2026-09-24`. Only test days with `as_of_date`
   after that count; everything earlier is training data only.
3. **Reference figures.** These are re-frozen on the clean labels under the confirmatory protocol
   (the "v2 reference" row above). The fold digest is `f715a48e…9ad751ee`. Library versions are part
   of the frozen record, so a `--check` in a different environment fails and names the library
   instead of reporting a silent numeric mismatch. Reproduce with
   `python scripts/analysis_prereg_reference.py --check` (v2, the default) and `--protocol v1 --check`
   (v1, still reproducible via `require_consecutive=False`). Run both in the pinned environment.
4. **Power target: 891.70 effective folds.** The rule is `max(v1's 144.17, the v2 reference's own
   figure)`. It uses the same conservative construction as v1: std = √(long-run variance), compared
   against effective n. Per G1/G2, it can never be lower than 144.17.

   **For future registrations only (G1):** that construction counts autocorrelation twice. The
   consistent formula uses std = √γ₀. It gives about 101 effective folds on the v1 reference, and
   about 680 on the v2 reference. v2 does not use it.

v2 is written to the log as `protocol_version: "adr042-v2"`. Each live-arm entry also records
`consecutive_day_labels: true` and its own `preregistered_n_for_power`. The proxy arm is unchanged:
it uses proxy labels, not IBJA labels, and is exploratory.

## Consequences

- **The confirmatory test will not be read for years.** New labelled days arrive at roughly 0.66–0.70
  per calendar day, so 891.7 effective folds is several years away. That matches ADR 040: the live
  direction signal is small, and only years more real IBJA days can settle it. Weekly entries still
  log n, effective n and p. No interim result is confirmatory.
- **First scoreable day.** A day counts only when its h2 label has matured and its as_of_date is
  after 2026-09-24. The earliest is as_of 2026-09-25 (a Friday), whose h2 label is Tuesday
  2026-09-29. **The Sunday 2026-09-27 run scores nothing (n = 0) by construction.** An amendment
  before the 2026-10-04 run would still come before any scored day.
- **The published direction numbers change.** `ml.direction.evaluate` uses the clean labels from
  this change onward. `eval-direction.yml` runs on the merge (the path `ml/direction/**`) and
  republishes `data/direction_baseline.json`. GG pre-approved that numbers-only update.
- **Earlier write-ups used the bridged labels.** ADR 034's selection result, ADR 038's references
  and ADR 040's live-IBJA diagnosis need re-measuring on clean labels before anyone cites them again.
  The proxy- and COMEX-based parts of ADR 040 are not affected.
- **A future IBJA backfill that fills old holes would change which rows exist**, and with them the
  v2 fold digest. `--check` would then fail loudly. It would not drift silently.

## Alternatives considered

- **Strict consecutiveness (no missing weekday at all).** Rejected. It would discard the known
  holiday steps listed above, and in the dense data every one-missing-weekday step falls on a real
  holiday.
- **Keep the power target at 144.17.** Rejected. At 144 effective folds, a 3.4-point edge would be
  detected about 26% of the time. Reading the test there would mostly produce false negatives,
  presented as a planned test. G2 sets 144.17 as a floor, not a fixed number.
- **Let v1 run on Sunday and register v2 later.** Not needed: v2 is wired before the first run.
- **Fix only the h2 labels.** Rejected. The h1 labels have the same defect (10 rows, up to 101 days).
