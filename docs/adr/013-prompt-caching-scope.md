# ADR 013 — Prompt Caching Scope: Do Not Apply to Live LLM Path

> **`ml/commentary.py` retired 2026-08-10.** The `call_groq()` call site this ADR analyzes
> no longer exists — "Today's read" in the PWA moved to a deterministic client-side
> synthesis with no LLM call at all, once the Groq blurb's only consumer (`app.js`'s old
> `renderCommentary()`) was removed. Kept as a historical record of the caching-scope
> reasoning below; the call site itself is gone.

**Status:** Accepted — we have decided NOT to use Anthropic prompt caching at this time.
**Author:** Gaurav Gandhi / CC
**Date:** 2026-05-19

---

## Context

Phase 3 PR G introduced two active LLM call sites:

| Call site | File | Provider | Prompt tokens (est.) | Cadence |
|-----------|------|----------|----------------------|---------|
| `call_groq()` | `ml/commentary.py` | Groq / llama-3.3-70b-versatile | ~280 | Every 6 hours |
| `call_groq_summary()` | `ml/daily_summary.py` | Groq / llama-3.3-70b-versatile | ~150 | Daily (DEPRECATED) |

This ADR evaluates whether Anthropic prompt caching (or Groq's automatic prefix KV-cache)
should be applied to either site, and documents the decision.

---

## Failure Criteria

Three independent criteria must all pass for caching to deliver meaningful savings.
Both live call sites fail at least two.

### Criterion 1 — Provider

Anthropic prompt caching requires the Anthropic Messages API (`cache_control` blocks).
Both call sites use **Groq**, not Anthropic. Anthropic prompt caching does not apply.

Groq provides *automatic* prefix KV-caching — no `cache_control` syntax exists or is
needed. Groq's cache activates when criteria 2 and 3 are met. Since both fail, the
automatic cache does not engage.

### Criterion 2 — Prompt size (minimum 1024 tokens)

Both Anthropic and Groq require ≥1024 prompt tokens in the cached block before any
caching takes effect.

| Call site | System prompt tokens | User message tokens | Total |
|-----------|---------------------|---------------------|-------|
| `call_groq()` | ~80 | ~200 | ~280 |
| `call_groq_summary()` | ~55 | ~95 | ~150 |

Both are well below 1024 tokens. **No caching would activate even if the provider were
correct.**

### Criterion 3 — Cadence vs cache TTL

| Provider | Cache TTL |
|----------|-----------|
| Anthropic | 5 minutes (300 s) |
| Groq (automatic) | ~1 hour (3600 s) |

`call_groq()` runs every **6 hours** — 72× longer than the Anthropic TTL, 6× longer than
the Groq TTL. By the time the next call arrives, any cache entry has long expired.

`call_groq_summary()` runs **daily** — even further outside any TTL window.

---

## Decision

**Do not apply prompt caching to the live Groq path.**

All three failure criteria apply to both call sites. Applying `cache_control` syntax to
Groq payloads would be a no-op (Groq ignores it). Growing prompts to ≥1024 tokens solely
to unlock caching would add token cost without value.

**Prepared infrastructure shipped as forward-looking code** in `ml/llm_cache_helpers.py`:

- `build_cached_system_prompt(text, ttl)` — Anthropic-formatted block with `cache_control`;
  ready to wire into any future Claude API call.
- `estimate_groq_cache_eligibility(prompt_tokens)` — encodes the 1024-token gate so future
  call sites can self-document their eligibility.
- `should_use_cache_for_batch(n_planned_calls, estimated_call_duration_seconds, prompt_tokens, model)` —
  decision gate for batch backfill paths; three failure-reason branches make the no-cache
  decision explicit rather than implicit.

These helpers are exercised by `tests/test_llm_cache_helpers.py` and will remain honest
as token counts and providers evolve.

---

## Future Re-evaluation Triggers

Revisit this ADR if any of the following occur:

1. **Claude migration** — if the live path moves from Groq to a Claude model (Anthropic API),
   Criterion 1 flips. Criteria 2 and 3 still need to pass — grow the system prompt and
   increase call frequency before enabling.

2. **Batch backfill path** — Phase 4 may introduce a bulk IBJA historical commentary
   generation job. A batch of N calls with a stable system prompt can satisfy Criterion 3
   if `N × call_duration_seconds ≤ cache_TTL`. Use `should_use_cache_for_batch()` to
   evaluate before wiring.

3. **Prompt size growth** — if the system prompt grows to ≥1024 tokens (e.g. via
   few-shot examples or extended context), Criterion 2 flips for Groq. Criterion 3 still
   applies — a 6-hour cadence will not benefit from a 1-hour TTL cache.

---

## Consequences

**Positive:**
- No unjustified SDK dependency (Anthropic SDK not added to `ml/requirements.txt`).
- No `cache_control` blocks in Groq payloads (would be silently ignored; adds noise).
- Helper infrastructure is tested and ready; no rework needed when a trigger fires.
- Both call sites carry docstrings referencing this ADR, so future maintainers have
  immediate context at the call site.

**Negative:**
- If the 6-hour commentary cadence is ever shortened to <1 hour and prompts grow past
  1024 tokens, savings are available but require a deliberate re-evaluation step.
  This ADR's trigger list covers that case.

---

## Alternatives Considered

**Alt 1: Migrate live path to Claude now for caching access.**
Rejected: the commentary path works correctly on Groq. Migrating solely for caching
when prompts are below 1024 tokens would add cost (Claude token prices > Groq) without
savings. Migrate on merit — latency, quality, features — not for caching.

**Alt 2: Pad system prompts to ≥1024 tokens with few-shot examples.**
Rejected: padding for infrastructure reasons rather than quality reasons violates the
"no cost without benefit" principle. Few-shot examples should earn their place on
commentary quality grounds.

**Alt 3: No infrastructure, revisit entirely when triggers fire.**
Rejected: `ml/llm_cache_helpers.py` is small (~120 lines), tested, and encodes the
failure criteria as executable code. It costs nothing to ship and prevents future
maintainers from rediscovering the same analysis.
