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

Twenty instances (twenty-nine as of §8.1, 2026-09-21) of one defect class have now been found across this
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

### 8.1 Instances #21–#29 (continuation, 2026-09-21)

Found by sweeping hand-maintained registries (§12.2) and by running the
"construct the failure" check against controls that had never had it. Each row
states what was *verified* (a command run, its output seen) separately from
what is *inferred*. Baseline: `origin/master` `e2a935fe` unless stated.

| # | Instance | What it substituted | How found | Status |
|---|---|---|---|---|
| 21 | `lint.yml`'s required `pwa-js` job ("gates app.js logic on every push/PR") | 9 of the 10 non-headless `tests/test_*.js` files exercise **inlined copies** of app.js functions (their headers say "must match app.js"), not app.js (the tenth, `test_scrape.js`, tests scraper output and was not classified). The one test that drives the real app.js, `test_stale_banner_headless.js`, was excluded from CI. **Verified:** with `isToday` inverted in app.js (the banner would call Monday's close "today's estimate" on Tuesday), all 115 tests across every `tests/test_*.js` except the headless one (a superset of the CI pwa-js list) passed (exit 0) while the headless test failed 3 checks. Of the 15 inlined functions that map to a named function in app.js/i18n.js, 9 are textually identical and 6 differ (2 inspected: both are i18n drift — app.js now calls `t()`, the copy hard-codes English, so a Hindi regression in those branches is invisible to CI; the other 4 were not inspected) | Reading why the headless test was excluded, then the test headers | **Fixed in draft #1788** (2026-09-21, AL2): the 9 files now load the real `app.js` via `tests/helpers/load_app.js` and the copies are deleted. Mutation proof on the real `app.js` (8 single-token mutations): `isToday` inverted was **115/115 green before, caught by 2 files after**; 6 of 8 caught by the converted suite and the 2 survivors (`computeBandPos90d` tie handling, `computeTrendResidual30d` MAD scale) were real gaps the copies had hidden, now killed by 2 new tests. Of the 6 diverged copies, 5 differed only by i18n (0 logic hunks) and the 6th was a `return null` stub: **no real bug found**; `computeTrendDescription` has no caller (dead code). Also the `pwa-headless` job in draft #1598. Requiring `pwa-headless` is a branch-protection change, GG — recommendation in §12.5 |
| 22 | `_config.yml`'s Pages `exclude:` list vs the files app.js fetches | `data/calibration.json` excluded 2026-07-18 (#212, nothing fetched it), fetched from 2026-08-11 (#786): **404 on every live load, two requests per load, ~41 days.** Verified in headless Chromium against the live origin before and after the fix (after: 0 responses ≥400, 0 console errors). Nothing reported it: render-smoke does not check optional-fetch status codes; the client's Sentry DSN is a placeholder (known open item, `docs/PROGRESS.md` "Sentry DSN placeholder not activated") and `Sentry.init` is additionally guarded by `typeof Sentry !== "undefined"` against an `async` bundle, so it silently does not run on page loads where `app.js` wins the race (see §10 item 8) | AK2 registry sweep, then a live probe | **Fixed** #1773 (`e2a935fe`): dead fetch removed, `tests/test_pages_surface.py` reports `data/calibration.json` against the real incident tree `443885b7`. Sentry DSN needs a credential, GG |
| 23 | `docs-freshness` red on master, unpaged | **12 of 12** scheduled `lint.yml` runs on master, 2026-09-09 → 09-20, failed with `docs-freshness` as the only failing job (each run's jobs inspected). It is not a required context, so nothing blocks and `ci-health.yml` (required contexts only) never looks at it. The fix mechanism — `docs-refresh.yml`'s bot PR #1578 — sat open from 2026-09-11 with **zero checks ever run on it**, native auto-merge (enabled 2026-09-11T10:29:58Z) waiting on `lint`/`pwa-js` that could never start. **Root cause (verified; this row's first draft and the continuation brief both had it wrong):** not a `GITHUB_TOKEN`-created PR — the workflow already used `CI_MERGE_PAT` — but `git commit -m "... [skip ci]"` at `docs-refresh.yml:87`: GitHub suppresses `pull_request` runs when the head commit message carries the marker. Proved with a scratch PR (#1786, same identity): marker-carrying head → **0 runs in 6+ min**; a next commit without it → checks in ~2.5 min; and #1578 itself, given a marker-free merge commit at 05:41:21Z, ran native checks and auto-merged at **05:45:43Z**. The 8 bot-pr-sync callers also commit with the marker but dispatch `lint.yml` explicitly, so only docs-refresh was affected. The README's Lint badge read failing (*inferred* from run conclusions; the rendered badge was not fetched) | Reading why a test-only PR (#1772) had a red check, then running `inject_metrics.py --check` on master; root cause found in AL1 | **#1578 merged 2026-09-21; master's `lint.yml` dispatch run on `448e8745` passed all jobs including `docs-freshness`** (the scheduled 06:00 run is not yet observed). **Structural fix in draft #1787** (drop the marker; `tests/test_pr_workflows_get_checks.py` discovers every PR-opening workflow and requires no marker or an explicit dispatch — reports `docs-refresh.yml` on unfixed master, nothing on the fix). Until #1787 merges the next docs-refresh cycle re-creates the stuck shape |
| 24 | `worker-deadman`'s PR-trigger-health channel (#1591) is structurally blind to fast merges | Pages only if a PR's head commit is ≥30 min old with zero required check-runs *at a 30-min tick*. **Verified** by running `classifyPrTriggerHealth` from the repo against #1539's and #1569's recorded state (head-commit time, merge time, 0 check-runs, commit status `pending`): #1569 was open 116 min → pages at 09:30Z, 57 min before it merged; **#1539 — the incident the module's own header cites as motivation — was open 5.2 min → zero ticks fall inside its lifetime, never pages.** A PR merged <30 min after its last push can never page; one is guaranteed to only if open ≥60 min. Assumes the cron fires on :00/:30 | AK3: constructing the detection case from the real recorded state | **Fix built (GG approved the scan), not deployed**: draft #1789 pages when a PR merged 10–180 min ago has no `lint`/`pwa-js` check-run on its head SHA, once per PR. Sized from data: over the 400 most recent merged PRs exactly 2 flag (#1539, #1569) and 0 of 355 bot PRs (false-positive rate 0/400). Proven with the real SHAs replayed through the Worker's :00/:30 ticks (#1539 pages at 14:00Z, 21.5 min after merge; #1569 at 11:00Z) and through `runCheck` against a mocked GitHub API — master's unmodified `runCheck` sends nothing for the same world. Deploy is GG's (`wrangler deploy`, no new secret) |
| 25 | `scripts/check_test_registry_complete.py` (#1596) counts a filename inside a YAML comment as "referenced" | It searched raw workflow text, so a test named only in `# TODO: add tests/x.js` passed. **Constructed and reproduced** (orphan reported `[]`), then fixed | Applying the "construct the violation" check to the check merged 2026-09-11 | **Fixed in draft #1598** with three regression tests |
| 26 | `service-worker.js`'s "network-first, fall back to cache when offline" data handling | **The fallback has never worked.** `loadJSON()` requests `data/x.json?t=<Date.now()>` (since the initial ML commit, 2026-05-09) and the worker cached under the full request URL, so the offline lookup used a different `?t=` than any stored entry and could not match. **Verified in headless Chromium against the live origin (and a local tree): with the real worker active, offline, all 7 data requests failed (`net::ERR_FAILED`) — including the 3 files the worker lists — and after 2 online loads the cache held 2 entries per file, keyed with `?t=`** (unbounded per-load growth until the next VERSION bump; only 2 loads were observed, so the growth rate beyond that is extrapolated). Nothing tested it | Testing an *inference* from the registry sweep (row 5, §12.2: "the three unlisted files lack an offline fallback") — the test showed the fallback was dead for all of them | **Fix prepared, not merged**: draft #1780 (stacked on #1598) — query-free cache key, `res.ok` guard, `/data/*.json` rule, and a real-worker test that fails 3 checks on unfixed master and passes on the fix. Changes what an offline user sees, GG. No device verification yet |
| 27 | The Worker's open-PR trigger-health channel excluded every `bot/`-prefixed PR (`fetchOpenPrTriggerHealth`) | On the stated assumption that bot PRs "auto-merge via bot-pr-sync's own polling within minutes". `bot/docs-refresh` does not use bot-pr-sync (native auto-merge) and was **the one PR stuck open with zero check-runs for 10 days (#1578) — skipped by construction**. The exclusion was also pinned by an existing test (`runCheck: ... excludes bot/ and scratch/ prefixed branches`). Measured over 100 merged bot PRs: head commit → first `lint`/`pwa-js` check-run start median 0.17 min, **max 0.35 min**; → merged max 4.5 min, so an open bot PR without a check-run for the existing 30-min threshold is a real anomaly (0 false pages in the sample). *Correction to my own working note:* I first said no test covered this fetch layer; that came from a grep I had truncated — it is tested, and the test encoded the wrong assumption | Reading why the Worker never paged about #1578 during AL1 | **Prepared, needs GG's OK**: separable second commit in draft #1789 (drop it to decline); rewrites the pinned test |
| 28 | ADR 028's `enforce_admins: false` — how often it is actually used, and the comfort that "a failing check is still blocked" | **3 of the 400 most recent merged PRs merged without `lint` and `pwa-js` recorded SUCCESS before the merge: #1539 and #1569 (never ran) and #1541 (`lint` FAILED on its head SHA at 13:55Z; merged 13 h later).** #1541 is a *third* bypass and the first over a **failing** check — it is what put master's `lint` red for 33m36s (§8 #19). The other 397 (incl. all 355 bot PRs; 0 needed the forwarded-status fallback) had both contexts green first. All 400 merges were by the one admin identity. No `--admin` anywhere in workflows/actions/scripts; 0 non-PR commits on master's first-parent history (last 400); the only `git push`es are to `bot/` branches. So: **nothing automated relies on bypass** (*verified* by the measurement; branch protection was not toggled, so how `enforce_admins: true` would behave is *documented GitHub behaviour, not tested here*) | AL3b: measuring real merges instead of reasoning about them | **GG's decision**: enabling it would have blocked exactly those 3 (0.75% of merges) — the three that caused audit incidents #18/#19/#20 — and nothing else. Cost: GG loses the merge-anyway hatch (Actions outage, emergency revert while CI is red) and would toggle the setting for those cases. Recommendation and Settings path in §12.5 |
| 29 | `ml/requirements-inference.lock` vs the floors in `ml/requirements.txt` | Production (`check-price`, `weekly-backtest`, `eval-direction`) installs the **lock**, which is generated by a manual `uv pip compile` (RUNBOOK: "regenerate whenever `requirements.txt` changes" — a written rule with no enforcement, same class as #18). Last regenerated 2026-08-11; floors were raised since, so **production ran below 5 of its 17 declared floors**: `cryptography 50.0.0` (<50.0.1) and `setuptools 83.0.0` (<84.0.0) — both labelled CVE minimum pins — plus `lxml 6.1.1`, `pandas 3.0.3`, `yfinance 1.4.1`. CI's `pip install -r ml/requirements.txt` resolves the newest releases, so nothing red showed it, and the dependency-audit job (`continue-on-error`) audits the lock and reports `accelerate 1.13.0 PYSEC-2026-3804` while staying green. **It also means the four dependabot `ml/` floor bumps (#1659, #1660, #1482, #1303) cannot change production.** *Not claimed:* that the below-floor versions are exploitable — pip-audit on the lock flags only `accelerate` | AL4: asking what production actually installs before measuring the dependabot PRs | **Fix prepared**: draft #1792 regenerates the lock (5 pins: cryptography 50.0.1, lxml 6.1.3, pandas 3.0.6, setuptools 84.0.0, yfinance 1.7.0) and adds `tests/test_lock_satisfies_floors.py`, which names exactly those 5 on unfixed master. Regression evidence in §12.5; **Chronos not measured** |

Grouped by what made each invisible: #21, #24, #25, #27 are controls whose surface
was narrower than their name (§11 category 3/7); #22 is a hand-maintained
registry drifting against its consumer with no reporting channel; #23 is a
control that is correctly implemented, red, and read by nobody because it
gates nothing (§11 category 7) — and, once diagnosed, a workflow that
disabled its own checks with a marker in its commit message; #26 is a
documented fallback that no test ever exercised, so it could be dead since its
introduction (§11 category 1); #28 is an accepted risk measured for the first
time; #29 is a written rule with no mechanical enforcement (§11 category 6).

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

- **Hand-maintained registries checked against reality and found in sync
  (AK2, 2026-09-21; full table §12.2).** Executed, not just read:
  `service-worker.js` `SHELL_FILES` → all 12 entries exist on disk (its install
  step swallows misses with `.catch(() => null)`, so a missing file would have
  been silent); `i18n.js` en/hi → 257/257 keys, no key missing either way, no
  function-vs-string type mismatch; `dependabot.yml` → all 3 ecosystems' directories
  match a manifest on disk; `scripts/check_bot_pr_sync_allowlist.py` re-run on
  master → OK across all 8 bot-pr-sync callers; six `norm #N` citations in
  workflows/`app.js`/`service-worker.js` → all six point at the norm they describe.
  Required status contexts have no hand-typed copy anywhere: both
  `check_required_checks_positive.py` and `ci-health.yml` read them live.
- **`post-required-check-status` (lint.yml) does not forward a false pass.**
  *Read, not constructed:* it is gated on `needs.lint.result == 'success' &&
  needs['pwa-js'].result == 'success'`, so it cannot post a green commit status
  under a required context's name when the underlying job failed.

## 10. Corrections the audit made to itself

An audit that never records being wrong about its own findings is not
reporting its own reliability. Nineteen corrections, in the order found:

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

7. **The diagnosis of the red `test_stale_banner_headless.js` ("a stale
   'today' assertion vs. the app's weekday-named copy") was wrong, and had
   spread to three places** — the continuation brief, draft #1598's
   `lint.yml` comment, and `KNOWN_EXCLUSIONS`'s reason text (which also
   pointed at "the finding" in this doc; no such finding existed, so the
   pointer dangled). Actual mechanism: Scenario C pinned IBJA's publish time
   at "2h ago", and the app compares IST day keys, so between 00:00 and
   02:00 IST (18:30–20:30 UTC, ~8% of wall-clock time) that fixture lands on
   the previous IST day and the app *correctly* renders the weekday form.
   Reproduced under a pinned clock: 19:00Z fails with "IBJA's Monday close",
   10:00Z passes. Editing the assertion to expect a weekday, as the brief
   asked, would have failed the other ~22 hours of every day. Fixed in #1772
   (fixture, not app copy); the wrong text is removed in draft #1598.
8. **This session's first Sentry probe overstated a state.** It reported the
   live page had "no Sentry client and no DSN"; a second probe minutes later
   found a client with the placeholder DSN. `Sentry.init` is guarded by
   `typeof Sentry !== "undefined"` and the bundle loads `async`, so whether
   init runs depends on which script wins the race on that page load — the
   first probe caught one outcome, not a fixed state. The conclusion (no
   client-side error reaches a real Sentry project) holds either way; the
   mechanism has two layers, not one.
9. **#1591's PR-trigger-health channel does not cover the incident its own
   header cites.** `pr_trigger_health.mjs` names #1539/#1569 as the shape it
   closes. Run against their real recorded state it pages for #1569 and
   cannot page for #1539 (open 5.2 min against a 30-min threshold) — §8.1 #24.
   Recorded because that header presents the channel as closing the
   #1539/#1569 gap (§8 #20).
10. **A per-slot delay table for AK4 was built, looked authoritative, and was
    discarded before being reported.** It attributed each scheduled run to the
    latest nominal slot at or before its creation time; with 1–5h delays against
    a 3h slot spacing that attribution is ambiguous, and it produced rows such as
    "01:37 slot: 0 of 7 runs" that read as a still-dead slot. The reported
    comparison uses run-creation hour of day, which needs no attribution
    (§12.4; `scripts/measure_schedule_windows.py` states this in its docstring).
11. **The registry sweep's first reading of the service worker was wrong, and
    was corrected by testing it.** Reading the fetch handler, I inferred that the
    3 files in `DATA_FILES` had a working offline fallback and the other 4 did not.
    An offline reload in Chromium showed no data file had one: the `?t=`
    cache-buster made every lookup miss (§8.1 #26). The doc's own row for this
    registry was rewritten before merge; the inference is kept here because it is
    the same shape as the audit's other findings — a plausible reading of code,
    stated with more confidence than a run supported, until a run said otherwise.
12. **The diagnosis of why #1578 never got checks was wrong twice.** The
    continuation brief hypothesised a `GITHUB_TOKEN`-created PR; this doc's own first
    draft of §8.1 #23 said "bot-token pushes do not trigger workflows". Both were
    false: the workflow already used `CI_MERGE_PAT`. The cause is a `[skip ci]` marker in
    the commit message (§8.1 #23), proved with a controlled scratch PR before any fix
    was written.
13. **My fix PR for #12 was defeated by the bug it fixes.** The first commit message on
    draft #1787 spelled the marker out in its subject line ("drop [skip ci] from its
    commit"), so GitHub skipped that PR's checks — the same 0-runs state, reproduced by the
    fix itself. Found by noticing #1787 had "no checks reported" long after the ~2.5-minute
    norm, confirmed on the commit message, fixed by rewording; checks then started within
    ~50 s. All my other open branches' head commits were checked for the same slip (none).
14. **I claimed the Worker's open-PR fetch layer had no tests.** That came from a grep I
    had truncated with `head_limit`. It is tested (`deadman.test.mjs`, via a `githubApiFetch`
    helper), and the test pinned the wrong `bot/` exclusion as intended behaviour
    (§8.1 #27). Corrected in the PR body before it was reviewed.
15. **§8 #20's comfort that "a failing check is still blocked" (ADR 028's test PR #676) does not
    hold for admin merges.** #1541 merged over a *failing* `lint` (§8.1 #28). #676 proved a
    failing check blocks a merge made without the bypass; it never covered an admin using
    it. Recorded here, not silently edited, because #20's own text is still true of what it
    tested.

16. **The dead-man's-switch was accepted as working on evidence of execution, not delivery
    (AP1b, 2026-09-21).** What this audit recorded for the Worker is execution-shaped: §9's
    deployed-vs-master parity (the endpoint echoing its own threshold constants after a redeploy),
    and §3, which made "force a real ESCALATE alert end-to-end" a mandatory README step but records
    no server-side receipt of one. GG relays that "heartbeat KV key written" was also accepted as
    behavioural proof; **that wording is not in this document, so it is recorded here as relayed,
    not verified.** A KV key proves the cron ran; it says nothing about whether ntfy received the
    message. GG's server-side poll of the topic (2026-09-21) found no Worker-originated message, and
    no Worker message has ever reached the phone, so every Worker channel (dead-man, Tanishq-silence,
    heartbeat, PR-health, merged-unchecked) may have paged into nothing since deployment. The old code
    made that undetectable (§12.8 #39). **Status (updated 2026-09-21 evening): CONFIRMED for the post-#1797 period.**
    Every Worker delivery attempt after the deploy failed: 4 of 4 (HTTP 522, 429, 522, 522), 0 delivered
    (§12.9 #46). **For the period before the deploy it remains inferred**: the old code logged nothing, so
    there is no record to read, and GG's server-side poll found no Worker message. Still required: a real
    page seen on the phone AND in a server-side poll (AP1c/AQ1d), which needs a working channel first.

17. **The topic-mismatch hypothesis for "the Worker's pages never arrived" was not the mechanism
    (AQ1, 2026-09-21).** The first diagnosis listed wrong topic, a swallowed non-2xx, or a POST that never
    executes, and the deploy plan leaned on a topic-fingerprint comparison to settle it. GG's fingerprint
    matched (`132fde71`), so the topic was right; the mechanism is that ntfy.sh is unreachable from
    Cloudflare's egress (522 three times, 429 once). Recorded as a refuted hypothesis, not silently dropped.
18. **"The catch-up absorbs platform delay and keeps user-facing cadence flat" was stated as fact and
    never verified end to end (AQ3a, 2026-09-21).** Earlier briefs and `check-price.yml`'s own comments
    carried it. Of the 65 catch-up dispatches since it was introduced (#1374, 2026-09-04): **60 failed, 3
    succeeded, 2 were cancelled**. The 60 failed on a stale checkout (§12.9 #48). Separately it cannot work
    by construction: the run that detects the gap is itself a full data run, so the dispatched copy is
    redundant, and it cannot fire at all when no run exists, which is exactly the digest-lateness case.
    My own first count ("0 successes") was also wrong: it used a timing heuristic where the run API's
    `actor` field is exact.
19. **I shipped a PR that failed mypy because I ran only the ruff hooks locally (#1825, 2026-09-21).**
    CI's pre-commit mypy caught two errors. The local mypy hook cannot run here (numpy stubs under this
    Python), so I skipped it instead of finding another way. Reproduced the exact errors in a throwaway
    venv with the hook's pinned version, fixed, and now run mypy that way before pushing `ml/` changes.

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

## 12. Continuation 2026-09-21: registry sweep, and the two measurement windows

Baseline `origin/master` `e2a935fe` (after #1772 and #1773).

### 12.1 What the brief said vs. what was found

Three premises in the continuation brief did not survive a check, each verified
against the repo or GitHub before acting on it: #1591 was described as a draft
needing deploy (it merged 2026-09-11); the red headless test was described as a
stale assertion (§10 item 7); and "confirm it is collected by CI once #1598 lands"
was false (#1598 as written *excluded* it — it now runs in a `pwa-headless` job in
the refreshed draft). Master itself was 169 commits ahead of the brief's snapshot
(bot data commits only, plus #1772/#1773 from this session).

### 12.2 Hand-maintained registry sweep (AK2)

**Scope actually covered (rule 85b):** hand-typed file lists in workflow YAML;
comment-enumerations of callers/offsets; regex allowlists; service-worker arrays;
Pages exclude list; parallel-language key sets; cross-references by number
(`norm #N`, ADR supersession); test-side copies of app code; doc numbers behind
`inject_metrics` markers; this doc's own counts. **Not covered:** the
`gg-portfolio` repo, `claude-config`, Cloudflare-side deployed config beyond the
Worker-vs-master parity already closed (§9), unmarked hand-typed numbers in
README prose (a candidate class, not swept), and any skill/agent registry
(this repo's `.claude/` holds only `settings.local.json`).

| # | Registry | Drifted? | Evidence | Discovery replaces it? | Status |
|---|---|---|---|---|---|
| 1 | `lint.yml` `pwa-js` test-file list | **Yes** | 2 files / 19 tests never ran for 7–8 days | Yes — completeness check | draft #1598 |
| 2 | `lint.yml` Worker-test `node --test` list | Yes, once | `pr_trigger_health.test.mjs` had to be added by hand; now clean | Same check | covered |
| 3 | bot-pr-sync caller list (comment) | Yes, once | said six, was eight; corrected by AF1 and the comment now says eight, matching the 8 real callers | Yes — the allowlist script discovers callers by grep | closed |
| 4 | `sw-version-guard`'s file regex | **Yes** | 4 files vs `SHELL_FILES` 11; `i18n.js` absent; constructed i18n-only change list: old not armed, new armed. Latent: 0 of 10 `i18n.js` commits changed it alone | Yes — derive from `SHELL_FILES` | draft #1775 |
| 5 | `service-worker.js` `DATA_FILES` (network-first list) | **Yes** | 3 entries vs the 7 data files `app.js` requests on every load (9 URL constants at the time: `calibration.json` since removed, `metrics_history.json` declared but never requested); comment says "All JSON data files". I first *inferred* the other 4 merely lacked an offline fallback the listed 3 had; **testing that showed the fallback was dead for all 7** (§8.1 #26, §10 item 11) | Yes — a `/data/*.json` rule | draft #1780 (offline behaviour change, GG) |
| 6 | `_config.yml` Pages `exclude:` | **Yes** | `data/calibration.json` excluded but fetched: live 404 ×2/load for ~41 days; its comment says "7 data files", 8 are fetched now | Yes — `tests/test_pages_surface.py` derives from `app.js` | fixed #1773 (`_config.yml` itself untouched: deploy config) |
| 7 | `scrape-tanishq-selfhosted.yml` cron comment | **Yes** | "same cadence/offset as check-price.yml"; crons are `7 */3` vs `37 1-22/3` since #1541. Whether the old alignment was load-bearing is not established | n/a (comment) | draft #1775, comment-only |
| 8 | ADR 025 → ADR 029 supersession | **Yes** | ADR 029 states it partially supersedes 025's premise; 025's status carried no pointer | n/a | fixed in this PR |
| 9 | Required status contexts | No | read live by `check_required_checks_positive.py` and `ci-health.yml`; no hardcoded copy exists | already discovery | clean |
| 10 | bot-pr-sync allowlist ↔ callers' `git add` | No | script run on master: OK across 8 callers | already discovery (#1564) | clean |
| 11 | `inject_metrics` marked numbers (README, `DIRECTION_SIGNAL_STATUS.md`) | **Yes** | 12/12 scheduled master runs red on it (§8.1 #23) | already discovery (`--check`) | #1578 merged; root cause (`[skip ci]` in docs-refresh's commit) + fix in draft #1787 |
| 12 | `dependabot.yml` directories | No | 3 ecosystems, each matches a manifest on disk; `ml/requirements-inference.lock` is generated and not dependabot-visible | — | clean |
| 13 | `i18n.js` en/hi keys | No | 257/257 | — | clean |
| 14 | `SHELL_FILES` vs disk | No | 12/12 exist (install swallows misses) | — | clean |
| 15 | Tests' inlined copies of app.js functions | **Yes** | 6 of 15 mapped functions differ (2 inspected: i18n drift) | Yes — test the real app.js | **fixed in draft #1788** (real app.js loaded; 8/8 mutations caught) + headless job in #1598 |
| 16 | `norm #N` citations | No | 6 sites, 6 correct | — | clean |
| 17 | `ml/requirements-inference.lock` ↔ the floors in `ml/requirements.txt` | **Yes** | 5 of 17 floors unmet by what production installs (§8.1 #29) | Yes — `tests/test_lock_satisfies_floors.py` | draft #1792 |

**Drift count (updated after the AL continuation): 11 of 17 registries examined had
drifted at some point** — 2 were already closed before this session (#2, #3), 2 are
fixed and merged (#6, #8), and 7 are in draft PRs awaiting GG (#1, #4, #5, #7, #11, #15,
#17). None is open without a prepared fix. Six were clean (#9, #10, #12, #13, #14, #16).
(The 17th, the lock, was found by AL4 — not by this sweep's original list, which is itself
the point: the sweep enumerated registries by hand.) Not counted: the alert catalog (`T1`–`T13`, `T8_EVENING`/
`T8_MORNING`, `T9_ESCALATE` in `ml/notifications.py`) — no doc claims to enumerate
it, so there is no registry to drift; and the ADR directory, which has no index file
(number 006 never existed in git history).

### 12.3 The PR-trigger-health channel (AK3), partial

GG's manual-trigger response (`?trigger=1`) showed `level: ok, ageHours: 3.98,
tanishqLevel: ok, tanishqAgeHours: 4.92, sent/tanishqSent/heartbeatSent: false` and
a key `prTriggerHealthSent` — so the deployed Worker is a build that includes #1591's
wiring (*verified* from the pasted key). The paste was cut off by PowerShell's default
display of `Content` (RawContentLength 438), so the remaining PR-health fields were
**not seen** and nothing further is claimed about them. The detection case, though,
needs no Worker access: §8.1 #24 runs the repo's own module against #1539's and
#1569's real recorded state. To finish the deployed-side check, GG can run
`(Invoke-WebRequest "https://gold-rate-tracker-deadman.gg5678g.workers.dev/?trigger=1" -UseBasicParsing).Content`
(the full body, not the truncated table).

### 12.4 Measurement windows (AK4) — both closed 2026-09-18

Provenance: `scripts/measure_schedule_windows.py` → `reports/schedule_windows_2026-09-18.json`,
generated from `origin/master` `e2a935fe`. Two independent sources: `data/forecast.json`
history in git (the series the Worker reads), and `gh run list --event schedule`.

**Window 2 — WARN page rate vs #1403's projection** (opened 2026-09-11T08:39:10Z,
7 days). #1403 projected **0** false pages for WARN=10h/ESCALATE=16h (0/36 gaps over
10h in its 7-day sample). Actual, replaying the Worker's */30 ticks against the
published `predicted_at` series: **0 of 336 ticks ≥10h**, 0 ≥16h; age median 2.29h,
p90 4.99h, **max 7.04h** (2.96h headroom to WARN); 37 inter-publication gaps, max
7.43h, 0 ≥10h. Consistent with the projection. **What n=37 can and cannot resolve:**
zero events in 37 gaps bounds the per-gap exceedance rate below ~7.8% (95%, rule of
three) — about 12 pages/month at ~5.3 gaps/day — so this window cannot distinguish
"0 per month" from "a handful per month"; it only shows the ladder was not tripped
by an ordinary week. It also does not model Pages deploy lag (minutes), and **the
Worker's actual push history is not readable from here** — whether any WARN push
arrived in the window is unverified (GG can check ntfy).

**Window 1 — the 06:07 slot and the 06:00 cohort** (opened 2026-09-11T02:45:48Z,
7 days). #1541's pre-registered test: after moving check-price's phase (old 06:07 →
07:37), does the moved slot behave like the other seven, or does the bad window move
with it; and does the recurring 7–8h gap in `run_cadence_log.jsonl` disappear?
Per-slot delays cannot be measured cleanly (§10 item 10), so the comparison is
run-creation hour of day:

| | 7 days before | 7 days after |
|---|---|---|
| check-price schedule runs created (of 56 possible) | 35 | 35 |
| days with a run created 06:00–08:59Z, check-price (moved cron) | **0 of 7** | **6 of 7** |
| days with a run created 06:00–08:59Z, scrape-tanishq-selfhosted (cron unchanged, still has a 06:07 slot) | 0 of 7 | 0 of 7 |
| `run_cadence_log` gaps ≥6.5h | 8 of 38 | 3 of 37 |
| cadence-log gap median / p90 / max (h) | 4.77 / 6.76 / 7.93 | 4.68 / 6.18 / 7.43 |

Reading: the delivery pattern changed for the workflow whose cron moved and did not
change for the one whose cron did not — a difference-in-differences with **n=7 days
and one moved workflow, suggestive, not proof** that delivery depends on cron phase
(the two workflows' pre-window hour histograms are near-identical, as is expected if
so). That supports #1541's hypothesis in the sense of its outcome (a) but does not
establish the *hour-of-day* mechanism it named. It did **not** raise delivery: 35
runs of 56 possible (62.5%) both before and after, so the moved workflow still loses
~37.5% of its slots. Gaps ≥6.5h fell 8 → 3, directionally the right way but **not
statistically resolvable** at n≈37 per side (Fisher two-sided p=0.19). The 06:00
cohort is unchanged and still severe: `lint.yml`'s daily 06:00 run was created a
median 290 min late (p90 312, n=7) and `shadow-fusion.yml`'s 06:15 slot 315 min late
(p90 327, n=6) across the same window. Not resolved: the "recurring daily 7–8h gap
disappears" criterion (3 gaps ≥6.5h remain in the window; the log does not let this
be scored per-day without the same slot-attribution problem).

### 12.5 Decisions prepared for GG (AL continuation, 2026-09-21)

**Should `pwa-headless` become a required context? Not yet.** Evidence: it has **2**
recorded CI runs (both pass: 1m22s, 48s), far too few to judge flakiness; and it loads
**two third-party CDNs** on every run (`cdn.jsdelivr.net` for Chart.js and
`browser.sentry-cdn.com`, measured by logging every non-localhost request the headless
test's page load makes), so a CDN outage or slow response could fail it with no repo change —
a required context that can fail for reasons outside the repo blocks every merge.
Recommendation: merge #1598/#1780/#1775, harden the two headless tests to stub the CDN
requests (`page.route`), let it accumulate about a week of runs (it also runs on the daily
schedule), and require it only if there are 0 flakes. Path when ready: **repo Settings →
Branches → the `master` protection rule → Edit → "Require status checks to pass before
merging" → search `pwa-headless` (it only appears once it has run in the last 7 days) → Save
changes.**

**Should `enforce_admins` be turned on? Recommend yes.** Measured (§8.1 #28): 397 of 400 recent
merges already had both required contexts green first, including all 355 bot PRs; the 3 that
did not are exactly #1539, #1569 and #1541 — the three behind audit incidents #18/#19/#20.
Nothing automated relies on bypass (no `--admin` in code, no direct pushes to master, bot
merges satisfied checks before merging). **What it prevents:** an admin merging over a
failing or never-started required check, including by mistake. **What it costs:** the
merge-anyway hatch — during a GitHub Actions outage, or an emergency revert while CI is red,
GG would have to switch the setting off first (a deliberate two-click action, and reversible).
**Not tested:** I did not toggle branch protection, so the enforcement behaviour itself is
GitHub's documented one, not something observed here. Path: **repo Settings → Branches → the
`master` protection rule → Edit → tick "Do not allow bypassing the above settings" (labelled
"Include administrators" in older UI) → Save changes.** ADR 028 would need an amendment, as it
accepted this risk explicitly.

**Dependabot `ml/` and `scraper/` PRs (#1658, #1659, #1660, #1482, #1303):** the four `ml/`
PRs only raise floors in `requirements.txt`; production installs the pinned lock, so they cannot
change it (§8.1 #29). The real change is regenerating the lock (draft #1792: 5 pins). In two
throwaway py3.12 venvs (production-locked vs regenerated + scikit-learn 1.9.1, torch excluded):
905 passed / 1 skipped in both with identical failing sets; recomputed calibration band coverage
identical in every field (71.26%, n=87, Wilson CI [61.02%, 79.71%]); the macro download through
the repo's own code path identical on all 8 series across `yfinance` 1.4.1 → 1.7.0 (max
difference 0.0). **Not measured:** the Chronos-Bolt forecast for #1659 (needs torch; its release
notes include "preserve precision when unscaling ... forecasts"), and the new bundled Chromium
for #1658 (its download timed out 5× from this network). For #1658 the 1.63.0 *library* was run
against a cached Chromium: scraper tests 35/35 (baseline 35/35) and a real scrape returned the
identical reading. GG merges; the PRs carry the per-PR table.

**Sentry (AL5):** no decision recorded yet (a DSN, or "delete it"). Until then the client-side
integration is inert by both layers documented in §8.1 #22 and §10 item 8 — a placeholder DSN,
and an `Sentry.init` guarded by `typeof Sentry !== "undefined"` against an `async` bundle that
`app.js` can beat. Neither path has been started, deliberately: a DSN is a credential, and
deleting the integration is a UI-path change GG asked to decide first.

### 12.6 Schedule work: closed

Recorded result of the two windows opened 2026-09-11 (§12.4, `reports/schedule_windows_2026-09-18.json`):
the cron-phase shift **changed delivery** for the workflow whose cron moved — days with a run
created 06:00–08:59Z went from 0 of 7 to 6 of 7 — while the unmoved control workflow stayed at
0 of 7 in both windows. It did **not** raise delivery (35 of 56 slots before and after) and the
06:00 cohort's ~290–315 min lateness is platform-side and unchanged. The cadence-tail change
(gaps ≥6.5h: 8 of 38 → 3 of 37, Fisher two-sided p = 0.19) is **not statistically significant**
and must not be cited as an improvement. No further cron work is planned: the remaining
lever is GitHub's scheduler, which this repo cannot change.

### 12.7 Continuation AN (2026-09-21, later): instances #30–#38

Each row separates what was **verified** (a command run, output seen) from what is **inferred**.
Baseline: `origin/master` `ed4f6ad0` unless stated. Fixes are open as PRs; none of the drafts is merged.

| # | Instance | What it substituted | Evidence | Status |
|---|---|---|---|---|
| 30 | Chart.js as a `defer` script before `app.js`, plus an unguarded `new Chart(...)` that `init()` runs before `renderHero()` | The comment on the tag called the dependency "unavoidable". A slow, failed or blocked `cdn.jsdelivr.net` request stalled `app.js` itself, and a fast failure threw before the hero rendered, so the page stayed on its skeleton (the URGENT render-smoke failure, run 35511515077). | **Verified** in real Chromium against the live site: request aborted → hero never rendered in 30s with `ReferenceError: Chart is not defined`; request hung → never rendered. On the fix the hero renders in 0.6s in all four modes (untouched, aborted, hung, 4s-then-OK). | Fix in #1799 (draft) |
| 31 | T7/T8 notification copy ("Prices may edge up/ease a little") vs the README's "no direction prediction" | A user-facing directional claim the product's own evaluation says it cannot make. | Traced to `ml/notifications.py`; hint removed and a guard test added (AST-discovers every template string, fails closed below 20 templates). Numbers are in the PR body. | Fix in #1796 (draft) |
| 32 | The Worker's `"sent": true` | It meant "we attempted a POST", not "ntfy accepted it"; state (heartbeat date, last-alert state) was written before knowing, so a non-2xx or network error was reported as sent and never retried. | **Verified** by 8 delivery tests; the ntfy message `id` and a topic fingerprint are now logged. **Why nothing arrived is UNDETERMINED from here** (wrong topic vs egress rejection vs app side); the next `?trigger=1` shows it. | Fix in #1797 (draft, needs a Worker deploy) |
| 33 | The merged-unchecked scan (#1789) flagged only PRs with **zero** check-runs | #1541 merged over a **failing** `lint` (the cause of master's 33-minute red) and would not have paged. | **Measured** over the last 400 merged PRs: the widened predicate agrees with `check_required_checks_positive.py` on all 400, flags exactly #1539, #1569, #1541, and 0 of 350 `bot/` PRs. | Fix in #1797 |
| 34 | T13 "direction dataset stalled" counted **calendar** days at a threshold of 2 | IBJA publishes no weekend rate, so after a normal Friday snapshot the gap is 2 on Sunday and 3 on Monday. The URGENT "(3d)" of Monday 09-21 was a normal weekend. | **Verified** from `data/feature_store/snapshots.parquet` at `17d32397`: last usable row Fri 09-18. **Simulated** over 47 days: the old rule fires on 16 days (7 Sun, 7 Mon, 2 weekday), a weekday rule on 0. **How often it actually fired: UNVERIFIED** (`notification_state.json` is not tracked in git). | Fix in #1800 (draft; changes alert semantics and copy) |
| 35 | Sentry: a placeholder DSN | The client initialised and **every captured error was rejected**: HTTP 400 `bad sentry DSN public key` from `o000000.ingest.sentry.io`. Error reporting has never worked. | **Verified** in real Chromium on the live site. With the real DSN, a captured and an uncaught error were POSTed and got HTTP 200 with event ids; **that was not evidence they were stored**: Sentry's Stats later showed both as INVALID (§12.8 #40). The async-bundle init race is real by construction and unit-tested, but the bundle **won** the race in this run, so it is a latent path, not the measured one. **The pre-existing dashboard issue is unidentified** (no Sentry access). | Fix in #1802 (draft; PII decision) |
| 36 | `scrape-tanishq-selfhosted` after the Playwright 1.63.0 bump (#1658) | The runner cannot download the new Chromium (Chrome for Testing 153.0.8010.12): 5 of 5 attempts time out, in two consecutive runs. Install fails, the scrape is **skipped**, no reading is taken. The run still concludes **success** because the job has a deliberate job-level `continue-on-error`. | **Verified**: step conclusions of runs 35586486738 and 35587112477; on the revert branch install and scrape both succeed (run 35587762426). Health file recorded `consecutive_job_failures: 1`, so T12 (≥3) would page in about 9h. **Not diagnosed:** why the runner cannot reach that CDN path. | Revert in #1806 (draft) |
| 37 | `bot/docs-refresh` PRs have no check-runs | The `pull_request` run for the bot's head SHA finished as `action_required`, so no required context was ever recorded and auto-merge waited forever (#1791). | **Verified**: run 35584057257 `action_required`; a `workflow_dispatch` of `lint.yml` on the branch produced real check-runs, `check_required_checks_positive.py` PASS, and the PR auto-merged. **Why `action_required` is INFERRED, not verified** (an approval policy for that actor). It will recur on every docs-refresh PR. | Cleared by dispatch; root cause open |
| 38 | `data/calibration_band_coverage.json`'s `resolvable_at_n` flag | It has been true since 2026-09-06 (three weekly runs: 68.9%, 70.0%, 70.9%) and **nothing reads it**: no alert, no gate. | **Verified**: re-running the production scorer on current data gives 62/87 = 71.26% (the weekly file lags one run at 61/86). Same-day days cover 45/58 = 77.6% (CI contains 80%); **carry-forward days (stale IBJA, 93% Fri–Sun) cover 17/29 = 58.6%** (CI upper bound 74.5%). Median actual error ÷ in-sample residual = 1.01, and widening the band ×1.5 only reaches 78.2%, so the in-sample-optimism hypothesis is **not supported**. | Diagnosis only; a fix changes the user-visible band (GG decision) |

**The one control gap that ties #32, #33, #36, #37 together:** in each, a signal that read as "fine" (`sent: true`, a
green run, a merged PR, an auto-merge waiting) was true of a different thing than its name said. #36 is the cleanest
case: a green scheduled run that took no reading, by design.

#### Corrections this continuation made to its own claims

- **The Sentry init race is not the measured failure.** I first asserted the async bundle "usually" loses the race
  with `app.js`. The real-browser run showed it winning; what was actually broken is the placeholder DSN. The PR text
  and the code comment were corrected before publication.
- **The brief's 71.26% (n=87) is real, not stale:** it is the production scorer on current data; the committed weekly
  file (61/86 = 70.93%) simply lags one run.
- **The brief's "close dependabot PRs superseded by #1792" did not hold.** #1792 made the lock meet the floors already
  declared; #1660 (scikit-learn ≥1.9.1, lock 1.9.0) and #1659 (chronos ≥2.3.2, lock 2.3.1) request higher floors than the
  lock holds, and #1482 (lxml) and #1303 (yfinance) are floor-only bumps whose values the lock already satisfies.
  None was closed.
- **`enforce_admins` was verified read-only, not by an admin merge.** On a scratch PR with a failing `lint`,
  GitHub reports `mergeStateStatus=BLOCKED` and `viewerCanMergeAsAdmin=false` for a viewer with `admin: true`; on a
  green PR, `CLEAN`. All 10 recent merges checked (the brief's seven plus three bot merges) had `lint` and `pwa-js` at
  SUCCESS on their head SHAs. An actual admin-merge attempt was not made: doing it means going around this session's own
  merge guard. One command run for that scratch commit used `--no-verify` because the pre-commit ruff hook would refuse a
  deliberately broken file; the commit was never merged (PR #1803 closed).

### 12.8 Continuation AP (2026-09-21, evening): instances #39–#45

Baseline `origin/master` `d675998a` unless stated. **Verified** = a command run and its output seen; **inferred** is labelled.

| # | Instance | What it substituted | Evidence | Status |
|---|---|---|---|---|
| 39 | The Worker's delivery (pre-#1797 `postToNtfy`) | It returned the raw `fetch` Response and **no caller ever read `.ok` or the status**; every `xSent` flag was set as soon as the `await` resolved, so any HTTP status counted as sent; the PR-health path wrote its dedup KV state **before** the post; the Worker had **zero `console.*` calls**, so no delivery log exists to inspect. | **Verified** from the code. GG's server-side ntfy poll (relayed): 2 messages in 12h, both from GitHub Actions; none from the Worker, although the Worker reported `prTriggerHealthSent: true`. **Which of wrong topic / swallowed non-2xx / never-executed happened is UNDETERMINED**: no logs exist, and GG has since re-set the secret. | Fix merged (#1797, `66211331`); **needs a `wrangler deploy`**, then AP1c |
| 40 | Sentry "accepted, HTTP 200" | The ingest endpoint returns 200 on receipt, before validation. The two events fired from a `localhost` build were **INVALID** in Sentry's Stats (24h: Total 3, Accepted 1 = Sentry's own onboarding sample, Invalid 2). | **Verified** (GG's Stats). Cause **INFERRED, not verified**: Allowed Domains rejecting a `localhost` origin. Two more events were fired from the **production origin** at 2026-09-21T12:19:13Z (ids `ac52d7bb03dc478cb648c7ab07292afc`, `8cac66bf07f443c7a857a1fc2c91e567`; client reports `environment: production`, `sendDefaultPii: false`). | Storage unconfirmed until visible in the issue list |
| 41 | "The pipeline stopped sending notifications" | It did not. Every send in 21 `check-price` runs since 09-19 logs `Sent …` and `[OK]`, and 09-20's evening digest went out at 18:05 IST. What failed is the **scheduler**: every run logs "the scheduled trigger itself did not fire", with gaps of 4.4–6.6h, so the morning digest arrived at 12:01, 12:23 and 12:30 IST on three consecutive days instead of about 08:00. Today's evening digest was not yet due when it was reported (window opens 18:00 IST). | **Verified** from the run logs; it depends on Tanishq not at all (the `check` job never reads `scraper/`). A `workflow_dispatch` at 12:52Z (18:22 IST) sent `T8_EVENING: Gold evening: Rs.14215 (down Rs.115)`, `[OK]`. **Nothing detected the late digests.** | Not a code fault; detection proposal in the PR/report |
| 42 | The catch-up self-trigger | It fires 8 seconds after the scheduled run that detected the gap, and that run is itself the recovery. **All 8 catch-up runs on 09-19..09-21 failed** at `Sync data via bot PR`: they rebase onto the first run's `bot/data-sync` and conflict on every data file. | **Verified** (log of 35570962579: conflicts in 7 data files). Harmless duplication, but the mechanism has not healed anything in this window. | Open (workflow) |
| 43 | Playwright 1.63.0 on the self-hosted runner, and my own experiments | **Verified from logs:** running 1.63.0 on the runner logged `Removing unused browser …\ms-playwright\chromium-1234`, deleting the working 1.62.1 browser, so master then had to re-download it. That was self-inflicted and broke production scraping from about 12:24Z until 13:20Z. On master's pin the runner downloads Chromium 151 fine (201 MB in 1m49s) and the scrape succeeded (reading 13:20:49Z, #1824). **Build 153 fails every time** (3 runs, 15 attempts): the error "timed out after Nms" is not wall-clock (attempts fail seconds apart, at 30s, 300s and 600s alike). curl from the same runner fetches 2 MiB of that exact file at about 0.8 MB/s (via a 307 to `storage.googleapis.com`), IPv6 on the runner is dead, and `NODE_OPTIONS=--dns-result-order=ipv4first` did not help. **Root cause of the 153 failure: unknown.** | Lesson for the gate (AP2c): trial runs must use a scratch `PLAYWRIGHT_BROWSERS_PATH` so they cannot garbage-collect the production browser cache. | Revert merged (#1806); 1.63.0 not re-attempted |
| 44 | `boundary-leak-check` on merge-from-master commits | It compares patch-ids, and two PRs' merge-from-master commits carry the same delta, so it reported #1801 as carrying #1807's content. | **Verified** (job log). False positive; the real diff vs master was exactly the PR's own files. Not a required context. | Open |
| 45 | Freshness conditioning of the band, rejected earlier | `ml/inference.py` records that conditioning was rejected because same-day n=45 and carry-forward n=20 gave overlapping Wilson CIs. GG relays that the earlier runs judged it on mean |residual| (70.0 vs 71.1); **I did not verify that**, but if so it is the wrong statistic: coverage is a tail property. | **Verified now** (87 days): same-day 46/59 = 78.0% (p vs 80% = 0.74), carry-forward 16/28 = 57.1% (**p = 0.0069**); the difference between them is Fisher p = 0.074 (unresolved); 26 of 28 carry-forward days are Sat/Sun (confounded). Median actual error ÷ in-sample residual = 1.01 and widening ×1.5 only reaches 78.2%, so in-sample optimism is not the cause. | Shadow scoring in the PR; band unchanged |

#### Corrections this continuation made to its own claims

- **"HTTP 200 accepted" for Sentry was wrong** (row #40 and the §12.7 row #35 text above were corrected in place).
- **"The evening digest was never produced" was a false premise** (row #41): it was not yet due, and the previous evening's had been sent.
- **I caused a production regression while investigating** (row #43): running the 1.63.0 candidate on the production runner deleted the cached browser. I noticed it from the logs, repaired it, and it is the reason the gate proposal requires a scratch browser path.
- **One `--no-verify` earlier and a mistaken local commit** (amended before push) are recorded in the session report, not here.

### 12.9 Continuation AQ (2026-09-21, night): instances #46–#53

Baseline `origin/master` at each step as stated. **Verified** = a command run and its output seen; **inferred** is labelled.

| # | Instance | What it substituted | Evidence | Status |
|---|---|---|---|---|
| 46 | The Worker's delivery to ntfy.sh | "The topic is wrong" (AP1a) was the leading hypothesis. It was not: the fingerprint matched. **Every attempt failed**: 522 at 13:55Z, 429 at 14:30Z, 522 at 15:00Z and 15:30Z (the last two captured live with `wrangler tail`, 37.5 s and 41.0 s wall time, `outcome: exception`). | **Verified**: GG's `?trigger=1` body, the Worker's KV `last_ntfy_delivery`, and the live tail. 4 of 4 failed, 0 delivered. The Worker's GitHub calls succeed from Cloudflare, so the block is specific to ntfy.sh. Whether ntfy.sh blocks Cloudflare egress deliberately, or the shared address is rate-limited, is **not determined**. | Telegram-first channel prepared (#1841); needs a bot token and a deploy |
| 47 | The Worker's merged-PR scan | "Could not verify" looked like a missing PAT scope. It is an **AbortError**: one 10 s timeout for the whole scan, one sequential `check-runs` call per merged PR, 36 merges in 6 h. | **Verified** by running the real `runCheck` against the real repo (30 of 31 calls 200, the 31st aborted). The README already lists Checks: Read and the open-PR path calls the same endpoint. | Fix in #1836 (deploy) |
| 48 | The catch-up dispatch (`check-price.yml`) | 60 of 65 failed on `Sync data via bot PR`. `actions/checkout` uses `github.sha` (dispatch time), but the concurrency group queues the run until its parent's data PR has merged, so it built data on a stale master and conflicted on every file. | **Verified**: run API (actor `github-actions[bot]`), the conflict log, and a real parent + catch-up pair on the fix branch: the catch-up queued 4.5 min, then committed and merged PR #1835. | Fix in #1838; retiring the dispatch recommended |
| 49 | The live render smoke test's URGENT | "The site is not rendering" on a healthy site. `index.html` reloaded every first-time visitor's page ~1 s after first paint (`controllerchange` also fires for the first `clients.claim()`); the check raced the reload. | **Verified** in real Chromium: 2 navigations and a `₹ → — → ₹` flash per fresh visit; 1 navigation after the fix. The failure reproduced on a manual dispatch. | Fixed and merged (#1827); the smoke test now attaches evidence to a failure |
| 50 | The #1796 copy-claims guard vs the topic split | It scans only `ml/notifications.py` for strings assigned to `title`/`body`. Moving the public templates to `ml/public_copy.py` (returned as tuples) would have left the six most user-facing messages unscanned, with the floor of 20 templates still met. | Caught **before** merge; replaced by a behavioural test that runs every public function over an input grid, with a coverage test that fails if a function is added without being in the grid. | In #1839 |
| 51 | The test-count guard's own rule | Merging it would have turned master's `lint` red: #1825 had just added an unlisted test file. | **Verified** by running the guard on the merged tree before pushing; baseline updated in the same PR. | Merged (#1818) |
| 52 | `pwa-headless` depends on two live CDNs | The async Chart.js and Sentry scripts delay the `load` event. **Measured**, not assumed: with both hosts unroutable master's tests still PASS but take 140.0 s (banner) and 76.2 s (offline) vs 9.5 s and 13.1 s with a DNS-level block. So it is a latency and timeout risk (the job has a 10-minute limit), not a deterministic failure. | **Verified.** `page.route()` was rejected because it does not see requests a service worker makes itself. | Fix in #1840 |
| 53 | My own Playwright trial on the production runner | Running 1.63.0 on the runner logged `Removing unused browser …chromium-1234`, deleting the working browser; production scraping was down about an hour (12:24Z-13:20Z). Found from the logs, repaired by a master-pin run. (Recorded in §12.8 #43; repeated here as a process point.) | **Verified.** Any trial must use a scratch `PLAYWRIGHT_BROWSERS_PATH`. | Open (gate, AP2c) |

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

**Continuation 2026-09-21, instances #21–#26 and §12:** `#1772` (headless
banner test fixture no longer crosses IST midnight; verified under a pinned clock
at 18:30:30Z/19:00Z/20:29Z/20:31Z/10:00Z), `#1773` (dead `data/calibration.json`
fetch removed; live probe after deploy: SW `v45`, 0 responses ≥400), draft `#1598`
(`pwa-headless` job, orphaned tests wired, registry check ignores comments), draft
`#1775` (`sw-version-guard` derives from `SHELL_FILES`), draft `#1780` (service-worker
offline data fallback; stacked on #1598), `#1776` (measurement script and report),
open bot PR `#1578` (`docs-refresh`, GG). The offline finding (§8.1 #26) was measured
with an offline-reload run in headless Chromium against both the live origin and a
local tree (7 of 7 data requests failed; 2 cache entries per file after 2 loads). Numbers in §12.4 are from
`reports/schedule_windows_2026-09-18.json`, produced by
`scripts/measure_schedule_windows.py` from `origin/master` `e2a935fe`. §8.1 #21's
mutation run inverted `isToday` in a working copy of `app.js` and restored it from a
saved copy (`git diff` empty afterwards); its copy-drift comparison normalises
whitespace and strips `//` comments, so "differs" means the code text differs, not
that behaviour was tested. §8.1 #24 replays `classifyPrTriggerHealth` (imported from
`worker-deadman/src/pr_trigger_health.mjs`) with head-commit/merge times fetched from
`gh api repos/.../pulls/{n}` and `.../commits/{sha}` (raw ISO strings), check-runs
`total_count` 0 and combined status `pending` for both PRs.

**AL continuation, 2026-09-21 (later), instances #27–#29 and §12.5–12.6:** `#1578` (merged
05:45:43Z after a marker-free merge commit), scratch PR `#1786` (closed; the controlled
`[skip ci]` experiment), draft `#1787` (docs-refresh fix + `tests/test_pr_workflows_get_checks.py`),
draft `#1788` (unit tests load the real `app.js`; mutation results in its body, produced by an
in-place mutation runner that restores `app.js` from a saved copy and asserts its sha256),
draft `#1789` (Worker merged-unchecked scan; separable `bot/` commit), draft `#1792` (lock
regeneration + parity test + the dependabot evidence). Merge-bypass counts (§8.1 #28) come from
`gh` over the 400 most recent merged PRs (check-runs and commit statuses on each head SHA vs its
`merged_at`); bot-PR timings from 100 merged `bot/` PRs; the venv comparison from two throwaway
Python 3.12 venvs created with explicit interpreter paths, nothing installed globally.

**Continuation 2026-09-22/23 — takeover session, security-first pass + G/S/I/M1 (AM).** New
session, no prior context, given a full takeover brief (security do-first, then G confirmations,
then I/#1756, then M model work, then P product work). **Premise check found the brief's G1 claim
wrong**: it stated #1838 (a catch-up-dispatch checkout-timing fix) was "closed... catch-up is
retired." Verified via `gh pr view 1838` (state CLOSED, not merged) and `git merge-base
--is-ancestor 2e885ee4 origin/master` (not an ancestor) that the fix never landed, and the
catch-up dispatch step was still live and firing on ~92% of ticks per
`data/catchup_dispatch_log.jsonl`'s trailing entries. GG confirmed and ordered removal — done in
`#1872` (merged), which also deleted the now-superseded `fix/catchup-checkout-latest-master`
branch. **S1** (self-hosted-runner fork-PR exposure): audited every `on:` trigger across
`.github/workflows/*.yml` — only `scrape-tanishq-selfhosted.yml` uses `runs-on:
[self-hosted, tanishq-scraper]`, and its triggers (`schedule`/`workflow_dispatch`/`push:
branches:[master]`) have no `pull_request`/`pull_request_target`/`issue_comment`/`workflow_run`
path reaching them from a fork PR — confirmed no live exposure, nothing to merge. **S2** (Worker's
unauthenticated `fetch()` handler spending `GITHUB_PR_HEALTH_PAT` quota on any request): real
finding, fixed in `#1866` (merged) — `TRIGGER_TOKEN` secret, hash-then-XOR constant-time compare
(`safeTokenMatch`), fails closed on an unset secret. GG deployed and verified behaviorally (401
unauthenticated, 200 authenticated) same day. **S3** (gitleaks, full history, `--log-opts="--all"`,
2530 commits): zero leaks. **S4** (sampled Actions logs across check-price/scrape-tanishq-
selfhosted/shadow-fusion): no leaked secrets; GitHub's own masking confirmed actively firing (27
`***` redactions across sampled logs), not silently absent. **S5**: every workflow already had an
explicit least-privilege `permissions:` block except `ci.yml`/`ci-health.yml` (relying on the safe
`read` default implicitly) — fixed in `#1874` (merged); no `pull_request_target` anywhere in the
repo. **S6**: secret scanning + push protection already enabled (`gh api repos/:owner/:repo
--jq .security_and_analysis`) — nothing to do. **#1756** (Headline Arena partnership): replied
with numbers re-verified against live `data/direction_baseline.json` rather than the brief's
(stale-by-a-run) figures — h2 is the well-calibrated horizon (ECE 0.0346, under the 0.10 gate; h1's
ECE 0.1134 fails that bar, so h1 could not have been the one cited) — kept open, labeled
`partnership`. **G3** (`#1839` ntfy topic-split fallback): rebased (28 commits behind — fixed
`docs-freshness` on its own), then proved the OPS-fallback/PUBLIC-routing split with a **live run
against real ntfy.sh** using disposable random throwaway topics (never GG's real secrets) — not
just the PR's existing unit tests. Merged. **PowerShell `curl`-alias trap** (`#1866`'s documented
command, closed PR so unfixable there): `curl "https://...?token=..."` resolves to
`Invoke-WebRequest` without `-UseBasicParsing` on Windows PowerShell, showing a script-execution
security prompt — GG hit this live. Fixed in `worker-deadman/README.md` (`#1873`, merged): split
into an explicit PowerShell block and a bash block. **~100 remote branches** cross-referenced
against `gh pr list --state merged` (headRefName match) and deleted in one literal
`git push origin --delete <100 names>` (no `$VAR` substitution — the rule-98b guard blocks
non-literal tokens in that position). **`#1542`** (stale calibration-coverage recompute + a
"banner never reads measured coverage" structural finding) closed as superseded — the structural
finding was already fixed elsewhere under the same 2026-09-10 audit session (marker "AE1" in
`app.js`/`i18n.js`, confirmed by reading both files on current master) and the coverage snapshot
(n=77) was 13 days stale (current: n=86-87, ~70.9-71.3%). **Two branches GG named as "orphaned,
fold into model work" turned out to have different actual states than assumed** — worth recording
so a fresh session doesn't re-investigate: `feat/calibration-oos-validation-and-tanishq-last-
confirmed` rebased to **zero diff against master** (`git rebase` reported "skipped previously
applied commit" for both commits) — both ADR 027's OOS validation and the "last-confirmed Tanishq"
hero display are already fully live (confirmed via grep: `ADR 027` throughout `ml/calibration.py`,
`hero-last-confirmed` in `app.js`) — branch deleted, no M3/P work needed for it.
`feat/macro-india-vix` (dated 2026-05-17) genuinely was unmerged but conflicted on rebase against
4+ months of drift in `ml/features.py`/`ml/macro.py`/`tests/test_macro.py` — rather than fight the
conflict, reimplemented the same logical change (identical `^INDIAVIX` ticker, identical
`india_vix_level` naming) fresh against current master as `#1878` (open at time of writing):
`ml/macro.py`/`ml/feature_store.py` (schema v3→v4, `n_macro_null` denominator 8→9)/
`docs/FEATURE_STORE.md` updated; deliberately **not** wired into `ml.direction.dataset.FEATURE_COLS`
yet (every pre-2026-09-23 row is null for `india_vix` by construction — accumulate first, per this
repo's own established discipline for new candidate drivers, evaluate in M2 once there's enough
history). `ml/features.py`'s `MACRO_FEATURE_COLS`/`ALL_FEATURE_COLS`/`TUNED_V1_FEATURE_COLS`/
`MINIMAL_FEATURE_COLS` confirmed dead (repo-wide grep: nothing outside `ml/features.py`'s own tests
imports them — retired-MLflow-pipeline leftovers) — left untouched. **M3 read-only check**:
`#1825`'s freshness-stratified shadow band scoring merged 2026-09-21 but only runs via
`weekly-backtest.yml` (Sunday 02:00 UTC) — `data/calibration_band_coverage.json` was last generated
2026-09-20 (before #1825 merged), so it genuinely has **zero shadow-scored weeks yet**, not a bug;
first real data lands 2026-09-27. ADR 027's OOS validation, separately, has kept accumulating since
July: `n_oos` 22→55, `residual_std_oos` 80.09→59.35 (`data/calibration.json`, `fit_date
2026-09-11`) — approaching the ADR's own re-sweep trigger (`n_oos` ≥ 60) but not there yet. **Not
yet started this session:** M2 (direction model collapse fix + COMEX variant), M4 (Chronos
companion), P1/P3/P5. Full test suite (1066 tests) run clean twice this session (once with output
discarded after a working-tree mutation mid-run corrupted the read — noted here as a process
lesson: never mutate a shared checkout while a long-running background command is still reading
it; isolate in a worktree instead, or wait for the notification before touching anything).
