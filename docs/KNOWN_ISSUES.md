# Known Issues

## KI-001 — LightGBM CRLF Corruption on Windows ~~(STATUS_STACK_BUFFER_OVERRUN)~~

**Severity:** N/A — closed
**Status:** CLOSED — LightGBM deleted in PR H (Phase 3, 2026-05-19). The model files
(`lgbm.txt`, `lgbm_lower.txt`, `lgbm_upper.txt`) and `ml/forecast.py` no longer exist.
The `.gitattributes` binary markers remain and are still correct for any future binary
artifacts under `models/`.

<!-- scratch test line, Z1c unlabeled-leak repro, will be reverted -->
