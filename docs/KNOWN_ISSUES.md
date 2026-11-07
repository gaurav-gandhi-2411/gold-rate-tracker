# Known Issues

## KI-001 — LightGBM CRLF Corruption on Windows ~~(STATUS_STACK_BUFFER_OVERRUN)~~

**Severity:** N/A — closed
**Status:** CLOSED — LightGBM deleted in PR H (Phase 3, 2026-05-19). The model files
(`lgbm.txt`, `lgbm_lower.txt`, `lgbm_upper.txt`) and `ml/forecast.py` no longer exist.
The `.gitattributes` binary markers remain and are still correct for any future binary
artifacts under `models/`.

## KI-002 — The data-only lint path selects tests by a static scan plus a committed measurement

**Severity:** Low (a data sync can be blocked, or a data-reading test skipped, never a code change)
**Status:** OPEN, accepted with the switch (docs/RUNBOOK.md, "What runs on a data-only PR")

On a data-only PR the required `lint` job runs only the tests that read `data/`: the static scan of
`tests/` plus `tests/data_reading_tests.txt` (measured). Blind spots: a NEW test file that reads
`data/` through a module's default path is in neither until the measurement is refreshed; the static
scan over-selects (a file that only names a data constant as a string, such as
`tests/test_tanishq_silent_alert.py`, which redirects those constants to a temp dir, is selected and
reads no data), and every selected test blocks, so a flaky or time-dependent test in the set can
block a data sync until it is fixed or master is repaired. The first is a silent gap (a check that
does not run); the second is loud (a blocked sync, cleared by repairing the test).
