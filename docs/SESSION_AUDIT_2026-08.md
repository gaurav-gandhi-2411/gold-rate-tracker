# Session audit — 2026-09-03

Production audit covering: a live staleness incident, full open-PR triage, the
dead-man's-switch deploy procedure, a sixth (and seventh, and eighth) instance
of the "emits a plausible value instead of failing" defect class, the Tanishq
single-point-of-failure, weekly monitoring of the IBJA-calibrated band's
coverage, and a repeatable sweep for the defect class going forward. Every
number below is sourced to a command, a commit, or a data file — see each
section.

## 1. Staleness incident — 2026-09-03

**What was verified, not assumed.** At the start of this session
`data/forecast.json`'s `predicted_at` was `2026-09-03T04:10:31Z`, ~4.15h old
at the moment of detection (08:25 UTC) — **not yet past the 5h WARN
threshold** the (undeployed) dead-man's switch would use. So this incident
never became visible staleness on the page. What had already happened: the
3-hourly cron's next expected fire, `06:07 UTC`, never created a workflow run
at all — confirmed via `gh api .../workflows/check-price.yml/runs`, zero
`schedule`-event runs between `04:10:07Z` and the next one this session
manually triggered at `08:25:50Z`. Left alone, `predicted_at` would have
crossed the 5h WARN line around `09:10 UTC`.

**Restored:** manual `gh workflow run check-price.yml --ref master` at
08:25:50Z (run `33733302208`), completed successfully — both the `check` job
and `scrape-tanishq-selfhosted` job green, new `predicted_at` fresh as of
that run.

**Is this the same signature as 2026-08-27 (established fact)?** Partially,
and worse in one respect. 2026-08-27 was one isolated ~12h silent gap with
everything else healthy. This session's finding: since PR #1222 (the
cron-minute change, merged 2026-08-27T15:07:57Z) through this session's start
(2026-09-03T08:26Z), **27 of 53 expected 3-hourly cron slots saw zero
workflow run created — a 50.9% miss rate**, verified by comparing each
expected `HH:07` slot to actual run `createdAt` timestamps in a 3h window.
This is not one incident, it's the chronic steady state since the fix
landed.

**Cron-change verdict: underpowered to tell, and the pre/post methodologies
aren't the same measurement.** The pre-#1222 figure (3.3% miss at `:00` vs
1.6% at `:15`/`:30`) was computed by a different, tighter methodology this
session didn't have access to reproduce exactly. This session's 50.9% figure
uses a coarser "did any run get created in the 3h window" test. The two
numbers are not apples-to-apples, so this session cannot honestly say the
cron-minute change made things worse — only that whatever it fixed (if
anything), a much larger reliability problem remains, and the minute-offset
hypothesis is not the dominant explanation: misses are spread across all
hours of day in the sample, not concentrated near any particular offset now
that there's only one offset (`:07`) to check.

**A plausible, unproven mechanism found this session:** the
`scrape-tanishq-selfhosted` job frequently sits `queued` for hours (self-
hosted runner backlog — see §4) before being auto-cancelled when the *next*
scheduled run starts. `docs/RUNBOOK.md` documents that this design
deliberately avoids a shared concurrency group specifically so a stuck
self-hosted job can't block the next `check` job. But the overall *workflow
run* (both jobs together) stays in a non-`completed` state for that entire
window — and GitHub Actions is known to skip a scheduled trigger while a
prior run of the same workflow is still not completed under some conditions.
If that's what's happening here, the runner backlog (§4) isn't just a
Tanishq-enrichment problem, it may be **actively causing the cron misses**
that produced this incident. Not proven — flagged as the next thing to
verify, not asserted as fact.

**Time to detection.** No automated system detected this incident — the
dead-man's switch is still not deployed (§3), and T9/T9_ESCALATE (IBJA
staleness) never fired because IBJA data was current. Detection was this
session manually diffing expected-vs-actual cron fires: ~2h18m from the
missed `06:07 UTC` slot to detection at `08:25 UTC`. **Automated detections
this incident: 0.** (2026-08-27's own record: also 0 automated detections,
12h to manual discovery.)

## 2. Open-PR triage

5 open PRs at session start, all Dependabot. Merged 4 (`#1153` lxml,
`#979` setuptools, `#977` pyarrow, `#1304` cryptography — all patch/minor,
none touch the price/forecast/scrape pipeline, CI green on lint+pwa-js).
Held `#1303` (yfinance `>=1.5.2`→`>=1.7.0`) for owner review: `yfinance` is
imported directly in `ml/macro.py`, which feeds `driver_context` in every
forecast — a pipeline dependency, not administrative. Full test suite run
after merging (not just CI): 831 passed, 0 failed, before this session's own
new work started.

