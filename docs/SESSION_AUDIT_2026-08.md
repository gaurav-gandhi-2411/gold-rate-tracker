# Session audit — 2026-09-03, extended through 2026-09-11

Production audit covering: a live staleness incident, full open-PR triage, the
dead-man's-switch deploy procedure, a sixth (and seventh, and eighth) instance
of the "emits a plausible value instead of failing" defect class, the Tanishq
single-point-of-failure, weekly monitoring of the IBJA-calibrated band's
coverage, and a repeatable sweep for the defect class going forward. Every
number below is sourced to a command, a commit, or a data file — see each
section.

**Consolidated 2026-09-11** after several further continuation sessions took
this from 13 catalogued instances to 19, closed the dead-man's-switch
deployment gap, found and fixed a live production incident this audit's own
prior work caused (§8 instances #17/#18), and built three new mechanical
checks (the bot-pr-sync allowlist guard, a boundary-leak false-positive fix,
and a CI-health monitor) — all pressure-tested with real, deliberately
constructed violations before being trusted, per this doc's own §8 closing
lesson. §9–§11 (added this pass) record what was checked and came back
clean, what the audit got wrong about itself and corrected, and what
generalizes beyond this repo.

**Extended again, same day**, to #20: testing what "required" actually means
under this repo's branch protection (rather than reading the docs and
inferring) confirmed ADR 028's own already-accepted residual risk —
`enforce_admins: false` lets a repo admin merge with required checks
permanently unreported, not just failing — has now actually occurred
twice, including the PR that caused the 7-hour production incident #18
documents. Not a new gap; new evidence that an old, named risk materialized.
See §8 instance #20 and §7's Accepted risk table.

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

## 7. What remains open (updated 2026-09-11, production audit)

