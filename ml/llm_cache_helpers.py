"""
llm_cache_helpers.py — Infrastructure for Anthropic prompt caching and Groq KV-cache
eligibility checks.

WHY THIS EXISTS
---------------
The live commentary and daily-summary paths run on Groq (llama-3.3-70b-versatile) at a
6-hour cadence.  Neither qualifies for caching today — see ADR 013
(docs/adr/013-prompt-caching-scope.md) for the full analysis.

These helpers are forward-looking infrastructure:
  - build_cached_system_prompt  : Anthropic-formatted payload for when we migrate to a
                                  Claude model or add a batch backfill path.
  - estimate_groq_cache_eligibility : Documents the Groq automatic KV-cache criteria so
                                  future maintainers know why a call site is or isn't
                                  expected to benefit.
  - should_use_cache_for_batch  : Decision gate for any future batch path.  Three
                                  failure-reason branches make the no-cache decision
                                  explicit rather than implicit.

None of these functions are called by the live inference path.  They are imported and
exercised by tests/test_llm_cache_helpers.py so the logic stays honest as constants
evolve.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Anthropic prompt caching: minimum cacheable block size (tokens).
# Source: Anthropic docs — "cache_control requires ≥1024 tokens in the cached block."
_ANTHROPIC_MIN_CACHE_TOKENS: int = 1_024

# Groq automatic KV-cache: minimum prompt size that Groq caches automatically.
# Source: Groq docs — prefix caching activates at ≥1024 tokens.
_GROQ_MIN_CACHE_TOKENS: int = 1_024

# Anthropic prompt cache TTL — used by should_use_cache_for_batch batch-duration gate.
# "5m" default maps to 300s; "1h" extended TTL maps to 3600s.
# Source: Anthropic docs — two supported TTL tiers for cache_control.
_ANTHROPIC_CACHE_TTL_SECONDS: int = 300  # 5-minute default tier

# Groq automatic KV-cache TTL (seconds).
# Source: Groq docs — "prefix caching has a ~1-hour TTL."
_GROQ_CACHE_TTL_SECONDS: int = 3_600  # 1 hour

# Minimum number of calls in a batch to break even on caching overhead.
# Caching saves ~90% on cached-read tokens but the first call pays full price.
# Two or more calls amortise the write cost.
_MIN_CALLS_FOR_CACHE_BENEFIT: int = 2


# ---------------------------------------------------------------------------
# Helper 1 — Anthropic-formatted cached system prompt
# ---------------------------------------------------------------------------


def build_cached_system_prompt(text: str, ttl: str = "5m") -> dict:
    """Return an Anthropic messages-API system prompt block with cache_control.

    ttl: "5m" (default, also the API default when omitted) or "1h" (extended cache,
    2x base input price per the Anthropic docs).

    Returns a single content block dict suitable for inclusion in the system list::

        {"type": "text", "text": <text>, "cache_control": {"type": "ephemeral"}}

    For "1h" TTL the cache_control includes ``{"ttl": "1h"}``.

    Caller wraps in a list for the API: ``system=[build_cached_system_prompt(...)]``.

    Example (Claude SDK)::

        import anthropic
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model="claude-opus-4-7-20251101",
            system=[build_cached_system_prompt(SYSTEM_PROMPT)],
            messages=[{"role": "user", "content": user_msg}],
        )

    See docs/adr/013-prompt-caching-scope.md for why this is not wired into the
    live Groq path.
    """
    if ttl not in ("5m", "1h"):
        raise ValueError(f"ttl must be '5m' or '1h', got {ttl!r}")
    cache_control: dict = {"type": "ephemeral"}
    if ttl == "1h":
        cache_control["ttl"] = "1h"
    return {"type": "text", "text": text, "cache_control": cache_control}


# ---------------------------------------------------------------------------
# Helper 2 — Groq KV-cache eligibility check
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GroqCacheEligibility:
    eligible: bool
    reason: str
    min_tokens_required: int = _GROQ_MIN_CACHE_TOKENS


def estimate_groq_cache_eligibility(prompt_tokens: int) -> GroqCacheEligibility:
    """Report whether a Groq call site is expected to benefit from Groq's automatic
    prefix KV-cache.

    Groq caches prompt prefixes automatically — no client-side ``cache_control`` needed.
    The cache activates when:
      1. The prompt is ≥1024 tokens (hard minimum).
      2. Repeated calls share an identical prefix (the system prompt is the stable anchor).

    This function encodes the minimum-token gate only.  The prefix-stability requirement
    is a structural property of the call site and cannot be evaluated from token count
    alone — callers should document this separately.

    Args:
        prompt_tokens: Estimated total prompt tokens for the call (system + user combined).

    Returns:
        GroqCacheEligibility with ``eligible=True`` when the token gate passes.

    Example::

        eligibility = estimate_groq_cache_eligibility(prompt_tokens=350)
        # eligibility.eligible == False
        # eligibility.reason  == "Prompt too short (350 tokens < 1024 minimum)"

    See docs/adr/013-prompt-caching-scope.md — both live Groq call sites currently
    produce prompts well below 1024 tokens.
    """
    if prompt_tokens >= _GROQ_MIN_CACHE_TOKENS:
        return GroqCacheEligibility(
            eligible=True,
            reason=f"Prompt meets minimum ({prompt_tokens} tokens ≥ {_GROQ_MIN_CACHE_TOKENS})",
        )
    return GroqCacheEligibility(
        eligible=False,
        reason=f"Prompt too short ({prompt_tokens} tokens < {_GROQ_MIN_CACHE_TOKENS} minimum)",
    )


# ---------------------------------------------------------------------------
# Helper 3 — Batch-path cache decision gate
# ---------------------------------------------------------------------------

CacheDecision = Literal["use_cache", "skip_cache"]


@dataclass(frozen=True)
class BatchCacheResult:
    decision: CacheDecision
    reason: str
    # Populated only when decision == "use_cache"
    estimated_savings_pct: float = 0.0


def should_use_cache_for_batch(
    n_planned_calls: int,
    estimated_call_duration_seconds: float,
    prompt_tokens: int,
    model: str,
) -> BatchCacheResult:
    """Decide whether to enable prompt caching for a batch LLM workload.

    Applies three failure branches in order — the first failing branch returns
    immediately with an explanatory reason string.

    Failure branches
    ----------------
    1. **Below minimum** — prompt is below the provider's minimum cacheable token count.
       The cache cannot activate regardless of batch size.
    2. **Too few calls** — fewer than two calls in the batch means the cache write cost
       is never amortised.  Single-call "batches" should always skip caching.
    3. **Batch exceeds max TTL** — if ``n_planned_calls × estimated_call_duration_seconds``
       exceeds the cache TTL, later calls in the batch will miss the cache.  This is not
       a hard block (some early calls still benefit) but is flagged as a warning so
       callers can decide whether partial savings justify the complexity.

    Provider routing
    ----------------
    - Models containing ``"claude"`` → Anthropic TTL (5 min / 300 s).
    - All other models → Groq TTL (1 hour / 3600 s), which is the default for
      ``llama-3.3-70b-versatile`` and similar Groq-hosted models.

    Args:
        n_planned_calls:              Number of LLM calls in the batch.
        estimated_call_duration_seconds: Estimated wall-clock time per call.
        prompt_tokens:                Estimated prompt token count (system + user).
        model:                        Model identifier string (e.g. ``"claude-opus-4-7-20251101"``
                                      or ``"llama-3.3-70b-versatile"``).

    Returns:
        BatchCacheResult with ``decision`` of ``"use_cache"`` or ``"skip_cache"`` and a
        ``reason`` explaining which branch fired.

    Example::

        result = should_use_cache_for_batch(
            n_planned_calls=200,
            estimated_call_duration_seconds=2.0,
            prompt_tokens=1500,
            model="claude-opus-4-7-20251101",
        )
        # result.decision == "skip_cache"
        # result.reason contains "exceeds max TTL"

    See docs/adr/013-prompt-caching-scope.md for the full caching-scope analysis.
    """
    # Branch 1: below minimum token count
    min_tokens = (
        _ANTHROPIC_MIN_CACHE_TOKENS if "claude" in model.lower() else _GROQ_MIN_CACHE_TOKENS
    )
    if prompt_tokens < min_tokens:
        return BatchCacheResult(
            decision="skip_cache",
            reason=(
                f"Below minimum: {prompt_tokens} prompt tokens < {min_tokens} required "
                f"for {model!r} cache activation."
            ),
        )

    # Branch 2: too few calls to amortise the cache write
    if n_planned_calls < _MIN_CALLS_FOR_CACHE_BENEFIT:
        return BatchCacheResult(
            decision="skip_cache",
            reason=(
                f"Too few calls: {n_planned_calls} call(s) < {_MIN_CALLS_FOR_CACHE_BENEFIT} "
                "minimum to amortise cache write cost."
            ),
        )

    # Branch 3: batch duration exceeds cache TTL
    max_ttl = _ANTHROPIC_CACHE_TTL_SECONDS if "claude" in model.lower() else _GROQ_CACHE_TTL_SECONDS
    batch_duration = n_planned_calls * estimated_call_duration_seconds
    if batch_duration > max_ttl:
        return BatchCacheResult(
            decision="skip_cache",
            reason=(
                f"Batch exceeds max TTL: {batch_duration:.0f}s estimated duration "
                f"> {max_ttl}s {model!r} cache TTL. "
                "Later calls in the batch will miss the cache."
            ),
        )

    # All gates passed — estimate savings (cached-read tokens are ~90% cheaper on Anthropic;
    # Groq automatic caching discount varies but ~50% is a conservative lower bound).
    savings_pct = 90.0 if "claude" in model.lower() else 50.0
    return BatchCacheResult(
        decision="use_cache",
        reason=(
            f"Eligible: {prompt_tokens} tokens ≥ {min_tokens} minimum, "
            f"{n_planned_calls} calls, "
            f"batch duration {batch_duration:.0f}s ≤ {max_ttl}s TTL."
        ),
        estimated_savings_pct=savings_pct,
    )
