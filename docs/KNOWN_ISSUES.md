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
reads no data). The data-reading test step is REPORT-ONLY (`continue-on-error`) until 5 clean real
runs over >= 24 h, so a flaky or time-dependent test there shows a warning and does not block a data
sync; once it is made blocking, such a test can block a sync until it is fixed or master is repaired.
Also not run on a data-only PR (they live only in pre-commit): the gitleaks secret scan and the
10 MB large-file check; no other workflow scans data files for secrets, so those files rely on being
bot-generated (found by the PR review, 2026-11-07). The first gap is silent (a check that does not run); the second is loud (a warning now, a blocked sync
after the flip).