Superseded from the original 2026-09-03 version: the dead-man's switch is
now deployed and verified live (§9); the "stuck self-hosted job suppresses
the next cron tick" hypothesis was superseded by AD1's more specific
finding (hour-of-day contention, §10 item 3); the runner-silence alert is
now built (the Tanishq-silence channel, Q4/#1384-#1386) and confirmed to
have fired at least once (production audit AC5a); `check-price.yml`'s
"Run inference" `continue-on-error: true` step remains unchased — carried
forward below, still nobody's picked it up.

**Accepted risk** (a deliberate, reasoned decision to not close a gap) is
listed separately from **unfinished work** (something that should still
get done) — conflating the two hides which items need a decision-maker
versus an engineer.

### Accepted risk

| Item | Decision | Where it's written down |
|---|---|---|
| Tanishq requests-path scraping (0% success, full history) | Not pursuing a fix — near-zero odds against Cloudflare's bot challenge, and any fix would just be a different headless-browser fingerprint fight | ADR 025 covers the broader IBJA-primary architecture decision this follows from, but **does not specifically address the self-hosted-runner-dies scenario** — see the gap below, not fully closed |
| Self-hosted Tanishq runner as a single point of failure, IBJA-only as the accepted steady state if it permanently dies | Formally ratified (AI4, 2026-09-11) — **closed**, no longer a gap | `docs/adr/029-ibja-only-on-runner-death.md`, superseding the affected premise of ADR 025 (which predates the self-hosted-runner split and didn't anticipate this specific failure mode). Cites the Tanishq-silence channel (PR #1384/#1386, WARN 48h/ESCALATE 72h, 9h-runner-health corroboration) and measures the current scrape path directly: 94/94 recorded scrapes (100%) via `fetch_method: playwright`, zero via `requests`, from `data/tanishq_scrape_outcomes.jsonl` |
| Direction-signal model collapse (McNemar gate) | Structurally unresolvable at current data volume — ~934 more folds (~18 years) needed to move the gate. Documented, unchanged, nothing ships | Established fact #4 (original session); reconfirmed unchanged this continuation |
| `check_pr_boundary_leak.py`'s `scratch/`-prefix exclusion (AG2b, §8 instance context) | A genuinely dangerous PR deliberately or carelessly named with a `scratch/` prefix would slip past the closed-PR comparison. Accepted as narrower and more acceptable than the alternatives (excluding all closed-unmerged, or all branch-deleted, PRs) | This doc, §8 instance table and §10; the script's own docstring |
| `enforce_admins: false` on `master`'s branch protection (§8 instance #20) | **Already decided, in ADR 028 (2026-08-06)** — not a fresh gap. ADR 028 named the exact consequence ("any future admin-level merge... bypasses required checks entirely") and verified one half of it (a **failing** check still blocks, PR #676). What's new (AI1, this round): the other half — a check that never reports at all — has now concretely occurred twice, once with a 7-hour production cost, a fact ADR 028's own revisit-trigger criteria (a second contributor joining, or regular concurrent file-overlapping PRs) never anticipated and doesn't currently cover. **Recommend GG revisit ADR 028** in light of this new evidence — not because the original decision was wrong for what was known in August, but because the cost side of the trade-off is no longer hypothetical | ADR 028 itself; §8 instance #20; #1539/#1569's recorded state (this doc's §8 table and Provenance) |

| Item | Owner | Notes |
|---|---|---|
| `#1303` (yfinance bump) | GG | Touches `ml/macro.py`, pipeline-adjacent — still held for review, not merged, unchanged since 2026-09-03 |
| `#1482` (lxml bump) | GG | Same category, opened since; not yet reviewed |
| `check-price.yml`'s "Run inference" step has `continue-on-error: true` | Next session | Flagged by `scripts/audit_silent_fallbacks.py` on 2026-09-03, still not chased down three sessions later — verify what gets committed if inference fails outright |
| **A PR's own required checks silently never starting** (found 2026-09-11, AH2; mechanism confirmed AI1, §8 instance #20) | Next session / GG | Distinct from §8 instance #19 (which watches master's state, not a PR's). Measured against the last 30 days' non-bot PRs (n=87): 2 clear, substantive instances (#1539, #1569 — both merged anyway, with **zero check-runs ever recorded** against their head SHA) plus 2 ambiguous very-short-lived scratch PRs. **2.3% of substantive PRs, recurring, not a one-off** — #1566 (the PR that first surfaced this) makes a third. `ci-health.yml` (#1569) does **not** cover this — it watches master's already-triggered check state, not whether a given PR's checks started at all. A fix would need to enumerate open PRs and flag any whose required checks haven't started within a reasonable window of the last push — not built this session, scope estimate not yet done. **AI1 found the other half**: this symptom was only ever able to reach production because `enforce_admins: false` (accepted risk, ADR 028) let both #1539 and #1569 merge anyway, with checks never having started at all — see §8 #20 and the Accepted risk row above |
| The two 7-day measurement windows (§ AH4 in the production-audit continuation) | This session, in progress | 06:07 UTC slot re-firing + delay distribution vs. the 06:00 cohort (started 2026-09-11T02:45:48Z); WARN page rate vs. PR #1403's projection now that the Worker is verified running 10h/16h (started 2026-09-11T08:39:10Z). Both close 2026-09-18 — not reported on until then |

## 8. The defect-class catalogue (Y3, audit 2026-09-05; extended to #20,
production audit 2026-09-10/11)

Twenty instances of one defect class have now been found across this
audit's sessions (2026-08-27 through 2026-09-11, the first 13 by
2026-09-05): **a control emits a plausible-looking result instead of
failing or raising when it cannot actually verify the thing it claims to
report.** Each was tracked at the time under a different session-local
codename (established fact #6, G1d, P6, Q4, R2/R3, U4, V3, X2, Y2, then
AD3/AE1, AE2, AF2, AF1, AG3, AI1 for #14–#20) and the letter/count used to
refer to it drifted between PR bodies — PR #1340 calls the same finding
"(e)" that this doc's §4 called "instance (f)," and PR #1394 cites "nine
known instances" at a point where this doc's own running count said
different. **That drift is itself the reason this section exists**: there
was never one canonical list. The numbering below (#1–#20) is the first
attempt at one and supersedes every ad hoc letter/count used in earlier PR
bodies — those PRs are not being renumbered, this is just where "the current
count" now lives.

| # | Instance | What it substituted | How found | What would have caught it earlier |
|---|---|---|---|---|
| 1 | `ml.inference._try_ibja_calibrated` silently used the Gaussian `residual_std_oos` band when `calibration.json` lacked `residual_abs_quantiles` (#1237) | A band that looked calibrated but measured 45.3% actual coverage against a 68.3% nominal claim | An independent walk-forward coverage audit (n=75) | A standing coverage monitor comparing displayed band to nominal — didn't exist yet; now exists as #6's weekly re-score (§6 above) |
| 2 | `volCtx.regime ?? "normal"` in `app.js` (#1340) | An absent regime field rendered as the specific claim "normal," not "unknown" | Manual read-through (grep + read) | `scripts/audit_silent_fallbacks.py`'s `js-default-near-render` heuristic — built *after* this instance, in direct response (#1344) |
| 3 | `renderDriverContext`'s `usd_inr_30d_pct_change ?? 0` / `gold_usd_30d_pct_change ?? 0` (#1340) | Missing driver data rendered as "nothing moved" (a specific, false claim) | Manual sweep for siblings after #2 was found | Same sweep script as #2 — but #1344's own body documents that its heuristic **misses** this one: the default sits more than 3 lines from its eventual render call, past the heuristic's window |
| 4 | 7d attribution headline's Rs-contribution fields (#1340) | Hardened defensively; no live bug, but no schema contract guaranteeing the invariant it relied on | Same manual sweep as #2/#3 | Same gap as #3 — a schema/type contract, not a runtime check, is the thing that would make this un-need-checking |
| 5 | `renderStaleBanner`'s `ibja_calibrated` branch (#1358) | Renders identically whether Tanishq last confirmed 2 hours or 3 weeks ago — "confirmation has gone silent" is invisible on the page | Reasoning through the consequence of a *documented, deliberate* design choice (T12 cannot fire while the runner is offline — ADR 025) to its blind spot | Nothing automated; found by tracing a known limitation's downstream effect, not by a measurement |
| 6 | `ml.metrics.record_prediction`'s `model_version` default (#1394) | A retired model name (`"lgbm-only"`) asserted in the permanent audit trail (`metrics_history.json`) for a missing field | Continuing the same manual/semi-automated triage that found #2–#4 | `scripts/audit_silent_fallbacks.py`'s `.get()`-default category — unverified whether it was run against `ml/metrics.py` specifically before this fix; flagged, not confirmed either way |
| 7 | bot-pr-sync's allowlist guard (#1376) | Correctly fails loud (`::error::` + exit 1) on an out-of-scope diff, but nothing pages on that failure — the only other monitor watches *open PR age*, and a rejected guard never creates a PR | Reasoning about what "silently blocked with no alert" actually meant — a loud failure with no page, not a silent pass | An audit of "does every failure path notify a human," not just "does every failure path exit non-zero" |
| 8 | The 50.9% cron miss-rate figure (§1 above) | A rate that cannot distinguish "created on time" from "created 2h59m late" — the pre/post-#1222 comparison used two different, incompatible measurement methodologies | This session, reasoning about why the pre/post comparison didn't add up | Pairing every rate/percentage metric with its underlying delay distribution as a standing habit, not just after the fact |
| 9 | PR #1393's threshold change reaching master through #1394's squash-merge (#1401 revert, X2) | The STOP boundary held at "is #1393 merged" (correctly showed OPEN throughout) and failed at "is #1393's commit an ancestor of what I'm about to merge" | Chance, while preparing an unrelated PR — explicitly, "nothing detected it" | `scripts/check_pr_boundary_leak.py` (#1407) — built in direct response; see #12/#13 below for what it still misses |
| 10 | README/docs numbers hand-copied from a live source and left to drift (`lint.yml`'s `docs-freshness` job) | Three numbers (R²=0.96, 97.3% coverage, direction-signal h1/h2 accuracy) sat 21-22 days stale despite live sources existing for all three | A 2026-08-27 audit pass reading README against the actual live data | `scripts/inject_metrics.py` + the `docs-freshness` CI gate — built in direct response; same chicken-and-egg as #2 |
| 11 | The original 5h WARN / 6h catch-up thresholds, and V1's (#1393) attempted fix | Both calibrated against whatever the gap distribution looked like at the moment of calibration — the original against the Aug-27 incident's ~20%-miss-rate broken state, V1 against the (different) state 2-3 days later | RUNBOOK.md's own "Threshold ladder" analysis, tracing the original threshold's provenance after V1 repeated the same mistake shape | Nothing mechanical — this is a design-review question ("what is this number derived FROM") that has to be asked explicitly every time a threshold changes, not something a script flags |
| 12 | `check_pr_boundary_leak.py`'s `check_branch_base` (Y2, this session, verified via scratch PRs #1419–#1422) | Reports clean ancestry for a branch built directly off an unmerged sibling PR's tip — the exact #1393-into-#1394 mechanism — because the divergence point's parent (wherever the sibling branch itself forked from master) is *always* trivially an ancestor of current master. `check_branch_base` returned `[]` on a deliberate, faithful reproduction of its own namesake incident | This session's Y2 deliberate-failure construction: four real scratch PRs, `check_branch_base`/`check_boundary_overlap` run in isolation as well as combined | Nothing in this repo's own review process — only running the exact reproduction against the check itself, which nothing prompted before Y2 explicitly asked for a pressure test |
| 13 | `check_pr_boundary_leak.py`'s `check_boundary_overlap` (Y2, same session, PR #1421) | Reports "OK: clean branch ancestry, no boundary-gated file overlap" for a branch built off an **unlabeled** open PR's tip (#1420, no `boundary-gated` label) — the overlap check only ever queries PRs carrying that label, so an ordinary, unlabeled open PR (the common case — #1393 itself was never proactively labeled anything) leaks with zero detection from either check | Same Y2 construction — PR #1421's combined check exited 0 | A label is a manual, forgettable step; the check's real coverage is "leaks from PRs someone remembered to tag," not "leaks," full stop — worth stating plainly rather than trusting the green result |
| 14 | Calibration-band confidence banner's `{coverage}%` (AE1, production audit 2026-09-10, PR #1546) | `forecast.json`'s `nominal_coverage` — traced through `ml/inference.py` to `ml.calibration.NOMINAL_COVERAGE_PCT`, a hardcoded design target (always `80`) — rendered on the live page as if it were the measured walk-forward accuracy of the band actually shown. `data/calibration_band_coverage.json` (the real measurement, continuously monitored since §6/instance-adjacent work) had **zero readers** in `app.js`/`i18n.js`/`ml/inference.py` — the two numbers were never connected. On 2026-09-06 the real measured coverage was 68.9%, resolvably below 80% (Wilson CI upper bound 78.3%); the banner would still have said "about 80% of the time" | AD3's routine weekly recompute, cross-referenced against what the live banner sentence actually renders — a repo-wide grep confirmed the measured-coverage file had no client-side reader at all | Nothing automated; found by asking "does this on-page number actually come from the file that measures it," the same question that surfaces every instance in this class |
| 15 | `index.html`'s static `firstVisitText`/`footerBody` pre-hydration markup (AE2, production audit 2026-09-10, PR #1552) | The literal string "checked every 3 hours," still present in the raw HTML `<p data-i18n="...">` fallback text, **after** PR #1406 had already fixed the same claim in `i18n.js`'s dynamic version. JS only overwrites this text after `app.js` loads and fetches `data/cadence_metrics.json` — view-source, no-JS clients, crawlers, and the brief pre-hydration flash on every load all still asserted the old claim | AE2's systematic MEASURED/CONSTANT/HYBRID sweep of every quantitative claim in `i18n.js`/`index.html`/`README.md`, tracing each to its ultimate source | Nothing — #1406's own fix and its review both looked only at `i18n.js`; nothing cross-checked the static HTML carrying the identical claim in a different form |
| 16 | `methAccurateP3`'s "gold rises on roughly 70% of trading days" (AE2, production audit 2026-09-10, PR #1552) | A hand-typed, one-time snapshot (ADR 019, 2026-06-02, P(actual up) over that specific 165-fold backtest) presented as an ongoing fact, with **no live field anywhere in the codebase measuring it**: `direction_baseline.json`'s `always_up_accuracy` is a different metric/horizon (next-day/2-day-ahead, not the 5-day window this sentence discusses); `backtest.json`'s own `dir_acc_5d_naive` is a hardcoded `0.5` constant in `ml/backtest.py`, not a measured up-day frequency at all | Same AE2 sweep — checked both plausible live sources before concluding neither matched | Nothing — the number was never wired into `inject_metrics.py`'s marker system, so `docs-freshness` (which only checks marked regions) had no way to see it drift out of date, because it was never "in date" to begin with |
| 17 | `docs-freshness`'s blind spot against the commits that actually change the numbers (found via #1539's own CI, fixed by #1566) | A CI gate ("docs-freshness") that is genuinely, correctly implemented — but the three workflows that write the `data/*.json` files its markers read from all commit with `[skip ci]`, so the gate never runs on the one class of commit that could actually cause drift. README sat 6 days / 3-4 regenerations stale before an unrelated human PR (#1539) happened to trip the check and surface it | Trying to fix the drift (PR #1539) tripped `docs-freshness` failing against **plain master**, unrelated to that PR's own content — the failure led straight back to the mechanism | Nothing — the gate is correctly implemented for the surface it was built to cover (human PRs); that surface simply never included the actual data-changing commits. The textbook shape of rule 85a: a control narrower than its name implies |
| 18 | The written rule "read bot-pr-sync's allowlist before touching a path it guards," enforced only by memory (#1353 2026-09-04, #1539 2026-09-10, fixed by PR #1564) | A rule stated in a commit message and a revert's own writeup after the **first** violation (#1353) — with no mechanical check behind it. Violated again, by the same author, under the same reasoning ("fix docs-freshness by widening a data-commit's `git add` list"), 6 days later (#1539) — this time blocking production data sync for 7+ hours, `forecast.json`'s age reaching 7.76h against the dead-man's-switch's 10h WARN | The **second** violation was found via routine AE4a verification (checking whether a scheduled data commit had landed since a merge) — not by anyone reading the written rule and remembering to check the allowlist first | A rule enforced only by a human remembering it is not enforced; closed by `scripts/check_bot_pr_sync_allowlist.py` (#1564), which parses every bot-pr-sync-calling workflow's `git add` paths against the allowlist's own regex on every PR that could touch one |
| 19 | Nothing watches a required status check's own health on master (found 2026-09-11, fixed by PR #1569) | An unstated assumption that *something* would notice if a required check (`lint`) went red on master itself. It did — for 33m36s, following an unrelated legitimate change (#1541) that broke a test hardcoding an exact value the change was meant to update. Every open PR's own required check inherited the failure for that window | Found by chance, while verifying an unrelated PR's own CI status — not by any monitoring. `ml/notifications.py`'s full T1–T13 alert catalog covers price/data state exclusively; nothing anywhere watches CI state for its own sake | Nothing — closed by `ci-health.yml` (#1569), a small, independent, hourly check reading required-contexts live from branch protection, deliberately *not* embedded in `lint.yml` itself (the same "control can't see its own failure mode" shape as instance #1) |
| 20 | A previously-accepted residual risk (ADR 028, `enforce_admins: false`), confirmed to have actually occurred — twice, with real production cost (found 2026-09-11, AI1) | **Correction to this row's own first draft, recorded here rather than silently edited**: `enforce_admins: false` is not an undocumented gap — ADR 028 (2026-08-06) made it an explicit, deliberate decision, verified at the time with a real test PR (#676, a deliberately-broken `lint`) proving the gate still blocks a **failing** check. ADR 028's own Consequences section already named the residual risk in these words: "`enforce_admins:false` also means any future admin-level merge... bypasses required checks entirely, not just the `strict` re-verification." What AI1 adds is a distinct, untested-at-the-time state: a required check that never reports at all (`status: "pending"`, forever) behaves differently from one that actively fails, and ADR 028's own #676 proof never covered it. #1539 and #1569 (§7's AH2 row) are exactly this state — `commits/{sha}/status` → `"pending"`, `check-runs` → 0, forever, both merged by `gaurav-gandhi-2411` (repo-admin). #1539 is the same PR that caused the 7-hour production incident #18 documents — its checks never ran, so they could not have caught anything regardless of content; separately, even had they run, `check_bot_pr_sync_allowlist.py` (PR #1564) didn't exist for another 13.5 hours and still isn't a required context today | AH2's 30-day sweep (§7) found the *symptom* (4/87 never-triggered PRs); AI1 asked the mechanism question against #1539/#1569's own recorded state and ADR 028's text — reading the ADR that already covers this, rather than treating it as a fresh gap | ADR 028 already named this exact risk in the abstract; nothing converted "we accepted this could happen" into "here is a concrete instance, twice, with a real cost" until this sweep asked for one |

**Grouped by what made each invisible:**

- **Green metrics** (something looked complete/correct while being wrong): #1, #2, #3, #4, #5, #6, #10.
- **A threshold calibrated during degradation** (a number encoded the bad state it was meant to detect): #11. Also, in spirit though not persisted as a numbered instance: the original bot-pr-sync catch-up-rate comment (~68%) measured during a degraded window and stated as if general-purpose — corrected in PR #1540 once a clean-window re-measurement (34%) was available; see §10.
- **A metric or control that structurally could not see the failure mode** (not wrong, just blind to the thing being asked of it): #3/#4's sweep-heuristic window, #8, #12, #13, #17, #19.
- **A boundary enforced at the wrong step** (the check ran, passed, and still let the thing through): #7, #9.
- **A claim rendered as if measured, but sourced from a hardcoded constant** (new category, production audit 2026-09-10): #14, #16. Distinct from "green metric" — there was no metric at all behind the claim, just a design target or a frozen one-time reading standing in for one.
- **Static copy desynced from its own dynamic-path fix** (new category): #15 — the same underlying claim exists in two forms (server-rendered/static and client-rendered/dynamic); fixing one form left the other silently unfixed.
- **A written rule with no mechanical enforcement** (new category): #18 — the audit's own standing corrective ("read the allowlist first") was itself an instance of the class it names: a plausible-looking safeguard (a documented rule) that could not actually catch what it claimed to prevent.
- **A named, accepted risk confirmed to have actually materialized** (new category): #20 — distinct from every category above, which are findings *about* a control's hidden surface. #20 is a case where the surface was **not** hidden: ADR 028 (2026-08-06) named `enforce_admins: false`'s exact consequence in its own Consequences section and even verified the gate still blocks a genuinely *failing* check (PR #676). What ADR 028's own test never covered was a required check that never reports at all — a different state (permanently `"pending"`, not `"failure"`) — and #1539/#1569 are the first confirmed instances of exactly that state occurring for real, one of them with a 7-hour production cost.

Seven of twenty (#12, #13, the sweep-heuristic gap under #3/#4, #17, #18,
#19, #20) are findings about **this audit's own controls or process**, not
about the product — the same shape CLAUDE.md rule 85a names: a control's
own construction can encode the narrower-than-advertised-surface
assumption it exists to catch elsewhere. #9's fix (#1407) is the clearest
early case: built specifically to catch the #1393-into-#1394 mechanism,
verified passing on its own PR and on two live open PRs, and only shown
that session — by deliberately reproducing the exact incident it was
named for — to still miss it whenever the leaking PR lacks a
manually-applied label. #18 (the allowlist rule) is the clearest later
case: a *process* control (a written rule, not code) that reads as a fix
after the first violation and is exactly as blind to a second violation
as any code-based control would be if it only checked the surface it was
first written against. #20 is the odd one out among the seven: not a
hidden surface at all — ADR 028 named the exact risk and tested one half
of it (a failing check still blocks). The gap was in the *test*, not the
*decision*: #676 never covered "never reports," only "reports red," and
that's the half that actually occurred. **A check that has never failed
on a real violation is unproven**, and the majority
of this audit's own-control findings (#8, #12, #13, #18, #19, #20) were
found by asking exactly that question of an existing, trusted control —
or, for #18/#19/#20, of an existing, trusted *process or assumption*.

## 9. Clean results — evidence of function, not absence of testing

A control that has never been deliberately tested against a real or
constructed violation is unproven, whichever way it reads (§8's own
closing point). The following were tested, not just read, and came back
clean — recorded with the same weight as the instances above, because a
reader auditing this system's reliability needs to know what was actually
checked, not just what was found broken.

- **`scripts/check_bot_pr_sync_allowlist.py` (AF1b, PR #1564).** Reconstructed
  both historical incidents (#1353's and #1539's actual diffs) as real
  scratch PRs and confirmed the checker fails on each, naming the exact
  offending path (`README.md`, `docs/*.md`, `docs/adr/*.md`) — then
  confirmed it passes cleanly on a genuinely compliant change. All three
  proof PRs closed and deleted after capturing the evidence.
- **`scripts/check_pr_boundary_leak.py`'s closed-PR comparison, post-fix
  (AG2, PR #1570).** A single real-PR run proved both directions at once:
  a `scratch/`-prefixed closed PR sharing exact content with the PR under
  test was correctly suppressed; a non-`scratch/` closed PR sharing the
  identical content was still correctly flagged. The fix narrows the
  comparison's blind spot without disabling it.
- **`ci-health.yml`'s detection logic, before it ever ran in production
  (AG3, PR #1569).** Its own first draft had a real bug (checking a
  `[skip ci]` tip commit's check-runs, which are empty by construction) —
  caught by dry-running the corrected logic against a known-red historical
  run (`65122f2b`, `lint (failure)`) and a known-green one, both correctly
  classified, before the workflow was ever merged.
- **AE3b's sweep for other silently-dropped schedule slots (production
  audit 2026-09-10).** Checked every scheduled workflow in this repo and
  `gg-portfolio` for the same 0%-fire-rate shape as check-price.yml's
  06:07 UTC slot (§ instance context, AD1). Found **none** — the 06:07
  slot (and `scrape-tanishq-selfhosted.yml`, which shares its cron) is the
  only instance of this specific failure shape across 15 workflows in two
  repos. A negative result with real evidentiary value: it bounds the
  scope of AD1's finding rather than leaving it open-ended.
- **PR #1376's ntfy-on-rejection alert, verified behaviorally during a
  real incident (AF3, production audit 2026-09-10).** Not just read as
  present in the code — confirmed it actually fired, twice, during the
  #1539 incident, via `ntfy.sh`'s own returned acknowledgment JSON
  (distinct message IDs `hvoWWNXNPjEv` and `pVWxvXotkde5`, correct topic —
  the same `secrets.NTFY_TOPIC` every other real alert in this repo uses,
  correct title/priority/content). The control worked exactly as designed;
  the gap (a ~2.5h human response delay) is outside repo-code scope.
- **The dead-man's-switch Worker's deployed-vs-master parity (AF4a, PR
  #1538 + manual redeploy).** Closed a gap open since first deployment:
  the Worker's manual-trigger endpoint now echoes its own live threshold
  constants; confirmed byte-exact against `deadman.mjs`'s actual exported
  values (`10`/`16`/`48`/`72`/`9`) after the real redeploy, not assumed
  from the source diff alone.
- **The calibration-banner fix, both branches, live in a real browser
  (AF4c).** Verified the fresh-data render (true measured percentage,
  not the constant) and separately constructed the staleness-degradation
  case (staled the coverage file past its 14-day contract) — confirmed
  the sentence degrades to omitting the percentage entirely, never
  falling back to the design target. Zero console errors either case.

## 10. Corrections the audit made to itself

An audit that never records being wrong about its own findings is not
reporting its own reliability. Six corrections, in the order found:

1. **`check_branch_base` proven unable to fire (§8, instance #12).**
   Already recorded in the catalogue above — restated here because it's
   the earliest example of this audit checking its own prior work and
   finding it insufficient, not just checking the product.
2. **AA2b's broader file-overlap+time-proximity signal, measured and
   rejected (production audit 2026-09-10).** Considered as a fix for the
   same rebase blind spot #12 exposed; measured against this repo's real
   last-30-days PR history (44 non-bot, non-scratch PRs, 946 pairs):
   16.7% of pairs shared a changed file, and 7.5% of *all* pairs (71/946)
   shared a file *and* were created within 24h of each other purely from
   ordinary sequential work — ~1.6 false flags per real PR. Not
   implemented; the false-positive rate itself was the finding, not a
   failure to find a fix. See `scripts/check_pr_boundary_leak.py`'s own
   docstring for the full numbers.
3. **The minute-of-hour cron hypothesis, refined rather than refuted
   (§1, this doc, vs. AD1, production audit 2026-09-10).** The original
   2026-09-03 session correctly declined to claim the minute-offset
   change (#1222) was the dominant explanation for the observed miss
   rate, calling its own measurement "underpowered to tell." AD1 later
   (2026-09-10) found the actual, more specific mechanism — hour-of-day
   contention concentrated at 06:00 UTC, corroborated across three
   independent workflows in two repos, sustained for 11+ days — and
   fixed it with a phase shift (#1541) explicitly distinguished from
   #1222's minute-only change. The earlier session's caution was
   justified: the real answer needed a different, later measurement to
   find, not a firmer restatement of the original hypothesis.
4. **RUNBOOK.md's 242.5→618.6min "WORSENED AGAIN" figure, retracted
   (PR #1540, production audit 2026-09-10).** A same-day re-measurement,
   controlling explicitly for two known analysis bugs, found pooled
   median 225.3→236.2min, p90 309.6→300.1min — **falling**, which cannot
   coexist with a genuine ~2.5x degradation in the same data. The
   original figure is retracted and kept verbatim in a collapsed
   `<details>` block in RUNBOOK.md (audit-trail convention: name what
   didn't work, don't delete it), not silently overwritten.
5. **This continuation's own "blocking every PR" framing, bounded to its
   actual measurement (AG3, PR #1569).** An earlier report in this same
   continuation described the lint-red incident's impact in terms that
   could read as larger than measured. The precise, evidenced figures:
   33m36s duration (2026-09-11T02:47:45Z → 03:21:21Z), and of the PRs
   with a `lint.yml` run in that exact window, all five were this same
   session's own in-flight work (the fix PR and its three scratch proof
   PRs, plus the eventual fix itself) — zero externally-authored PRs were
   actually caught in the window. The gap is real regardless; this is
   the bounded, sourced version of its cost, not the unbounded one.
6. **§8 instance #20's own first draft, corrected within the same PR that
   introduced it (AI1, this continuation).** Written up initially as an
   undocumented gap ("nothing read `enforce_admins` and asked what happens
   on bypass"). Reading ADR 028 before shipping that framing found it was
   wrong: ADR 028 (2026-08-06) already named this exact risk explicitly in
   its Consequences section and tested one half of it (a failing check
   still blocks, PR #676). The real, narrower finding — that the other
   half (a check that never reports at all) has now concretely occurred,
   twice, with real cost — replaced the original framing before merge,
   not after. Left here as a record that this audit's own drafts get
   checked against existing documentation before being trusted, the same
   standard applied to every other claim in this doc.

**Not corrected — checked and found to still hold, unverifiable as stated.**
This session searched for documented evidence of "nine merge_gate gates
each pressure-tested with a deliberate violation" (as referenced in this
continuation's own task framing) and found no trace of it anywhere in
this repo: no `merge_gate.py` exists here, `.github/workflows/lint.yml`
has 8 jobs (not 9), and only 2 of them (`boundary-leak-check`,
`bot-pr-sync-allowlist-check`) have actually been pressure-tested with a
deliberate, constructed violation as of this writing (§9, above). Stated
here rather than silently included or silently dropped, per this
project's own standing rule against writing an unsourced claim into a
document as if independently confirmed.

## 11. What transfers beyond this repo

The specific instances above are all gold-rate-tracker findings. The
*shape* of each is not repo-specific. Seven categories, each with the
check that finds it — kept to what's actually generalizable, no
repo-specific detail:

1. **Green metrics that don't prove function.** A passing check, a
   non-zero invocation count, and a "success" log line are all consistent
   with a control that never actually verifies the thing it claims to.
   *Check:* construct the specific failure the control claims to catch,
   and confirm it fires — not just that it exists and runs clean.
2. **A threshold or rate calibrated during a degraded/abnormal period.**
   A number derived from data collected while the system was already
   broken silently encodes that broken state as the new normal.
   *Check:* before trusting any threshold, ask what state the system was
   actually in when the number was derived — and re-derive it from a
   clean window before treating it as general-purpose.
3. **A metric or control structurally blind to a specific failure mode.**
   Not wrong — just built to answer a narrower question than its name
   implies, so a real failure of the *unasked* question passes silently.
   *Check:* ask what this control's own failure mode would look like, and
   whether the control itself could ever detect that shape happening.
4. **A boundary/gate enforced at the wrong step in the pipeline.** The
   check runs, passes, and the thing it exists to stop still reaches
   production through a different path than the one being checked.
   *Check:* trace the full path from "change is made" to "change reaches
   production," and confirm the gate sits on every step of that path —
   not just the step someone assumed was sufficient.
5. **A claim rendered as if measured, sourced from a constant.** A
   design target, default, or frozen one-time reading gets displayed with
   the same confidence as a live measurement, and nothing distinguishes
   them to the reader.
   *Check:* for every user-facing number, trace it to its ultimate
   source and classify it as measured or constant. A constant whose
   surrounding copy implies measurement is a defect regardless of whether
   the constant happens to be numerically accurate today.
6. **A written rule with no mechanical enforcement.** A rule that can
   only be followed by a human remembering it will eventually not be
   followed — often by the same person who wrote the rule, under the
   same reasoning that produced the first violation.
   *Check:* if a rule matters enough to write down, it matters enough to
   enforce as a check that runs on the same path as the action it
   constrains — not as a comment or a memory.
7. **A control's own detection surface silently excludes the events it
   exists to watch.** A docs-freshness gate that can't see the commits
   that change the docs; a CI-health check that can't see itself; an
   alert catalog that covers data state but never CI state. The gap isn't
   that the control is wrong — it's that its surface quietly shrank (or
   was never as wide as its name implied) and nothing said so.
   *Check:* name the control's surface explicitly (which events, which
   triggers, which code paths) and ask what a gap at the edge of that
   surface would look like from the outside — usually a silent absence,
   not a loud error, which is exactly why it survives unnoticed.

## Provenance

All PRs referenced: `#1237` (band fallback fails loud), `#1340`
(fix/vol-regime-fails-loud), `#1341` (docs/fix-scraper-architecture-claim),
`#1342` (chore/deadman-deploy-readme), `#1343`
(feat/calibration-band-weekly-rescore), `#1344`
(chore/audit-silent-fallbacks), `#1358` (Tanishq confirmation silence
named), `#1376` (bot-pr-sync allowlist guard pages on rejection), `#1393`/
`#1394`/`#1401` (the threshold-leak incident and its revert), `#1403`
(three priced threshold-ladder alternatives — open, gated), `#1406`
(cadence-claim p90 — open, gated), `#1407` (boundary-leak detection).
Section 8's #12/#13 findings are sourced to four real, deliberately-opened
and closed scratch PRs (#1419–#1422) run against `scripts/
check_pr_boundary_leak.py` as checked out from `origin/master` at commit
`2f92429e5ce77a3b6559e9b43d4fe44b81e5cf22` — both scratch PRs and branches
were closed/deleted immediately after the check was run against them; no
product code changed as a result of that test. Every number in this
document is either a command output captured live during a session or a
field read from a committed `data/*.json` file at the commit that session
made — see each PR's own Testing section for the exact commands run.

**Production-audit continuation (2026-09-10/11), instances #14–#19 and §9–§11:**
`#1546` (calibration-band confidence wired to measured coverage — #14),
`#1552` (index.html static fallback + stale 70% direction aside — #15,
#16), `#1539`/`#1549` (docs-freshness auto-refresh and its production-
breaking revert — #17), `#1564` (mechanical bot-pr-sync allowlist guard —
#18's fix), `#1566` (docs-freshness re-applied via an independent path,
not bot-pr-sync), `#1569` (CI-health monitor — #19's fix), `#1570`
(boundary-leak-check's `scratch/`-prefix exclusion, §8/§10), `#1538`
(dead-man's-switch self-reporting thresholds, closing the deployed-vs-
master gap), `#1540` (retracts the 242.5→618.6min figure, §10 item 4),
`#1541` (cron phase shift off 06:00 UTC contention, §10 item 3), `#1547`
(catch-up cause classification). §9's proof PRs: `#1561`–`#1563` (AF1b,
reconstructing #1539/#1353 and a clean case), `#1571`/`#1572` (AG2,
`scratch/`-suppressed vs. non-`scratch/`-still-caught) — all six closed
and deleted immediately after capturing the logged evidence quoted in
§8/§9, no product code changed as a result. §10 item 5's exact figures
(33m36s, the 5-PR window) are sourced to each commit's own named
check-run conclusion via `gh api repos/.../commits/{sha}/check-runs`, not
the workflow run's overall conclusion. §7's AH2 finding is sourced to a
scripted sweep of the 87 non-bot PRs created in the last 30 days as of
2026-09-11, checking `gh api repos/.../commits/{sha}/check-runs` for each
PR's head SHA.

**Same-day continuation, §8 instance #20 and §7's Accepted-risk row (AI1,
2026-09-11):** `enforce_admins: false` read directly from
`gh api repos/.../branches/master/protection`, not inferred from GitHub's
documentation — cross-referenced against `docs/adr/028-disable-strict-
branch-protection.md`, which documents the decision, dated 2026-08-06.
#1539 and #1569's own recorded state, both queried live:
`gh api repos/.../commits/{head_sha}/check-runs` → 0 for both;
`gh api repos/.../commits/{head_sha}/status` → `"pending"` for both,
never resolved; `merged_by`/`author` on both PRs → `gaurav-gandhi-2411`
(the account holding repo-admin rights) via
`gh api repos/.../pulls/{n} --jq '{merged_by: .merged_by.login, author:
.user.login}'`. PR #1564's merge timestamp
(`2026-09-11T03:26:37Z`) checked directly against #1539's
(`2026-09-10T13:38:30Z`) to confirm the 13.5-hour gap stated in the
table above, rather than trusting recollection of the ordering.
