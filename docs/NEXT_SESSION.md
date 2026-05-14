# Deferred Work

## ✓ Completed — Operational cleanup (2026-05-14)

- **PR #2 (checkout v4→v6):** merged. Runner 2.334.0 ≥ 2.329.0. All three workflows updated. Validated by scheduled bot run 0a63a65.
- **PR #3 (setup-node v4→v6):** merged. All steps green including npm install + playwright. Explicit `cache: "npm"` config unaffected by v6 auto-caching change.
- **Roadmap #4 (rebase guard):** `git pull --rebase origin master` added between `git commit` and `git push` in check-price.yml's "Commit updated data files" step. Commit `4fef06e`. Validated live — first post-fix path-triggered run: Commit step green.
- **Observed failure mode:** The pre-fix `workflow_dispatch` run showed the exact race the guard fixes — bot committed locally, scheduled run pushed in between, push rejected. Now resolved.
- **weekly-backtest.yml:** has the same push pattern without the rebase guard. Not fixed — out of scope. Consider applying same fix if the weekly run starts failing on push.

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
