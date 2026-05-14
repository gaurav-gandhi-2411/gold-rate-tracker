# Deferred Work

## Session plan — Operational cleanup (~45 min)

### 1. Merge PR #2 + #3 (actions/checkout + setup-node v4→v6)
- Both skip v5 entirely (Dependabot jumped straight to v6)
- **checkout v6:** persist-credentials now stored under `$RUNNER_TEMP` instead of git config — requires runner ≥v2.329.0. Verify GitHub-hosted runners are on this version before merging.
- **setup-node v6:** limits automatic caching to npm only (v5 introduced auto-detection via `packageManager` field in package.json). Verify our lint.yml and check-price.yml still cache as expected.
- Merge one at a time, watch Lint between. Coordinated bump across check-price.yml, lint.yml, and any other workflow using them. Deadline: June 2, 2026.

### 2. Roadmap item #4: bot push rebase guard in check-price.yml
- Add `git pull --rebase origin master` before the bot's push step
- Prevents push rejection when a manual push lands between scheduled runs
- Self-resolves the manual `git pull origin master && git push` workaround used this session

### 3. PR #15 (pyarrow >=13→>=24) — revisit if time permits
- Skip if no CVE has dropped on pyarrow <24 since last check
- If revisiting: test parquet round-trip with ml/macro.py cache files before merging (spans 11 major Apache Arrow releases)

---

## Session plan — Model simplification (~1 hour)

### 4. Roadmap item #5: drop TFT + N-BEATS from prod inference
- Edit ml/inference.py to skip TFT and N-BEATS branches entirely
- Keep training code, model files, champion/challenger plumbing intact
- Add constant: `MIN_READINGS_FOR_DEEP_MODELS = 1000` — re-introduce via champion/challenger when real_readings_count hits that threshold
- Update tests to reflect LightGBM-only inference

### 5. Roadmap item #10: warmup banner in UI
- Show banner when `forecast.json.warmup === true`
- No inference changes — UI-only

---

## Session plan — Data quality (~2–3 hours)

### 6. Roadmap item #6: replace seed data with IBJA rates
- Re-run seed_history.py targeting ibjarates.com for 2 years of real Indian retail reference rates
- Fixes the 1.15× multiplier miscalibration introduced by the July 2024 import duty regime change
- Highest-leverage ML improvement available — do this before any model retraining

---

## Remaining roadmap items (not yet scheduled)

- #7: Add fallback scrape source (goodreturns.in or IBJA) as secondary in scraper/scrape.js
- #8: Pydantic schema validation at prices.json write boundary
- #9: Interval coverage metric (% of actuals inside p10–p90)
- #11: Surface backtest accuracy card in UI (direction accuracy vs naive + backtest timestamp)
- #12, #13: Re-introduce N-BEATS / TFT via champion-challenger gated on real-readings count
- #14: GROQ key rotation reminder (quarterly)

---

## Held Dependabot PRs

- **PR #15:** pyarrow >=13.0→>=24.0.0. Spans 11 major releases, no CVE forcing urgency, parquet round-trip untested. Revisit when: (a) a security advisory lands on pyarrow <24, or (b) we have time to test parquet read/write across the version jump on real data files. Currently installed at 19.0.1.

---

## Deferred ML migrations

- **MLflow 3.x migration:** rewrite promotion.py and test_promotion.py to use registered-model aliases instead of stages. Removes deprecated API (`transition_model_version_stage`, `get_latest_versions(stages=...)`). Estimated effort: 1 focused session. Reason for deferral: out of scope of security cleanup; requires test rewrite.

---

## Behavior notes for CC in future sessions

- When a git command fails (rebase, push, etc.): surface the failure and propose alternative. Do NOT silently switch strategies (e.g. rebase → merge fallback).
- Diagnostic ranking: pull actual logs before ranking hypotheses. The May 2026 stale-scraper diagnostic put "workflow disabled" and "IP block" above the actual cause (₹ symbol in HTTP header at update-and-notify.js:47), which was visible in the Actions log all along.
- Do NOT use `cast()` for mypy fixes — use TypedDict or `assert isinstance` instead. cast() bypasses mypy silently; if the dict shape changes, the cast lies and produces a runtime crash instead of a mypy error.
