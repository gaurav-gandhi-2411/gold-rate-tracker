# Cleanup map, October 2026 (analysis-only)

Status: analysis-only. Nothing was deleted, moved or edited outside `docs/` and `reports/`.
Base: `origin/master` at `55d672c9`. Machine-readable map: `reports/cleanup_map_2026-10/cleanup_map.csv`
(one row per tracked file, plus 16 top-level directory rows). Local-only items:
`reports/cleanup_map_2026-10/untracked.csv`. Reproduce with `gather_facts.py` then `classify.py` in the
same directory (stdlib only).

## 1. Headline

| Class | Tracked files | of which UNCERTAIN |
|---|---|---|
| KEEP | 695 | 25 |
| ARCHIVE | 32 | 2 |
| DELETE | 326 | 315 (all screenshots, one policy question) |
| Total | 1053 | |

The repo is mostly live or frozen-research-dependent. Real, low-risk dead code is small:
**12 files in batch B1**. The only large saving is PNG evidence: **315 screenshot files = 76.6 MB of
~90 MB of tracked bytes**, and that is a policy call (rule 15c), not a code call. Chronos probe: keep
(section 4).

## 2. Size and shape (VERIFIED, `git ls-files` + file sizes, 2026-10-05)

| Area | Files | MB | Lines (text) |
|---|---|---|---|
| reports | 344 | 58.62 | 71,862 (289 files are screenshots) |
| docs | 114 | 24.29 | 18,458 (30 files are screenshots, 61 ADRs) |
| tests | 301 | 1.78 | 36,085 |
| data | 45 | 1.56 | 42,931 |
| ml | 85 | 0.94 | 22,642 |
| scripts | 81 | 0.78 | 19,002 |
| root (25 files) | 25 | 0.86 | 10,406 |
| worker-deadman / scraper / .github / archive / fonts / icons / other | 55 | ~1.0 | ~14,000 |
| Total tracked | 1053 | ~90 | |

Working tree without `.git`: 94 MB. `.git` pack in the main checkout: 161.8 MB (`git count-objects -vH`,
25,128 objects; screenshots and 704 commits of `commentary.json` are history that stays regardless).
Ignored local weight in the main checkout: `.mypy_cache` 454 MB (22,394 files), `models/` 20.8 MB,
`scraper/node_modules` 16.8 MB.

## 3. How "used" was determined, and what each sweep cannot see (rule 85b)

Tools run: own AST import graph (`gather_facts.py`), vulture 2.16 (min-confidence 80 and 60) in a venv
outside Temp (`C:\Users\gaura\ml-projects\grt-venv-clean`, no global install), `npx knip` in `scraper/`
(it needs a `package.json`; the repo root and `worker-deadman/` have none, so knip could not run there).

Shapes searched:
- Python AST: `import ml.x`, `from ml.x import y`, `from ml import x`, relative imports, string literals
  equal to a dotted `ml.*` / `scripts.*` module name (covers `importlib.import_module("ml.x")`).
- Text, every tracked text file: `python -m ml.x`, `python path/x.py`, `node path/x.mjs`, bare
  `ml/x.py`, `scripts/x.py`, `data/x.json`, `reports/...` path strings, dotted module names, and any
  basename of 8+ characters. YAML `run:` lines, Makefile, `.ps1`, MD, JSON, JS `fetch('data/...')`
  and quoted `data/...` literals in `app.js`, `flags.js`, `how-we-know*.js`, `service-worker.js`, HTML.
- JS (PWA): the PWA is plain script files (`flags.js`, `i18n.js`, `app.js` loaded by `<script src>`),
  so knip/ts-prune have no module graph to analyse. Instead: every top-level `function`/`const`/`let`/`var`
  name in the six root JS files was counted across all tracked JS/HTML; a name occurring once is dead.

Result of each sweep:
- Import graph: 268 Python files; 80 reachable from workflow-invoked entry points; 198 from tests.
- vulture 80 on `ml scripts`: 0 findings. With `tests` added: 22 findings, all test-local unused
  variables (`fixture_json`, `down_count`), no deletion candidates.
