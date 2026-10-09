# ADR 074: pre-registration of three shadow-only fixes for P3's known weaknesses

**Status:** Proposed 2026-10-09 (GG brief 4e). Pre-registration only: no model code is added by this
file. Each model below is implemented in its own PR; **forward days count from the merge of that PR**,
and nothing here is a result. Research and shadow only; a model reaches the live forecast only through
ADR 072 (a new amendment adds it to the challenger pool, with its own family size), never from this
document.

## Context (what is known, disclosed, from ADR 071 and ADR 072 and `docs/MODEL_AUDIT_2026-10.md`)

Retrospective, descriptive, not a test, and read before these definitions were written, so any
retrospective comparison of the three models is biased in their favour and never counts:
- P3's realised return is 1.64 times its forecast return (calibration slope; 95% block-bootstrap
  interval [0.98, 2.28]): the point forecast is too small. The conformal range is built from realised
  errors scaled by volatility, so it already widens; coverage of the stated 80% range has to be read on
  forward days before the range is called wrong.
- Monday is the only weekday where P3 is worse than repeating the last rate (+7.8%, n 29). ADR 071's
  `p3_monday` (already frozen and being scored) fits Monday's own slope.

## The three models (frozen)

All three forecast the next IBJA fix on decision day D with the same inputs, clocks and leak guard as
P3 (ADR 069, ADR 073); `y` is the next fix's log return, `ret` P3's forecast, `vol` the volatility
used for the conformal scale.

| Id | Definition |
|---|---|
| **W1 `p3_scaled`** | `ret_W1 = m x ret`, with `m = clip(beta, 1.0, 2.0)` and `beta` the least-squares slope through the origin of `y` on `ret` over the most recent 60 resolved out-of-sample folds with `d1 <= d0` (fewer than 30 such folds: `m = 1`). Its range comes from its own folds with the live conformal code. |
| **W2 `p3_bandbucket`** | The point forecast is P3's. The conformal 80% quantile of `abs(error) / (vol x pm0)` is computed separately for folds whose forecast size `abs(ret) / vol` is above, and at or below, its median over the most recent 60 folds; a bucket with fewer than 15 folds in the conformal window uses the pooled quantile. The range widens only where the model forecasts large moves. |
| **W3 `p3_monday_split`** | Monday decisions only; every other weekday is P3. Monday's world move is split at the Sunday open into the part before it and the part after, each with its own slope fitted on resolved Mondays (at least 15, else P3). **Step 0 (feasibility, by 2026-10-23):** it needs world prices with trustworthy known-at times across the weekend boundary. If the hourly record cannot supply them, W3 is not built and that is reported here. |

## Scoring and what counts (frozen)

- **Data:** forward folds only (`retro` not true), logged every check-price run in a new file per
  model, encrypted like the other raw-IBJA-derived records (ADR 060 registry) before any file is
  committed.
- **W1:** compared with P3 under the ADR 072 rule (sequential bound on the 5%-better error difference,
  decision-day horizon, coverage and direction gates). Reported only until an amendment of ADR 072
  adds it to the challenger pool.
- **W2:** judged on the range, not the point: coverage of the nominal 80% with an exact 95% Wilson
  interval (adequate if the interval contains 0.80), and mean range width no more than 10% wider than
  P3's on the same days; at least 40 forward decision days before any statement, and the same days
  split by the two buckets are reported separately.
- **W3:** its Monday stratum (n from 2026-10-12) against P3 and against `p3_monday` on the same days,
  at least 15 forward Mondays (about 4 months) before any statement.
- **Multiplicity:** three further models are not free looks. Any promotion path goes through an
  ADR 072 amendment that raises the family size; the 0.05 error rate and the 5% minimum gain do not
  move.

## Frozen specification hash

SHA-256 of the canonical JSON of the specification below (checked by `tests/test_adr074.py`):

```json
{"w1":{"m_clip":[1.0,2.0],"min_folds":30,"window":60},"w2":{"bucket_split":"median_abs_ret_over_vol_60","min_bucket_folds":15},"w3":{"feasibility_by":"2026-10-23","min_mondays":15,"split":"sunday_open"},"coverage_nominal":0.8,"width_cap":1.1,"min_forward_days_w2":40}
```

`sha256 = f6a3fe6c50cc17997e2844441e49737bd3be0f32cd990ddde44b2daf77801cb0`

## Consequences

- None of this changes a number users see today.
- The rule that keeps these honest is the one that applies to every challenger: forward days, a
  recorded hash, a status line in `docs/MODEL_STATUS.md`, and a verdict only after the stated days.
- Honest limits: W1 may only re-scale a slope that ADR 071's `p3_roll60` already tests from another
  angle; W2 cannot improve the point forecast; W3 may be impossible with the data on hand.

## Results

None yet. Forward n at registration: 0 for all three.
