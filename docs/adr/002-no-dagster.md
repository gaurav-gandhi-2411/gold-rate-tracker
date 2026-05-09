# ADR 002: No Dagster (Python scripts + Makefile instead)

**Status:** Accepted

## Context

The pipeline has five steps: scrape → macro → regime → forecast → commentary. Tools like
Dagster, Airflow, and Prefect offer dependency graphs, retries, scheduling, and observability
for multi-step pipelines. We considered Dagster specifically because it has a good local
developer experience and first-class Python support.

## Decision

Use Python scripts orchestrated by a Makefile (or PowerShell equivalents on Windows). GitHub
Actions handles scheduling and retry. No Dagster, Airflow, or Prefect.

## Consequences

**Good:**
- Zero new infrastructure to learn, install, or maintain.
- Each script (`ml/forecast.py`, `ml/macro.py`, etc.) is independently runnable and testable.
- GitHub Actions YAML is the single source of truth for the production DAG.
- No persistent scheduler process — everything is triggered by cron or `workflow_dispatch`.

**Bad:**
- No visual pipeline graph or Dagster UI.
- Retry logic must be handled at the shell level (`continue-on-error: true` in CI).
- Adding a new step means editing the workflow YAML instead of declaring a Dagster asset.

**Right-sizing:** This is a single univariate time series with five pipeline steps and one
output (a JSON file). Dagster would be appropriate if we had dozens of assets, multiple data
sources with complex dependency graphs, or a team of data engineers. At current scale,
Makefile + GitHub Actions is the right tool.
