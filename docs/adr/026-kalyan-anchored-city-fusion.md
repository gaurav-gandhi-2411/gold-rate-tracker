# ADR 026 — Kalyan-Anchored City-Level Consensus (Fusion Foundation, Option 1)

**Status:** Accepted — Phases A/B implemented, Phase C (shadow) live, Phase D (promotion) pending.
**Update 2026-07-30:** the city-level precision this ADR originally targeted is not supported by
accumulated data — see "Update: city-differentiation finding" below. The two-layer architecture
stands; the *labeling* of its output changes from "city-specific" to "national retail consensus."

**Date:** 2026-07-19

**Deciders:** GG (owner), CC (implementor)

---

## Context

The product has run on a single retail source (Tanishq) since inception, with IBJA-calibrated
estimate as fallback (ADR 021), then promoted to primary once Tanishq's Cloudflare block became
sustained (ADR 025). Both states are strictly *national or single-retailer* — no location
granularity beyond the assumption that Tanishq's board rate represents Bengaluru.

A research pass (this same day, not separately documented) tested reachability of alternative
retail sources from an actual GitHub Actions runner IP (not a residential IP — the distinction
matters, since Tanishq's block is IP-range-specific and a residential-IP test would have been
worthless evidence). Findings, torn-down diagnostic workflows, nothing merged from that phase:

| Source | GH Actions reachable | Granularity | Extraction |
|---|---|---|---|
| Tanishq | No — `403 Access Blocked`, Cloudflare | N/A | — |
| **Kalyan Jewellers** | **Yes** | **Genuine city-level** (explicit place_name, e.g. "BANGALORE") | JSON via `POST kalyan_gold_rates/ajax/get_rate` |
| GRT Jewellers | Yes | National only (no city selector) | Embedded JSON in page HTML |
| Malabar G&D | Yes | National only in practice (schema has a `state` field but it returns empty even with an explicit state filter) | GraphQL `getMetalRate` |
| IBJA (existing primary) | Yes (already used) | National only | Existing pipeline |
| goodreturns.in / bankbazaar.com | Yes | City-labeled but **not any named retailer's number** — undisclosed methodology / MCX-derived | Would be dishonest to attribute to Tanishq or any jeweler |
| Joyalukkas | Page loads, no rate-fetching call observed | Unknown | Likely a static CMS page, not a live source |

Kalyan is the only source with genuine, self-labeled city granularity. GRT, Malabar, and IBJA are
all national-only. All four currently agree within roughly ±0.3% of each other — there is nothing
to arbitrate today, which is exactly why this ADR proposes a *lightweight* consensus now, not the
full online-learning fusion system (that remains planned as Option 2, see Future Work).

## Decision

Build a two-layer foundation now, architected so Option 2 slots in without a rewrite:

```
retail_price(location) = fused_national_benchmark × location_markup(location)
```

- **fused_national_benchmark**: reliability-weighted consensus of IBJA + GRT + Malabar. Weights
  are **static** today (`ml/fusion.py::DEFAULT_WEIGHTS`), not learned — IBJA weighted highest
  since it carries the only externally-validated calibration (R²=0.963 vs Tanishq, ADR 025).
  Disagreement check is a simple threshold (band widens if any source diverges beyond a fixed
  percentage from the weighted mean) — not the historical-noise-based version Option 2 will build.