- vulture 60 on `ml scripts`: 55 findings, mostly keyword-argument fields and constants
  used by dataclasses/JSON. Real module-level candidates: `ml/features.py` (`build_feature_matrix`,
  `get_train_Xy`, `get_predict_row`, `ALL_FEATURE_COLS`), `ml/logging_setup.py:configure_logging`,
  `ml/llm_cache_helpers.py` (4 functions), `ml/metrics.py:aggregate_metrics`. These are function-level
  and were **not** classed; a function-level pass is a possible follow-up (UNCERTAIN: tests cover several).
- knip on `scraper/`: 9 "unused files" (8 are test files that workflows run via `node --test`, which knip
  does not model) plus 3 genuinely unreferenced one-offs, 1 "unused dependency" (`lighthouse`, a false
  positive: used by `scripts/measure-lighthouse.js`) and 4 unused exports in `retailer-enabled.mjs`.
- Root PWA JS: 1 dead declaration (`METRICS_URL` in `app.js:32`). Editing `app.js` forces a service-worker
  VERSION bump, so it is not worth its own PR; fold into any future PWA change.

NOT covered (so a clean result here is not "confirmed unused"):
- Paths assembled at runtime (f-strings with variables, `Path(a) / "b"` split literals), `getattr` dispatch.
- `analysis.yml` builds `scripts/analysis_${{ inputs.analysis }}.py` dynamically; I read its `options:`
  list by hand (10 real scripts + 1 dangling name) instead of by grep.
- References outside the tracked tree: merged-PR bodies, the live site, GitHub-side settings, other repos
  (none searched), secrets/remote dispatch payloads, and git history.
- Gitignored files were not graph-analysed; they are listed separately.
- Function-level reachability (only module/file level was classified).

## 4. Chronos probe (VERIFIED from `gh run view --json jobs`, 12 recent successful check-price runs, 2026-10-02 to 05)

Run IDs and per-step seconds: `reports/cleanup_map_2026-10/tool_output/chronos_probe_step_durations.json`.

| Item | Value |
|---|---|
| Still runs every cycle? | Yes. `check-price.yml` step "Run Chronos probe" (`python -m ml.chronos_forecast --probe`), cron `37 1-22/3 * * *` plus dispatches; 12 of 12 sampled runs executed it, all success |
| Step time | mean 7.6 s (range 5 to 13 s) |
| Weights cache restore + post-save | mean 1.4 s |
| Whole `check` job | mean 381 s; the probe path is **2.4 %** of it. The 322 s "Sync data via bot PR" step dominates |
| Runs per day | 7 to 15 (counted from `gh run list`, 2026-09-30 to 10-05) |
| Runner minutes | roughly 9 s x ~10 runs/day = ~1.5 min/day = ~45 min/month. INFERRED (frequency varies); GitHub bills per job rounded up to the minute, so removing the step would very likely save no billed minute at all |
| Consumers | `data/chronos_probe.json` is read by `ml/inference.py` (`chronos_companion` block of `forecast.json`), `ml/notifications.py`, and `app.js:2544` (copy when the direction card is off). ADR 009/012/015/020 |

Classification: KEEP `ml/chronos_forecast.py`, `data/chronos_probe.json`, the workflow step. Retiring it
would be a product decision (it feeds a user-visible sentence and notification gating), not a cleanup,
and would save about 9 s per run. Note `data/chronos_probe.json` has 1015 commits in history, the churn is
the real cost of the file, not runner time.

## 5. Findings that are not deletions (route to GG or separate fix PRs)

1. **`ci.yml` has never run.** It triggers on `branches: [main]`; the default branch is `master`.
   `gh run list --workflow ci.yml` returns zero runs. It is the only workflow that runs
   `scripts/check_manifest_provenance.py` (grep: no other workflow names it). Per rule 85a this control
   covers less than its name implies. Fix is a one-line trigger change in its own PR; it is not a cleanup.
2. **`analysis.yml` offers `chronos_m4`** but `scripts/analysis_chronos_m4.py` does not exist. Its last
   runs (2026-09-24 20:02 to 20:06Z) failed. Dangling choice.
