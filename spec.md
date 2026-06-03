# Spec — Batch Φ13: PR Preview Deploy — Feasibility Diagnosis FIRST, then Decision

**Date:** 2026-06-03
**Author:** External consultant (via GG) → Orchestrator (CC)
**Status:** Draft for orchestrator execution
**Type:** DIAGNOSIS-FIRST. Part 1 is read-only research + a recommendation. Do NOT build any deploy
infrastructure until GG + consultant pick an option from Part 1's findings (norm #1).

---

## Motivation

Two frontend fixes (WI-5 info-panel, Φ9B-3 two-click nav) shipped on code-review confidence and
could only be verified AFTER merge on a real iOS device — the "merge then discover" gap. A PR
preview deploy would turn that into "see it live before merge" for the whole UI layer. This is the
structural fix for that class.

BUT: the repo is plain GitHub Pages (production deploys from master only — confirmed in the Φ-era
cleanup). GitHub Pages does NOT natively provide per-PR preview URLs. So whether a clean preview is
even achievable on this stack — within the project's standing Rs.0 / no-new-external-dependency
discipline (the same constraint that rejected DagsHub, W&B, residential proxies) — is an OPEN
QUESTION. Resolve it before building anything.

All norms apply — flag-and-stop (#1), all-CI-green (#2), Rs.0/no-vendor-lock discipline
(CURRENT_STATE), append-only PROGRESS (#10).

---

## PART 1 — Feasibility diagnosis (READ-ONLY, no infra built)

Research and report, with concrete tradeoffs, the genuinely-available options for previewing a PR's
built PWA on a REAL DEVICE (the iOS-render requirement — a downloadable zip does NOT satisfy this,
since the motivating bugs were live-device-only). For each option report: what it delivers, setup
complexity, whether it gives a real phone-openable URL, cost, new dependencies, and how it interacts
with the existing production Pages deploy.

Options to evaluate (at minimum):
1. **GitHub Actions artifact** — build site, upload as artifact. Likely gives only a downloadable
   zip, NOT a live URL → probably fails the iOS-device requirement. Report whether any GH-native
   mechanism gives a live URL from an artifact.
2. **GitHub Pages preview path / second environment** — deploy PR builds to a `/preview/pr-N/`
   subpath or a separate Pages environment. Report whether this is achievable WITHOUT colliding with
   the production master deploy, and how cleanup of stale previews would work.
3. **Third-party free-tier (Netlify / Cloudflare Pages) PR previews** — wired to the repo, auto
   preview URL per PR. Report: does this violate the Rs.0/no-new-external-dependency discipline?
   (It adds an account + a third-party deploy dependency — flag this against the project's standing
   constraints; it is the consultant/GG's call whether the tradeoff is acceptable, NOT yours to
   assume.)
4. **Any other genuinely-available mechanism** CC finds.

**Honest recommendation required:** end Part 1 with CC's recommendation — including the option of
"NONE worth it: the manual post-deploy device-check discipline already in use is the right call at
this project's scale, and a preview deploy is over-engineering." That is a legitimate and possibly
correct conclusion. Do NOT default to building something just because the batch exists.

**STOP after Part 1.** Report findings + recommendation. GG + consultant decide. No infra is built
until an option is chosen.

---

## PART 2 — Implementation (ONLY if an option is chosen after Part 1)

Deferred. Scope written after the Part 1 decision. If the chosen option is "none / keep manual
checks," Part 2 does not happen and this batch closes as a documented decision (an ADR-style note
in PROGRESS: "evaluated PR preview deploy, chose manual device-checks because X").

---

## Acceptance gates

- Part 1 is READ-ONLY: no workflow files changed, no deploy infra built, no third-party account
  created (norm #1).
- The Rs.0 / no-new-external-dependency discipline is explicitly weighed for any third-party option
  — flagged, not silently accepted.
- The "none / not worth it" outcome is presented as a first-class legitimate recommendation.
- Findings + recommendation reported for GG + consultant decision BEFORE any Part 2 build.
- If a decision is reached, PROGRESS Decision Log records it either way (build X, or chose-not-to
  because Y) — norm #10.
