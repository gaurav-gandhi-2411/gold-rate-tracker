# Run pre-commit hooks on all files (lint + format + type-check).
$ErrorActionPreference = "Stop"
pre-commit run --all-files