3. **Ghost workflow registrations**: GitHub lists `_diag_reachability.yml` (last ran 2026-07-19) and
   `scraper-dependency-guard.yml` (ran today on a PR) as active, but neither is in the tree of
   `origin/master` (16 workflow files there). Nothing to delete from the repo; UNCERTAIN whether the
   first is a stale registration.
4. **ADR 024 was incomplete**: it states the MLflow scaffolding was removed, but
   `scripts/win/mlflow-up.ps1`, `mlflow-down.ps1`, `train-all.ps1` still exist (batch B1), and local
   ghost dirs `ml/models`, `ml/training`, `ml/tuning`, `mlruns/`, `mlflow-db/` remain on disk.
5. **Two flag-gated PWA cards have no scheduled producer on master**: `data/event_watch_today.json`
   (ADR 050) and `data/markup_today.json` (comment in `app.js:2722` says its producer is on an unmerged
   branch). Both files are stale since 2026-09-26. UNCERTAIN, KEEP.
6. `.claude/settings.local.json` is tracked.
7. The parked Telegram code (PR #2296, merged) lives in `worker-deadman/src/index.mjs` and its tests.
   Whole directory classified KEEP. No file there is in any DELETE or ARCHIVE batch.

## 6. Classification rules actually applied

- KEEP: reachable from a workflow-invoked module, fetched by the PWA, written by a workflow, named in a
  frozen/pre-registered/shadow ADR (number 26 or higher; those ADRs say the named code is frozen, so moving
  it breaks the pre-registration text), or dispatchable via `analysis.yml`.
- ARCHIVE: closed experiment, no workflow, no frozen ADR dependency, result already frozen as data/ADR text
  (Phi7/Phi10 set, ADR 018/019), plus historical docs superseded by later waves.
- DELETE: no consumer in any sweep, no ADR/result dependency, and either provably dead scaffolding (ADR 024
  leftovers, `commentary.json`) or regenerable/recoverable from git history.
- UNCERTAIN: stated per row in the `question` column.

Frozen-result dependencies were checked per row: everything an ADR>=26 or a `reports/` result names is KEEP.
Mechanically, "ARCHIVE" means a `git mv` into `archive/` (keeping reproducibility) plus rewriting the
inbound path references, so each ARCHIVE batch must carry its link rewrites and its count-baseline updates.

## 7. Proposed batches (ordered by risk; numbers from `summary.json`, VERIFIED)

| Batch | Content | Files | Lines removed/moved | Bytes | Risk |
|---|---|---|---|---|---|
| B1 dead scaffolding and one-offs (DELETE, 1 ARCHIVE) | 3 `scripts/win` MLflow/train `.ps1`, 3 `scripts/capture-*.js`, 3 `scraper/*` one-offs, `ml/logging_setup.py`, `data/commentary.json`, `scripts/inspect-tanishq.js` (UNCERTAIN) | 12 | 837 | 37 KB | Lowest. Nothing live consumes them |
| B2 stale docs and notebook (ARCHIVE to `docs/archive/`, `archive/`) | 11 dated/superseded docs (DESIGN, METRICS_DESIGN, DAILY_SUMMARY_DESIGN, FEATURE_IMPORTANCE, FEATURE_INVENTORY, PHASE_3_RETROSPECTIVE, UI_AUDIT, UI_PLAN, CLEAN_IP_FETCH, MODELLING_ASSESSMENT, proposals/p5) + `notebooks/01_eda_and_modeling.ipynb` | 12 | 2,623 | 134 KB | Low. Inbound links in README/CONTRIBUTING/ADR 1 must be rewritten; `_config.yml` lists `notebooks/` |
| B3 closed experiments (ARCHIVE) | `ml/experiments/{__init__,driver_decomp,festival_seasonality,horizon_sweep,premium_carry}.py`, `scripts/run_phi7a-d`, `run_phi10a`, `phi10a_flag_and_stop`, `data/experiments/*.json` (2), 5 tests, `ml/llm_cache_helpers.py` (UNCERTAIN) | 19 | 2,981 | 114 KB | Medium. Imports (tests, `test_no_dead_imports` sweep), 5 `tests/test_count_baseline/*.count` files and `check_test_registry_complete` must be updated together |
| B4 screenshot retention (DELETE, GG policy first) | 286 `reports/screenshots/**` (excluding README's 3), 29 `docs/screenshots/**` | 315 | 0 | 76.6 MB | Low technical risk, real policy question (rule 15c). History keeps every file; `.git` size does not shrink |

Each batch is a reviewable PR (B1/B2/B3 under 3,000 reviewable lines; B4 has zero code lines but 315 files,
so its body must state "generated/binary" split per rule 39b/gate 3b). Note: B4 changes only docs/reports paths.

Proof plan per batch:
- Common to all: full Python suite on the PR branch vs the clean-master baseline in section 8;
  `node --test` PWA step and the 8 headless Playwright tests from `lint.yml`; `scripts/check_test_counts.py`,
  `scripts/check_test_registry_complete.py`, `scripts/check_plain_language.py`; all required checks green with
  `scripts/check_required_checks_positive.py`; no conflicts vs current master (re-check at READY time).
- B1: `git grep` for every removed basename on the PR head returns nothing; confirm `data/commentary.json` is
  not precached (`service-worker.js` shell list: grep clean) and not fetched on the live site (network tab on the
  deployed page shows no `commentary.json` request: INFERRED from `app.js`, verify live before merge).
- B2: link-checker over README/CONTRIBUTING/CHANGELOG/ADR for the moved paths; Pages build (Jekyll
  `_config.yml` excludes) renders; live-site compare unnecessary (no served file changes) but run `pages-deploy`
  preview to confirm.
- B3: `python -m pytest tests` plus `tests/test_no_dead_imports.py`; re-run one moved experiment from `archive/`
  with a seed-42 smoke to prove it is still reproducible; verify ADR 018/019 paths resolve; one real dispatched
  `check-price.yml` and `weekly-backtest.yml` run to prove no scheduled path imported them.
- B4: GG policy decision first; then check PR-body image links for the retained waves; before/after live-site
  compare is not needed (no served file changes; `reports/` and `docs/` are not served after ADR-era deploy scoping,
  verify with `pages-deploy.yml` file list).

Not cleanup, separate fix PRs: `ci.yml` branch trigger; `analysis.yml` dangling `chronos_m4` option.

## 8. Test suite baseline on clean master

VERIFIED (I ran it, base `55d672c9`, venv `grt-venv`, CI-parity `-m "not integration"`): **1976 passed, 1 skipped, 1 deselected (the integration test), 0 failed, 19 warnings, 638 s wall (10m38s)**. No failures exist on clean master. Slowest: `test_leak_guard_replays::...test_prior_day_vix_passes` 97 s, `test_stamp_sw_version` 41 s, `test_event_watch` 35 s. JS tests (`node --test`, headless Playwright) were not run in this pass. Artifact: `reports/cleanup_map_2026-10/tool_output/pytest_baseline.txt`.

## 9. UNCERTAIN items and the question that resolves each

See the `question` column of `cleanup_map.csv`. Summary:
- `ml/llm_cache_helpers.py`: does GG still want ADR 013's prepared infra for a Claude migration?
- `ml/experiments/drift_naive.py`: is ADR 018's out-of-regime revisit still wanted?
- `ml/experiments/direction_enrichment.py`: still the re-check command in `docs/DIRECTION_SIGNAL_STATUS.md`?
- `scripts/inspect-tanishq.js`: manual Tanishq-DOM debugging aid, still used?
- `data/event_watch_today.json`, `data/markup_today.json`, `data/stale_day_shadow.json`: will producers be scheduled?
- `docs/PROGRESS.md`, `docs/SESSION_AUDIT_2026-08.md`: freeze as history or keep living?
- `scripts/analysis_proxy_subperiods.py`, `analysis_timing_audit.py`: confirm report provenance headers.
- All screenshots: retention policy under rule 15c.
- Local: `models/` (20.8 MB pre-Chronos artifacts, not regenerable) and `worker/` (retired Worker): GG decides;
  neither is touched by this PR.
