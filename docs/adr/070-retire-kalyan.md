# ADR 070 — Retire Kalyan (live fallback tier, shadow fusion, city layer)

**Status:** Accepted (GG decision D4, 2026-10-05). Supersedes the Kalyan parts of ADR 026.
**Date:** 2026-10-05
**Deciders:** GG (owner), CC (implementor)

## Context

ADR 026 built a two-layer fusion: a national benchmark (IBJA, GRT, Malabar) scaled by a city
markup taken from Kalyan, the only source with a city-granular endpoint. Kalyan fed two places:
the live tier-3 fallback (`ml/inference.py::_try_fusion_fallback`) and the shadow cycle
(`ml/shadow_fusion.py`, `shadow-fusion.yml` every 6 h).

What the stored data shows (VERIFIED, `data/fusion_snapshots.parquet` read 2026-10-05 on this
branch):

| Fact | Value |
|---|---|
| Kalyan rows stored | 1,038 of 1,935 rows (ibja 301, grt 300, malabar 296) |
| First / last Kalyan capture | 2026-07-19T12:21:11Z / 2026-09-24T17:06:07Z |
| Rows with an epoch placeholder `observed_at` (year < 2000) | 445 |
| Latest genuine Kalyan `observed_at` | 2026-09-07 07:30 UTC |
| Cities | Bangalore, Chennai, Hyderabad, Ernakulam |

- Kalyan's own `updated_time` returns the sentinel `01 Jan 1970 00:00` when its board is not
  live. Before `ml.sources.base.validate_observed_at` existed the adapter stored
  that as a real reading. Since the adapter now fails closed, no Kalyan row has been stored at all
  after 2026-09-24, and no valid one after 2026-09-08. Every shadow cycle since then logs a
  Kalyan "structure" failure (see the `kalyan_failures` block still in
  `data/shadow_fusion_output.json` until the next cycle rewrites it).
- It never delivered city granularity: Kalyan's rate was identical across all four cities in 259 of
  259 multi-city cycles (`docs/SESSION_AUDIT_2026-08.md`;
  `reports/tanishq_update_times/kalyan_city_identity.json`), so the per-city output was always a
  national number with a city label (ADR 026 update, 2026-07-30).
- The live fallback had effectively been running without Kalyan for four weeks: tier 3 only ever
  served `["grt","malabar"]`-style consensus.

## Decision

Retire Kalyan everywhere it is active. Keep what frozen research needs.

Removed:

- **Live fallback tier.** `_try_fusion_fallback` fetches GRT and Malabar only. Tier order is
  unchanged: 1 fresh Tanishq, 2 IBJA-calibrated, 3 GRT + Malabar consensus, 4 last-known Tanishq.
  The tier-3 band is unchanged: `degraded_band_half_width(national)` is
  `national.band_half_width * NATIONAL_DERIVED_BAND_MULTIPLIER`, the exact value
  `fuse_city_price(None, ...)` returned while Kalyan was down, so no served number changes.
- **Shadow fusion.** `ml/shadow_fusion.py` no longer fetches Kalyan, writes Kalyan rows, or emits
  `cities` / `kalyan_failures`. The workflow's per-source failure annotation reads
  `national_failures` only.
- **City layer.** `fuse_city_price`, `FusedCityPrice` and `compute_city_markup` in `ml/fusion.py`
  existed only to apply Kalyan's markup; removed.
- **Switch.** `kalyan` is removed from `config/retailers.json`, `ml.retailers.KNOWN_RETAILERS` and
  `scraper/retailer-enabled.mjs` (all three must agree or the fail-loud loader raises).
- **Markup tracker.** `kalyan_<city>` rows dropped from `ml.markup.FUSION_RETAILERS`.
- **PWA / alerts.** The `fusionSourceKalyan` string (EN and HI) and its label mapping in `app.js`;
  the Kalyan name in the T11 alert text.
