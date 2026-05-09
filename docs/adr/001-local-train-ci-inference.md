# ADR 001: Train locally, serve via ONNX in CI

**Status:** Accepted

## Context

GitHub Actions runs four times a day on `ubuntu-latest`. The inference step needs to produce a
next-day forecast from the latest scraped prices. PyTorch is ~2 GB installed, takes 2–3 minutes
to `pip install`, and doesn't fit in CI's free tier time budget alongside Playwright.

We also have a GPU locally (RTX 3070 Laptop) that trains models in minutes. Training in CI would
mean CPU-only, no GPU, and a much longer training loop per scrape.

## Decision

Train neural models (TFT, N-BEATS) locally on the RTX 3070. Export to ONNX and commit the ONNX
files to `models/production/`. CI installs only `onnxruntime` (no PyTorch) and runs inference
via the exported ONNX graphs. LightGBM retrains from scratch on every CI run (< 1 s).

## Consequences

**Good:**
- CI install is fast and cheap (onnxruntime ~50 MB vs PyTorch ~2 GB).
- Neural model quality is determined locally where we have GPU acceleration.
- ONNX files in git give a clear audit trail of what model is in production.

**Bad:**
- Neural models don't auto-retrain when new data arrives; must retrain and push manually.
- ONNX export must pass a parity check (max abs diff < 1e-3) to catch export bugs.
- ONNX files in git increase repo size; mitigated by keeping models small (< 30 MB).
