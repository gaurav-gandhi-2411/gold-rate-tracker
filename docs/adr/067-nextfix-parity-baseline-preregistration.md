# ADR 067: pre-registration: does the ridge + neural-net next-fix model beat a one-line parity rule?

**Status:** Proposed 2026-10-05. Pre-registration. This text and the code it names
(`scripts/analysis_nextfix_parity.py`, `tests/test_analysis_nextfix_parity.py`) are frozen by the
commit that adds this file, **before the parity rules have been scored on any day**. Results are
appended below a `## Results` heading. The pre-registration hash is the sha256 of this file's text
above that heading. Research only: nothing a user sees changes; any change to the live model is
GG's call in a separate PR.

**Builds on:** ADR 064 / 065 (the promoted model, `ml/nextfix.py`), ADR 061 (leak guard).

## Context

The promoted model's edge plausibly comes from one fact: IBJA's PM fix at ~11:30 UTC lags the
world price, which keeps trading until the US close. If that is all the model learns, the right
baseline is not "always up" (48.9%) or flat-hold, but a parity rule that uses the same fact with
no fitting. ADR 064's published comparison never included one.

**What was seen before this freeze (disclosed).** The model's own track record in
`data/nextfix_oos.json` (143 folds, 2025-07-17..2026-09-30): MAE, direction accuracy and coverage.
The parity rules below have never been scored on any day. Because the ensemble's design (features,
ridge + NN mix) was chosen on this same sample, the comparison is biased **in the ensemble's
favour**. A parity rule that matches it here is therefore strong evidence for the rule.

The only forward data (folds whose decision time is after the 2026-10-02 promotion) is n = 0
resolved at the time of writing: IBJA did not publish on 2026-10-02 (holiday) or the weekend, and
the 2026-10-01 forecast resolves with the 2026-10-05 PM fix. The forward test below is therefore
registered now and scored later.

## Decision: the rules (frozen)

Notation, on the model's own pairs (`ml.nextfix.build_pairs`): `pm0` the PM fix of IBJA day D,
`pm1` the next IBJA PM fix, `g0`, `gprev` the world gold-in-rupees value at the US close of D and
of the day before, `x_glob = ln(g0/gprev)`, `bdev` the log basis `ln(g0/pm0)` minus its mean over
the previous 20 pairs.

| Id | Rule (point forecast of `ln(pm1/pm0)`) | Parameters |
|---|---|---|
| **P1 (primary)** | `x_glob`: last official rate times the world move over D | none |
| P2 | `bdev`: world value at D's close times the usual basis, as a return on `pm0` | none |
| P3 | `b * x_glob`, `b` by expanding-window least squares through the origin on pairs resolved before D | 1 |
| E | the shipped ensemble, its recorded `ret` | n/a |

Direction of a rule = sign of its forecast. Direction of E = `p_up > 0.5` (as published).

## Decision: the test (frozen)

- **Sample:** every fold in `data/nextfix_oos.json` as of the run (retrospective, 143 at freeze),
  plus separately the forward folds (`d0` on or after 2026-10-01: the first forecast issued live
  under the promoted code, at 2026-10-02 18:22 UTC) when n >= 20.
- **Metric 1:** MAE in Rs./g of 22K (IBJA), E vs each rule, paired by day. Statistic: the mean
  paired absolute-error difference; two-sided Diebold-Mariano with Newey-West (4 lags), plus a
  moving-block bootstrap (blocks of 5, 2000 draws, seed 42) 95% interval of the relative change.
  Effective n is reported as n / (1 + 2 * sum of the first 4 Bartlett-weighted autocorrelations of
  the paired difference).
- **Metric 2:** direction accuracy on days the fix moved, E vs each rule, exact McNemar test.
- **Multiplicity:** 3 rules, Bonferroni alpha = 0.05 / 3 = 0.0167 (per-rule p also reported with BH).
- **"E clearly beats rule R":** E's MAE is at least 2% lower than R's (the repo's champion-challenger
  margin, rule 41), **and** the DM p-value for that difference is below 0.0167, **and** E's
  direction accuracy is not significantly lower.
