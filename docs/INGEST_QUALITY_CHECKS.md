# Ingestion data-quality checks (item 2f)

Status: module added, NOT wired into any live path (no scraper, workflow or `ml.inference` imports
it). Provenance for every number below: `reports/ingest_checks_replay_2026-10-05.json`, produced by
`scripts/replay_ingest_checks.py` at master `1189d22a`.

## Why

Kalyan's own placeholder `updated_time` ("01 Jan 1970 00:00" IST) parses to
`1969-12-31T18:30:00+00:00`. `ml.sources.base.validate_observed_at` now rejects it per reading at the
adapter (2026-09-24), but before that fix 445 of the 1,038 Kalyan rows in
`data/fusion_snapshots.parquet` (2026-07-22 .. 2026-09-24) carried it, and the last valid Kalyan
reading is 2026-09-08. The adapter check is a single-reading control; nothing re-validated what was
stored, and nothing looked at series-level properties (jumps, duplicates, ordering, cadence,
cross-source agreement). `ml/ingest_checks.py` is that second layer.

## Contract

* Pure functions in `ml/ingest_checks.py`; `now` is always a parameter.
* Every check returns `list[Violation]` (`source, check, severity block|warn, code, message, value,
  where`). An empty list is the only pass.
* Fail closed (repo rule 98a): missing, unparseable or wrong-typed input is a violation. A string
  number (`"13700"`), a bool, NaN, inf, zero/negative, an epoch placeholder, a naive timestamp, a
  future timestamp, a per-10g value in a per-gram field (and the reverse) are never coerced to pass.
* CLI: `python -m ml.ingest_checks [--now ISO] [--out PATH] [--strict] [--macro-cache P]
  [--macro-intraday P]` writes `data/data_quality_status.json`
  (`schema_version`, `generated_at`, `overall_status`, `advisory_only`, per-source `status`,
  `counts_by_code`, `violations`). Exit code is 0 unless `--strict` and a block exists.
* The status file is time-dependent (staleness), so it is generated, not committed.

## Per-source specification

Severity: B = block, W = warn. Bounds: hard range = [0.5 x historical min, 2 x historical max]
(a x10 / /10 unit slip always lands outside); warn band = [0.8 x min, 1.25 x max] (a new extreme is
information, not an error). Jump limit = |ln(v / previous)| (divided by sqrt(calendar days) for
sparse series); warn at 2x and block at 4x the historical 99.9th percentile.

