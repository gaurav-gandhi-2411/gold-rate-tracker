# gold-rate-tracker — Current State for Orchestrator Handoff

*Snapshot as of 2026-05-20. Maintained for context that isn't in the code. The repo itself shows what exists; this document explains why.*

## Project goal

Free-tier gold price tracker for Indian retail (Tanishq 22K). Live scrape every 6h, 5-day forecast trajectory, ntfy directional alerts, GitHub Pages PWA. Production architecture is **naive flat-hold as headline + Chronos-Bolt-Tiny as directional companion** per ADR 012 — the model does not beat naive on magnitude at current data scale, so naive IS the production prediction. Chronos's verified direction signal (55.8% accuracy, 63.3% on last-30 folds) gates notifications.

## Production architecture (one-paragraph + diagram)

```
[GitHub Actions cron, every 6h at 10/16/22/04 IST]
        ↓
[Tanishq scrape]──→ data/prices.json
[IBJA scrape]────→ data/ibja_rates.parquet
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
   T1..T5 evaluated, ntfy sent if conditions met, state cached via actions/cache
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
│   ├── check-price.yml          ← LOAD-BEARING 6h production loop
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
| `ml/inference.py` | Writes `data/forecast.json` every 6h | PWA reads its output directly. Schema changes are user-visible. Backward-compat aliases (`predicted_22k`, `lower`, `upper` at top level) must persist until PWA migrates. |
| `ml/chronos_forecast.py` | Writes `data/chronos_probe.json` | Both inference and notifications consume this schema. `num_samples` controls forecast stochasticity. |
| `ml/notifications.py` | Trigger evaluation + state management | State persists across CI runs via `actions/cache` (prefix-match on `notification-state-`). Cooldowns, anti-spam, quiet hours all live here. |
| `ml/calibration.py` | Maps IBJA→Tanishq via HuberRegressor | `data/calibration.json` has `valid: bool`. Flips to `true` at 30 overlap pairs (currently 21). When it flips, inference applies calibration. |
| `ml/ibja.py` | Live scrape + 30-day PDF backfill | Sole source of IBJA-916-PM history. Single point of failure for the model's context. |
| `ml/backtest.py` | Walk-forward h=5 backtest, weekly cron | Produces `data/backtest.json` that feeds `naive_mae_recent_30` into inference and `direction_acc_30f` into notification gating. |
| `data/forecast.json` | PWA reads this every page load | Schema IS the PWA contract. Structured blocks (`headline`, `chronos_companion`) are canonical; top-level aliases are backward-compat shims. |
| `data/chronos_probe.json` | Inference reads this for companion block | If probe fails, inference still runs (writes `chronos_companion.status: "failed"`); T5 fires once per IST day. |
| `data/notification_state.json` | Anti-spam state | Gitignored; cached via `actions/cache/restore@v4` + `save@v4` with `notification-state-${run_id}` key and `notification-state-` restore-keys prefix. Master branch only. |
| `data/calibration.json` | Calibration gate | `valid: false` until 30 IBJA-Tanishq overlap days. Don't manually flip; the fit function handles it. |
| `.github/workflows/check-price.yml` | 6h production cron | Step ORDER is load-bearing: scrape → ibja-append → chronos-probe → notification-restore → inference → notification-evaluate → notification-save → commit. |
| `app.js` | PWA logic | Currently reads stale schema (Φ2 fixes this). Any JS error breaks the live site. |
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
- Quiet hours 22:00–07:00 IST, queue or drop if >12h old
- Cooldown enforced via `notification_state.json`

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
| No prompt caching on live LLM | Wrong provider (Groq), wrong size (<1024 tokens), wrong cadence (6h > 1h TTL) | ADR 013 |
| ibjarates.com primary (not ibja.co) | ibja.co structurally cannot return AM+PM in one request | PROGRESS.md §3.1 |
| TFT/N-BEATS retired | Required 2000/1000 readings; data accumulation timeline ~years | ADR 009 |
| Fail-fast conformal PI (no fabricated default) | A 1.5× multiplier on an unverified constant is still fabrication | ADR 014 |

## Known issues / gotchas / explored dead ends

**Currently noisy but accepted:**
- `pytest` locally without ignore flags shows 9 failures in training-deps test files. CI uses ignores; clean. Local devs need the same flags.
- IBJA PM rate sometimes `NaN` if CI runs before ~17:00 IST publication. Inference falls back to most recent complete PM row.
- `data/calibration.json` shows `valid: false` until 30 overlap pairs (currently 21). Self-flips. No action needed.
- Chronos forecasts can flip direction between consecutive runs (PR E observed: DOWN 2.29% → UP 3.73% in 24h on same context). Stochastic sampling. **Φ4 addresses this with multi-sample consensus.**
- PWA shows stale fields ("val MAE —", "LightGBM" attribution). **Φ2 fixes this.**

**Dead ends already explored — do NOT re-investigate:**
- MCX Bhavcopy direct download → Akamai WAF blocks
- yfinance for MCX symbols → returns empty
- nsepython / nsetools for MCX → out of scope, NSE only
- investpy for MCX → HTTP 403, library unmaintained
- Metals.Dev API for IBJA → returns USD/troy oz, not INR/g (wrong denomination)
- Migrating to Claude API for prompt caching → cost/benefit fails at 6h cadence
- Synthetic `GC=F × INR=X × premium` training seed → 92% synthetic, archived
- TFT / N-BEATS as Chronos alternatives → data threshold too far
- ibja.co as primary IBJA source → cannot return both AM and PM in one HTTP call
- iOS standalone PWA does not provide developer-controllable 100%-reliable update mechanism. SW lifecycle in standalone mode is platform-controlled — iOS aggressively suspends service workers when backgrounded. Maximum achievable without user action: `registration.update()` on load + `visibilitychange` listener + 30-min periodic check (timers are frozen during suspension, so the interval only fires while foregrounded). When auto-update fails, the platform-level workaround is: open App Switcher (swipe up, hold), swipe the app away, reopen from Home Screen. Implemented in Ψ3B with tap-to-refresh affordance and in-app guidance.

## Current CI / test state

| Item | Status |
|---|---|
| Lint workflow (ruff + ruff-format + mypy + pytest) | **Pending Φ1 merge** (PR #30). Was RED on master post-PR-H; Φ1 fixes 3 mypy errors, ruff-format pass, removes dead test files. |
| pytest with CI ignores | Green on master pre-Φ1 (270 pass) |
| pytest local without ignores | 9 failures, all in training-deps files. Pre-engagement, accepted. |
| check-price.yml | Green every 6h run |
| weekly-backtest.yml | Green; last run on PR F.5 day (2026-05-19) |
| notification state cache chain | Verified unbroken across PR H merge |

**The orchestrator must verify Φ1 has been merged before starting this sprint.** If master Lint is still red, escalate immediately.

## Discipline norms (the orchestrator must inherit these)

These earned their place across nine PRs. They are NOT visible in the code.

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

## Open questions (things to verify when implementing)

- **Φ4 wall-clock budget on GH Actions runner.** Multi-sample probe `num_samples=5` extrapolating from local 13ms = ~65ms inference + 1 model load. Audit assumed <2s. Validate empirically in CI before merging the schema bump.

- **Calibration flip detection (Φ3) without race conditions.** The flip can happen mid-CI-cycle. Recommendation: idempotent T6 firing with daily IST dedup (mirrors T5 pattern). Avoids needing to cache prior-run state.

- **PI band explanation copy in PWA.** Exact phrasing is subjective. Suggested: "These bands are 5-day prediction intervals. They are intentionally wide because the model predicts a 5-day window, not a single day." Adjust as needed.

- **Φ2's PWA test gap.** No Playwright or browser-test infrastructure exists. Validation is currently manual ("open the live site, check console"). Acceptable for this sprint; consider adding Playwright in a future hygiene PR.

- **The PR E falsifiable bet resolves 2026-05-23.** Current partial data (1 of 5 days): price moved UP, Chronos predicted DOWN. Worth recording the final outcome in PROGRESS.md Decision Log when the window closes.
