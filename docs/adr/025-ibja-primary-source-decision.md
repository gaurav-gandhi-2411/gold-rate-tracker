# ADR 025 — Invert Source Hierarchy: IBJA Primary, Tanishq Enrichment

Status: **Accepted and implemented** (2026-07-18). Ratified by the owner and shipped:
`ml/ibja.py`'s business-day gap helpers, `ml/inference.py`'s IBJA-primary source
selection, `ml/notifications.py`'s T9/T9_ESCALATE re-wire to IBJA freshness, the
`app.js` freshness-pill/stale-banner honest labeling, and the README/index.html/
manifest copy pass all landed in the same change as this status flip.
Date: 2026-07-18
Supersedes/relates: ADR 016 (scraper hardening — proxy/stealth already rejected here),
ADR 021 (H5 IBJA-calibrated fallback — the mechanism this ADR promotes to primary).
The retired Cloudflare Worker relay (2026-06-13–2026-06-25, see docs/RUNBOOK.md) was
the prior escalation in the same pattern.

## Context

**Second Tanishq anti-bot escalation in one month.** 2026-06-25: Tanishq extended its
Cloudflare bot-blocking to Cloudflare Workers egress, retiring the Worker relay
(commit history + RUNBOOK). 2026-07-18: Tanishq's Cloudflare challenge now also blocks
the Playwright *headless-browser* fallback from GitHub Actions egress, not just the
plain-`fetch` path — confirmed via `gh run view --log` on 4 consecutive scheduled runs
plus one fresh `workflow_dispatch` re-dispatch, all producing identical
`Cloudflare challenge page` failures on all 3 retry attempts.

ADR 016 (2026-06-08) already named this exact risk and already rejected the standard
countermeasures on record:
- Stealth Playwright plugins — "fight an adversarial system that actively patches
  against them."
- Residential proxy rotation — "monthly cost, added latency, third-party dependency.
  Breaks the ₹0/month free-tier constraint."
- An official Tanishq API — "dead end explored in the original Phase 3 audit. ... DOM
  scraping is the only available path."
- ADR 016, verbatim: *"IP-level CF blocking still produces 3-attempt failure ... No
  code can change this without proxy rotation or an alternative data source."*

Two escalations in five weeks, against a system that (per ADR 016) already uses
"ML-based behavioral scoring, TLS fingerprinting, IP reputation," is evidence of a
trend, not a blip. **Honest read: this is very unlikely to be transient, and not
cheaply defeatable within the project's zero-cost constraint.** The retry/fallback
hardening in ADR 016 (H1/H2) already assumed this ceiling and was never meant to
survive an IP-reputation-level block — it hasn't failed, it's doing exactly what it
was scoped to do.

ADR 021 (H5) anticipated this too and built the IBJA-calibrated fallback specifically
for it — as of this incident, IBJA is the **only source currently reachable at all**,
not an occasional bridge. The system degraded exactly as designed: `price_source:
"ibja_calibrated"`, honestly labeled "Approximate price," current calibration (per
`data/calibration.json` at commit `be763ef`) is `r_squared=0.9631`, `n_observations=51`,
`residual_std≈90.68` (INR/g terms). That calibration quality is strong enough to ask a
different question than "how do we patch the outage" — namely, **should IBJA remain a
fallback, or become the primary source, with Tanishq demoted to an opportunistic
enrichment?**

This ADR scopes that decision. It does not implement it.

## Decision

**Recommended: Option B — invert the hierarchy. IBJA-calibrated becomes the primary
displayed price; a successful Tanishq scrape becomes an enrichment signal (narrows the
band, confirms the calibration) rather than a hard dependency.**

### Option A — Keep fighting for Tanishq access (proxy rotation / stealth plugins)
**Rejected**, for the same reasons ADR 016 already gave, now stronger: this is an
arms race against a system that has escalated twice in five weeks specifically to
close the gaps our hardening opened. Any workaround found today has a visible
precedent (Workers egress, now Actions egress) of being closed within weeks. Proxy
rotation also breaks the ₹0/month constraint outright. Continuing to invest here is
throwing effort at a trend line that points down, not a one-time fix.

### Option B — IBJA primary, Tanishq enrichment-when-reachable (recommended)
**What it costs vs. what it keeps:**
- **Update cadence:** IBJA publishes AM + PM once per weekday (not 8×/day like the
  current 3h cron). This is the real, honest cost — the displayed price would update
  roughly 2×/day instead of up to 8×/day on a healthy Tanishq day. Whether that
  matters depends on how much intraday movement Tanishq's own retail rate actually
  shows between cron cycles — not measured here, worth a quick historical check
  before committing, but the product has never claimed sub-daily precision (ADR 012:
  naive flat-hold headline, no false-precision claims).
