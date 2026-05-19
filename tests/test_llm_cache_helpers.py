"""Tests for ml/llm_cache_helpers.py."""

from __future__ import annotations

import pytest

from ml.llm_cache_helpers import (
    BatchCacheResult,
    GroqCacheEligibility,
    _ANTHROPIC_CACHE_TTL_SECONDS,
    _ANTHROPIC_MIN_CACHE_TOKENS,
    _GROQ_CACHE_TTL_SECONDS,
    _GROQ_MIN_CACHE_TOKENS,
    _MIN_CALLS_FOR_CACHE_BENEFIT,
    build_cached_system_prompt,
    estimate_groq_cache_eligibility,
    should_use_cache_for_batch,
)

# ---------------------------------------------------------------------------
# build_cached_system_prompt
# ---------------------------------------------------------------------------


class TestBuildCachedSystemPrompt:
    def test_build_cached_system_prompt_5m(self):
        block = build_cached_system_prompt("You are a gold price analyst.", ttl="5m")
        assert block["type"] == "text"
        assert block["cache_control"] == {"type": "ephemeral"}
        assert "ttl" not in block["cache_control"]
        assert "_ttl_seconds" not in block

    def test_build_cached_system_prompt_1h(self):
        block = build_cached_system_prompt("You are a gold price analyst.", ttl="1h")
        assert block["cache_control"]["type"] == "ephemeral"
        assert block["cache_control"]["ttl"] == "1h"

    def test_build_cached_system_prompt_invalid_ttl(self):
        with pytest.raises(ValueError, match="ttl must be"):
            build_cached_system_prompt("text", ttl="30m")

    def test_default_ttl_is_5m(self):
        block = build_cached_system_prompt("text")
        assert "ttl" not in block["cache_control"]

    def test_text_is_preserved(self):
        text = "System prompt with special chars: ₹, Rs., gold 22K."
        block = build_cached_system_prompt(text)
        assert block["text"] == text

    def test_empty_string_accepted(self):
        block = build_cached_system_prompt("")
        assert block["text"] == ""

    def test_cache_control_type_is_ephemeral(self):
        block = build_cached_system_prompt("x")
        assert block["cache_control"]["type"] == "ephemeral"


# ---------------------------------------------------------------------------
# estimate_groq_cache_eligibility
# ---------------------------------------------------------------------------


class TestEstimateGroqCacheEligibility:
    def test_below_minimum_not_eligible(self):
        result = estimate_groq_cache_eligibility(_GROQ_MIN_CACHE_TOKENS - 1)
        assert isinstance(result, GroqCacheEligibility)
        assert result.eligible is False
        assert str(_GROQ_MIN_CACHE_TOKENS - 1) in result.reason

    def test_at_minimum_eligible(self):
        result = estimate_groq_cache_eligibility(_GROQ_MIN_CACHE_TOKENS)
        assert result.eligible is True

    def test_above_minimum_eligible(self):
        result = estimate_groq_cache_eligibility(_GROQ_MIN_CACHE_TOKENS + 500)
        assert result.eligible is True

    def test_zero_tokens_not_eligible(self):
        result = estimate_groq_cache_eligibility(0)
        assert result.eligible is False

    def test_reason_mentions_minimum(self):
        result = estimate_groq_cache_eligibility(200)
        assert str(_GROQ_MIN_CACHE_TOKENS) in result.reason

    def test_min_tokens_field_populated(self):
        result = estimate_groq_cache_eligibility(500)
        assert result.min_tokens_required == _GROQ_MIN_CACHE_TOKENS

    def test_eligible_reason_mentions_token_count(self):
        result = estimate_groq_cache_eligibility(2000)
        assert "2000" in result.reason


# ---------------------------------------------------------------------------
# should_use_cache_for_batch — five boundary cases
# ---------------------------------------------------------------------------

_GROQ_MODEL = "llama-3.3-70b-versatile"
_CLAUDE_MODEL = "claude-opus-4-7-20251101"


