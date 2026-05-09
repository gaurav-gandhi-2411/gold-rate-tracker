# ADR 004: Local MLflow instead of DagsHub or W&B

**Status:** Accepted

## Context

MLflow experiment tracking requires a server. Options:

- **Local Docker** (SQLite + local artifact volume) — free, zero latency, no account needed.
- **DagsHub** — free tier, hosted MLflow + Git integration. Requires account and repo push.
- **Weights & Biases** — excellent dashboards, free tier. Separate account, vendor lock-in.
- **MLflow on a cloud VM** — full control, but costs money and requires infra management.

## Decision

Run MLflow locally via Docker Compose (`ghcr.io/mlflow/mlflow:v2.22.0`) on port 5001. SQLite
backend at `mlflow-db/mlflow.db`, artifacts on local volume `mlruns/`. Both gitignored.

## Consequences

**Good:**
- Free, no account, no data leaves the machine.
- Zero network latency — logging to localhost is fast.
- `docker compose up -d` is all setup needed.
- Port 5001 avoids conflict with other local MLflow instances (e.g., separate ML projects).

**Bad:**
- Runs are not backed up unless you copy `mlruns/` manually.
- No multi-user collaboration — single-developer setup only.
- If you change machines, you lose experiment history (ONNX files stay in git; that's enough).

**Not chosen — DagsHub:** Would add a remote push dependency and require a DagsHub account.
The repo is public on GitHub; we don't need DagsHub's Git+MLflow bundle.

**Not chosen — W&B:** Excellent product but heavier. We log params, metrics, and ONNX
artifacts — MLflow handles this with no additional SDK overhead.
