# Train all models (LightGBM + TFT + N-BEATS) with current Hydra configs.
# Requires: MLflow running (.\scripts\win\mlflow-up.ps1) and training venv active.
$ErrorActionPreference = "Stop"
python -m ml.training