class TestShouldUseCache:
    # Boundary case 1: prompt exactly at token minimum (Groq) → eligible on tokens alone
    def test_boundary_at_token_minimum_groq(self):
        result = should_use_cache_for_batch(
            n_planned_calls=10,
            estimated_call_duration_seconds=1.0,
            prompt_tokens=_GROQ_MIN_CACHE_TOKENS,
            model=_GROQ_MODEL,
        )
        assert result.decision == "use_cache", result.reason

    # Boundary case 2: prompt one token below minimum → branch 1 fires
    def test_boundary_one_below_token_minimum(self):
        result = should_use_cache_for_batch(
            n_planned_calls=100,
            estimated_call_duration_seconds=1.0,
            prompt_tokens=_GROQ_MIN_CACHE_TOKENS - 1,
            model=_GROQ_MODEL,
        )
        assert result.decision == "skip_cache"
        assert "Below minimum" in result.reason

    # Boundary case 3: single call in batch (n_planned_calls=1) → branch 2 fires
    def test_boundary_single_call_batch(self):
        result = should_use_cache_for_batch(
            n_planned_calls=1,
            estimated_call_duration_seconds=2.0,
            prompt_tokens=_GROQ_MIN_CACHE_TOKENS + 500,
            model=_GROQ_MODEL,
        )
        assert result.decision == "skip_cache"
        assert "Too few calls" in result.reason

    # Boundary case 4: batch duration exactly equals TTL → should pass (≤ not <)
    def test_boundary_batch_duration_equals_ttl(self):
        # duration == TTL exactly: batch_duration > max_ttl is False → use_cache
        calls = 10
        duration_each = _GROQ_CACHE_TTL_SECONDS / calls  # exactly fills the TTL window
        result = should_use_cache_for_batch(
            n_planned_calls=calls,
            estimated_call_duration_seconds=duration_each,
            prompt_tokens=_GROQ_MIN_CACHE_TOKENS,
            model=_GROQ_MODEL,
        )
        assert result.decision == "use_cache", result.reason

    # Boundary case 5: batch duration one second over TTL → branch 3 fires
    def test_boundary_batch_duration_one_second_over_ttl(self):
        calls = 10
        duration_each = (_GROQ_CACHE_TTL_SECONDS + 10) / calls  # pushes total just over TTL
        result = should_use_cache_for_batch(
            n_planned_calls=calls,
            estimated_call_duration_seconds=duration_each,
            prompt_tokens=_GROQ_MIN_CACHE_TOKENS,
            model=_GROQ_MODEL,
        )
        assert result.decision == "skip_cache"
        assert "exceeds max TTL" in result.reason

    # ---------------------------------------------------------------------------
    # Provider routing: Anthropic vs Groq TTL/minimum
    # ---------------------------------------------------------------------------

    def test_claude_model_uses_anthropic_ttl(self):
        # Batch fits within Groq TTL (3600s) but not Anthropic TTL (300s)
        calls = 10
        duration_each = 40.0  # 10 × 40 = 400s  > Anthropic 300s TTL, < Groq 3600s
        result = should_use_cache_for_batch(
            n_planned_calls=calls,
            estimated_call_duration_seconds=duration_each,
            prompt_tokens=_ANTHROPIC_MIN_CACHE_TOKENS,
            model=_CLAUDE_MODEL,
        )
        assert result.decision == "skip_cache"
        assert "exceeds max TTL" in result.reason

    def test_groq_model_uses_groq_ttl(self):
        # Same batch: fits within Groq TTL, should be eligible
        calls = 10
        duration_each = 40.0  # 10 × 40 = 400s  < Groq 3600s TTL
        result = should_use_cache_for_batch(
            n_planned_calls=calls,
            estimated_call_duration_seconds=duration_each,
            prompt_tokens=_GROQ_MIN_CACHE_TOKENS,
            model=_GROQ_MODEL,
        )
        assert result.decision == "use_cache", result.reason

    def test_eligible_result_has_positive_savings(self):
        result = should_use_cache_for_batch(
            n_planned_calls=50,
            estimated_call_duration_seconds=1.0,
            prompt_tokens=2000,
            model=_GROQ_MODEL,
        )
        assert result.decision == "use_cache"
        assert result.estimated_savings_pct > 0

    def test_claude_eligible_savings_higher_than_groq(self):
        kwargs = dict(n_planned_calls=5, estimated_call_duration_seconds=1.0, prompt_tokens=2000)
        claude_result = should_use_cache_for_batch(model=_CLAUDE_MODEL, **kwargs)
        groq_result = should_use_cache_for_batch(model=_GROQ_MODEL, **kwargs)
        assert claude_result.decision == "use_cache"
        assert groq_result.decision == "use_cache"
        assert claude_result.estimated_savings_pct > groq_result.estimated_savings_pct

    def test_skip_result_has_zero_savings(self):
        result = should_use_cache_for_batch(
            n_planned_calls=1,
            estimated_call_duration_seconds=1.0,
            prompt_tokens=500,
            model=_GROQ_MODEL,
        )
        assert result.decision == "skip_cache"
        assert result.estimated_savings_pct == 0.0

    # ---------------------------------------------------------------------------
    # Return type
    # ---------------------------------------------------------------------------

    def test_returns_batch_cache_result(self):
        result = should_use_cache_for_batch(
            n_planned_calls=5,
            estimated_call_duration_seconds=2.0,
            prompt_tokens=1500,
            model=_GROQ_MODEL,
        )
        assert isinstance(result, BatchCacheResult)
        assert result.decision in ("use_cache", "skip_cache")
        assert isinstance(result.reason, str) and result.reason
