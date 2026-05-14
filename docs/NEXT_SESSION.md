# Roadmap

Primary metric going forward: **decision accuracy** — when the model says "wait, price will drop," does the next-5-day minimum drop ≥ ₹100 below today's price? MAE and directional accuracy kept for academic transparency only.

---

## ✓ Phase 0 — Operational cleanup (2026-05-14)

- PR #2 (checkout v4→v6): merged, runner 2.334.0 ✓, validated by scheduled bot push.
- PR #3 (setup-node v4→v6): merged, all steps green, validated by full workflow_dispatch.
- Roadmap #4 (rebase guard): `git pull --rebase origin master` added between `git commit` and `git push` in check-price.yml. Commit `4fef06e`. Validated live.
- **weekly-backtest.yml:** same push pattern without rebase guard — not yet fixed. Apply same one-liner if weekly run starts failing on push.

---

## ✓ Phase 1A — Tanishq history investigation (2026-05-14)

**Findings:**

- **Location:** Same URL (`tanishq.co.in/gold-rate.html`), same page. No separate endpoint needed.
- **Date range:** 31 daily entries visible (14-Apr-2026 → 14-May-2026). UI has 7/14/30-day period tabs — 30 days is the maximum; all data is pre-loaded in DOM, no lazy API calls for more history.
- **Granularity:** One reading per calendar day. Weekends carry forward Friday's rate (e.g., 09-05 Sat = same as 10-05 Fri). No intraday data.
- **Purity tiers:** All three (22K, 24K, 18K) present in `data-goldrate22kt/24kt/18kt` attributes on each `span.goldpurity-rate` row — identical mechanism to live scraper.
- **Format match:** History 22K for 14-05-2026 = ₹14,845. Live scrape = ₹14,845. ✓ Same retail-inclusive price.
- **Parseability:** Trivial. Same `querySelectorAll("span.goldpurity-rate[data-goldrate22kt]")` query, collect ALL results instead of just `[0]`. No API, no auth, no pagination.
- **No backend API found:** Data fully embedded in page HTML. No deeper history accessible beyond 31 days.
- **Bot detection:** None observed.

**Corpus impact:**
- prices.json currently: 31 readings, 6 unique dates (2026-05-09 → 2026-05-14).
- Backfill would add 25 net new daily dates → 31 total unique daily dates.
- Net effect: 5× expansion of real-reading corpus.

**Verdict: Middle case. Recommend Phase 1C immediately after Session B.**
The backfill is trivial (30 min) and multiplies our real-data corpus 5×. Not a substitute for IBJA reseed (2 years of data, Phase 2), but provides 31 real Tanishq retail reference points from our actual scrape source before Phase 2 lands.

---

## Phase 1B — Session B: Model simplification (~1 hour)

1. **Roadmap #5: drop TFT + N-BEATS from prod inference (ml/inference.py)**
   - Skip TFT and N-BEATS branches entirely in the hot path
   - Keep training code, model files, champion/challenger plumbing intact
   - Add constant: `MIN_READINGS_FOR_DEEP_MODELS = 1000`
   - Update tests to reflect LightGBM-only inference

2. **Roadmap #10: warmup banner in UI**
   - Show banner when `forecast.json.warmup === true`
   - UI-only, no inference changes

---

## ✓ Phase 1C — Tanishq history backfill (2026-05-14)

- Script: `scraper/backfill-history.js` (one-shot, not wired into CI)
- Merged 25 net new daily readings (2026-04-14 → 2026-05-08) into prices.json
- prices.json: 31 → 56 rows; 6 → 31 unique dates (5× corpus expansion)
- Backfilled entries carry `source: "...?lang=en_IN (history backfill)"` for auditability
- Idempotent — second run inserts 0 entries ✓
- Weekend carry-forward observed: Tanishq holds Friday rate through weekends. Real behaviour, not a bug. Relevant for metrics phase — don't penalize direction errors on weekends.
- Commit: `da001d9`

---

## Session B.5 — Smart daily notification (~45 min)

Goal: 4pm IST cron (10:30 UTC) that sends a push notification only when something interesting happened, with LLM-curated commentary. Most days produce no notification — preserves alert-fatigue hygiene.

#### Trigger rules (notify if ANY are true)

- Price moved ≥ 2% from previous day's 22K reading
- Today's 22K is at or within ₹50 of the 30-day low
- Today's 22K is at or within ₹50 of the 30-day high
- 5-day cumulative move ≥ 3% in either direction
- First reading after a 24h+ scrape gap ("we're back online, here's where we are")

If multiple triggers fire, mention all of them in the LLM prompt — commentary is richer for it.

Threshold note: 2% / ₹50 / 3% are first-guess values. Revisit after ~2 weeks of running — goal is ~3–4 notifications per week, not daily.

#### Implementation

