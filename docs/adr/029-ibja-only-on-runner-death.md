# ADR 029 — IBJA-Only Is the Accepted Steady State If the Self-Hosted Tanishq Runner Dies

**Status:** Accepted — formalizing a decision already reasoned through and implemented across the
production-audit continuation (2026-09-10/11), not new behavior. No code change accompanies this
ADR; it documents and supersedes the affected premise of ADR 025.

**Date:** 2026-09-11

**Deciders:** GG (owner), CC (implementor, this and prior continuation-audit sessions)

**Supersedes (partially):** ADR 025's premise that Tanishq access loss is a *transient* condition
the product degrades through. This ADR supersedes that specific premise for the scenario where the
self-hosted runner itself is permanently gone (as opposed to Tanishq's own site blocking it) — ADR
025's Option B decision (IBJA primary, Tanishq enrichment) and its own Context/Decision/Consequences
stand unedited; only the assumption named below is superseded. ADR 025's own text is not rewritten
(this repo's convention: name what's superseded, don't silently edit history).

---

## Context

ADR 025 (2026-07-18) inverted the source hierarchy — IBJA-calibrated becomes the primary displayed
price, Tanishq becomes opportunistic enrichment — after Tanishq's Cloudflare challenge began
blocking GitHub Actions' own (datacenter) egress IPs. The fix that followed ADR 025, described in
that ADR's Context but implemented afterward, was to run the Tanishq scrape from a **self-hosted
runner on a residential connection in GG's home** (`.github/workflows/scrape-tanishq-selfhosted.yml`,
`runs-on: [self-hosted, tanishq-scraper]`) instead of GitHub-hosted infrastructure — Tanishq's WAF
blocks datacenter IPs but not this one.

**This measurably closed the reachability gap, and closed it completely.** The scraper's own
recorded `fetch_method` field (`data/tanishq_scrape_outcomes.jsonl`, `scraper/scrape.js`'s
`update-and-notify.js` step) shows **94 of 94 recorded scrapes (100%) via `playwright`, zero via
`requests`**, across the full recorded window (2026-08-21 through 2026-09-11) — confirming
production runs **0% plain-HTTP requests-path, 100% via the self-hosted Playwright runner** (verified
live for this ADR via `data/tanishq_scrape_outcomes.jsonl`, not assumed from the workflow's own
architecture description).

That closes ADR 025's original reachability problem, but replaces it with a narrower, different one:
**a single self-hosted runner, on a single home connection with no redundancy, is now the entire
Tanishq-enrichment path.** ADR 025 did not anticipate this specific runner — it predates the
self-hosted-runner split (`.github/workflows/scrape-tanishq-selfhosted.yml` was split out of
`check-price.yml` in the audit-2026-09 continuation, per that file's own header comment) — so ADR
025's Consequences section reasons about Tanishq access loss as a *platform-side* condition
(Cloudflare blocking), never about *this repo's own single point of failure* going offline for
mundane reasons (power, ISP outage, the host machine being off). That gap is what this ADR closes.

### What makes accepting IBJA-only safe, not silent

Accepting "IBJA-only, indefinitely, if the runner dies" as steady state is safe specifically
**because** three independent things are true, verified below rather than assumed:

1. **A dedicated detection channel exists for exactly this scenario**, distinct from every other
   staleness alert in this system. `worker-deadman/src/deadman.mjs` (PR #1384, "Tanishq-silence
   alert channel — runner death is undetectable today," merged 2026-09-04T08:25:40Z; recalibrated
   by PR #1386 the same day) defines:
   - `TANISHQ_WARN_HOURS = 48`
   - `TANISHQ_ESCALATE_HOURS = 72`
   - `RUNNER_CONFIRMED_OFFLINE_HOURS = 9` — a corroborating signal: once the price data has already
     crossed the 48h WARN mark, if the runner's own health record
     (`data/tanishq_selfhosted_health.json`, written every run by
     `scrape-tanishq-selfhosted.yml`'s "Commit Tanishq reading and record job health" step) is
     *also* stale past this 9h mark, the alert **escalates immediately** rather than waiting the
     full 72h — `deadman.mjs` lines 185–191: `if (ageHours >= TANISHQ_WARN_HOURS && !healthFresh)
     return { level: "escalate", ..., reason: "corroborated: ... nothing has run, not just failed"
     }`. Two independently-stale signals (price data AND the runner's own heartbeat) distinguish
     "the runner is dead" from "Tanishq itself is just being slow," and only the former skips the
     wait to ESCALATE.
2. **This channel is distinct from `ml/notifications.py`'s T9/T9_ESCALATE**, which gates on IBJA's
   own business-day staleness (2/4 business days) — a genuinely different failure mode (IBJA itself
   going dark) that this ADR does not touch. Both exist and neither substitutes for the other.
3. **The channel has been verified to have fired** — recorded in this audit's own prior-round
   findings (`docs/SESSION_AUDIT_2026-08.md` §7, tagged AC5a in that document's own numbering) as
   confirmed to have fired at least once. **Sourced honestly**: this ADR cites that prior
   verification rather than re-deriving it fresh — it was not independently re-confirmed with a new
   artifact in this pass. If a future reader needs the underlying evidence re-verified, that is the
   pointer to start from.

## Decision

**Accept IBJA-only (no Tanishq enrichment) as the steady-state product if the self-hosted runner
permanently dies — no fallback build-out, no second scraper, no alternate self-hosted host.**

This is not a new mechanism. ADR 025 already built and shipped the entire degraded path: the
IBJA-calibrated estimate, the "Approximate" banner labeling, the confidence-band display. This ADR
does not add code — it accepts, explicitly, that if the one self-hosted runner permanently stops
scraping, the product simply continues to run ADR 025's already-shipped degraded path indefinitely,
rather than treating that as an incident requiring a second scraping host, a cloud-hosted residential
proxy, or any other new infrastructure.

### Why this is the simplest option that satisfies the constraints

- **Building redundancy (a second self-hosted runner, or a paid residential-proxy service) costs
  real money or real personal infrastructure** (a second machine, a second home connection) for a
  benefit — Tanishq enrichment on top of an already-functional IBJA-calibrated estimate — that ADR
  025 already established is not load-bearing for the product's core promise. R²=0.9631 (ADR 025,
  `data/calibration.json`) means the estimate tracks Tanishq closely even with zero enrichment.
- **The failure is detectable, not silent** — the entire reason this acceptance is safe rather than
  a drift (echoing ADR 025's own Option D rejection: "the risk isn't the mechanism — it's that
  'fallback' quietly becomes 'only working path' without anyone ever deciding that's what the
  product is now") is the Tanishq-silence channel above. This ADR is that explicit decision ADR 025
  asked for, scoped to the runner-death scenario specifically.
- **Reversible**: if the runner comes back online (or a new one is provisioned), Tanishq enrichment
  resumes automatically — nothing about the IBJA-primary path needs to be un-done, per ADR 025's own
  "fully reversible" framing, which this ADR does not change.

## Alternatives considered

**A second self-hosted runner as a hot standby.** Rejected: doubles the personal-infrastructure
burden (a second machine/connection in GG's home, or a second trusted human elsewhere) for
redundancy against a scenario the detection channel already makes non-silent. Revisit only if the
runner's actual failure rate (not measured in this ADR) turns out to be high enough that IBJA-only
periods become frequent rather than rare.

**A cloud-hosted residential-proxy service as the scrape path.** Rejected on the same grounds ADR
025's Option A rejected proxy rotation for the primary scrape itself: real monthly cost, breaks the
₹0/month constraint, and is still exposed to the same escalating-block risk ADR 016/025 already
documented Tanishq's WAF trending toward.

**Formal SLA / defined outage budget for the runner.** Considered and rejected as premature: this
is a solo hobby-infrastructure host (GG's home connection), not a service with an availability
contract. The Tanishq-silence channel's 48h/72h thresholds already function as the practical
equivalent — WARN gives a human 48 hours to notice and act (restart the host, check the connection)
before ESCALATE assumes the worst.

## Consequences

**Positive:**
- Zero new infrastructure, zero new cost, zero new code — this ADR ratifies an already-built,
  already-tested detection-and-degradation path rather than commissioning a new one.
- Closes ADR 025's own stated gap (Option D's "should be a decision, not a drift") for the specific
  scenario ADR 025 didn't anticipate: this repo's own single point of failure, not Tanishq's.

**Negative / honest limits — residual risk accepted:**
- During any runner-death window, Tanishq enrichment is genuinely absent — the displayed price is
  IBJA-calibrated only, with all of ADR 025's already-accepted limits (≈2×/day cadence, no
  intraday movement, formally always an estimate). This ADR does not change that cost; it accepts
  it applies for however long the runner stays offline, not just during Tanishq-side outages.
- **This ADR's own "verified to have fired" claim is sourced to a prior round of this same audit
  (§7, AC5a), not independently re-verified here** — stated plainly rather than presented as freshly
  confirmed, per this project's own standing rule against writing an unsourced claim into a document
  as if independently confirmed.
- The self-hosted runner remains a genuine single point of failure by design (this ADR accepts that,
  it does not resolve it) — a home-connection outage, hardware failure, or the host machine being
  powered off all produce the identical "runner dead" state this ADR's acceptance covers.

## Revisit trigger

Reconsider (build redundancy, or reassess the detection thresholds) if either becomes true:

- The runner's actual observed downtime, measured from `data/tanishq_selfhosted_health.json`'s own
  `consecutive_job_failures`/`last_job_outcome` history, shows IBJA-only periods becoming frequent
  (not just theoretically possible) rather than rare.
- The Tanishq-silence channel is found, on a future direct re-verification, to not actually have
  fired when it should have — this ADR's safety claim rests on that channel working, and the
  citation above is to a prior pass, not a fresh proof.

No calendar-based revisit — usage-pattern- and evidence-triggered, matching ADR 028's own
convention.