| Source (file) | Schema | Range (how derived) | Staleness (newest vs cadence) | Jump | Timestamp / ordering / duplicates | Unit / cross-source |
|---|---|---|---|---|---|---|
| Tanishq `data/prices.json` (751 rows: 726 live + 25 backfill) | list of `{timestamp, 22k, 24k, 18k, source}` | 22K Rs/g hard 6,445-25,000 (hist 12,890-15,075; 25,000 = scraper `RANGE_MAX` = `base.MAX_PLAUSIBLE_RATE_22K`), warn 10,312-18,844; 24K, 18K scaled by purity | W > 48 h, B > 168 h (cadence 3 h, p99 gap 37 h, max 101 h) | W 7.4% / B 14.8% per reading (p99.9 3.71%) | tz-aware, year >= 2020, not future (+5 min), strictly after previous (B), duplicate timestamp (B) | x10 -> `UNIT_SUSPECT_PER_10G`; 24K/22K = 24/22 and 18K/22K = 18/22 within W 0.5% / B 2%; latest vs IBJA pm_916/10 ratio W outside 0.95-1.08, B outside 0.85-1.20 (observed 0.9894-1.0450, n 118) |
| Tanishq `data/tanishq_scrape_outcomes.jsonl` | `{timestamp, outcome, fetch_method, trigger, slot_ist, blocked}` | outcome in {success, skipped, failure, blocked, error} else W | same as prices | n/a | same as prices | n/a |
| IBJA `data/ibja_rates.parquet` | `date` (YYYY-MM-DD), `fetched_at`, `{am,pm}_{999,995,916,750,585}` Rs/10 g | pm/am_916 hard 22,061-297,888 (hist 44,122-148,944), warn high 186,180; other purities x purity/916 | business days (`ml.ibja.business_days_since`): W > 3, B > 7; gap checks only from 2026-04-17 (store was a sparse backfill before) | W 10.7% / B 21.4% of |ln| / sqrt(days) (p99.9 5.35%) | date parseable, not future (IST), year >= 2020, duplicate date (B), file order not date-ordered (W), null AM (B), null PM on a past date (W) | purity ratios vs 916 within W 0.5% / B 5%; pm vs am same day W 3.3% / B 6.6% (max seen 1.64%) |
| Fusion `data/fusion_snapshots.parquet` | `capture_utc, as_of_date, schema_version, source, city, rate_22k, observed_at, attribution` | rate_22k as Tanishq 22K | per source newest capture W > 24 h, B > 72 h (6 h cron, max gap 25.7 h); `observed_at` age vs capture per source: grt W 1/B 24 h, malabar 96/192, ibja 144/240, kalyan 36/96 | W 5.3% / B 10.6% between captures of one series (p99.9 2.64%) | `observed_at` tz-aware, not epoch (the Kalyan 1969 case), not future (+5 min vs capture); duplicate (capture, source, city) B; known source; city only for kalyan | per-capture spread of national sources W 5% / B 10% (max seen 3.44%, n 301) |
| `data/shadow_fusion_output.json` | `capture_utc, as_of_date, national_failures, kalyan_failures, national_benchmark, cities` | benchmark and city values as Tanishq 22K; `band_half_width` W if > 10% of value | W > 12 h, B > 48 h (6 h cron) | n/a | `capture_utc` as above | every entry in `*_failures` is surfaced as W `SOURCE_REPORTED_FAILURE` so the 1969 text is visible |
| Feature store `data/feature_store/snapshots.parquet` (macro + IBJA + Tanishq PIT) | 38 columns, `*_asof_date` per value | per column hard/warn from the 232 committed rows (`MACRO_BOUNDS`; vol indices x3 high) | newest live_pit capture W > 120 h, B > 240 h; each `*_asof_date` may not be after `as_of_date` (B) nor > 6 days older (W) | per column, gap-scaled (`MACRO_JUMP`, p99.9 of backfill + live) | tz-aware capture, `as_of_date` parseable, duplicate (source, as_of_date) B, null core macro field B | tanishq_22k vs ibja_pm_916 same row (as above) |
| Macro daily/intraday `data/macro_cache.parquet`, `macro_intraday.parquet` (NOT committed, regenerated each CI run) | DatetimeIndex (bar start, UTC), `gold_usd`, `usd_inr` (+ the rest for daily) | `MACRO_BOUNDS` | intraday W > 6 h, B > 48 h; daily W > 120 h, B > 14 d (matches the macro job's own 14-day hard threshold) | daily limits applied per bar (an hourly step cannot exceed the daily limit) | index tz-aware, no duplicates, sorted, not future (+1 h intraday / +1 d daily) | n/a. INFERRED limits: there is no committed history to replay; pass `--macro-cache/--macro-intraday` in CI to evaluate them |
| Seeds `data/history_seed_inr22k_{label,proxy}.parquet` | daily UTC index 2013-01-01..2026-09-23, 22K Rs/10 g plus raw/duty columns | hard 11,232-312,980 (hist 22,464-156,490), min year 2000 so epoch placeholders still fail | static artifact, no staleness | W 14.4% / B 28.7% day over day (p99.9 7.18%) | tz-aware, sorted (B), duplicate date (B), > 5 day gap W | label vs proxy same date W 15% / B 30% (observed -10.9%..+7.3%) |

Units: `ml.direction.price_units` guards the dataset-level INR/10g vs USD/oz mix-up; these checks add
the field-level equivalent (`UNIT_SUSPECT_PER_10G`, `UNIT_SUSPECT_PER_G`). A per-gram value inside
the (wide) per-10g seed range is not caught by range; it is caught by the day-over-day jump
(`JUMP_BLOCK`).

## Replay results (VERIFIED, `reports/ingest_checks_replay_2026-10-05.json`)

Head replay (13,410 rows over 8 stores plus 1 JSON object):

| Source | Rows | Block | Warn | Reading |
|---|---|---|---|---|
| tanishq_prices | 751 | 0 | 5 | 5 real outage gaps of 61-101 h |
| tanishq_scrape_outcomes | 190 | 0 | 1 | same 61.9 h outage |
| ibja_rates | 273 | 0 | 12 | 8 past dates with no PM fix recorded (2026-05-19..29), 1 pm_750/pm_916 off by 3.4% (2026-06-30), 3 unsorted rows (2026-07-13..15) |
| fusion_snapshots | 1,935 | 446 | 0 | 445 Kalyan 1969 placeholders (true positives) + Kalyan feed stale (last valid reading 2026-09-08) |
| shadow_fusion_output | 1 | 0 | 1 | the reported Kalyan 1969 failure |
| feature_store | 232 | 0 | 0 | |
| history_seed_label / proxy | 5,014 / 5,014 | 0 / 0 | 0 / 0 | |

False-positive block rate on legitimate rows: 0 of 12,965 non-placeholder rows. All 19 warns trace to a real
condition (an outage, a missing fix, an unsorted write), not a bad threshold.

Revision-union replay (row-level checks over every distinct row in every git revision of the file;
736 / 301 / 170 / 162 revisions): the only blocks are the 445 Kalyan rows and 2 rows of
2026-05-09 in `prices.json` with 24K/18K = 2026 (a year parsed as a price; commit `41d27572` moved
them to an audit file), i.e. true positives that a check at ingestion would have stopped. Warns: IBJA
rows as first committed, before their PM fix was filled in (82), 5 purity warns, and 6 schema_version 1
feature-store rows that predate the `*_asof_date` columns (downgraded to warn on purpose).

Fault injection (copies of real rows corrupted: x10 unit, string number, NaN, negative, zero, epoch,
future, naive timestamp, duplicate, out-of-order, x1.3-1.5 jump, broken purity ratio, missing field,
wrong container, Kalyan 1969/1970 placeholder): 73 of 73 flagged `block` across 7 sources.

Time-split check of the jump limits (limits derived from the first half, counted on the second): block
limit exceeded 0 times in every series; warn limit exceeded once for IBJA (5.98%, the real
2026-05-13 move) and once per seed (11.7%, the real late-January 2026 move). The committed limits
use the full history, so their in-sample false-positive rate is optimistic by construction; the
split above is the honest estimate (0 blocks, 3 real-move warns of 5,671 test steps).

## Optional CI step (proposal only; `check-price.yml` is being edited in PR #2390 and is NOT touched here)

After the macro-cache and IBJA-append steps, before the commit step, advisory and non-blocking:

```yaml
      - name: Ingest quality checks (advisory)
        continue-on-error: true
        run: |
          python -m ml.ingest_checks \
            --macro-cache data/macro_cache.parquet \
            --macro-intraday data/macro_intraday.parquet \
            --out "$RUNNER_TEMP/data_quality_status.json"
          cat "$RUNNER_TEMP/data_quality_status.json" >> "$GITHUB_STEP_SUMMARY" || true
```

Writing to `$RUNNER_TEMP` keeps the status file out of the bot's commit. Promotion path: run it
advisory for ~2 weeks, read the warn/block counts in the step summary, then (a separate PR) consider
`--strict` as a gate for block-severity codes only.

## Limits (stated, not hidden)

* Not wired: nothing here prevents a bad row being committed today.
* Bounds are derived from about six months of live data (IBJA from 2022); a regime change (gold +60%)
  turns `VALUE_OUTSIDE_HISTORY` warns on, and only a >2x move turns blocks on.
* The IBJA clock-assumed `observed_at` (11:30 UTC on the data date) is not a source timestamp; its
  age limit (144 h warn) is wide for that reason.
* Cross-source checks compare only sources captured in the same cycle.
