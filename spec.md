# Spec — Batch Φ9A: Honesty Fixes (FIX-NOW — live overclaims to users)

**Date:** 2026-06-02
**Author:** External consultant (via GG) → Orchestrator (CC)
**Status:** Draft for orchestrator execution — HIGHEST PRIORITY
**Why now:** Two live regressions are currently telling users things ADR 019/020 disproved.

---

## Context

Diagnosis INV-1/INV-2 found two honesty regressions shipping in production:

- **INV-1:** `ml/commentary.py` SYSTEM_PROMPT (lines ~44-47) EXPLICITLY instructs the LLM to
  render the direction signal's hit-rate as "right about 6 times out of 10" from a 0.63 input,
  with NO base-rate context. ADR 019 established the direction signal (56% full / 63% last-30) is
  BELOW the ~70% bull-regime base rate — it does NOT beat "always guess up." So the commentary has
  been claiming a directional edge that does not exist. The prompt predates ADR 019 and was never
  updated (a norm #15 consumer-not-audited-after-pivot miss — the fifth instance of this class).
  Also: `forecast.chronos_companion.direction_prob_basis` is currently `None`, not
  `"base_rate_fallback"` as ADR 019 specified — the ADR's own remediation field was never wired.

- **INV-2:** `app.js` computeVerdict() (~lines 207-216) renders the hardcoded headline
  "Trending down — no rush to buy" (+ up/flat equivalents). The `reason` field is descriptive and
  fine; the "no rush to buy" CLAUSE is soft financial advice that presupposes prices won't reverse
  — a future-direction implication derived from past momentum. This is the exact move ADR 019/020
  removed from T1/T2, leaking back through the verdict card.

All discipline norms apply — especially honest-baseline (#4/ADR 005), consumer audit (#15),
ASCII-safe ntfy (#12), append-only PROGRESS (#10), flag-and-stop (#1).

---

## WI-Φ9A-1 — Strip directional hit-rate language from commentary

**Goal:** The "Today's read" commentary must NOT claim the directional lean has any accuracy edge,
because per ADR 019 it does not (below base rate).

**Changes (`ml/commentary.py`):**
1. **Remove from SYSTEM_PROMPT** the instruction (lines ~44-47) to describe direction reliability
   in fractions ("right about 6 times out of 10" / "tends to be right more often than not"). Remove
   the worked example entirely. The commentary may still DESCRIBE the recent trend (past-tense,
   descriptive — same discipline as T1/T2 momentum copy) but must say NOTHING about the lean's
   hit-rate or reliability.
2. **Remove the directional-lean forward claim** if it implies prediction. Acceptable: "prices have
   eased a little over the past week" (past, descriptive). NOT acceptable: "they look likely to edge
   up" presented as a reliable lean, or any "right X times out of 10" phrasing.
3. **Stop passing `Direction acc. (last 30 folds)` into the user message** (build_user_message
   ~lines 214-217) since no copy should reference it. Confirm removing it doesn't break the prompt
   assembly.
4. Keep `test_system_prompt_blocks_technical_jargon` green; the blocked-term list stays.

**Constraint:** This is honest-baseline (ADR 005/019). The commentary's job is a friendly plain-
language description of where prices are and where they've recently been — NOT a forecast, NOT a
confidence claim. If after removing the hit-rate language the commentary feels thin, that is
correct: we have no honest directional claim to add.

**Acceptance:** SYSTEM_PROMPT no longer instructs hit-rate/reliability language; no "X times out
of 10" can be produced; commentary is descriptive-only; jargon test green; lint green.

---

## WI-Φ9A-2 — Wire `direction_prob_basis` per ADR 019

**Goal:** The field ADR 019 specified actually gets written.

**Changes:** In the inference path that writes `forecast.chronos_companion`, set
`direction_prob_basis` to `"base_rate_fallback"` (ADR 019: no calibrated probability ships; the
basis is the base-rate fallback). It is currently `None`. Confirm the writer
(`ml/inference.py` companion-block assembly) and set it correctly.

**CONSUMER AUDIT (norm #15):** grep all readers of `direction_prob_basis` — app.js, commentary.py,
notifications.py, drift.py. app.js (Φ8C') already renders it faithfully ("base_rate_fallback" → no
probability shown); confirm it now receives the correct value instead of None and still renders
correctly. commentary.py must NOT start reading it to re-introduce a claim.

**Acceptance:** `direction_prob_basis: "base_rate_fallback"` in forecast.json chronos_companion;
app.js renders correctly with the real value; no consumer uses it to re-add a directional claim.

---

## WI-Φ9A-3 — Drop the advice clause from the verdict headline

**Goal:** The verdict card describes the trend; it does NOT advise on buying/waiting.

**Changes (`app.js` computeVerdict()):**
1. Replace "Trending down — no rush to buy" with a description-only headline: e.g. "Trending down
   this week" (and up/flat equivalents — "Trending up this week" / "Roughly flat this week").
2. Keep the `reason` field as-is — it's already descriptive and honest ("Prices have slipped Rs.X
   over the last 7 days...").
3. Remove ANY clause across all three verdict types (up/down/flat) that implies a future action is
   correct ("no rush to buy", "good time to buy", "wait", etc.). Past-tense description only.

**Constraint:** We are not financial advisors (and don't claim to be). The verdict is a plain trend
description, not a buy/sell signal.

**Acceptance:** no verdict headline or reason implies future direction or buy/sell action; all
three verdict types are descriptive past-tense; JS tests green.

---

## PR plan

Single PR — **PR-Φ9A** (honesty fixes). Small, high-priority. All three WIs together (they're one
coherent honesty correction).

## Acceptance gates

- `gh pr checks <N>` green incl lint (norm #2); strip `[skip ci]` (norm #13).
- Consumer audit (norm #15) on `direction_prob_basis`.
- Honest-baseline (ADR 005/019/020): no hit-rate claim, no advice clause, no implied forecast.
- Jargon test green; ASCII-safe if any ntfy-bound copy touched (norm #12).
- PROGRESS Decision Log appended referencing ADR 019 + the INV-1/INV-2 findings (norm #10).
- STOP before merge: show the consultant (a) the revised SYSTEM_PROMPT directional section and
  (b) the three revised verdict headlines + reasons, for copy review. Do not merge until approved.
