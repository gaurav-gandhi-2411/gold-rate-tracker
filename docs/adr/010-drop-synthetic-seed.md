# ADR 010 — Drop Synthetic Seed as Training Input

**Status:** Accepted — 2026-05-18

## Context

The initial training corpus for the gold price forecaster was 92% synthetic: 444 rows
generated from Yahoo Finance USD/INR + gold-USD prices multiplied by a time-varying premium
factor (`ml/seed_history.py`). Only 71 rows (8%) were real Tanishq scrapings at the time of
this decision (Phase 1 audit, 2026-05-18).

Phase 1 audit verdict: the synthetic seed was necessary to bootstrap the model before
sufficient real data existed, but it introduces a systematic bias. The time-varying premium
factor is a rough approximation; the calibration step (`_calibrate_seed()` in `ml/forecast.py`)
partially corrects this at the boundary, but interior seed values remain uncalibrated.

The upcoming IBJA backfill (PR C) will provide 2+ years of real, exchange-sourced INR/g
price data to replace the synthetic seed. Once PR C lands, there is no reason to retain
synthetic rows in the training corpus.

## Decision

Retire `ml/seed_history.py` and archive the synthetic seed JSONs:
- `data/history_seed.json` → `archive/history_seed_synthetic.json`
- `data/history_seed_v1_uniform_premium.json` → `archive/history_seed_v1_uniform_premium.json`
- `ml/seed_history.py` → `archive/scripts/seed_history.py`

During the interim period between PR B (this change) and PR C (IBJA backfill), the model
loads from `archive/history_seed_synthetic.json` with a deprecation log line. This preserves
Option A continuity (no behavior regression from PR B alone) while making the deprecated
status explicit. The legacy seed path is removed entirely in PR H when the legacy LightGBM
inference path is also retired.

## Consequences

**Positive:**
- Training corpus is honest: real-data-only from PR C onward.
- Removes 444 synthetic rows whose premium-factor calibration was approximate.
- Simplifies `ml/forecast.py:load_combined_history()` — PR H will remove the seed loading
  entirely once IBJA backfill provides sufficient real data depth.

**Negative / accepted risks:**
- Warmup flag (`warmup: true` in `forecast.json`) remains `true` until real-data count
  reaches 100 (currently 71). This is surfaced to users via the PWA header per ADR 005.
- During the PR B → PR C window, the model trains on the archived synthetic seed + 71 real
  rows. Forecast quality is unchanged from pre-PR-B (same corpus, same path — only the
  file location changed).
- MCX-to-IBJA basis adjustment risk per §3.1.6 of PROGRESS.md: when IBJA backfill lands
  (PR C), a rolling 30-day median ratio will be applied to the MCX-derived backfill segment
  to align it with IBJA-916-PM levels. Until ≥30 overlap days exist, `basis_adjustment_applied`
  will be `false` in `forecast.json`.

## Alternatives Considered

**Option B — Skip seed loading entirely (train on 71 real rows only):**
Rejected for PR B. PR B's scope is TFT/N-BEATS retirement; introducing a training corpus
change with only 71 rows would simultaneously regress forecast quality and conflate two
distinct changes in one PR. Option B is the natural end-state after PR C provides real depth.

**Keep synthetic seed in `data/` permanently:**
Rejected. The Phase 1 audit flagged 92% synthetic training data as a blocker-level risk.
Retaining the file in `data/` would leave the misleading corpus composition intact.
