# gold-rate-tracker — Current State for Orchestrator Handoff

*Snapshot as of 2026-05-31. Maintained for context that isn't in the code. The repo itself shows what exists; this document explains why.*

---

## RESUME HERE — Open time-gated items (as of 2026-05-31)

Four items need attention at defined future dates. A new session can pick up directly here.

**(a) Calibration unlock ~2026-06-12 — highest priority.**
The first-ever automatic flip of `calibration.json` to `valid: true`. Trigger: `run_refit_if_needed()` detects ≥30 valid overlap pairs in CI (currently accumulating at ~1/trading day via the IBJA pm_916 upsert fix, PR #56). What to verify the day it fires:
- CI log shows "refitting… n=30 slope=X.XX intercept=Y.Y"
- T6 ntfy notification "Gold forecast: calibration unlocked" arrives on device (norm #16)
- `data/calibration.json` has `valid: true` with real slope/intercept
- Dashboard "Adjusted to Tanishq prices" shows "Yes"
- `data/forecast.json` `chronos_companion.calibration_applied` is `true`

The full chain is pre-verified by `tests/test_calibration_unlock_chain.py` (commit `cf6fc78`). If anything fails, check that test first.

**(b) Scraper gap-rate trend post-4h-cadence (~2026-06-28).**
Baseline: 27% gap rate (34 gaps >9h across 124 readings). ADR 016 re-evaluation trigger is >15% sustained over 4 weeks. Check CI run history at ~2026-06-28 and record in Decision Log.

**(c) H5 calibrated fallback — decision needed post-unlock.**
ADR 016 deferred the IBJA-calibrated price fallback because calibration was invalid ("invalid calibration = noise"). Once item (a) resolves, H5 becomes a legitimate option (flagged in ADR 017). No code needed until (a) resolves; flag to consultant for a decision at that point.

**(d) ntfy topic rotation — RESOLVED (WONTFIX) 2026-06-07.**
GG accepts `gold-msgg-7k2x9p4r` as the permanent live topic. No rotation will be performed. Residual risk: a public ntfy topic permits unsolicited publishes (notification spam) only — no data, repo, or pipeline access. Revisit only if the product goes multi-tenant. The topic lives only in the GitHub secret — zero hardcoded references in the repo.

---

## Project goal

Free-tier gold price tracker for Indian retail (Tanishq 22K). Live scrape every 4h, 5-day forecast trajectory, ntfy directional alerts, GitHub Pages PWA. Production architecture is **naive flat-hold as headline + Chronos-Bolt-Tiny as directional companion** per ADR 012 — the model does not beat naive on magnitude at current data scale, so naive IS the production prediction. Chronos's verified direction signal (55.8% accuracy, 63.3% on last-30 folds) gates notifications.

## Production architecture (one-paragraph + diagram)

```
[GitHub Actions cron, 0 */4 * * * UTC → ~05:30/09:30/13:30/17:30/21:30/01:30 IST]
        ↓
[Tanishq scrape — 3-attempt retry, CF detection (ADR 016)]──→ data/prices.json
[IBJA scrape — upsert pm_916 when post-PM-fix run has value]─→ data/ibja_rates.parquet
[ml.calibration — refit if ≥30 overlap pairs (ADR 017)]─────→ data/calibration.json
[macro fetch]────→ data/macro_cache.parquet (gitignored)
        ↓
[ml.chronos_forecast --probe]
   reads parquet → writes data/chronos_probe.json
        ↓
[ml.inference]
   reads prices + chronos_probe + backtest + calibration
   writes data/forecast.json (naive headline + chronos companion)
        ↓
[ml.notifications]
   T1..T8 evaluated, ntfy sent if conditions met, state cached via actions/cache
   Quiet hours 22:00–07:00 IST: alerts queued + stamped (PR #55 dedup fix)
        ↓
[git commit + push] → GitHub Pages PWA serves data/*.json
```

## Directory structure (key folders/files only)

```
gold-rate-tracker/
├── ml/                          ← Python ML code, all live unless noted
│   ├── inference.py             ← LOAD-BEARING (forecast writer)
│   ├── chronos_forecast.py      ← LOAD-BEARING (probe writer)
│   ├── notifications.py         ← LOAD-BEARING (trigger logic + state)
│   ├── calibration.py           ← Tanishq↔IBJA mapping (Huber)
│   ├── ibja.py                  ← Live scrape + 30-day PDF backfill
│   ├── backtest.py              ← Walk-forward methodology
│   ├── metrics.py               ← Metric definitions
│   ├── macro.py                 ← yfinance covariates (not used by Chronos yet)
│   ├── commentary.py            ← Groq LLM, every 6h
│   ├── drift.py                 ← Drift monitor (partial; legacy logic, no harm)
│   ├── features.py              ← Feature engineering helpers (legacy)
│   └── llm_cache_helpers.py     ← Forward-looking, no live consumer
├── data/                        ← Reference data, committed (except notes below)
│   ├── prices.json              ← Tanishq scrape history
│   ├── forecast.json            ← LOAD-BEARING (PWA contract)
│   ├── chronos_probe.json       ← LOAD-BEARING (inference reads)
│   ├── calibration.json         ← Gate file, valid: true/false
│   ├── ibja_rates.parquet       ← Committed reference data
│   ├── backtest.json            ← Weekly backtest output
│   ├── notification_state.json  ← GITIGNORED, cached in GH Actions
│   └── macro_cache.parquet      ← GITIGNORED, regenerated each run
├── docs/
│   ├── PROGRESS.md              ← LOAD-BEARING engagement record (append-only)
│   ├── ARCHITECTURE.md
│   ├── KNOWN_ISSUES.md
│   ├── RUNBOOK.md
│   ├── PHASE_3_RETROSPECTIVE.md ← Engagement record from Phase 3
│   └── adr/                     ← ADRs are canonical, read-only
├── tests/
│   └── fixtures/                ← Real PDF, HTML samples
├── .github/workflows/
│   ├── check-price.yml          ← LOAD-BEARING 4h production loop
│   ├── lint.yml                 ← ruff + ruff-format + mypy + full pytest
│   ├── weekly-backtest.yml
│   ├── monthly-ibja-backfill.yml
│   ├── generate-og-image.yml
│   └── scraper-canary.yml
├── archive/                     ← Deprecated reference data, never imported
├── app.js                       ← LOAD-BEARING PWA logic
├── index.html
└── service-worker.js
```

## Load-bearing files

| File | What it does | Why don't casually touch |
|---|---|---|
| `ml/inference.py` | Writes `data/forecast.json` every 4h | PWA reads its output directly. Schema changes are user-visible. Backward-compat aliases (`predicted_22k`, `lower`, `upper` at top level) must persist until PWA migrates. |
| `ml/chronos_forecast.py` | Writes `data/chronos_probe.json` | Both inference and notifications consume this schema. `num_samples` controls forecast stochasticity. |
| `ml/notifications.py` | Trigger evaluation + state management | State persists across CI runs via `actions/cache` (prefix-match on `notification-state-`). Cooldowns, anti-spam, quiet hours all live here. |
| `ml/calibration.py` | Maps IBJA→Tanishq via HuberRegressor | `data/calibration.json` has `valid: bool`. Flips to `true` at 30 overlap pairs via the `run_refit_if_needed()` step in CI (added ADR 017). When it flips, inference applies calibration to Chronos horizon arrays. |
| `ml/ibja.py` | Live scrape + 30-day PDF backfill | Sole source of IBJA-916-PM history. Single point of failure for the model's context. |
| `ml/backtest.py` | Walk-forward h=5 backtest, weekly cron | Produces `data/backtest.json` that feeds `naive_mae_recent_30` into inference and `direction_acc_30f` into notification gating. |
| `data/forecast.json` | PWA reads this every page load | Schema IS the PWA contract. Structured blocks (`headline`, `chronos_companion`) are canonical; top-level aliases are backward-compat shims. |
| `data/chronos_probe.json` | Inference reads this for companion block | If probe fails, inference still runs (writes `chronos_companion.status: "failed"`); T5 fires once per IST day. |
| `data/notification_state.json` | Anti-spam state | Gitignored (`.gitignore` entry was absent until 2026-06-07, added in chore PR); cached via `actions/cache/restore@v4` + `save@v4` with `notification-state-${run_id}` key and `notification-state-` restore-keys prefix. Master branch only. |
| `data/calibration.json` | Calibration gate | `valid: false` until 30 IBJA-Tanishq overlap days. Don't manually flip; `run_refit_if_needed()` in CI handles it (ADR 017). |
| `.github/workflows/check-price.yml` | 4h production cron | Step ORDER is load-bearing: scrape → ibja-append (upsert) → **calibration-refit** → chronos-probe → notification-restore → inference → notification-evaluate → notification-save → commit. |
| `app.js` | PWA logic | Reads current schema (Φ2 migrated; Ψ3C redesigned). Any JS error breaks the live site. |
| `docs/PROGRESS.md` | Engagement record + Risks Register + Decision Log | Append-only. Don't rewrite history. |

## Key conventions

**Code style:**
- Python: ruff (E, F, W, I, N, UP, B, SIM, RUF) at line-length=100, ruff-format
- Type hints on all function signatures; mypy `strict = false` with most ml/ modules in the `ignore_errors = true` override. New files (notifications.py, llm_cache_helpers.py, calibration.py, chronos_forecast.py) are strict-checked.
- `random_state=42` on every sklearn instance

**Testing:**
- pytest with `--ignore` flags for training-deps tests (test_config.py, test_promotion.py, test_tracking.py, test_tuning.py). These have known pre-engagement failures; ignored in CI, not fixed.
- Mocked HTTP for all external calls. No live API calls in tests.
- Real-data fixtures preferred where small (e.g., `tests/fixtures/ibja_30day_sample.pdf`).
- New non-trivial functions require a unit test.

**File layout:**
- `ml/` = production Python
- `data/` = CI-committed reference data (NOT gitignored except `notification_state.json`, `macro_cache.parquet`)
- `archive/` = deprecated reference; never imported by live code
- `tests/fixtures/` = test data, committed
- `scripts/` = one-shot tooling

**Notifications:**
- ASCII-only titles (no ₹; use `Rs.`)
- Priority 4/5 actionable, 2/3 informational
- Quiet hours 22:00–07:00 IST; alerts queued during quiet hours are stamped immediately on queue (PR #55 dedup fix) to prevent accumulation across consecutive CI runs
- T1–T5 conditional/ML-gated; T6 once-ever (calibration unlock); T7 3-day floor; T8 twice-daily digest
- All user-facing bodies plain-language (no ML jargon) as of PR #55; T6 body is owner-facing, retains technical language
- Commentary SYSTEM_PROMPT blocks: 'Chronos', 'model', 'baseline', 'naive', 'MAE', 'backtest', 'folds', 'fold', 'Wilcoxon' (pinned by `test_system_prompt_blocks_technical_jargon`)

**Schemas:**
- `forecast.json`: top-level aliases + `headline` block + `chronos_companion` block
- `chronos_probe.json`: status / wall_clock / horizon arrays / lean fields
- `backtest.json`: full folds array + aggregate metrics including `mae_5d_avg_chronos`, `mae_5d_avg_naive`, `dir_acc_5d_chronos`, `wilcoxon_p`

## Important decisions (the "why" not visible in code)

| Decision | Why | Reference |
|---|---|---|
| Naive flat-hold as headline | 165-fold walk-forward: Chronos 10.4% worse than naive, p=0.0089 | ADR 012 |
| Chronos kept as directional companion | 55.8% direction acc (above 50%); user value for notifications | ADR 012 |
| Single-series IBJA target (no MCX) | No free programmatic INR feed for MCX exists; IBJA covers it directly via ibjarates.com 30-day PDF + Wayback + live scrape | ADR 010 |
| Drop synthetic training seed | 92% synthetic data taught wrong distribution | ADR 010 |
| Chronos-Bolt-Tiny (not Base) | 8.65MB / 13ms inference; quality difference negligible at our context length | ADR 009 |
| HuberRegressor for calibration | Robust to occasional Tanishq promotional outliers | PROGRESS.md §3.1.3 |
| GH Actions cache for notification state | Free, persistent, prefix-match recovery; alternatives noisier | ADR 011 |
| No prompt caching on live LLM | Wrong provider (Groq), wrong size (<1024 tokens), wrong cadence (4h > 1h TTL) | ADR 013 |
| ibjarates.com primary (not ibja.co) | ibja.co structurally cannot return AM+PM in one request | PROGRESS.md §3.1 |
| TFT/N-BEATS retired | Required 2000/1000 readings; data accumulation timeline ~years | ADR 009 |
| Fail-fast conformal PI (no fabricated default) | A 1.5× multiplier on an unverified constant is still fabrication | ADR 014 |

## Known issues / gotchas / explored dead ends

**Currently noisy but accepted:**
- `pytest` locally without ignore flags shows 9 failures in training-deps test files. CI uses ignores; clean. Local devs need the same flags.
- IBJA PM rate sometimes `NaN` on early-morning CI runs (before ~17:00 IST publication). The upsert fix (PR #56) captures the PM value on the post-17:00 run; inference falls back to the most recent complete PM row until then. This is expected and accepted.
- `data/calibration.json` shows `valid: false` until 30 valid overlap pairs (pm_916 non-null). Currently accumulating at ~1 pair/trading day via the IBJA upsert fix. ETA for unlock: ~2026-06-12 (see RESUME HERE section). Refitted automatically by `run_refit_if_needed()` in CI (ADR 017). Do NOT manually flip `valid`.
- Chronos forecasts can flip direction between consecutive runs. Stochastic sampling. **Addressed in Φ4 (PR #35) with 5-sample majority consensus; T1/T2 gate on direction_consensus ≥ 0.6.**

**Recurring pattern — "computed-but-never-wired" bugs:**
This codebase has produced four instances of code that was tested in isolation but never connected to the live CI cycle:
1. `commentary.py` consumer miss — never updated after the naive-headline pivot (ADR 012); kept labelling the baseline as "model forecast" for 8 PRs. Fixed Ψ3C-fix. Now guarded by `test_system_prompt_blocks_technical_jargon`.
2. `calibration.py` refit never called — `fit_calibration()` existed and was tested for weeks before `run_refit_if_needed()` was wired into `check-price.yml`. Fixed Φ5.
3. IBJA PM-fix capture blocked — write-once `append_ibja_today` silently kept pm_916=NaN after the first early-morning write; the 4h cadence alone would not have helped. Fixed PR #56.
4. ntfy topic — only lives in GitHub secret (correct), but delivery was never verified end-to-end until flagged this session (norm #16 gap).

Prevention going forward: norm #15 (consumer audit), norm #16 (delivery verification), and `tests/test_calibration_unlock_chain.py` proving tests before the calibration unlock fires.

**Dead ends already explored — do NOT re-investigate:**
- MCX Bhavcopy direct download → Akamai WAF blocks
- yfinance for MCX symbols → returns empty
- nsepython / nsetools for MCX → out of scope, NSE only
- investpy for MCX → HTTP 403, library unmaintained
- Metals.Dev API for IBJA → returns USD/troy oz, not INR/g (wrong denomination)
- Migrating to Claude API for prompt caching → cost/benefit fails at 4h cadence (ADR 013)
- Synthetic `GC=F × INR=X × premium` training seed → 92% synthetic, archived
- TFT / N-BEATS as Chronos alternatives → data threshold too far
- ibja.co as primary IBJA source → cannot return both AM and PM in one HTTP call
- iOS standalone PWA does not provide developer-controllable 100%-reliable update mechanism. SW lifecycle in standalone mode is platform-controlled — iOS aggressively suspends service workers when backgrounded. Maximum achievable without user action: `registration.update()` on load + `visibilitychange` listener + 30-min periodic check (timers are frozen during suspension, so the interval only fires while foregrounded). When auto-update fails, the platform-level workaround is: open App Switcher (swipe up, hold), swipe the app away, reopen from Home Screen. Implemented in Ψ3B with tap-to-refresh affordance and in-app guidance.

## Current CI / test state

| Item | Status |
|---|---|
| Lint workflow (ruff + ruff-format + mypy + pytest) | Green on master (commit `cf6fc78`, 2026-05-31). **365 Python tests pass.** |
| JS tests | Green: 9 pure-function (tests/test_scrape.js) + 4 Playwright fixture DOM (scraper/test_scrape.js) + 16 hardening mock-HTTP (scraper/test_scraper_hardening.mjs) + 5 comparison cards (tests/test_comparisons.js). |
| check-price.yml | Green on master. 4h cadence. Scraper hardened with 3-attempt retry + CF detection (ADR 016). Calibration-refit step wired (ADR 017). IBJA upsert captures post-PM-fix rates. |
| scraper-canary.yml | Triggers on PR push to scraper/** paths (live DOM canary guarded to schedule/manual). |
| weekly-backtest.yml | Green; last run 2026-05-19. |
| notification state cache chain | Verified unbroken. T1-T8 all covered. Dedup-on-queue fix (PR #55) prevents quiet-hours accumulation for all IST-date-deduped triggers. |
| Calibration unlock chain | Pre-verified via `tests/test_calibration_unlock_chain.py` (4 tests, 6 links asserted end-to-end). Will fire for real ~2026-06-12. |

## Discipline norms (the orchestrator must inherit these)

These earned their place across PRs #1–56. They are NOT visible in the code.

1. **Flag-and-stop on plan deviations.** When the executor encounters a constraint that contradicts the spec — a file isn't where expected, a dependency conflicts, a "bug fix" requires a design decision — STOP and report to the orchestrator. The orchestrator escalates to the user. Do not silently substitute, even if the right answer feels obvious. This rule was violated three times in Phase 3 (linter auto-fix outside scope, MCX→COMEX substitution, gitignore reversal); each was acknowledged after the fact. Don't repeat.

2. **All CI workflows green pre-merge.** Not just "tests pass" — every workflow on the PR branch must be green. Lint counts. Use `gh pr checks <N>` before merging.

3. **ADR for every "we decided NOT to do X" decision.** ADR 012 (naive headline, not Chronos), ADR 013 (no prompt caching) are exemplars. Document failure criteria and re-evaluation triggers.

4. **Honest baseline reporting (ADR 005, load-bearing cultural artifact).** Always report `naive_mae` next to model `mae` in any forecast or backtest output. When the baseline wins, the baseline IS the model.

5. **Walk-forward backtest as evidence source for any model claim.** Not hold-out splits, not random splits. Expanding-window walk-forward at the production horizon (h=5). 30+ folds for statistical relevance.

6. **Conformal PI fail-fast.** Don't fabricate PI bands. If `naive_mae_recent_30` doesn't exist, return `model_status="insufficient_backtest_history"` with null bands. ADR 014.

7. **Falsifiable bet pattern.** When a model makes a directional prediction in a PR, log it in the PR description with a resolution date. Decision Log entry after resolution.

8. **No silent fallback in production.** If Chronos probe fails, write `chronos_companion.status: "failed"` AND fire T5 notification. The user must be able to tell when the system is degraded.

9. **PR scope discipline.** PR scope is defined by intent, not by what a linter happened to touch. If pre-commit auto-fixes a file outside the diff, revert and split.

10. **PROGRESS.md is append-only.** Mark phases complete; don't rewrite earlier sections. The doc IS the engagement record.

11. **Tests use mocked HTTP, never live calls.**

12. **ASCII-safe ntfy payloads.** No ₹; use `Rs.`.

13. **PR squash-merge commit messages MUST NOT carry `[skip ci]` in the body.** GitHub respects `[skip ci]` platform-wide for push events with no per-workflow override (verified PR Ψ3A diagnosis). To avoid suppressing master Lint on merge, strip `[skip ci]` from the squash body before confirming the merge — use `gh pr merge --squash --subject "..." --body "..."` with the body explicitly cleaned. The daily 06:00 UTC schedule in `lint.yml` is a backstop, not a substitute for this discipline.

14. **Visibility/visual-state claims must be verified via computed style (`getComputedStyle().display`) or rendered screenshot — NEVER via DOM attribute presence (`el.hidden`).** The `hidden` attribute and visual visibility are independent: `el.hidden === true` AND `getComputedStyle(el).display === "flex"` can hold simultaneously when an author-side `display` rule overrides the UA `[hidden]{display:none}`. This bug class shipped twice (`.pwa-help-btn` fixed in Ψ3B pre-PR; `.pwa-help-panel` missed in Ψ3B, caught in Ψ3B-hotfix) before this norm was formalized.

15. **When an architecture pivot changes what a data field means, audit ALL consumers — not just the primary consumer.** The Chronos companion block (`forecast.chronos_companion`) was added in Ψ2A for the PWA but `ml/commentary.py` was never updated alongside it. `commentary.py` predated the naive-headline pivot (ADR 012) and kept reading `predicted_22k` with a "Point estimate" label — presenting the naive flat-hold baseline as a model forecast. Fixed in Ψ3C-fix (PR #47), 8 PRs after the pivot. Consumer audit checklist: `app.js` (PWA), `ml/commentary.py`, `ml/notifications.py`, `ml/drift.py`. When a new block is added to `forecast.json`, grep for all callers of `forecast.get()` / `forecast["..."]` before closing the PR.

16. **An alert channel must be verified end-to-end (delivery confirmed), not just wired.** The scraper-down `curl` in `check-price.yml` uses `|| true`, meaning any ntfy delivery failure — wrong topic, unsubscribed topic, network error — is silently swallowed. For weeks, scraper failures fired the alert step (CI log showed it ran) but the alert never reached anyone because NTFY_TOPIC was misconfigured. The CI step reporting OK is NOT the same as the alert being delivered. Lesson: whenever a new alert path is added, verify receipt end-to-end (send a test notification to the actual device/channel and confirm arrival) before treating the path as operational. `[OK] ≠ delivered.`

## Open questions (things to verify when implementing)

- **Φ4 wall-clock budget on GH Actions runner.** ✅ RESOLVED — ADR 015. Actual probe wall-clock ~10s (dominated by model deserialization, not forecast compute). 5-sample probe adds ~75ms; 6h cadence makes this operationally fine.

- **Calibration flip detection without race conditions.** ✅ RESOLVED — ADR 017. `run_refit_if_needed()` is idempotent; T6 uses IST-date dedup (mirrors T5). Wired into CI. Race-free.

- **PI band explanation copy in PWA.** ✅ RESOLVED — Ψ3C-copy (PR #49) rewrote methodology accordion to plain language: "Range covers 80% of typical 5-day swings."

- **Φ2's PWA test gap.** ⏸️ STILL OPEN — No Playwright or browser-test infrastructure exists. Validation remains manual (open live site, check console). Acceptable for current phase; consider adding headless browser tests in a future hygiene PR.

- **Scraper gap-rate post-hardening.** ⏸️ CHECK ~2026-06-28 — Baseline was 27% gap rate. ADR 016 trigger: >15% sustained over 4 weeks. Compare CI run history at that date.
