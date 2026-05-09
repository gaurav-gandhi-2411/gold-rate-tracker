# Start the MLflow container (port 5001).
$ErrorActionPreference = "Stop"
docker compose up -d mlflow
Write-Host "MLflow running at http://localhost:5001"
