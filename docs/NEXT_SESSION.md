# Deferred Work

## Operational fixes
- Roadmap item #4: `git pull --rebase` before bot push step in check-price.yml — prevent bot push conflicts when I push between scheduled runs
- actions/checkout@v4 and actions/setup-node@v4 deprecated — bump to v5 before June 2, 2026. Coordinated bump across check-price.yml, lint.yml, and any other workflow using them.

## ML improvements (from Phase 2 plan)
- #5: Drop TFT + N-BEATS from prod ensemble. Keep training code and model files. Add data gate: re-introduce via champion/challenger when real_readings_count >= 1000 (N-BEATS) and >= 2000 (TFT).
- #6: Replace seed data — re-run seed_history.py targeting ibjarates.com for 2 years of real Indian retail reference rates (fixes the 1.15 multiplier miscalibration post-July-2024 budget).
- #7: Add fallback scrape source (goodreturns.in or IBJA) as secondary in scraper/scrape.js.
- #8: Pydantic schema validation at prices.json write boundary.
- #9: Interval coverage metric (% of actuals inside p10–p90).
- #10: Warmup banner in UI when forecast.json.warmup === true.
- #11: Surface backtest accuracy card in UI (direction accuracy vs naive + backtest timestamp).
- #12, #13: Re-introduce N-BEATS / TFT via champion-challenger gated on real-readings count.
- #14: GROQ key rotation reminder (quarterly).

## Held Dependabot PRs
- PR #15: pyarrow >=13.0→>=24.0.0. Spans 11 major releases, no CVE forcing urgency, parquet round-trip untested. Revisit when: (a) a security advisory lands on pyarrow <24, or (b) we have time to test parquet read/write across the version jump on real data files.

## Deferred ML migrations
- MLflow 3.x migration: rewrite promotion.py and test_promotion.py to use registered-model aliases instead of stages. Removes deprecated API (transition_model_version_stage, get_latest_versions(stages=...)). Estimated effort: 1 focused session. Reason for deferral: out of scope of security cleanup; requires test rewrite.

## Behavior notes for CC in future sessions
- When a git command fails (rebase, push, etc.): surface the failure and propose alternative. Do NOT silently switch strategies (e.g. rebase → merge fallback).
- Diagnostic ranking: pull actual logs before ranking hypotheses. The May 2026 stale-scraper diagnostic put "workflow disabled" and "IP block" above the actual cause (₹ symbol in HTTP header at update-and-notify.js:47), which was visible in the Actions log all along.