- New script: `ml/daily_summary.py`
- New workflow: `.github/workflows/daily-summary.yml` on cron `30 10 * * *` (10:30 UTC = 4pm IST)
- Reads `data/prices.json` + `data/forecast.json`; computes triggers; exits 0 silently if none fire
- If trigger fires: build structured context (today/yesterday price, 7-day avg, 30-day low/high, which triggers fired) → Groq prompt (1–2 sentences, ≤200 chars, factual, no emojis, no financial advice) → ntfy push
- ntfy Title: ASCII-only summary ("Gold 22K at 30-day low" or "Gold 22K +2.1% today"). ₹-in-header bug pattern: reuse `fmtHdr` approach from `scraper/update-and-notify.js`
- Idempotent: marker file `data/last_summary.json` with date + content hash; do not send twice on same day with same data

#### Constraints

- Reuse Groq integration from `ml/commentary.py`; reuse `NTFY_TOPIC` secret
- `continue-on-error` on LLM call; ntfy alert if workflow itself crashes (same hardening as check-price.yml)
- Unit tests for trigger logic against synthetic price series; mock Groq in CI

#### Out of scope

- Adjustable thresholds in UI; multiple LLM providers; per-user preferences; historical backtest of what would have fired

#### Scheduling

After Session B. Can slot before or after Session C — notification logic doesn't depend on data quality.

---

## Phase 2 — Session C: IBJA seed replacement (~2–3 hours)

Replace synthetic Yahoo-derived seed history with real IBJA daily rates:
- Re-run seed_history.py targeting ibjarates.com for 2 years of real Indian retail reference rates
- Fixes the 1.15× multiplier miscalibration from the July 2024 import duty regime change
- Highest-leverage ML improvement — do this before any model retraining
- Note: Tanishq backfill (Phase 1C) and IBJA reseed are complementary: different sources, different date ranges

---

## Phase 3 — Metrics infrastructure

Define and compute three metrics:
- **Decision accuracy (primary):** when model says "wait (price drops ≥₹100 in next 5 days)," was it right?
- **MAE (academic):** kept for transparency, not optimisation target
- **Directional accuracy (academic):** up/down direction vs naive

Two evaluation tracks: synthetic backtest (sanity), real-data track (trust).
Surface decision accuracy in UI (replaces or supplements current backtest card).

---

## Phase 4 — Tier 2 features

Add one at a time, gate on decision accuracy improvement:
- Festival proximity (Akshaya Tritiya, Diwali, Dhanteras, Gudi Padwa)
- Day-of-week / day-of-month seasonality
- Tanishq premium lag (Tanishq retail vs IBJA spot spread)
- 14-day realised volatility

---

## Phase 5 — Tier 3 macro audit + adds

- Feature importance audit: drop dead-weight features from ml/macro.py
- Add: India 10Y government bond yield
- Add: India VIX (if accessible)
- Add: DGFT monthly gold import volume (if accessible)

---

## Phase 6 — Deep model re-introduction (eventually)

Re-introduce N-BEATS/TFT via champion/challenger when:
- N-BEATS: real_readings_count ≥ 1,000
- TFT: real_readings_count ≥ 2,000

---

## Remaining roadmap items (not yet scheduled)

- #7: Add fallback scrape source (goodreturns.in or IBJA) as secondary in scraper/scrape.js
- #8: Pydantic schema validation at prices.json write boundary
- #9: Interval coverage metric (% of actuals inside p10–p90)
- #14: GROQ key rotation reminder (quarterly)

---

## Held Dependabot PRs

- **PR #15:** pyarrow >=13.0→>=24.0.0. Spans 11 major releases, no CVE forcing urgency, parquet round-trip untested. Currently installed at 19.0.1. Revisit when: (a) security advisory lands on pyarrow <24, or (b) time to test parquet read/write on real data files. Last checked: 2026-05-14 — no CVE.

---

## Deferred ML migrations

- **MLflow 3.x migration:** rewrite promotion.py and test_promotion.py to use registered-model aliases instead of stages. Removes deprecated API (`transition_model_version_stage`, `get_latest_versions(stages=...)`). Estimated effort: 1 focused session.

---

## Behavior notes for CC in future sessions

- When a git command fails (rebase, push, etc.): surface the failure and propose alternative. Do NOT silently switch strategies (e.g. rebase → merge fallback).
- Diagnostic ranking: pull actual logs before ranking hypotheses. The May 2026 stale-scraper diagnostic put "workflow disabled" and "IP block" above the actual cause (₹ symbol in HTTP header at update-and-notify.js:47), which was visible in the Actions log all along.
- Do NOT use `cast()` for mypy fixes — use TypedDict or `assert isinstance` instead. cast() bypasses mypy silently; if the dict shape changes, the cast lies and produces a runtime crash instead of a mypy error.