- **Decision rule.** If E clearly beats P1, P2 and P3, keep E and say so. Otherwise recommend the
  simplest rule that E does not clearly beat (P1 over P2 over P3, by simplicity), for GG to decide.
  Nothing ships from this ADR.
- **Not a reason to switch alone:** the range model. Whichever point model is used, the
  conformal range is recomputed on that model's own out-of-sample errors, so the comparison of
  coverage and width is also reported (same conformal recipe as ADR 064).

## Limits

- 143 days with strong autocorrelation: expect wide intervals; a non-significant difference is not
  proof of equivalence. The report states the interval of the difference.
- `x_glob` covers the whole of D's trading day, including hours before the 11:30 UTC fix that the
  fix already reflects. An hourly "since the fix" rule would be sharper; hourly bars exist only for
  the last ~60 days (ADR 066 shadow), so it is out of this test.
- Holiday and weekend gaps (pm1 several days after pm0): the world moved on days after D that no
  feature sees. They are reported as a stratum, not removed.

## Results

Pre-registration hash (sha256 of this file as committed in `bcaa0bac`, before this section):
`04133349a0f114cffaea3d57ec233e7797708cd031d8f82383ce646fd22ccaab`. Frozen at commit `bcaa0bac`;
run 2026-10-05 on that commit's code plus one post-hoc function (marked below). Artifact:
`reports/model_audit_2026-10/parity.json`. Retrospective sample: 143 folds, 2025-07-17 ..
2026-09-30. Forward sample: **n = 0** (the 2026-10-01 forecast resolves with the 2026-10-05 PM fix);
not scored.

| Rule | MAE Rs./g (E = 105.95) | E vs rule, 95% block-bootstrap CI | n_eff | DM p (Bonferroni) | Direction E / rule (McNemar p) | Clearly beaten by E? |
|---|---|---|---|---|---|---|
| P1 `x_glob` | 149.81 | -29.3% [-38.7, -18.6] | 120.9 | 1.5e-5 (4.6e-5) | 64.3% / 62.9% (0.86) | **yes** |
| P2 `bdev` | 158.33 | -33.1% [-49.1, -8.5] | 50.9 | 0.033 (0.100) | 64.3% / 64.3% (1.00) | no (fails Bonferroni) |
| P3 `b * x_glob` | 107.27 | **-1.2% [-4.2, +1.6]** | 143.0 | 0.41 (1.00) | 64.3% / 62.9% (0.86) | no |

Flat-hold (always the last fix): MAE 115.77; always-up direction 48.95%. All numbers VERIFIED
(measured by `scripts/analysis_nextfix_parity.py`).

**Mechanical outcome of the frozen rule:** "recommend the simplest rule E does not clearly beat" =
P2. That outcome is an artefact of low power (P2's n_eff is 51), not a sensible recommendation: P2
and P1 are worse than flat-hold itself (MAE 158 and 150 vs 116). Reported as is, not edited.

**What the data say (reading, not part of the registered test):**
- The literal rule "last fix x the world's overnight move" (P1) is **worse than flat-hold**: the
  fix already reflects most of day D's world move (it was published at 11:30 UTC, mid-day).
- A single fitted slope on that move (P3, b about 0.19, range 0.06-0.28 across the walk) captures
  nearly all of the ensemble's edge. **EXPLORATORY, post hoc** (added after the results above were
  seen): P3 vs flat-hold = -7.3% MAE, CI [-13.2, -1.5], DM p = 0.023, n_eff 94.8; P3 direction
  accuracy 62.9% vs always-up 48.95%.
- The ensemble's additional gain over P3 is 1.2% with an interval that includes zero, on a sample
  that favours the ensemble (see Limits). Ridge + 5 networks is therefore **not shown to be better
  than a one-parameter regression**. Decision for GG (nothing changes from this ADR): keep the
  ensemble, or ship P3 as the simpler, explainable alternative. Neither is demonstrably better on
  143 days.