- **location_markup**: for cities Kalyan covers, `markup = kalyan_city_rate / national_benchmark`
  computed fresh each cycle (no smoothing yet — there's no history to smooth over on day one).
  For everywhere else: no markup, national-benchmark-derived, labeled as such. Never fabricated.
- **The weighting seam**: `fuse_national_benchmark(readings, weight_fn=DEFAULT_WEIGHTS)` takes the
  weight function as a parameter. Option 2 replaces `DEFAULT_WEIGHTS` with a learned function of
  the same signature (`readings -> {source: weight}`) — callers (`ml/shadow_fusion.py`, and later
  the promoted display path) never change.

### Source adapter interface

`ml/sources/base.py` defines `SourceReading` (the common output shape: source name, city or
`None` for national, rate_22k, observed_at, raw attribution string) and a `SourceAdapter` Protocol
(`fetch() -> SourceReading`, structurally typed per this repo's Protocol-over-ABC convention).
Adding a source later is: write a module implementing the Protocol, register it in
`ml/shadow_fusion.py`'s adapter list. No change to the fusion engine.

### Honest limitation: Kalyan's granularity is store-location, not always city-canonical

Kalyan's dropdown gives a literal, unambiguous city-name label for **Bangalore, Chennai,
Hyderabad, and Ernakulam (Kochi)** — these four are registered as city sources. Mumbai, Delhi, and
Kolkata only have *neighborhood* entries (Andheri/Borivalli/Thane for Mumbai; Karol
Bagh/Dwarka/Pitampura for Delhi; Camac Street/Salt Lake for Kolkata) — no single option is
literally labeled "Mumbai." Rather than pick a neighborhood and imply it represents the whole
metro (a labeling decision that isn't mine to make silently), these three are **deliberately
deferred** — not registered as city sources in this pass. Flagging for GG: if city coverage should
expand to these metros, the product needs an explicit choice of representative locality and how to
label it (e.g. "Kalyan – Andheri (Mumbai area)"), not an autonomous pick.

### PIT snapshot store extension

A new store, `data/fusion_snapshots.parquet` (`ml/fusion_snapshot_store.py`), separate from the
existing `data/feature_store/snapshots.parquet`. The existing store is a wide one-row-per-day ML
feature vector; fusion PIT data is naturally tidy/long (one row per source × city × cycle) and
would badly distort the existing schema if crammed in. Idempotent append per `(source, city,
capture_utc)`, same philosophy as `feature_store.append_snapshot`. This starts accumulating now —
it is the history Option 2's weight-learning will consume once there's enough of it to be
meaningful (see Future Work).

### Shadow mode, not live

`ml/shadow_fusion.py` runs the full fetch → fuse → log cycle and writes to
`data/fusion_snapshots.parquet` and `data/shadow_fusion_output.json` (what the fused price *would*
show, per registered city, this cycle). It does **not** touch `data/forecast.json`, `app.js`, or
any live-displayed value. Wired into a new `shadow-fusion.yml` workflow on a 6-hour schedule
(deliberately looser than the existing 3h `check-price.yml` cadence — these are three *new*,
unproven scraping relationships with third-party sites; starting polite and infrequent is the
right default until there's a reason to tighten it).

### Canary / quiet-fail handling

Each adapter distinguishes a **network failure** (timeout, non-200, connection error) from a
**structure failure** (200 response but the expected JSON keys / HTML pattern are absent — the
site changed shape). Both raise, but as distinct exception types
(`SourceNetworkError` / `SourceStructureError`) so `shadow_fusion.py`'s per-source try/except can
log which failure mode occurred — this is the repo's own known recurring failure class (silent
breakage from a site redesign), and the whole point of catching it is knowing *which* kind of
failure just happened, not just that something failed. A single source failing is normal handling,
not an alert; only the shadow-mode summary is expected to show it as `null` for that cycle.
Cross-referenced against the existing "a source being unreachable is the expected steady state,
not an outage" precedent from ADR 025 (Tanishq specifically) — the same tolerance now applies to
all four sources uniformly.

## Alternatives considered

**Build the full Option 2 (online-learning weights + historical-noise disagreement) now.**
Rejected for this pass: with zero PIT history and sources agreeing within ±0.3%, there is nothing
for online learning to learn yet, and no historical noise distribution to calibrate a
disagreement threshold against. Building the learning machinery before there's data to learn from
is premature complexity — the static-weight seam exists specifically so this isn't a rewrite later,
just a swap.

**Pick a Mumbai/Delhi/Kolkata neighborhood as a stand-in "city" reading.** Rejected as a silent
decision. Whether "Andheri" is an honest stand-in for "Mumbai" is a labeling/product-identity call,
not a scraping detail — flagged for GG rather than decided here (same posture ADR 025 took on the
IBJA-primary copy change).

**Fuse raw retail prices directly across cities (skip the two-layer decomposition).** Rejected per
the standing architectural instruction: this blurs city-specific retail variation into national
mush and defeats the purpose of having Kalyan's city data at all.

## Consequences

**Positive:**
- Four independent sources feeding the national benchmark, rather than one (IBJA alone) — more
  robust to any single source's outage or drift, with the reliability-weighting seam already in
  place for Option 2.
- Genuine city-level pricing for Bangalore/Chennai/Hyderabad/Ernakulam, honestly distinguished
  from national-derived estimates everywhere else — no location gets a fabricated number.
- PIT history starts accumulating today across all four sources and four cities; this is the
  precondition for Option 2 (see Future Work) and currently doesn't exist at all.

**Negative / honest limits:**
- Static weights are a judgment call (IBJA weighted highest for its calibration pedigree, not
  because it's been empirically shown best among these four — that empirical comparison is exactly
  what Option 2 will eventually produce).
- Mumbai/Delhi/Kolkata remain uncovered pending a labeling decision — this is a real coverage gap,
  not an oversight; documented rather than silently worked around.
- Three new scraping relationships (Kalyan, GRT, Malabar) — each one more surface area that can
  eventually escalate the way Tanishq did (ADR 016/025's own honest precedent: "not blocked from
  day one either"). The 6h shadow cadence and per-source canary distinction are the mitigations
  available at zero cost; there is no guarantee against future blocking.
- Shadow-mode validation (Phase C promotion gate) needs real elapsed time to produce meaningful
  numbers — this ADR documents the mechanism going live, not a validated outcome. See PROGRESS.md /
  the relevant PR for validation results once the accumulation period has passed.

## Update: city-differentiation finding (2026-07-30)

**Finding:** across all 43 accumulated shadow-fusion cycles (2026-07-19 to 2026-07-30, ~10.6
days at the 6h cadence), Kalyan's `rate_22k` is **identical across all four registered cities**
(Bangalore/Chennai/Hyderabad/Ernakulam) in every single cycle — 0/43 show any variation, range
exactly ₹0.00 city-to-city. The national layer, by contrast, does show real disagreement worth
arbitrating (IBJA/GRT/Malabar spread: mean 0.36%, up to 1.3% across the same cycles) — the fusion
engine's *national* consensus is doing genuine work; it's specifically the *city-markup* layer
that has produced no signal.

**Mechanistic reason:** Indian retail gold pricing quotes one national MCX/IBJA-derived metal
rate; the store-to-store/city-to-city variation that genuinely exists (making charges, GST
handling nuances) lives outside `rate_22k` and isn't captured by this scrape. Kalyan's dropdown
giving city-name labels was never proof the underlying *number* varies by city — this is not a
scraping bug, it's the real absence of city-differentiated metal pricing behind the label.

**Decision:** relabel, don't remove. The two-layer architecture
(`retail_price = fused_national_benchmark × location_markup`) and the PIT snapshot collection
across all four Kalyan cities both stay exactly as built — cheap to keep, and the only way to
notice if a genuinely city-differentiated source appears later or if Kalyan's behavior changes.
What changes is presentation: `ml.fusion.fuse_city_price`'s `coverage` value is renamed
`"city_specific"` → `"kalyan_anchored"`, and its `attribution` string now reads "National retail
consensus, Kalyan-anchored (...)" rather than implying location-specific pricing. Any future
Phase D promotion must present fusion output as a *national retail consensus (GRT, Malabar,
Kalyan)*, never as per-city pricing, until a source demonstrates real city-to-city variation.

**Consequence for Phase D:** the original promotion vision (showing "Bangalore: ₹X, Chennai: ₹Y…"
as differentiated numbers) is not supportable today — promoting to a *national* display remains
viable once shadow validation (Phase C) clears its own bar (see Future Work below), but a
city-differentiated display specifically would need a new source, not just more history from
Kalyan.

## Future work — Option 2 (planned, not started)

Revisit once `data/fusion_snapshots.parquet` has enough history that a learned weight function
would have something real to fit — by analogy with ADR 021's H5 calibration bar (30 overlap pairs,
~3 months at the prior accumulation rate), a rough target is **4-6 weeks of 4x-daily snapshots per
source** before weight-learning is meaningful, though the actual trigger should be a check of
realized per-source tracking error variance, not just a calendar date. At that point: replace
`DEFAULT_WEIGHTS` with a learned function (same signature), replace the fixed disagreement
threshold with one derived from each source's realized historical noise, and reassess whether
Mumbai/Delhi/Kolkata coverage should be added (pending the labeling decision above).
