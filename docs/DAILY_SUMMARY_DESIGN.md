# Daily Summary Notification Design

Session B.5 design document. Records decisions that shape ml/daily_summary.py
and .github/workflows/daily-summary.yml.

---

## Trigger thresholds

### Empirical sanity-check (data/prices.json, 31 days Apr 14 – May 14 2026)

Daily close = last reading per IST calendar date.

| Trigger | Historical fires | Historical rate | Forward state (as of May 14) |
|---|---|---|---|
| T1: ≥2% daily move | 2/30 | 7% | Normal; last fire May 13 (+5.5%) |
| T2: within ±50 of 30d-low | 9/31 | 29% | Dormant — 7.3% drop needed to retrigger |
| T3: within ±50 of 30d-high | 8/31 | 26% | Live — only ₹40 below the trigger band |
| T4: 5-day cumulative ≥3% | 2/26 | 8% | Currently active (May 13 + 14 both fire) |
| T5: first after 24h+ gap | 1 | — | Rare; once per scraper failure event |
| **Union all triggers** | **19/31** | **61%** | — |

**Why 61% is an artifact, not a calibration problem:**

T2 and T3 fire constantly in the first 30 days because the rolling window
is establishing its min/max range — every new low IS the 30-day low, trivially
within ₹50 of itself. This effect disappears once the window matures. Going
forward from May 14, the 30-day low (₹13,715) is ₹1,080 below current price;
T2 requires a 7.3% crash to retrigger. T3 is live (₹40 gap) but will settle
as the May 13 spike ages out of the 30-day window in mid-June.

**Realistic ongoing rate:** ~1–2 fires/week (dominated by T1 and occasional T3
near highs). Below the 3–4/week target, but empirical tuning after 2 weeks is
the right mechanism — not pre-emptive loosening against a noisy dataset.

**Threshold tuning note:** If T3 fires every other day for 2+ weeks (price
hovering near the 30-day high), tighten to ±30. Do NOT widen T2/T3 to ±150 —
that makes the historical rate 84% and adds noise with no signal gain at the
current volatility regime.

### Chosen thresholds (first-guess; revisit ~2026-05-28)

```python
PRICE_MOVE_PCT  = 0.02   # T1: ≥2% daily move
BAND_30D        = 50     # T2/T3: ±₹50 from 30-day low/high
FIVE_DAY_PCT    = 0.03   # T4: ≥3% 5-day cumulative
GAP_HOURS       = 24     # T5: first reading after ≥24h gap (detected as no
                         #     readings on yesterday's IST date)
```

---

## Decision 1 — Triggers (finalised above)

Five trigger rules. Any combination may fire simultaneously; all fired triggers
are reported in the notification. The LLM prompt receives the full list.

---

## Decision 2 — Previous day comparison basis

**Last reading whose IST calendar date = yesterday.**

IST = UTC + 5:30. Conversion is done explicitly in code (not as a "last 24h"
heuristic) so the boundary is correct for late-evening IST readings:
- A reading at 22:00 UTC = 03:30 IST next calendar day → counts as *tomorrow*
- A reading at 16:30 UTC = 22:00 IST → counts as *today*

The daily summary runs at 10:30 UTC (16:00 IST). At that point, the most recent
scraper runs were at 00:00 and 06:00 UTC (05:30 and 11:30 IST). Both are within
today's IST calendar date. "Yesterday's last reading" = last reading with IST
date = today_ist − 1 day, regardless of how many readings are on that day.

Edge case (no reading from yesterday — e.g. scraper was down): T5 fires;
use last available reading as contextual reference only, not as the comparison
basis for T1/T4.

---

## Decision 3 — 30-day window

Strict 30 calendar days. Not "last 30 readings."

At 4 readings/day, "last 30 readings" = ~7.5 days — wrong metric. Calendar days
match the user's intuitive notion of "last month."

Implementation: include all readings with IST date > (today_ist − 30 days) and
≤ today_ist. One close per IST day (latest reading for that day). Then take
min/max of the resulting daily closes.

---

## Decision 4 — LLM failure mode

**Option A: send with template fallback commentary.**

Template is a factual one-liner derived from which triggers fired, e.g.:
- `"Gold 22K +5.5% today at Rs.14,935 — at 30-day high."`
- `"Gold 22K near 30-day low of Rs.13,715 (currently Rs.13,740)."`

The ntfy notification is always sent if triggers fire, with or without LLM
commentary. Groq failure is logged but never suppresses the alert. The LLM
only adds a sentence of context — the trigger facts are the primary value.

---

## Decision 5 — Idempotency

**Marker key:** IST date only. If any notification was sent today (any triggers),
skip. No content hash — simplest possible duplicate prevention.

**Marker file:** `data/last_summary.json`, committed to the repo after each
send. Schema: `{"date": "2026-05-14", "triggers_fired": [...], "price_22k": N}`

**Write-after-send ordering:** The marker is written ONLY after a successful
ntfy send. If ntfy fails (exit 1), the marker is not written, so the next run
retries. If the marker is written but the commit/push fails (e.g., concurrent
bot push wins the race), the marker is lost and a manual re-run could re-send.

**Race condition mitigation (low probability):** `daily-summary.yml` uses the
same `git pull --rebase origin master` pattern as `check-price.yml` before
the push. This handles the common concurrent-push case. The remaining race
(workflow crashes between write and push) is accepted — probability is very low
with the rebase guard in place.

---

## Implementation

### ml/daily_summary.py

Core functions:
- `_ist_date(ts: datetime) -> date` — explicit IST conversion with comment
- `_latest_for_ist_date(prices, ist_date) -> dict | None` — last reading on a given IST day
- `_daily_closes_in_window(prices, end_ist_date, days) -> dict[date, int]` — one price per IST day
- `check_triggers(prices, now_utc) -> tuple[list[str], dict]` — evaluates all 5 triggers; returns fired list + context stats
- `_template_commentary(triggers, stats) -> str` — ASCII fallback text
- `_build_title(triggers, stats) -> str` — ASCII-only ntfy Title header
- `call_groq_summary(api_key, triggers, stats) -> str` — 1-2 sentence LLM commentary; raises on failure
- `send_ntfy(topic, title, body) -> bool` — posts to ntfy.sh, returns success
- `_is_already_sent(today_ist) -> bool` / `_mark_sent(...)` — idempotency via last_summary.json
- `main()` — orchestrates: load → idempotency → triggers → commentary → send → mark

Groq integration reused from ml/commentary.py (same endpoint, same model, shorter max_tokens=120).
ntfy title pattern reused from update-and-notify.js fmtHdr (ASCII-only, `Rs.XX,XXX` format).

### .github/workflows/daily-summary.yml

- Cron: `30 10 * * *` (10:30 UTC = 4:00 PM IST)
- `workflow_dispatch` for manual trigger / smoke test
- `continue-on-error: true` on the summary step; separate step checks outcome for ntfy alert
- Bot commit includes `data/last_summary.json`
- `git pull --rebase origin master` before push (same as check-price.yml)

### Tests: tests/test_daily_summary.py

Trigger tests against synthetic price series (parametrised). LLM call mocked
(mock requests.post). Template fallback tested directly. Idempotency test with
tmp_path fixture.
