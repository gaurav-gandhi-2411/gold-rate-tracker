# ADR 024 — Remove dead MLflow + Hydra training scaffolding

**Status:** Accepted 2026-07-18
**Author:** Gaurav Gandhi / CC

---

## Context

The [docs/ARCHITECTURE.md rewrite](../ARCHITECTURE.md) (2026-07-18) surfaced that MLflow (`docker-compose.yml`'s `mlflow` service, `ml/tracking.py`) and the Hydra config loader (`ml/config.py`, `configs/`) were never formally retired alongside the rest of the pre-Phase-3 training pipeline in [ADR 014](014-production-architecture.md) / PR #29 (2026-05-20) — they were simply left behind, undocumented as dead.

**Rigorous caller audit before removal (per standing "confirm zero live callers" discipline):**

| Component | Finding |
|---|---|
| `ml/tracking.py` | Zero production callers — grepped the full `ml/*.py` tree, every `.github/workflows/*.yml`, and `scripts/`. `import mlflow` at module level, but `mlflow` is declared in **no** requirements file (`ml/requirements.txt`, `ml/requirements-inference.txt`, `.lock`) — uninstallable in CI or any fresh environment. Its own tests pass locally (12/12, non-integration) with all deps installed — functionally sound, just orphaned. |
| `ml/config.py` | Zero production callers. Imports `hydra`/`omegaconf`, also absent from every requirements file. **Actively broken, not merely unused**: 9 of 13 of its own tests fail *even locally with all deps installed* — `hydra.errors.MissingConfigException: Could not find 'model/ensemble'`, because `configs/model/ensemble.yaml` was itself deleted in PR #29 two months ago. Nobody has successfully called `load_config()` since. |
| `configs/` | Zero references outside `ml/config.py` and its own test. |
| `docker-compose.yml` | Single service (`mlflow`), nothing else defined in the file, nothing else depends on it. Only referenced by `Makefile`'s `mlflow-up/down/logs` targets and `scripts/win/mlflow-*.ps1` — dev convenience commands, never invoked by any workflow. |
| Hidden-coupling check | `tests/test_no_dead_imports.py` auto-discovers every `ml/*.py` module via `pkgutil` and parametrizes an import-check per module. It currently exercises `ml.tracking`/`ml.config`, but is self-adjusting (fewer parametrized cases once the modules are gone, no edits needed) and already treats a missing *external* package as a skip rather than a failure — built for exactly this "optional training deps" scenario. |

No live pipeline code, no CI-gating test, and no scheduled workflow depends on any of the four components above.

**Adjacent dead scaffolding found during the audit, explicitly out of scope for this ADR** (flagged for a future pass, not addressed here): `ml/training/callbacks.py` + `ml/training/utils.py` (zero callers anywhere, not even tests); unused `hmmlearn` dependency in `ml/requirements.txt` (its only consumer, `ml/regime.py`, was deleted in PR #29); pre-existing `--ignore=tests/test_promotion.py`/`tests/test_tuning.py` entries in `lint.yml` referencing files that don't exist in this repo; `Makefile`'s `train-lgbm`/`train-tft`/`train-nbeats`/`inference-test` targets calling other already-deleted modules (`ml.training`, `ml.forecast`); stale dead-module entries in `pyproject.toml`'s mypy override list (`ml.forecast`, `ml.regime`, `ml.models.lgbm`, `ml.training.train_lgbm`).

---

## Decision

Delete, with no replacement:

- `ml/tracking.py`
- `ml/config.py`
- `configs/` (`config.yaml`, `data/default.yaml`, `inference/default.yaml`, `tracking/default.yaml`, `training/default.yaml`, `training/optuna.yaml`)
- `docker-compose.yml`
- `tests/test_tracking.py`
- `tests/test_config.py`

Trim the references these deletions leave dangling:

- `Makefile`: remove `mlflow-up`/`mlflow-down`/`mlflow-logs` targets (they'd call a deleted `docker-compose.yml`); correct the `test-integration` help line, which cited "requires MLflow up" — the only remaining `@pytest.mark.integration` test (`test_chronos_forecast.py`) needs live network access, not MLflow.
- `.github/workflows/lint.yml`: drop the now-dead `--ignore=tests/test_config.py`/`tests/test_tracking.py` entries; correct the comment above them (it previously implied `test_promotion.py`/`test_tuning.py` exist and need training deps — they don't exist in this repo at all, unrelated pre-existing cruft, left as-is).
- `pyproject.toml`: reword the `integration` pytest marker's description to drop the now-inapplicable MLflow example (the marker itself stays — `test_chronos_forecast.py` still uses it for its live HuggingFace download).

---

## Consequences

**Positive:**
- No functional change to the live pipeline — nothing above had a production caller.
- Removes two locally-passing-but-uninstallable-in-CI test files and one file (`test_config.py`) that's been silently broken for two months, closing a gap where `docs/ARCHITECTURE.md` previously (before this pass) implied a working local training stack that no longer functions.
- `docker-compose.yml`'s removal also removes the last reference to the `mlruns/`/`mlflow-db/` gitignored local directories as a *repo-documented* workflow — those directories were already untracked and remain so; nothing to clean up there.

**Negative:**
- If local model training is ever revived (Phase 4 deep-model reintroduction per `docs/ARCHITECTURE.md`'s retired-components table), MLflow tracking and Hydra config composition would need to be rebuilt from scratch rather than reactivated. Accepted: the code as it stood was already broken (config.py) or undeployable (tracking.py) two months into disuse — reviving stale, already-drifted scaffolding would cost more than writing fresh code against whatever Phase 4 actually needs.
- The adjacent dead scaffolding flagged above (training/callbacks.py, unused hmmlearn dep, stale --ignore/mypy-override entries) remains in the repo. Accepted as a deliberate scope boundary for this ADR — each is a smaller, separable cleanup that doesn't need to block this one.

## Alternatives Considered

**Alt: Keep as documented-but-dormant infrastructure, no code change.** This is what the 2026-07-18 `docs/ARCHITECTURE.md` rewrite already did (a "Retired components" table entry). Rejected as the final state: code that's uninstallable (`tracking.py`) or actively broken (`config.py`) has no value sitting in the tree — it cannot be revived as-is regardless, and its presence invites exactly the kind of stale-doc confusion this ADR's own audit was triggered by.

**Alt: Fix `ml/config.py`'s broken `configs/model/ensemble.yaml` reference instead of deleting.** Rejected: there is no current caller to fix it *for*. Fixing dead code to make it pass its own tests, with nothing depending on it, is effort spent on a feature nobody uses.

**Alt: Fold in the adjacent dead-scaffolding findings (training/callbacks.py, hmmlearn, stale ignores) into this same PR.** Rejected: those are separable findings surfaced incidentally during this audit, not the MLflow/Hydra scope this ADR was scoped to address. Bundling them risks a larger, harder-to-review diff for unrelated cleanup; each deserves its own confirm-before-delete pass.
