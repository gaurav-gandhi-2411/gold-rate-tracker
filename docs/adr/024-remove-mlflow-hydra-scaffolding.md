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

**Adjacent dead scaffolding found during the audit, initially left out of this ADR's diff to keep it reviewable:** `ml/training/callbacks.py` + `ml/training/utils.py` (zero callers anywhere, not even tests); unused `hmmlearn` dependency in `ml/requirements.txt` (its only consumer, `ml/regime.py`, was deleted in PR #29); pre-existing `--ignore=tests/test_promotion.py`/`tests/test_tuning.py` entries in `lint.yml` referencing files that don't exist in this repo; `Makefile`'s `train-lgbm`/`train-tft`/`train-nbeats`/`inference-test` targets calling other already-deleted modules (`ml.training`, `ml.forecast`); stale dead-module entries in `pyproject.toml`'s mypy override list (`ml.forecast`, `ml.regime`, `ml.models.lgbm`, `ml.training.train_lgbm`). **All five removed in the 2026-07-18 follow-up — see Update below.**

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
- The adjacent dead scaffolding flagged above (training/callbacks.py, unused hmmlearn dep, stale --ignore/mypy-override entries) remained in the repo at initial merge. Accepted as a deliberate scope boundary for this ADR's first PR — each was a smaller, separable cleanup that didn't need to block the main removal. All five were removed in the follow-up (see Update below).

## Alternatives Considered

**Alt: Keep as documented-but-dormant infrastructure, no code change.** This is what the 2026-07-18 `docs/ARCHITECTURE.md` rewrite already did (a "Retired components" table entry). Rejected as the final state: code that's uninstallable (`tracking.py`) or actively broken (`config.py`) has no value sitting in the tree — it cannot be revived as-is regardless, and its presence invites exactly the kind of stale-doc confusion this ADR's own audit was triggered by.

**Alt: Fix `ml/config.py`'s broken `configs/model/ensemble.yaml` reference instead of deleting.** Rejected: there is no current caller to fix it *for*. Fixing dead code to make it pass its own tests, with nothing depending on it, is effort spent on a feature nobody uses.

**Alt: Fold in the adjacent dead-scaffolding findings (training/callbacks.py, hmmlearn, stale ignores) into this same PR.** Rejected: those are separable findings surfaced incidentally during this audit, not the MLflow/Hydra scope this ADR was scoped to address. Bundling them risks a larger, harder-to-review diff for unrelated cleanup; each deserves its own confirm-before-delete pass.

---

## Update — 2026-07-18: adjacent findings removed

Re-verified zero live callers for each of the five adjacent items (same rigor as the original audit — full grep of `ml/*.py`, every workflow, `scripts/`, and every requirements file) before removing anything:

| Item | Re-verification | Removed |
|---|---|---|
| `ml/training/callbacks.py`, `ml/training/utils.py` | Zero callers anywhere (grep found nothing, not even a test import). `ml/training/__init__.py` was empty (0 lines) — the whole package was dead, not just these two files. | Deleted `ml/training/` entirely, not just the two flagged files. |
| Unused `hmmlearn` dependency | Zero code references. Declared in **three** requirements artifacts, not just `ml/requirements.txt` as originally noted: `ml/requirements.txt`, `ml/requirements-inference.txt`, and `ml/requirements-inference.lock` (the lockfile CI actually installs from). | Removed from both hand-maintained requirements files; `ml/requirements-inference.lock` regenerated via the documented `uv pip compile` command (never hand-edited — see note below). |
| `--ignore=tests/test_promotion.py`/`test_tuning.py` in `lint.yml` | Confirmed neither file exists in the repo (`git ls-files` returns nothing for either). | Both `--ignore` lines removed; the pytest suite now runs with no exclusions beyond `-m "not integration"`. |
| `Makefile`'s `train-lgbm`/`train-tft`/`train-nbeats`/`train-all`/`inference-test` targets | Confirmed `python -m ml.training` was already unrunnable (no `__main__.py` in the now-deleted package) and `python -m ml.forecast` referenced a module deleted in PR #29. Zero workflows invoke any of these targets. | All five targets removed, along with their `.PHONY`/`help` entries. |
| Stale `pyproject.toml` mypy override entries (`ml.forecast`, `ml.regime`, `ml.models.lgbm`, `ml.training.train_lgbm`) | None of the four modules exist. | Removed from the override list; the five still-live modules (`ml.features`, `ml.macro`, `ml.backtest`, `ml.commentary`, `ml.inference`) kept unchanged. |

**Lockfile regeneration note:** `uv pip compile ml/requirements.txt --index-strategy unsafe-best-match --output-file ml/requirements-inference.lock --python-version 3.12` (the exact command in the lockfile's own header) was used rather than hand-editing — per standing discipline, lockfiles are never hand-edited. Removing `hmmlearn` also incidentally bumped `scikit-learn` 1.8.0→1.9.0 (hmmlearn had been forcing it below the version `ml/requirements.txt` itself already declared, `scikit-learn>=1.9.0` — the old lockfile was silently out of sync with that constraint), plus minor bumps to `torch`, `pytest`, and `lxml` from the resolver re-optimizing the full graph. Validated by a dry-run install (`uv pip install --dry-run`) against a scratch venv using the exact install command `check-price.yml` runs in production — resolved cleanly, no conflicts.

**Verification:** full local test suite (excluding two files broken by a pre-existing, unrelated Windows torch/CUDA DLL issue, and the two now-removed `--ignore` targets since they no longer exist) — all passing, zero new failures. `ruff check` clean. Full grep sweep post-removal for any remaining reference to any of the five removed items — zero hits.
