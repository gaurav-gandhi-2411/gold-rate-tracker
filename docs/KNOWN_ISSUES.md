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
The gitleaks secret scan and the 10 MB large-file check live in pre-commit, which a data-only PR
skips (found by the PR review, 2026-11-07). They now run on data-only and full PRs through
`scripts/pr_hygiene.py` (GG 2c, 2026-11-13), REPORT-ONLY until 5 clean real runs over >= 24 h
(docs/RUNBOOK.md, 'What runs on a data-only PR'); until the flip a finding warns and does not block.
Found while building it: the pre-commit gitleaks hook's entry is `--staged`, and a CI checkout has
nothing staged, so that hook scanned nothing in CI even on full PRs; the new step scans the PR's
own commits instead. The first gap was silent (a check that does not run); the second is loud (a
warning now, a blocked sync after the flip).
