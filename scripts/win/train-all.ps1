# Train all models (LightGBM + TFT + N-BEATS) with current Hydra configs.
# Requires: MLflow running (.\scripts\win\mlflow-up.ps1) and training venv active.
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"  # Prevent MLflow emoji from crashing CP1252 terminals
python -m ml.training
