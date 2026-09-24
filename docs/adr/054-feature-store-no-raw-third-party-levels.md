# ADR 054 — Feature Store Stops Committing Raw Third-Party Levels (schema_version 5)

**Status:** Accepted, implemented 2026-09-25 (D3).

---

## Context

`data/feature_store/snapshots.parquet` is committed to this public repo and accumulates one row
per IST trading day since 2026-06-07 (target: ~12 months before any training, per
`docs/FEATURE_STORE.md`). Through schema_version 4, nine of its columns (`gold_usd`, `usd_inr`,
`us_10y_yield`, `dxy`, `sensex`, `vix`, `crude_wti`, `tips`, `india_vix`) held the raw daily close
fetched from Yahoo Finance for each series (`ml/macro.py`'s `TICKER_MAP`). Committing those raw
closes to a public git history republishes Yahoo Finance's data, which is not covered by Yahoo's
terms of use (same finding as the rest of this PR's inventory — see the PR body).

Three code paths write these columns: `ml.feature_store.capture_daily_snapshot` (the live CI
path, `check-price.yml`, 8x/day), `ml.feature_store_backfill.run_backfill` (manual, one-off
historical reconstruction), and `ml.feature_store_backfill.patch_missing_macro_series` (manual,
one-off repair of null `crude_wti`/`tips` on old backfill rows). All three needed the same fix.

## Decision

From schema_version 5, the nine columns above hold each series' **z-score against its own
trailing `MACRO_ZSCORE_WINDOW_CALENDAR_DAYS` (365 calendar day) window** — `(x - trailing_mean) /
trailing_std`, `ddof=1` — computed at capture time from the in-memory macro frame
(`data/macro_cache.parquet`, itself never committed — see `ml/macro.py`'s own docstring). The
column **names are unchanged**; only their semantic content changes at the v4→v5 boundary. A
parallel `{series}_sha256` column is added for each series: `sha256` of the raw value at fixed
6-decimal precision, written at capture time so a later, independently-sourced re-fetch of the
same historical date can be checked against what this snapshot actually captured, without the raw
number itself ever being committed.

Implementation: `ml.feature_store.macro_zscore` / `ml.feature_store.macro_value_hash`, used by
all three writer paths (`capture_daily_snapshot`, `run_backfill`, `patch_missing_macro_series`).

**Why z-score over a 1-day return.** The task that produced this ADR named both as acceptable
("derived features (returns/z-scores)"). A pure day-over-day return only encodes "how much did it
move today"; a z-score against a trailing window also encodes "is it currently high or low
relative to its own recent range" — closer to the information content the raw level carried,
without being invertible back to the level (recovering the level from a z-score requires the
window's mean and std, which aren't published either). A single day's return is also almost
useless for a series like `us_10y_yield` where daily moves are tiny relative to the level's
information content.

**Why the same column names, not new ones.** `ml/direction/dataset.py` (`FEATURE_COLS`) and 15
other files consume these columns as direct model-input features (see the PR body's blast-radius
note). Renaming would require reworking that whole live/actively-developed pipeline (ADR 032's
M1/M2/M3 work is landing in this same window) as part of a data-licensing fix — out of scope here
and a real conflict risk with concurrent ML work. Reusing the column names keeps every existing
reader mechanically working; the cost is that the columns' *meaning* silently changes at the
schema_version boundary, which is why this ADR exists and why the change is called out loudly in
`_ALL_COLUMNS`' comments, in `docs/FEATURE_STORE.md`, and here.

**Consequence a future training run MUST account for:** rows with `schema_version <= 4` hold raw
levels; rows with `schema_version >= 5` hold z-scores. Mixing them as one feature without
filtering on `schema_version` first will silently train on two different units for the same
column. Filter (`df[df["schema_version"] >= 5]`) or treat the boundary explicitly.

**What was NOT done, and why.** The alternative the task's author raised —
"move raw capture to a non-public location" (a private bucket/repo/database) — would preserve raw
levels for eventual research use but requires a new account, new storage, and a new secret in CI.
Per the standing rule that new-account/new-storage decisions are GG's to make, that option was
**not implemented**; it is written up for GG below. A third option was considered and rejected:
GitHub Actions artifacts are not actually non-public for a public repo (anyone with read access to
the repo can view and download workflow artifacts), so uploading raw captures there would not
solve the republishing problem — GitHub Actions **cache** (`actions/cache`) genuinely is
repo-scoped and not publicly downloadable, but caches are explicitly best-effort/evictable
(GitHub does not guarantee retention; practically ~7 days unused), unsuitable as the sole copy of
a slow-accumulating, multi-year research dataset.

## Alternative for GG: move raw capture to non-public storage

If full-fidelity raw levels are wanted for future research (rather than z-scores), the options
are, roughly in order of setup cost:
1. A private GitHub repo (same GitHub account, `git submodule` or a separate clone in CI) — needs
   a new repo and a PAT/deploy key secret in `check-price.yml`.
2. A private cloud bucket (GCS/S3) — needs a new bucket, a service account or access key, and a
   secret in CI. Given the SOLE-IDENTITY GCP mandate already in force for this account, a GCS
   bucket would be the most consistent choice if GG picks this path.
3. A managed database (e.g. a free-tier Postgres) — more setup than either of the above for no
   real benefit here (the data is already tabular/parquet-shaped).

None of these were implemented in this PR. Git history for `data/feature_store/snapshots.parquet`
also already contains every raw value captured through schema_version 4 — see the PR body's plain
statement on that.

## Consequences

- Every future `live_pit` row captured by the CI pipeline (`check-price.yml`, 8x/day) is
  z-score-only from this PR onward; no new raw Yahoo Finance levels enter the public git history
  through this path.
- `run_backfill` and `patch_missing_macro_series` (manual, not called by any CI workflow today —
  verified by grep) carry the same fix, so a future manual re-run doesn't reopen the gap.
- `n_macro_null` semantics are preserved: a `None` z-score (fewer than 2 trailing points, or the
  series absent) still counts as null exactly as a missing raw value did.
- `docs/FEATURE_STORE.md`'s schema table and version history are updated in this PR to describe
  the v5 semantics.

## Alternatives considered

- **Store only derived features, dropping the raw-named columns and adding new
  `{series}_ret1`/`{series}_z` columns instead of reusing the existing names.** Rejected for the
  reason above: doing so would null every schema_version-5+ row's `FEATURE_COLS` inputs in
  `ml/direction/dataset.py` unless that 16-file consumer pipeline were reworked in lockstep,
  which is out of scope for this PR and risks conflicting with concurrent M1/M2/M3 ML work.
- **Do nothing until GG picks non-public storage.** Rejected: leaves the live CI pipeline
  republishing new raw Yahoo Finance levels into the public repo 8x/day for however long that
  decision takes.