- **Docs.** `docs/RETAILER_TAKEDOWN.md`, `docs/MODEL_INVENTORY_2026-10.md`, ADR 026 header note.

Kept:

- **Historical rows** in `data/fusion_snapshots.parquet`, untouched (no rewrite, no deletion).
  `ml.fusion_snapshot_store` and `ml.markup.load_fusion_readings` read any `source`, so frozen
  analyses (markup, timing audit, ADR 026/048/055/059 evidence) still reproduce.
- **The adapter**, moved with `git mv` to `archive/kalyan/kalyan.py` with its mocked tests
  (`archive/kalyan/test_sources_kalyan.py`, 17 tests, run on demand with `pytest archive/kalyan`;
  CI does not collect them). Nothing imports it. Archived rather than deleted so ADR 026's
  adapter evidence stays inspectable.
- Frozen reports that mention Kalyan (`reports/markup_analysis.json`,
  `reports/timing_audit/*`, `reports/silent_fallbacks_audit.md`,
  `reports/tanishq_update_times/kalyan_city_identity.json`) are unchanged.
- `scripts/check_retailer_language.py` still lists "kalyan" as a retailer name; it is a copy-policy
  list, and removing it would only weaken the check.

## Consequences

What was lost, stated plainly:

- **No city-granular source exists any more.** The PWA never had a city UI and never showed a
  per-city price (the hero is a national estimate; "Bengaluru" in the page is an identity label,
  not a Kalyan-derived number), so no city view had to be hidden. The product does not offer
  city-level prices and must not claim to.
- A fourth independent retailer opinion is gone from the tier-3 consensus. Practically this
  changes nothing today (Kalyan contributed to zero fallback results since 2026-09-08), but
  tier 3 now rests on two boards. If one fails it serves one board; if both fail it falls to
  tier 4.
- ADR 026's Phase D (promotion of the shadow fusion) loses its city dimension. The remaining
  shadow data is national-only and starts from the same 301/300/296-row history.
- Kalyan's published terms (see `docs/SESSION_AUDIT_2026-08.md`) already restricted public
  reproduction; retiring removes that exposure on the live path.

Follow-ups, not done here:

- **PR #2075 (raw third-party data encryption):** register these Kalyan paths there:
  `data/fusion_snapshots.parquet` (the 1,038 `source == "kalyan"` rows; the file also holds
  ibja/grt/malabar rows), `reports/tanishq_update_times/kalyan_city_identity.json`, and
  `archive/kalyan/kalyan.py` only if the adapter's captured payload fixtures are treated as raw
  data (they are synthetic-shape fixtures, so probably not).
- `data/shadow_fusion_output.json` still carries the old `cities` / `kalyan_failures` blocks until
  the next scheduled cycle rewrites it (the file is bot-regenerated; not hand-edited here).
- `shadow-fusion.yml` triggers on `ml/**` pushes to master, so merging this PR starts one cycle.

Residual risks: a consumer outside this repo reading `shadow_fusion_output.json["cities"]` would
see the key vanish (none found by grep in app, scripts, workflows, or tests); and the `kalyan`
key disappearing from `config/retailers.json` would break any stale branch that still lists it
(the loader rejects unknown keys loudly, by design).

## Alternatives

1. **Keep Kalyan, fix the adapter.** Rejected: Kalyan stopped publishing a live timestamp; there
   is nothing to fix on our side, and its value (city granularity) was disproven.
2. **Disable via `config/retailers.json` (`enabled: false`) only.** Rejected: leaves dead code,
   dead alert wording, and a permanently "disabled" entry; the ADR 059 switch is for retailers
   that may return.
3. **Delete the adapter and tests.** Rejected: ADR 026 and frozen results cite it; version control
   alone is a weaker archive than a runnable, tested file.
4. **Rewrite or drop Kalyan rows from the parquet.** Rejected: committed history rows are
   evidence; encryption of raw third-party data is PR #2075's job.
