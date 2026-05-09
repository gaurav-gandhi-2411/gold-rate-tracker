.PHONY: help mlflow-up mlflow-down mlflow-logs train-lgbm train-tft train-nbeats \
        train-all inference-test test test-integration lint format clean \
        setup-train setup-inference

help:
	@echo "Targets:"
	@echo "  mlflow-up         - Start MLflow stack (docker compose, port 5001)"
	@echo "  mlflow-down       - Stop MLflow stack"
	@echo "  mlflow-logs       - Tail MLflow logs"
	@echo "  setup-train       - Install training deps (see scripts/win/setup-train.ps1 on Windows)"
	@echo "  setup-inference   - Install inference deps (CI parity)"
	@echo "  train-all         - Train all models with current configs"
	@echo "  train-lgbm        - Train LightGBM only"
	@echo "  train-tft         - Train TFT only"
	@echo "  train-nbeats      - Train N-BEATS only"
	@echo "  inference-test    - Run inference path locally (mimics CI)"
	@echo "  test              - Run unit tests (no integration)"
	@echo "  test-integration  - Run integration tests (requires MLflow up)"
	@echo "  lint              - Run pre-commit on all files"
	@echo "  format            - Format code with ruff"
	@echo "  clean             - Remove build artifacts and caches"

mlflow-up:
	docker compose up -d mlflow
	@echo "MLflow at http://localhost:5001"

mlflow-down:
	docker compose down

mlflow-logs:
	docker compose logs -f mlflow

setup-train:
	pip install -r ml/requirements-train.txt

setup-inference:
	pip install -r ml/requirements-inference.txt

train-all:
	python -m ml.training

train-lgbm:
	python -m ml.training model=lightgbm

train-tft:
	python -m ml.training model=tft

train-nbeats:
	python -m ml.training model=nbeats

inference-test:
	python -m ml.forecast

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
