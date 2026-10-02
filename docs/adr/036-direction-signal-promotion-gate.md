# ADR 036 — A Passing Gate Is a Measurement, Never an Authorization

**Status:** Accepted, implemented 2026-09-23. Behavior-preserving — self-merged per GG's explicit
pre-authorization ("this keeps behaviour unchanged, so merge it yourself").

---

## Context

GG merged PR #1903 (a shadow-only diagnosis module for the direction model's flatline) and asked,
before anything else: does a passing `ml.direction.gate.decide_direction_signal`/
`decide_timing_signal` gate automatically reach users, and did #1903 change the model the weekly gate
actually evaluates? Promoting a model is explicitly GG's decision, never CC's.

## Investigation (traced end to end, with evidence)

**(a) Does a passing gate automatically un-dark the signal for users?** No. Traced every consumer of
`data/direction_baseline.json` and the gate functions:

- `.github/workflows/eval-direction.yml` (weekly, Monday 04:00 UTC): runs `ml.direction.evaluate`,
  commits the refreshed `direction_baseline.json`. Its own header comment already states: *"This
  workflow only MEASURES and records — it never ships a user-facing claim."*
- `app.js`'s "Direction signal" section (the only UI surface that could plausibly show this) is a
  **hardcoded, permanently-"off" DARK state** (ADR 019/020) — the copy literally reads *"We test
  direction models weekly; none beats the 'gold usually rises' base rate with significance, so we
  show NO directional prediction."* Its visibility is gated on `fc.chronos_companion.status` — an
  **entirely unrelated field** (the separate Chronos price-*magnitude* forecast's own success/failure,
  not the direction classifier's gate at all).
- Repo-wide grep for `direction_baseline`, `probability_gate`, `timing_gate`, `decide_direction_signal`,
  `decide_timing_signal` outside `ml/direction/**` and test files: zero real hits. (`i18n.js` and
  `ml/notifications.py` each had one hit — both are prose *comments* explaining unrelated history, not
  code that reads these fields.)

**Nothing currently reaches users from a passing gate.** But that's a fact about the CURRENT code, not
a structural guarantee — nothing stops a future change from wiring `app.js` directly to the gate's
output without anyone treating that as a promotion decision.

**(b) Did #1903 change the live-evaluated model?** No — verified directly against the actual merge
commit diff, not from memory: #1903 touched only `docs/adr/034-...md`, `ml/direction/config_sweep.py`
(new file), `tests/test_config_sweep.py` (new file), `tests/test_count_baseline.json`. It did **not**
touch `ml/direction/models.py`, `ml/direction/evaluate.py`, or `ml/direction/gate.py`.
`config_sweep.py` is imported by nothing outside its own tests. The live `evaluate.py` call sites
(`fit_logistic(X_train, y_train, calibration_method=calibration_method, random_state=42, cv=3)` and
`fit_lightgbm(X_train, y_train, random_state=42)`) pass neither `C` nor `class_weight` — both new,
optional parameters #1901 added — so they resolve to the exact same defaults as before either PR
merged. **The weekly-evaluated model is bit-for-bit unchanged.**

(#1901's `ml/direction/**` path match did trigger `eval-direction.yml` to re-run and commit a refresh
— `chore: refresh direction eval results` — but that's the workflow re-running the *unchanged*
computation on the feature store's latest rows, not a behavior change from #1903 itself.)

## Decision

Both of GG's trigger conditions for the mandatory revert-and-safeguard are **false** — no revert of
#1903 is needed. But the underlying risk (a fact-about-today that isn't enforced by any code) is real
and worth closing regardless, since GG explicitly pre-authorized exactly this shape of change as
self-mergeable: add a **mechanically enforced** promotion gate, not just a documented one.

1. `ml.direction.gate.is_signal_promoted(path=PROMOTION_RECORD_PATH) -> bool` — `True` only if
   `data/direction_promotion_record.json` exists. Any future code that wants to show the direction/
   timing signal MUST check this in addition to the gate itself.
2. `scripts/check_direction_signal_not_wired_without_promotion.py` — a new CI check (added to
   `lint.yml`, same pattern as `check_bot_pr_sync_allowlist.py`): fails if any user-facing surface
   (`app.js`, `i18n.js`, `index.html`, `service-worker.js`, `ml/notifications.py`,
   `ml/notification_routing.py`, `ml/public_copy.py`) references the gate's decision UNLESS the
   promotion record exists. Currently: 0 references found, check passes.
3. Proof, per GG's explicit ask: `tests/test_direction_gate.py::TestPromotionGateNeverWired` — a
   synthetic baseline that ships **both** `decide_direction_signal` and the stricter
   `decide_timing_signal` (both `ship: True`), then asserts `is_signal_promoted()` is still `False`
   with no record present, confirms the default (no-record) state, and statically re-verifies the
   real repo has zero live-surface references — belt-and-suspenders with the CI script.

## Consequences

- Zero behavior change: no file any user's browser or notification path touches was modified.
- `.github/workflows/lint.yml` gained one new job (additive, not modifying any existing job) — the one
  intentional exception to "no `.github/workflows/` changes" in this continuation's other items, since
  GG's own item 1 explicitly asked for a CI-mechanized safeguard.
- Any future PR that wires the direction signal into `app.js`/notifications will now fail CI unless it
  also adds `data/direction_promotion_record.json` — which forces that decision to be explicit and
  visible in the diff, not something that can slip in as a side effect of an unrelated change.

## Alternatives considered

- **A runtime check inside `ml/inference.py` or `app.js` itself**, gating some hypothetical future
  signal-rendering code path. Rejected: there is no such code path today to gate — adding one
  preemptively would be speculative complexity for a feature that doesn't exist. The CI check instead
  catches the moment such a path is FIRST added, which is the right time to force the decision.
- **A repo setting or branch-protection rule** instead of a script. Rejected: this repo's established
  convention for exactly this class of guarantee (`check_bot_pr_sync_allowlist.py`,
  `check_pr_boundary_leak.py`) is a checked-in Python script in CI, reviewable in a normal diff — no
  reason to deviate for this one.