## 3. Dead-man's switch — still not deployed

Deploy prerequisites verified complete on master: real KV namespace id (not
a placeholder), WARN=5h/ESCALATE=10h thresholds, heartbeat logic, 34/34 tests
passing (README previously claimed 26 — stale, corrected). The Worker itself
is still not deployed — this session has no Cloudflare credentials.
`worker-deadman/README.md` rewritten: the forced-alert verification step was
previously optional, now split into two mandatory sub-steps (force a real
ESCALATE alert end-to-end; confirm the cron fires unattended within 30 min)
with exact dashboard URLs, since neither step alone proves the other and
this repo has two separate incidents of "the trigger looked fine but didn't
fire" to distrust dashboard-only verification.

No automated liveness check is possible today (no deployed URL committed
anywhere yet). Cheapest addition once deployed: a low-frequency GitHub
Actions step that curls the `.workers.dev` URL and alerts on non-200/
malformed JSON — catches "deployed but now broken," not "GitHub Actions
itself is fully dark" (that's still the daily heartbeat's job, watched by a
human).

## 4. Silent-fallback defect class — sixth, seventh, eighth instances

Established fact #6 named `volCtx.regime ?? "normal"` as instance (f). This
session found two undocumented siblings in the same file and fixed all
three in `fix/vol-regime-fails-loud`:

- **(g) `volCtx.regime ?? "normal"`** — an absent regime field rendered the
  "normal volatility" note. Now falls through to the existing neutral
  `volNoteFallback` copy.
