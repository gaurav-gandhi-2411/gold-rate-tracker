# Phase 3 Retrospective — gold-rate-tracker ML rebuild

*Period: 2026-05-18 to 2026-05-19. Eleven build PRs (#11 A, #12 A.5, #13 B, #14 C, #15 D, E, F, #25 F.5, #27 G, #28 prompt-caching, #29 H). Six ADRs (009–014). Two docs PRs (#9, #10).*

---

## What we shipped

The production stack at Phase 3 close is a two-layer system running entirely on free infrastructure. The headline forecast is an explicit naive flat-hold (`predicted_22k = most_recent_ibja_pm_916 × calibration_factor`), with an 80th-percentile conformal prediction interval derived from the last 30 folds of naive walk-forward errors. Alongside it, Chronos-Bolt-Tiny runs as a directional companion probe: it writes `data/chronos_probe.json` each CI cycle (pinned revision `a0e552de`), and the inference step reads it to populate the `chronos_companion` block in `forecast.json` — never calling the model directly. The data layer is 100% real: live IBJA scrape (`ml/ibja.py`) + 30-day PDF backfill + Wayback Machine historical fill (177 rows total). A HuberRegressor calibration layer (`ml/calibration.py`) converts IBJA-916-PM INR/g to Tanishq 22K; it gates on `valid=True` at 30 overlap pairs (21 pairs at Phase 3 close). Five notification triggers (T1–T5, `ml/notifications.py`) cover directional signal alerts, large observed moves, weekly digest, and model-fallback surfacing; state persists across CI cycles via GitHub Actions cache. The legacy LightGBM training path, synthetic seed, TFT/N-BEATS artifacts, regime module, and daily-summary module are all deleted.

| Deliverable | PRs | What landed |
|---|---|---|
| Engineering hygiene | A, A.5 | Lockfile pinning, gitleaks, smoke test, full pytest in CI |
| Legacy retirement | B | TFT/N-BEATS retired, synthetic seed archived → `archive/` |
| IBJA data layer | C (#14) | `ml/ibja.py`, live scrape + 30-day PDF backfill |
| Calibration layer | D (#15) | `ml/calibration.py`, HuberRegressor, 21 overlap pairs |
| Chronos probe path | E | `ml/chronos_forecast.py`, parallel probe, legacy still active |
| Walk-forward backtest | F | `ml/backtest.py`, 165 folds at h=5, Wilcoxon statistics |
| Wayback backfill | F.5 (#25) | 73 new rows via Mode A HTML parser, 177-row parquet |
| Notification system | G (#27) | `ml/notifications.py`, T1–T5, IST quiet hours, state persistence |
| Prompt-caching infra | #28 | `ml/llm_cache_helpers.py`, ADR 013, no-cache decision documented |
| Naive headline + cleanup | H (#29) | Naive as production headline, LightGBM deleted, ADR 014 |

**Lines of code:** +17,551 / −11,505 across 95 files (source: `.py`, `.ts`, `.tsx`, `.yml`, `.json`, `.toml`, `.md`).

**Tests:** 19 Python test files at Phase 3 start → 26 at close. New Phase 3 test files: `test_backtest`, `test_calibration`, `test_chronos_forecast`, `test_ibja`, `test_inference_main`, `test_llm_cache_helpers`, `test_notifications`. Current passing: 270 passing, 7 skipped. 53 failing + 8 collection errors are legacy test files referencing deleted LightGBM/TFT/N-BEATS code; they remain on disk as untracked artifacts pending a `git rm` cleanup pass.

---

## What the evidence said

The central empirical story of Phase 3 is that no zero-shot model beat naive on IBJA-916-PM MAE over 5-day horizons at this data volume, but directional signal is real and exploitable.

- **Legacy LightGBM (Phase 1 audit):** MAE Rs.225.33 vs naive Rs.167.36 — 34.6% worse on 69 folds. Root cause was 92% synthetic training data, not model design. The model had learned premium-factor patterns from Yahoo Finance proxies that did not generalise to live retail prices.
- **Chronos-Bolt-Tiny (PR F.5 backtest):** MAE Rs.275.5 vs naive Rs.249.5 — 10.4% worse on 165 folds; Wilcoxon signed-rank p=0.0089. Statistically significant underperformance. The dominant driver is the 2025–2026 uptrend (~Rs.85,000 → Rs.145,000): on a strongly trending series, flat-hold is extremely hard to beat.
- **Direction accuracy:** 55.8% average across 165 folds (naive is 50%); 63.3% on the last 30 folds. This is the one positive finding — non-trivial directional signal, above the T1/T2 activation gate of 55%.
- **Calibration:** Tanishq-22K / IBJA-916-PM ratio median 1.017, std 0.015 across 21 overlap pairs. Tight enough to use; `valid=False` until 30 pairs accumulate (~9 more trading days from Phase 3 close).
- **Conclusion:** At this data scale on this series, no zero-shot model beat naive on MAE at h=5. The right engineering response was to name the naive baseline as the production forecast (ADR 012) and use Chronos for what it actually demonstrates — directional signal.

---

## The pivots

Three architectural pivots happened mid-engagement, each triggered by evidence rather than preference.

**1. Synthetic data → real IBJA data (Wayback + 30-day PDF)**
Triggered by the Phase 1 audit finding that 92% of the training corpus (444 of ~515 rows) was Yahoo Finance-derived synthetic data. The pivot decision (ADR 010, PR B) was to archive the synthetic seed immediately and replace it with real IBJA exchange data. PR C introduced the live IBJA scraper; PR F.5 extended historical depth from 21 rows to 177 via Wayback Machine HTML parsing and the monthly 30-day PDF.

**2. MCX proxy → single-series IBJA**
The Phase 3 plan originally included MCX Gold near-month contracts as a backfill data source to extend history before Wayback Machine depth was known. CC's data source audit (pre-PR C) found that Metals.Dev returns troy ounce prices (wrong denomination for INR/g), and the Wayback Machine CDX index confirmed sufficient IBJA HTML history existed for backfill without MCX at all. Single-series IBJA is simpler, denominationally correct, and avoids basis-adjustment complexity.

**3. Chronos magnitude headline → naive headline + Chronos directional companion**
The Phase 3 plan assumed Chronos would become the production headline forecaster after passing a walk-forward backtest. PR F.5 backtest results (MAE Rs.275.5 vs naive Rs.249.5, p=0.0089) made this untenable under ADR 005. Instead of rationalising deployment of a model 10.4% worse than naive, ADR 012 was enacted: naive is the headline by construction; Chronos is retained only for its directional signal (55.8% / 63.3%), which is the one verified positive finding.

---

## Engineering patterns that worked

**Read-only Phase 1 audit before any code change.** Starting with a comprehensive read-only audit of the live system — backtest JSON, forecast.json blend weights, data file composition — established a shared, evidence-grounded picture of what was broken before any plan was written. It prevented the engagement from carrying legacy assumptions forward.

**"Falsifiable bet" recorded at PR E.** Before the PR F backtest ran, CC stated a predicted outcome for the Chronos directional forecast (lean -2.29% by 2026-05-23 vs naive 0%). This is the minimum form of epistemic discipline: commit to a prediction before seeing the outcome. It made the PR F.5 verdict — Chronos loses on MAE — harder to rationalise away.

**ADR 005 (honest-baseline reporting) as a cultural load-bearing wall.** The pre-existing discipline of reporting both `val_mae` and `naive_mae` and surfacing them in the PWA created the conditions for accepting uncomfortable evidence. When the backtest said "naive wins," there was no temptation to choose a different validation window — the policy already existed. ADR 005 was written in Phase 1; it governed Phase 3 decisions.

**Walk-forward backtest with paired Wilcoxon statistics.** The 165-fold paired test at h=5 provided not just a point comparison but a p-value (0.0089). "Chronos 10.4% worse, p<0.01" is a different claim from "Chronos averaged slightly higher MAE." The statistical test is what made the ADR 012 decision clean rather than ambiguous.

**ADRs written before flipping production, including "we decided NOT to do X."** ADR 013 (do not apply prompt caching) documents a decision to *not* implement something. Writing a full ADR for a deferral forces explicit engagement with failure criteria — in this case, Groq provider mismatch, sub-1024-token prompts, and 6h cadence vs 1h TTL. The helper infrastructure shipped anyway, tested and ready, because documenting the reasoning also clarified exactly what would need to change for the decision to flip.

**Phased probe before production flip (PR E → F → H).** Chronos ran as a parallel probe for multiple CI cycles before replacing the production forecast. This meant ADR 014's production flip (PR H) was merging a path that had already produced live output, not introducing untested inference code to CI.

---

## Patterns that needed iteration

**"Flag and stop" vs. silent substitution.** Three times across the engagement, encountering a constraint that contradicted the current plan led to a workaround attempt rather than surfacing the conflict: PR A.5 linter errors (attempted to route around rather than fix), PR C MCX data source (attempted denomination conversion rather than flagging Metals.Dev as wrong-denomination), PR E gitignore conflict (attempted to resolve rather than stopping to confirm). In each case, the right behaviour was to stop, surface the specific contradiction, and wait for direction. The pattern became more automatic by PR F but required explicit reminders in PRs A.5 and C.

**Verification of expected outputs vs. claimed outputs.** PR B's forecast.json and the Wayback backfill's before/after parquet shape both required prompting to produce explicit before/after comparisons. Shipping a change that writes to a data file and not immediately showing "here is what changed in the file" is a gap — the claim "it worked" and the evidence "here is the diff" are different things.

**Cron-wait patterns when workflow_dispatch is available.** During PR G state persistence verification, a sleep/poll pattern was used to wait for CI state when directly triggering a workflow_dispatch run would have been faster and more deterministic. Using the primitive that models the actual signal (dispatch + wait for completion) is cleaner than sleeping and checking.

**Scope drift toward completeness.** On several PRs, there was a tendency to add "while we're here" changes (additional test assertions, schema field additions, monitoring improvements) that were not in the PR scope. These were mostly benign but added noise to diffs and occasionally introduced second-order issues. The PRs that stayed cleanest were the ones with a named single deliverable.

---

## Open from Phase 3 — Phase 4 candidates

- **Chronos-2 multivariate (USD/INR + Gold-USD covariates):** Addresses the mean-reversion bias on trending series that caused Chronos-Bolt-Tiny to underperform naive. Requires Chronos-2's exogenous covariate interface and sufficient row count to evaluate. Phase 4 entry condition: ≥250 IBJA rows + Chronos-Bolt promotion criterion not met after honest evaluation.

- **Time-of-day alignment in calibration:** Tanishq scrapes land at 10:00/16:00/22:00/04:00 IST; IBJA fixes at ~09:30/17:00 IST. Current UTC-date pairing is approximate. Alignment matters when intra-day moves are large. Fix: carry a `fix_time` column in the IBJA parquet and match on nearest fix rather than calendar date.

- **`naive_mae` and schema fields in the PWA:** The `chronos_companion` block fields (`naive_mae`, `mae_5d_avg_chronos`, `direction_acc_30f`) are present in `forecast.json` but the PWA currently degrades them to "—". Wiring them into the dashboard completes the honest-display loop that ADR 005 requires.

- **Wayback PDF deep-history extraction from GitHub Actions runners:** Local extraction was constrained by Wayback Machine connectivity. GitHub Actions runners have clean external connectivity and could run the PDF extraction step without the timeout/retry issues encountered locally. This could extend IBJA history before the ~2024 HTML CDX ceiling.

- **Promotion criterion execution:** When does Chronos earn the headline? ADR 012 specifies: ≥250-row backtest, `mae_5d_avg_chronos < mae_5d_avg_naive`, Wilcoxon p<0.05, evaluated on ≥30-context folds only. At current accumulation rate (~20–25 new rows/month), the 250-row threshold is reachable by approximately 2026-09 to 2026-10. Phase 4 trigger: run the re-evaluation once data crosses 250 rows.

---

## Numbers worth recording

| Metric | Before Phase 3 | After Phase 3 |
|---|---|---|
| Training data composition | 92% synthetic (444/515 rows) | 100% real (177 IBJA rows) |
| Forecast horizon | 1 reading (~6h flat-hold) | 5 days (h=1..5 quantile + conformal PI) |
| Backtest folds | 69 (LightGBM, sub-30 context mixed in) | 165 (Chronos, 143 of 165 with ≥30-row context) |
| Headline model | LightGBM ensemble (34.6% worse than naive) | Naive flat-hold (the baseline IS the model) |
| Notification triggers | 0 | 5 (T1–T5, live via ntfy.sh) |
| Tracked files in repo | 182 | 176 |
| Python test files | 19 | 26 (7 new Phase 3 files; 13 legacy files pending `git rm`) |
| Passing tests | ~270 (LightGBM stack, many now broken) | 270 passing, 7 skipped (Phase 3 stack) |
| LLM call sites with caching ADR | 0 | 2 (both deferred per ADR 013, infra shipped in `llm_cache_helpers.py`) |
| ADRs enacted | 008 | 014 |

---

## Closing

*From CC.*

Phase 3 is the most honest engagement I've participated in, and that's not a compliment in the flattering sense — it's an observation about what the work required. The pre-existing ADR 005 discipline was the load-bearing structure: when the walk-forward backtest came back with p=0.0089 against Chronos, there was no escape hatch. The policy already said what to do. What's worth noting for the next person who picks up this codebase: the naive headline is not a failure state. It is the correct engineering decision for this data volume on this series at this time. Deploying a model 10.4% worse than the baseline because it "uses machine learning" would have been the actual failure. The promotion criterion in ADR 012 is not a consolation prize — it's a real gate. When 250 rows exist and Chronos passes Wilcoxon p<0.05, flip it. Until then, the naive path earns its place every run. The discipline this codebase needs most in Phase 4 is not more model sophistication; it's accumulating real data and not making architectural decisions until that data justifies them.
