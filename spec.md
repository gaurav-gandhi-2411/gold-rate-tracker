# Project Spec: gold-rate-tracker — Phase 4 Sprint 1 (Φ2 → Φ3 → Φ4)

## Goal

Three sequential PRs that complete Phase 4 Sprint 1: align the PWA with Phase 3's production schema (Φ2), prepare for calibration gate unlock around 2026-06-02 (Φ3), and harden T1/T2 notification gating against single-sample Chronos direction noise (Φ4).

After this sprint, the PWA accurately reflects the production architecture, calibration auto-activates with operator notification, and notifications fire on multi-sample consensus rather than coin-flip single-sample direction.

## Current state (existing project)

This is an in-production ML project with established discipline norms. **The orchestrator MUST read `CURRENT_STATE.md` in the repo root before any planning or executor invocation.** That document contains:

- Load-bearing files and why they shouldn't be casually touched
- Conventions (code style, tests, schemas, notifications)
- Decisions made and the rationale (architecture, data sources, deferred work)
- Dead ends already explored — do NOT re-investigate
- Discipline norms the orchestrator must inherit (flag-and-stop, all-CI-green, ADR-for-NO, etc.)

**Prerequisite:** Φ1 (PR #30, hygiene + Lint CI recovery) must be merged to master before this sprint starts. If `gh run list --workflow Lint --branch master --limit 1` does not show success, escalate immediately.

## Scope

### In scope — three sequential PRs

**Φ2 — PWA schema alignment**
- Update `app.js` to read new `forecast.json` schema (`headline.*` and `chronos_companion.*` blocks)
- Update backtest stats section to read new `backtest.json` aggregate fields (`mae_5d_avg_chronos`, `mae_5d_avg_naive`, `dir_acc_5d_chronos`, `wilcoxon_p`, `n_folds`)
- Surface `chronos_companion` block as a directional signal display in UI (`lean_direction`, `lean_strength_pct`, `direction_acc_30f`)
- Update methodology drawer copy — remove "LightGBM" references, label "naive flat-hold" correctly
- Add a single-sentence explanation near the PI band for why bands are wide post-PR-H
- Verify with a live-site smoke check (console clean, fields populated)

**Φ3 — Calibration gate prep**
- Add T6 notification trigger in `ml/notifications.py`: fires once per IST day when `data/calibration.json` shows `valid: true` for the first time after being `false`
- Implement using idempotent daily dedup via new `last_t6_fired_date_ist` state field (mirrors T5 pattern; no need to cache prior-run calibration state separately)
- Add `workflow_dispatch` trigger on `.github/workflows/weekly-backtest.yml` for manual re-run capability
- Add integration test for calibrated Chronos output path (calibration becomes valid → companion correctly calibrated → T6 fires)

**Φ4 — Multi-sample Chronos probe + majority-direction gating**
- Modify `ml/chronos_forecast.py`: probe runs 5 independent forecast samples per cycle (was 1)
- Bump `chronos_probe.json` schema_version to 2 with new fields: `num_samples`, `sample_directions`, `majority_direction`, `direction_consensus`
- T1/T2 in `ml/notifications.py`: gate on `majority_direction` (not single-run `lean_direction`) AND `direction_consensus >= 0.6` (3 of 5 minimum)
- Tests: mocked probe with mixed directions, assert majority computation correct; low-consensus probe blocks T1/T2
- Write ADR 015 documenting consensus gating rationale (the PR E direction-flip evidence motivates this)

### Out of scope — do not build

- Chronos-2 multivariate (deferred to ≥250 IBJA rows, ~2026-09)
- LightGBM residual head (gate not met; Chronos/Naive ratio is 1.104, exceeds 1.00 threshold)
- Wayback PDF deep-history extraction (ceiling appears genuine; deferred)
- Direction accuracy in notification body (natural follow-up to Φ2/Φ4, but separate PR)
- ADR 006 retroactive write (pure doc work, deferred)
- Any data-source changes (single-series IBJA stays)
- Any backtest methodology changes
- Any change to legacy aliases in `forecast.json` top level (`predicted_22k`, `lower`, `upper` stay until a coordinated PWA-migration PR; not this sprint)
- Any change to `check-price.yml` step ORDER

## Tech stack

- Python 3.12 (existing)
- sklearn 1.7+ HuberRegressor (existing)
- chronos-forecasting 2.2.2 (existing; Φ4 modifies its probe usage)
- JavaScript (vanilla, no framework) for PWA (existing)
- pdfplumber, pandas, pyarrow (existing)

**No new dependencies.** If any pass requires one, escalate.

## Architecture — new or modified files only

```
ml/
├── chronos_forecast.py          ← MODIFIED (Φ4: num_samples=5 + sample tracking + schema v2)
├── notifications.py             ← MODIFIED (Φ3: T6 trigger; Φ4: T1/T2 gate revision)
└── inference.py                 ← MODIFIED (Φ3: surface calibration_just_unlocked in companion block)

data/                            ← schema changes propagate via code; do not edit files directly
├── chronos_probe.json           ← schema additions (Φ4); schema_version → 2
├── notification_state.json      ← new field: last_t6_fired_date_ist
└── forecast.json                ← new field in chronos_companion: calibration_just_unlocked

.github/workflows/
└── weekly-backtest.yml          ← MODIFIED (Φ3: add workflow_dispatch trigger)

tests/
├── test_notifications.py        ← MODIFIED (T6 cases; T1/T2 gate revisions)
├── test_chronos_forecast.py     ← MODIFIED (multi-sample assertions)
└── test_calibration_integration.py  ← NEW (Φ3: end-to-end calibration apply path)

app.js                           ← MODIFIED (Φ2: full schema alignment)
index.html                       ← POSSIBLY MODIFIED (Φ2: copy updates if structural changes needed)

docs/
├── PROGRESS.md                  ← APPENDED (§4.1, §4.2, §4.3 sections + Decision Log entries)
└── adr/
    └── 015-multi-sample-chronos-gating.md  ← NEW (Φ4)
```

## Data model

**`chronos_probe.json` (Φ4 additions only):**

```json
{
  "probed_at": "...",
  "status": "success",
  "wall_clock_ms": { "pipeline_load": ..., "forecast": ..., "total": ... },
  "ibja_context_days": 178,
  "ibja_last_date": "...",
  "ibja_last_value": 14448.9,
  "horizon": 5,
  "ibja_forecast": [...],
  "tanishq_forecast": [...],
  "calibration_applied": false,
  "model_version": "chronos-bolt-tiny@<revision>",
  "num_samples": 5,
  "sample_directions": ["up", "up", "down", "up", "neutral"],
  "majority_direction": "up",
  "direction_consensus": 0.6,
  "schema_version": 2
}
```

**`notification_state.json` (Φ3 addition only):**

```json
{
  "schema_version": 1,
  "last_sent": {...},
  "queued": [...],
  "sent_today": [...],
  "last_t5_ist_date": "",
  "last_t6_fired_date_ist": ""
}
```

**`forecast.json.chronos_companion` (Φ3 addition only):**

```json
{
  "calibration_applied": true,
  "calibration_valid": true,
  "calibration_just_unlocked": false
}
```

## Verification commands

```yaml
- name: tests
  cmd: pytest --ignore=tests/test_config.py --ignore=tests/test_promotion.py --ignore=tests/test_tracking.py --ignore=tests/test_tuning.py tests/ -v
  required: true
- name: lint-ruff
  cmd: ruff check ml/ tests/
  required: true
- name: lint-format
  cmd: ruff format --check ml/ tests/
  required: true
- name: types
  cmd: mypy ml/
  required: true
- name: js-syntax
  cmd: node -c app.js
  required: true  # Φ2 only
- name: schema-roundtrip
  cmd: python -c "import json; json.load(open('data/forecast.json'))"
  required: true
- name: ci-workflows-green
  cmd: gh pr checks <PR_NUMBER>
  required: true  # pre-merge; ALL workflows must show green, not just tests
```

## Subagent usage rules

- Use `executor` for any pass that writes or edits files
- Use `verifier` for running tests, lint, types, JS syntax checks
- The orchestrator does NOT write code — always delegates
- Batch related changes into single executor invocations to amortize the ~10k token startup cost per WORKFLOW.md guidance

## Escalation rules (STRICT — existing project with discipline norms)

The orchestrator MUST pause and ask the user before:

- **Installing any dependency** not listed in Tech stack
- **Any executor pass that would touch more than 5 files**
- **Any existing test starting to fail** (the 270 currently-passing tests in CI are the relevant set; CI-ignored tests don't count)
- **Changing any existing function signature** in modules listed as load-bearing in CURRENT_STATE.md
- **Adding files or directories beyond Architecture section**
- **Verification failing 3 times in a row on the same check**
- **Modifying any of these (load-bearing):**
  - `data/forecast.json` (write through code, never edit data file directly)
  - `data/chronos_probe.json` (same)
  - `data/calibration.json` (same)
  - `.github/workflows/check-price.yml` step order (Φ3 only adds workflow_dispatch to weekly-backtest.yml; that's fine)
  - `archive/` (deprecated, read-only)
  - Any ADR file (read-only)
- **Silently substituting a data source, library, or design pattern** — the flag-and-stop rule from CURRENT_STATE.md
- **Merging a PR with any CI workflow showing red**
- **Encountering a Chronos sample direction flip in test data** (sanity check; this pattern motivates Φ4)

## Hard rules (do NOT touch)

- ADRs 005, 009, 010, 011, 012, 013, 014 — read-only
- Anything in `archive/` — deprecated but referenced; never delete
- Backward-compat aliases in `forecast.json` (`predicted_22k`, `lower`, `upper`, `model_status` at top level) — PWA still reads these; keep until coordinated PWA-migration PR, not this sprint
- Step ORDER in `check-price.yml` — load-bearing
- HuberRegressor as the calibration fit method — deliberately chosen
- The conformal PI computation method (80th percentile of recent naive 5d errors) — ADR 014
- **Do NOT introduce** a fabricated default PI value if `naive_mae_recent_30` is absent — fail-fast per ADR 014
- **Do NOT add** live HTTP calls in tests — mocked only

## Budget

- Soft target: one CC session per PR, three sessions total
- Hard cap: stop and escalate after **15 executor invocations per PR** (45 total for the sprint)
- Cost check: orchestrator runs `/cost` at the start of each PR and after every 5 executor invocations

## Success criteria

### Φ2 done when
- PWA renders all of these without falling back to `—`: `headline.predicted_22k`, `headline.lower`, `headline.upper`, `headline.conformal_pi_half`, `headline.naive_mae_recent_30`
- PWA renders all of these: `chronos_companion.lean_direction`, `chronos_companion.lean_strength_pct`, `chronos_companion.direction_acc_30f`
- PWA backtest section renders `mae_5d_avg_chronos`, `mae_5d_avg_naive`, `dir_acc_5d_chronos`, `wilcoxon_p`, `n_folds`
- Methodology drawer no longer references "LightGBM"
- A short PI-band-width explanation sentence is visible
- Live site loads without JS console errors (verified by orchestrator with a screenshot or curl-based smoke check)
- All CI workflows green on PR branch (`gh pr checks` confirmed green)

### Φ3 done when
- T6 trigger logic implemented and tested
- `last_t6_fired_date_ist` field added to state schema
- `workflow_dispatch` trigger added to `weekly-backtest.yml`
- New integration test covers: calibration `valid: false` → `valid: true` transition → companion calibrated → T6 fires once
- `calibration_just_unlocked` field added to forecast.json `chronos_companion` block
- All CI workflows green on PR branch

### Φ4 done when
- `chronos_forecast.py` runs 5 samples per probe, all complete in <2s total CI wall-clock (validate in CI before merge)
- `chronos_probe.json` schema bumped to v2 with new fields populated
- T1/T2 gate uses `majority_direction` + `direction_consensus >= 0.6`
- Existing test suite still passes (270+ pass in CI)
- New tests cover mixed-direction samples + low-consensus blocking
- ADR 015 written documenting the consensus gating rationale
- All CI workflows green on PR branch

### Sprint done when
- All three PRs merged in order to master
- No regressions in 270+ CI-passing tests
- Live site reflects Φ2 changes (verified)
- Next scheduled `check-price.yml` run completes end-to-end with new schemas
- PROGRESS.md has §4.1, §4.2, §4.3 sections appended with Decision Log entries

## Build order (this iteration)

1. **Φ2 — PWA schema alignment** (first; user-visible, unblocks visual verification of all other work)
   - Branch: `feat/pr-phi2-pwa-schema-alignment`
   - Steps:
     a. Read current `app.js`, `index.html`, and live `data/forecast.json`
     b. Map every field PWA reads against the new schema
     c. Update reads, preserve fallbacks for legacy aliases (defensive coding)
     d. Update methodology drawer copy
     e. Add PI band explanation
     f. Local smoke test (open in browser, check console clean)
     g. Open PR, verify all CI workflows green, request user review
     h. Merge after user approval

2. **Φ3 — Calibration gate prep**
   - Branch: `feat/pr-phi3-calibration-gate-prep`
   - Steps:
     a. Add `last_t6_fired_date_ist` to NotificationState schema
     b. Add T6 trigger logic in `notifications.py` (mirror T5 pattern with daily IST dedup)
     c. Add `calibration_just_unlocked` flag computation in `inference.py` (compare current `calibration.json.valid` to a cached prior state OR rely on idempotent T6 — recommend the latter, simpler)
     d. Add `workflow_dispatch` trigger to `weekly-backtest.yml`
     e. Write `tests/test_calibration_integration.py` for the full flip → calibrated → T6 path
     f. Open PR, verify CI green, request review
     g. Merge

3. **Φ4 — Multi-sample probe + consensus gate**
   - Branch: `feat/pr-phi4-multisample-chronos`
   - Steps:
     a. Read existing `chronos_forecast.py` probe function
     b. Modify for 5-sample probe; assert <2s total wall-clock
     c. Bump `chronos_probe.json` `schema_version` to 2
     d. Update `notifications.py` T1/T2 gate logic (use `majority_direction` and require `direction_consensus >= 0.6`)
     e. Write tests for mixed-sample scenarios + low-consensus blocking
     f. Validate wall-clock budget on PR's CI run before merging
     g. Write `docs/adr/015-multi-sample-chronos-gating.md` with rationale referencing PR E direction-flip evidence
     h. Open PR, verify CI green, request review
     i. Merge

After all three PRs merge, append §4.1 / §4.2 / §4.3 to PROGRESS.md (one section per PR, each ~one paragraph plus Decision Log entry).
