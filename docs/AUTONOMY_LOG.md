# Autonomy log

Running log of the unattended run that started 2026-11-25 (GG away; report from this file when GG asks).
One entry per task per session. Status: DONE / IN PROGRESS / BLOCKED / WAITING ON DATE / SKIPPED.
Numbers are VERIFIED (measured in the session, source named) or INFERRED (reasoned).
Newest session first. Rules: `.claude/CLAUDE.md`; operating model and decision rules: the brief of 2026-11-25.

## Session 2026-11-25 (UTC), first session after a 9-day gap

The previous session ended 2026-11-16 17:00Z. Nothing but bot data syncs merged on master between then and
2026-11-25 (VERIFIED: every PR created since 2026-11-16T17Z whose branch is not `bot/*` is a Dependabot PR
(#3924, #3925, #4062); `git log` since then holds only bot data commits).

### 1. Reconstruction 2026-11-16 -> 2026-11-25

| Item | Status | Evidence |
|---|---|---|
| a. yfinance floor | DONE 2026-11-25: #4083 merged 17:01Z (verifier PASS: lint installed yfinance==1.8.0, 3606 passed, 0 failures). First real check-price run on it, 45662171945 (dispatched 17:11Z): success; its macro table matches the standalone fetch (gold_usd 3685.5, 3515.4, 3651.4, 3561.9; usd_inr 93.4653, 93.295, 92.673, 92.9152), "(ii) reference: 42 sampled rows ... OK", `next_fix: mode=after_afternoon_rate Rs.11350 [11162-11538]` (VERIFIED) | Dependabot #3690 closed and replaced. Before opening: yfinance 1.8.0 and 1.7.0, each in its own throwaway venv, fetched identical GC=F and INR=X closes for 10 sessions (GC=F last five 3685.5, 3515.4, 3651.4, 3561.9, 3478.7 vs 3481.2 on the live tick; INR=X 93.47, 93.29, 92.67, 92.92, 92.22), inside 1500-6000 and 60-120. check-price on 1.7.0 (run 45638675340): "(ii) reference: 42 sampled rows ... OK" (VERIFIED) |
| b. Engineering-closed declaration | NOT MADE | No PR merged by the session; 3 Dependabot floors still open (yfinance #4083, pyarrow #3924, soupsieve #4062). No count started |
| c. ADR 076 look | DONE 2026-11-25, late | Gates held on Monday 2026-11-23 for after_afternoon_rate; nobody dispatched `adr076-look.yml` that day (it is manual only). Deviation recorded. Run 45653839030 on 2026-11-25 16:26Z, unmodified: look day 2026-11-23 (the script counts only entries logged before 00:00Z of that Monday and returns the same answer on a re-run). after_afternoon_rate: pass on beta 1.0 (a, b, c all hold): change -22.6%, bootstrap 95% CI [-35.7, -6.3], n = 21; DM one-sided p = 0.0045 (alpha 0.025); coverage 0.857 on n = 21, Wilson 95% [0.654, 0.95], calibration on 46 earlier fixes. after_morning_rate: counts only, 17/20 fixes, 31/28 days: no look yet. Consequence per ADR 076 point 4: the window is eligible for an ADR 072 amendment, which is GG's decision. NOT promoted. The look is spent (final) |
| d. ADR 077 look code | DONE earlier | merged 2026-10-23 as #3068; `.github/workflows/adr077-look.yml`, `tests/test_adr077_look.py`, `tests/test_adr076_effect_guard.py`. The look needs ADR 076's successful run on or after its look day: run 45653839030 qualifies. First possible look Monday 2026-12-14 |
| e1. #3913 lighthouse bot PR | DONE | merged by the sweep 2026-11-16 20:40Z: ops-log "stranded bot PR merged ... stranded 400 min" (first natural stranded-PR merge) |
| e2. Unproven ops-log writers | STILL UNPROVEN | ops-log sources since 11-16: bot-pr-sweep, ci-health, heartbeat, notifications, check-price, price-history-integrity. None from scraper-canary, visitors-weekly or the Windows twin: each writes only on a condition that did not occur |
| e3. Branches | LOGGED | `fix/alerts-one-page-per-incident` (3 commits) and `fix/forecast-down-page-and-dedupe` (PR #3819 closed): content is on master by other commits (`REPAGE_H`, `_valid_incident` in `scripts/live_freshness.py`, `model_pause_lines` in `scripts/gg_digest.py`); `git cherry` shows them as unique because the PRs were squashed. Kept until a separate safety check |
| e4. Primary tree | LEFT ALONE | `docs/closeout-update` checked out with GG's uncommitted `.claude/settings.local.json` and untracked `.wrangler/`; not touched |
| f. Failures since 11-16 | P0 FOUND, FIXED #4082 | see section 2 |
| g. Laptop | Tanishq scrape successes per day (VERIFIED, `scrape-tanishq-selfhosted.yml`): 11-16: 6, 11-17: 5, 11-18: 4, 11-19: 5, 11-20: 6, 11-21: 5, 11-22: 0, 11-23: 0, 11-24: 0, 11-25: 2 (so far). Gap: newest reading Sat 21 Nov 19:51 IST until 11-25 13:50Z (T14 pages at 56 h and 77 h went to the ops log) |
| h. Model | P3 live (docs/MODEL_STATUS.md of 11-22, 26 real days to 11-18): miss Rs.111 vs Rs.133 holding the last rate (-16.5%); direction 77%; range coverage 69% = 18 of 26, Wilson 95% [50.0%, 83.5%] (target 80% inside); fallback not triggered. Challengers all "not yet". W2: 11 of 40 days (earliest 2026-12-29 per MODEL_STATUS; the plan said about 12-25) |

### 2. P0: data PRs blocked by a real market move (DONE, #4082)

- Verified: PR #4011 stayed open 16 h (2026-11-20 09:06Z to 11-21 01:00Z) and #4058 20.7 h (11-23 09:42Z to 11-24 06:23Z); the live page showed stale data; check-price failed at "Sync data via bot PR" in 7 runs.
- Cause: `tests/test_price_history_integrity.py::test_committed_explanations_cover_every_move_in_the_committed_history` asserted every >3% one-day move has an entry in `data/price_move_explanations.json`; real days -4.59% (11-20) and -3.4% (11-23) failed lint on each data PR (7 failed lint runs on bot/data-sync). The script's own header says an unexplained move must not block.
- Fix #4082: the test warns instead of failing; a replay of the 11-20 case pins it. Published gaps still fail.
- Still open: the two moves need a cause and evidence in `data/price_move_explanations.json` (not invented); the published IBJA chart series has missing days 2026-11-20..24.

### 3. Token expiry (CI_HEALTH_PAT, expires 2026-12-10)

DONE: #4084 merged 2026-11-25 (verifier PASS; CI 3613 passed). An expired token now produces one ops-log entry per 7 days instead of failing every ci-health run; proved by running the workflow step's own shell text against a stub `gh` answering the protection call with a 401 (exit 0, one warning, one ops-log annotation), a 403 and a 500 (both still exit 1). token-expiry.yml still pages the phone at most once a day. A real incident after a simulate run still pages exactly once (#3918's test, 11-16). Only one ci-health step uses the token (the branch-protection read); `token-expiry.yml` also uses it for its own probe. Prices, forecast and bot syncs do not use it (grep of `CI_HEALTH_PAT` in `.github`, `scripts`, `ml`: only those two workflows).

### 4. Closeout / count

Open floors: pyarrow #3924 (2026-11-26) and soupsieve #4062 (2026-11-27), one a day. Still to write: the "clean day" definition and its tracking line in the daily digest job summary. Count not started; engineering closes 2026-11-27 if nothing slips, the count then runs 2026-11-28..12-11, maintenance mode about 2026-12-12 (INFERRED).

### 5. Decisions left for GG (in `docs/FOR_GG.md`)

ADR 072 amendment after the ADR 076 afternoon pass (GG's decision, nothing promoted); optional causes for the -4.59% and -3.4% days; `CI_HEALTH_PAT` renewal by 2026-12-09.

### 6. Merged this session (UTC 2026-11-25)

#4082 (P0 moves test), #4083 (yfinance floor), #4084 (expired-token handling): 3 of 10; a verifier agent passed each before merge.