- **Weekends/holidays:** IBJA does not publish (confirmed this incident — 2026-07-18
  is a Saturday). This is **not a new UX gap to solve** — it is the exact state the
  product already handles today via the H5 banner and estimate range (carry the last
  published close, labeled "Approximate," bounded by `residual_std`). Making IBJA
  primary does not require new weekend-handling logic; it makes the *existing,
  already-shipped* fallback the *default* path instead of an occasional one.
- **What's actually lost:** the displayed number becomes, formally, always a
  *calibrated estimate* of Tanishq's retail price (`ibja_calibrated_22k = slope ×
  ibja_per_g + intercept`) rather than a directly observed one. But that is precisely
  what H5 already shows during every outage today, and R²=0.963 over 51 days means
  the estimate tracks Tanishq closely. The honest framing does need to change: the
  product currently opens its README as "the live retail rate" (implying direct
  observation); under Option B the accurate framing is "a live, IBJA-calibrated
  estimate of the retail rate, confirmed directly by Tanishq whenever reachable."
  That is a **product-identity and copy decision**, not just a code flip — flagging
  for owner sign-off, not deciding here.
- **What's kept:** near-zero new code. The calibration, the band display, the
  "Approximate" labeling, the weekend carry-forward — all already built and tested
  for H5. This is a *promotion* of an existing, proven path to default status, plus
  demoting the scrape step from "the source of truth" to "an enrichment call that
  updates the band/confirms the estimate when it succeeds." The scraper, its
  hardening (ADR 016 H1/H2), and the calibration refit loop are all unchanged and
  keep running exactly as they do today — nothing about them is retired.
- **Reversibility:** fully reversible. If Tanishq access improves, flipping the
  priority back is a config/labeling change, not a rebuild — the scrape path was
  never removed.

### Option C — Add alternate retail sources (reduce single-retailer dependency)
**Deferred, not rejected.** A quick reachability check (single unauthenticated GET,
no scraping build, no repeated requests) against three other major Indian jewellery
chains' homepages found no immediate Cloudflare challenge and permissive
`robots.txt`:

| Site | Homepage | robots.txt |
|---|---|---|
| Kalyan Jewellers | HTTP 200 | permits `/`, disallows a couple of campaign paths |
| Joyalukkas | HTTP 200 | permits `/`, disallows account/sign-in paths |
| GRT Jewellers | HTTP 200 | permits `/` (Googlebot-scoped) |
| Malabar Gold & Diamonds | 301 redirect (not followed) | not checked |

**This is a weak, first-order signal only** — a single manual GET from a residential
IP proves nothing about resistance to CF Bot Management under sustained automated
cron traffic, which is exactly the failure mode that eventually caught Tanishq too
(it was not blocked from day one either). Standing up a second retail scraper means:
a new DOM canary, a full ADR-016-style hardening pass, and a fresh ≥30-day overlap
accumulation before any calibration/enrichment role could be trusted for that source
— real effort, uncertain payoff, and exposed to the identical eventual-escalation
risk. **Recommend revisiting only if Option B ships and user feedback specifically
asks for more retail-granularity**, not as a now-decision.

### Option D — Status quo (leave H5 as fallback-only, indefinitely)
**Rejected as a silent default**, though it costs nothing today (it's already the
live behavior). The risk isn't the mechanism — it's that "fallback" quietly becomes
"only working path" without anyone ever deciding that's what the product is now.
If Tanishq access doesn't recover, the product drifts into being an IBJA tracker
that still *markets* itself as a Tanishq tracker, with no copy update and no
explicit decision behind it. This ADR exists so that doesn't happen by default —
whatever the owner decides, it should be a decision, not a drift.

## Consequences

**If Option B is accepted:**
- Positive: removes the dependency on an adversarial, escalating block as the
  critical path for the product's core number. Reuses ~90% of already-shipped,
  already-tested code (H5, calibration, banner states). Fully reversible.
- Positive: the T9/T9_ESCALATE staleness guards were re-wired in this same change to
  key off the IBJA business-day gap rather than Tanishq's scrape freshness — Tanishq
  being unreachable no longer false-alarms (it's the expected steady state), while a
  genuine IBJA outage still escalates.
- Negative/accepted: update cadence drops from up to 8×/day to ~2×/day on Tanishq-dark
  days; the displayed number is formally always an estimate, never a raw observation,
  until Tanishq confirms it. Requires an honest copy/README pass (README's opening
  line, in-app banner logic reversal, "Approximate" semantics) — scoped as follow-up
  work, not included here.
- Negative/accepted: does not reduce single-retail-market dependency (IBJA is a
  national benchmark, not a second retailer) — Option C remains open if that turns
  out to matter.

**If Option A, C, or D is chosen instead:** no code change follows from this ADR;
revisit triggers should be set explicitly (e.g., "reassess after N more days of
sustained Tanishq block" for D, or "reassess after Option B ships" for C).

## Alternatives considered
See Options A–D above — this ADR's decision section *is* the alternatives analysis,
per the scope of this pass (decision doc only, no implementation).
