# Force UTF-8 stdout on Windows so MLflow's emoji output (🏃) doesn't crash CP1252 terminals.
export PYTHONUTF8 := 1

.PHONY: help test test-integration lint format clean setup-inference

help:
	@echo "Targets:"
	@echo "  setup-inference   - Install inference deps (CI parity)"
	@echo "  test              - Run unit tests (no integration)"
	@echo "  test-integration  - Run integration tests (requires live network access)"
	@echo "  lint              - Run pre-commit on all files"
	@echo "  format            - Format code with ruff"
	@echo "  clean             - Remove build artifacts and caches"

setup-inference:
	pip install -r ml/requirements-inference.lock

test:
	pytest -m "not integration"

test-integration:
	pytest -m integration

lint:
	pre-commit run --all-files

format:
	ruff format ml tests
	ruff check --fix ml tests

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache __pycache__
	find . -name "*.pyc" -delete
