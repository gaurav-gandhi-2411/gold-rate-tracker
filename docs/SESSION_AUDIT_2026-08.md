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

## 8. The defect-class catalogue (Y3, audit 2026-09-05)

Thirteen instances of one defect class have now been found across this
audit's sessions (2026-08-27 through 2026-09-05): **a control emits a
plausible-looking result instead of failing or raising when it cannot
actually verify the thing it claims to report.** Each was tracked at the
time under a different session-local codename (established fact #6, G1d,
P6, Q4, R2/R3, U4, V3, X2, Y2) and the letter/count used to refer to it
drifted between PR bodies — PR #1340 calls the same finding "(e)" that
this doc's §4 called "instance (f)," and PR #1394 cites "nine known
instances" at a point where this doc's own running count said different.
**That drift is itself the reason this section exists**: there was never
one canonical list. The numbering below (#1–#13) is the first attempt at
one and supersedes every ad hoc letter/count used in earlier PR bodies —
those PRs are not being renumbered, this is just where "the current
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

**Grouped by what made each invisible:**

- **Green metrics** (something looked complete/correct while being wrong): #1, #2, #3, #4, #5, #6, #10.
- **A threshold calibrated during degradation** (a number encoded the bad state it was meant to detect): #11.
- **A metric or control that structurally could not see the failure mode** (not wrong, just blind to the thing being asked of it): #3/#4's sweep-heuristic window, #8, #12, #13.
- **A boundary enforced at the wrong step** (the check ran, passed, and still let the thing through): #7, #9.

Four of thirteen (#12, #13, and the sweep-heuristic gap noted under #3/#4)
are findings about **this audit's own controls**, not about the product —
the same shape CLAUDE.md rule 85a names: a control's own construction can
encode the narrower-than-advertised-surface assumption it exists to catch
elsewhere. #9's fix (#1407) is the clearest case: built specifically to
catch the #1393-into-#1394 mechanism, verified passing on its own PR and
on two live open PRs, and only shown this session — by deliberately
reproducing the exact incident it was named for — to still miss it
whenever the leaking PR lacks a manually-applied label. **A check that has
never failed on a real violation is unproven**, and three of this audit's
four newest instances (#8, #12, #13) were found by asking exactly that
question of an existing, green, trusted control.

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
