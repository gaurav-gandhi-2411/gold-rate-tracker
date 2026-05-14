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

## Phase 1C — Tanishq history backfill (~30 min, bolt-on to Session B)

Write a one-shot backfill script:
- Load `tanishq.co.in/gold-rate.html`, collect ALL `span.goldpurity-rate[data-goldrate22kt]` elements (same DOM as live scraper)
- Pair with dates from `table.goldrate-history-table` rows
- Use noon IST (06:30 UTC) as the canonical timestamp for each daily reading
- Deduplicate against existing prices.json entries by date before merging
- Write merged result back to prices.json
- Adds 25 net new real daily readings to the corpus

**Format note:** Tanishq history does NOT include a `source` field per row. Either omit it (prices.json entries from the bot do include `source`) or set `"source": "tanishq-history-backfill"` to distinguish from live readings.

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
