# Run unit tests (excludes integration tests that require live services).
$ErrorActionPreference = "Stop"
pytest -m "not integration"