- **(h) `renderDriverContext`'s 30d driver fields** — `ds.usd_inr_30d_pct_change
  ?? 0` / `ds.gold_usd_30d_pct_change ?? 0` fed directly into the
  `driverAllFlat` ("nothing much moved") claim; `w30?.delta_pct_premium ?? 0`
  treated `ml/drivers.py`'s real, reachable `None` (insufficient premium
  data) as "premium flat." New `driverStateUnavailable` copy replaces
  `driverAllFlat` specifically when premium data is the missing piece.
- **(i) the 7d attribution headline's Rs-contribution fields** — hardened
  for consistency, though currently atomic-by-construction with
  `total_move_rs_per_g` in `ml/drivers.py` (no live bug found, just no
  schema contract guaranteeing it stays that way).

Full `??`/`\|\|` sweep of `app.js` restricted to user-visible-text-feeding
expressions (not layout/styling/numeric-formatting, per the audit's own
scope): 19 value-substitution defaults found, 6 were CLAIMs (all fixed
above), 13 judged NEUTRAL with reasoning — see the PR body for the full
table, including the two closest calls (a documented 3-source PI-half-width
cascade, and two legacy-schema-compat field pairs).

`scripts/audit_silent_fallbacks.py` (new, `chore/audit-silent-fallbacks`)
turns this into a repeatable sweep across four categories (`.get()`
defaults, swallowed exceptions, JS text defaults, workflow
`continue-on-error` steps) — a review aid, not a CI gate, false positives
expected. 169 findings on current master. Confirmed it catches (g) via
the `js-default-near-render` heuristic; confirmed, and documented rather
than hid, that the same heuristic **misses** (h) — those defaults sit more
than 3 lines from their eventual render call, past the heuristic's window.
No new (tenth) instance of the defect class surfaced in this run — one
`continue-on-error` step worth a follow-up look (`check-price.yml`'s "Run
inference," §7) flagged but not chased down.

## 5. Single point of failure — Tanishq self-hosted scraping

**Requests-path success rate: 0% over the full recorded history**
(`data/tanishq_scrape_outcomes.jsonl`, n=66, 2026-08-21 to 2026-09-03). This
is dead code, not a fallback tier — `scrape.js`'s `fetchWithRequests` always
hits Tanishq's Cloudflare bot-challenge page (`isCFChallengeHtml`), which is
Cloudflare's whole purpose; there is no realistic engineering fix that
doesn't amount to rebuilding a headless-browser fingerprint, at which point
it isn't a "fast path" anymore. `README.md` and the architecture diagram
previously said "requests-first with Playwright fallback" unqualified —
corrected in `docs/fix-scraper-architecture-claim`, now injecting the
requests-path count live from `data/tanishq_scrape_success_rate.json` so it
can't drift out of sync by hand again.

**T12 (the runner-health alert) cannot fire when the runner is offline —
confirmed, and this is a *deliberate, documented* design choice, not an
accidental instance of the defect class above.** `ml/notifications.py`'s
T12 docstring and `docs/RUNBOOK.md`'s "Graceful degradation" section are
explicit: a runner with zero jobs starting sits `queued` and auto-cancels
after 24h with **no alert**, by design — "an idle self-hosted runner isn't
a system failure, it's just enrichment currently unavailable" (ADR 025).
T12 only fires for a *different* failure shape: the runner picking up jobs
and those jobs genuinely failing (≥3 consecutive). The distinction is real
and was deliberately reasoned through, not overlooked — but the
**consequence is the same shape the defect class names**: a genuine
multi-week runner outage produces zero alerts from any current mechanism,
*and* the dead-man's switch (once deployed) wouldn't catch it either, since
`predicted_at` keeps refreshing fine off the IBJA-calibrated fallback the
whole time Tanishq is dark. A permanently-dead self-hosted runner is
currently undetectable, full stop, by anything in this repo or planned for
it.

**24h/72h user impact if the runner dies:** nothing user-visible changes at
either mark. The site keeps serving IBJA-calibrated estimates exactly as it
already does most of the time (Tanishq is opportunistic enrichment, not on
the primary path — ADR 025). No alert fires at 24h, 72h, or ever, for this
specific cause.

**Recommendation:** formally document IBJA-only as the accepted steady
state (ADR 025 already does this in substance; this session's README fix
makes the *scraper* description match it too) rather than investing in a
requests-path repair (near-zero probability of durable success against
Cloudflare) or a second scrape route (real engineering cost for a tier
that's explicitly non-critical by design). The one genuine gap worth
closing is detection, not scraping: a runner-silence alert (e.g., "no
successful Tanishq reading in N days" checked from the *public* Pages data,
independent of whether the runner or GitHub Actions itself is what's
silent) would close the "permanently dead, zero alerts" gap without
touching the scrape architecture at all. Not built this session — described
per the task's own instruction.

## 6. Band coverage — now monitored, not assumed

`ml.calibration.evaluate_empirical_band_coverage` scores the IBJA-calibrated
tier's actual displayed band (`est_low`/`est_high`) but had zero callers
outside tests — its 70.8/83.1/92.3%-at-n=65 numbers were a one-time reading
frozen in a docstring. `feat/calibration-band-weekly-rescore` adds
`save_calibration_band_coverage()`, wired into `weekly-backtest.yml`
alongside the existing `ml.metrics --resolve` step, persisting Wilson 95% CI
+ n + a `resolvable_at_n` flag to `data/calibration_band_coverage.json`
every week, injected into README via the same `inject_metrics.py`
`unresolved_if=` mechanism `data/coverage_metrics.json` already uses.
Current reading: 72.2% observed, n=72, 95% CI [61.0%, 81.2%] against 80%
nominal — CI still contains nominal, not yet resolvable, consistent with the
old docstring's 83.1%/n=65 reading within Wilson-CI noise.

## 7. What remains open

| Item | Owner | Notes |
|---|---|---|
| Deploy the dead-man's switch | GG (Cloudflare credentials) | Prereqs verified complete; `worker-deadman/README.md` deploy steps rewritten with mandatory live-proof; see `chore/deadman-deploy-readme` |
| Confirm/deny the "stuck self-hosted job suppresses next scheduled cron tick" hypothesis (§1) | Next session | Would explain the 50.9% miss rate mechanistically instead of just measuring it; needs either GitHub support/docs confirmation or a controlled reproduction |
| `#1303` (yfinance bump) | GG | Touches `ml/macro.py`, pipeline-adjacent — held for review, not merged |
| A runner-silence alert independent of scrape architecture (§5) | Next session / GG | Closes the "permanently dead runner, zero alerts, ever" gap; described, not built |
| `check-price.yml`'s "Run inference" step has `continue-on-error: true` | Next session | Flagged by `scripts/audit_silent_fallbacks.py`, not chased down this session — verify what gets committed if inference fails outright |
| Direction-signal model collapse (McNemar gate) | No owner — structurally unresolvable | ~934 more folds (~18 years) needed to move the gate; documented, unchanged, nothing ships (established fact #4) |

## Provenance

All PRs referenced: `#1340` (fix/vol-regime-fails-loud), `#1341`
(docs/fix-scraper-architecture-claim), `#1342` (chore/deadman-deploy-readme),
`#1343` (feat/calibration-band-weekly-rescore), `#1344`
(chore/audit-silent-fallbacks). Every number in this document is either a
command output captured live during this session or a field read from a
committed `data/*.json` file at the commit this session made — see each
PR's own Testing section for the exact commands run.
