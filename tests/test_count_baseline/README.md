# Test-count baseline

One file per test file: `<test path>.count` holds the minimum number of test definitions that file
must keep (for example `tests/test_drivers.py.count`). `scripts/check_test_counts.py` fails CI if a
file drops below its floor, disappears, or is not listed here.

- Adding tests to an existing file needs no edit here.
- A new test file needs its own `.count` file: run `python scripts/check_test_counts.py --update`.
- Lowering or removing a floor is only ever done with `--update`, so it shows up in the PR diff.

This replaced the single `tests/test_count_baseline.json` on 2026-09-26, because every PR that added a
test file edited that one JSON object and any two such PRs conflicted there.
