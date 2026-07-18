"""
Unit tests for ml/commentary.py.

Tests:
  - build_user_message produces a non-empty structured string
  - append_commentary adds an entry with correct schema {ts, text, model, prompt_hash}
  - The rolling list is capped at MAX_COMMENTARY_ENTRIES
  - call_groq sends the right payload (mocked)
  - main() works end-to-end with mocked Groq (mocked)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from ml.commentary import (
    MAX_COMMENTARY_ENTRIES,
    SYSTEM_PROMPT,
    _violates_content_policy,
    append_commentary,
    build_user_message,
    call_groq,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_PRICES = [
    {"timestamp": "2026-04-01T00:00:00.000Z", "22k": 7100, "24k": 7745, "18k": 5325},
    {"timestamp": "2026-04-08T00:00:00.000Z", "22k": 7150, "24k": 7800, "18k": 5362},
    {"timestamp": "2026-04-15T00:00:00.000Z", "22k": 7050, "24k": 7691, "18k": 5287},
    {"timestamp": "2026-05-01T00:00:00.000Z", "22k": 7200, "24k": 7854, "18k": 5400},
    {"timestamp": "2026-05-08T00:00:00.000Z", "22k": 7180, "24k": 7833, "18k": 5385},
]

SAMPLE_FORECAST = {
    "predicted_at": "2026-05-09T00:00:00.000Z",
    "target_time": "2026-05-09T06:00:00.000Z",
    "predicted_22k": 7190,
    "lower": 7100,
    "upper": 7280,
    "model_version": "lgbm-v1-abc12345",
    "training_rows": 312,
}

SAMPLE_FORECAST_WITH_COMPANION = {
    **SAMPLE_FORECAST,
    "chronos_companion": {
        "status": "success",
        "lean_direction": "up",
        "lean_strength_pct": 2.645,
        "direction_acc_30f": 0.633,
        "majority_direction": "up",
        "direction_consensus": 1.0,
    },
}

SAMPLE_FORECAST_WITH_FAILED_COMPANION = {
    **SAMPLE_FORECAST,
    "chronos_companion": {
        "status": "failed",
    },
}

SAMPLE_BACKTEST = {
    "generated_at": "2026-05-04T02:00:00.000Z",
    "backtest_days": 90,
    "folds": 83,
    "model": {"mae": 142.5, "mape": 1.98, "direction_acc": 0.53},
    "baseline": {"mae": 135.0, "mape": 1.88, "direction_acc": 0.51},
}

MOCK_NOTE = (
    "Gold prices are steady near ₹7,180 per gram, about where they've been over the past week. "
    "Nothing unusual to report today."
)


# ---------------------------------------------------------------------------
# build_user_message
# ---------------------------------------------------------------------------


class TestBuildUserMessage:
    def test_returns_non_empty_string(self):
        msg = build_user_message(SAMPLE_PRICES, SAMPLE_PRICES, SAMPLE_FORECAST, SAMPLE_BACKTEST)
        assert isinstance(msg, str)
        assert len(msg) > 50

    def test_contains_latest_price(self):
        msg = build_user_message(SAMPLE_PRICES, SAMPLE_PRICES, SAMPLE_FORECAST, SAMPLE_BACKTEST)
        assert "7180" in msg or "7,180" in msg

    def test_contains_forecast_price(self):
        msg = build_user_message(SAMPLE_PRICES, SAMPLE_PRICES, SAMPLE_FORECAST, SAMPLE_BACKTEST)
        # Naive baseline value is still present (honest framing)
        assert "7190" in msg or "7,190" in msg
        # Must NOT use the old misleading "Point estimate" label
        assert "Point estimate" not in msg
        # Must use honest flat-hold framing
        assert "Naive baseline" in msg

    def test_contains_chronos_directional_signal_when_present(self):
        """Chronos companion fields reach the prompt when status=success.

        direction_consensus is no longer included in the user message (ADR 020:
        field is a constant 1.0 and carries no information).
        """
        msg = build_user_message(
            SAMPLE_PRICES, SAMPLE_PRICES, SAMPLE_FORECAST_WITH_COMPANION, SAMPLE_BACKTEST
        )
        assert "directional_signal_available: true" in msg
        assert "5/5" not in msg  # consensus no longer sent to LLM (ADR 020)
        assert "up" in msg  # lean_direction
        assert (
            "Direction acc. (last 30 folds)" not in msg
        )  # Φ9A: Chronos hit-rate removed (ADR 019)

    def test_skips_directional_when_probe_failed(self):
        """When companion status != success, directional fields are N/A; no lean fabricated."""
        msg = build_user_message(
            SAMPLE_PRICES, SAMPLE_PRICES, SAMPLE_FORECAST_WITH_FAILED_COMPANION, SAMPLE_BACKTEST
        )
        assert "directional_signal_available: false" in msg
        assert "directional_signal_available: true" not in msg

    def test_contains_interval(self):
        msg = build_user_message(SAMPLE_PRICES, SAMPLE_PRICES, SAMPLE_FORECAST, SAMPLE_BACKTEST)
        assert "7100" in msg and "7280" in msg

    def test_contains_backtest_mae(self):
        msg = build_user_message(SAMPLE_PRICES, SAMPLE_PRICES, SAMPLE_FORECAST, SAMPLE_BACKTEST)
        assert "142.5" in msg

    def test_no_forecast_graceful(self):
        msg = build_user_message(SAMPLE_PRICES, SAMPLE_PRICES, None, None)
        assert isinstance(msg, str)
        assert len(msg) > 20

    def test_empty_prices_graceful(self):
        msg = build_user_message([], [], SAMPLE_FORECAST, None)
        assert isinstance(msg, str)

    def test_insufficient_real_data_marks_stats_unavailable(self):
        """With < 4 real readings, short-term stats should be marked as insufficient."""
        few_real = SAMPLE_PRICES[:2]  # only 2 real readings
        msg = build_user_message(few_real, SAMPLE_PRICES, SAMPLE_FORECAST, SAMPLE_BACKTEST)
        assert "sufficient_for_short_term_stats: false" in msg
        assert "N/A" in msg  # delta should be N/A


# ---------------------------------------------------------------------------
# append_commentary
# ---------------------------------------------------------------------------


class TestAppendCommentary:
    def _make_entry(self, text: str = MOCK_NOTE, idx: int = 0) -> dict:
        return {
            "ts": f"2026-05-0{idx + 1}T00:00:00Z",
            "text": text,
            "model": "llama-3.3-70b-versatile",
            "prompt_hash": f"abc{idx:09d}",
        }

    def test_creates_file_if_missing(self, tmp_path, monkeypatch):
        import ml.commentary as comm

        monkeypatch.setattr(comm, "DATA_DIR", tmp_path)
        entry = self._make_entry()
        append_commentary(entry)
        out = json.loads((tmp_path / "commentary.json").read_text())
        assert len(out) == 1
        assert out[0]["text"] == MOCK_NOTE

    def test_appends_to_existing(self, tmp_path, monkeypatch):
        import ml.commentary as comm

        monkeypatch.setattr(comm, "DATA_DIR", tmp_path)
        (tmp_path / "commentary.json").write_text(json.dumps([self._make_entry(idx=0)]))
        append_commentary(self._make_entry(text="Second note", idx=1))
        out = json.loads((tmp_path / "commentary.json").read_text())
        assert len(out) == 2
        assert out[-1]["text"] == "Second note"

    def test_rolling_capped_at_max(self, tmp_path, monkeypatch):
        import ml.commentary as comm

        monkeypatch.setattr(comm, "DATA_DIR", tmp_path)
        # Pre-fill with MAX entries
        existing = [self._make_entry(idx=i) for i in range(MAX_COMMENTARY_ENTRIES)]
        (tmp_path / "commentary.json").write_text(json.dumps(existing))
        append_commentary(self._make_entry(text="overflow entry", idx=99))
        out = json.loads((tmp_path / "commentary.json").read_text())
        assert len(out) == MAX_COMMENTARY_ENTRIES
        assert out[-1]["text"] == "overflow entry"

    def test_schema_has_required_keys(self, tmp_path, monkeypatch):
        import ml.commentary as comm

        monkeypatch.setattr(comm, "DATA_DIR", tmp_path)
        entry = {
            "ts": "2026-05-09T12:00:00Z",
            "text": MOCK_NOTE,
            "model": "llama-3.3-70b-versatile",
            "prompt_hash": "deadbeef1234",
        }
        append_commentary(entry)
        out = json.loads((tmp_path / "commentary.json").read_text())
        for key in ("ts", "text", "model", "prompt_hash"):
            assert key in out[0], f"Missing key '{key}' in commentary entry"


# ---------------------------------------------------------------------------
# SYSTEM_PROMPT — jargon blocklist
# ---------------------------------------------------------------------------


def test_system_prompt_blocks_technical_jargon():
    """SYSTEM_PROMPT must explicitly forbid all ML/stats jargon terms that could
    surface in T4's commentary snippet (which goes to non-technical family subscribers).
    """
    required = ["Chronos", "model", "baseline", "naive", "MAE", "backtest", "folds", "Wilcoxon"]
    for term in required:
        assert term in SYSTEM_PROMPT, (
            f"SYSTEM_PROMPT is missing '{term}' in its NEVER USE list — "
            "add it to prevent jargon reaching family subscribers via T4 weekly digest"
        )


# ---------------------------------------------------------------------------
# call_groq (mocked)
# ---------------------------------------------------------------------------


class TestCallGroq:
    @patch("ml.commentary.requests.post")
    def test_returns_note_text(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": MOCK_NOTE}}]}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = call_groq("fake-key", "some data")
        assert result == MOCK_NOTE

    @patch("ml.commentary.requests.post")
    def test_sends_correct_model(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": "x"}}]}
        mock_post.return_value = mock_resp

        call_groq("k", "msg")
        payload = mock_post.call_args[1]["json"]
        assert payload["model"] == "llama-3.3-70b-versatile"
        assert payload["temperature"] == 0.3
        assert payload["max_tokens"] == 200

    @patch("ml.commentary.requests.post")
    def test_sends_system_prompt(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": "x"}}]}
        mock_post.return_value = mock_resp

        call_groq("k", "user data")
        payload = mock_post.call_args[1]["json"]
        messages = payload["messages"]
        system_msgs = [m for m in messages if m["role"] == "system"]
        assert len(system_msgs) == 1
        assert "buy/sell" in system_msgs[0]["content"]


# ---------------------------------------------------------------------------
# _violates_content_policy — output-side guard (audit fix)
# ---------------------------------------------------------------------------


class TestViolatesContentPolicy:
    def test_clean_compliant_text_passes(self):
        """A clean note using only the allowed past-tense direction framing passes."""
        text = (
            "Gold prices have eased a little over the past week, now near ₹7,180 per gram. "
            "That's around what it's been lately."
        )
        assert _violates_content_policy(text, directional_signal_available=True) is None

    def test_clean_text_with_no_signal_passes(self):
        """A clean note with no directional language passes when the signal is unavailable."""
        text = "Gold is trading near ₹7,180 per gram today, about where it's been lately."
        assert _violates_content_policy(text, directional_signal_available=False) is None

    def test_banned_word_detected(self):
        """A generation that leaks a banned jargon word is flagged, whatever else it says."""
        text = "Gold is steady today. Based on the model's bullish outlook, prices held firm."
        reason = _violates_content_policy(text, directional_signal_available=True)
        assert reason is not None
        assert "banned word" in reason

    def test_forward_looking_language_detected(self):
        """Forward-looking modal + direction verb combos are exactly what the prompt bans."""
        text = "Gold may keep climbing toward festival season, buyers should take note."
        reason = _violates_content_policy(text, directional_signal_available=True)
        assert reason is not None
        assert "forward-looking" in reason

    def test_directional_claim_while_signal_unavailable_detected(self):
        """DARK-gate contradiction: any directional claim when the signal isn't earned yet,
        even plain past-tense language that would otherwise be allowed.
        """
        text = "Gold prices have risen a little this week, now near ₹7,180 per gram."
        reason = _violates_content_policy(text, directional_signal_available=False)
        assert reason is not None
        assert "directional_signal_available=False" in reason

    def test_directional_claim_allowed_when_signal_available(self):
        """The same past-tense phrasing is fine once the signal is available."""
        text = "Gold prices have risen a little this week, now near ₹7,180 per gram."
        assert _violates_content_policy(text, directional_signal_available=True) is None


# ---------------------------------------------------------------------------
# End-to-end main() (fully mocked)
# ---------------------------------------------------------------------------


class TestMain:
    @patch("ml.commentary.requests.post")
    def test_main_appends_entry(self, mock_post, tmp_path, monkeypatch):
        import ml.commentary as comm

        monkeypatch.setattr(comm, "DATA_DIR", tmp_path)
        monkeypatch.setenv("GROQ_API_KEY", "test-key")

        # Write fixtures into tmp_path
        (tmp_path / "prices.json").write_text(json.dumps(SAMPLE_PRICES))
        (tmp_path / "forecast.json").write_text(json.dumps(SAMPLE_FORECAST))
        (tmp_path / "backtest.json").write_text(json.dumps(SAMPLE_BACKTEST))

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": MOCK_NOTE}}]}
        mock_post.return_value = mock_resp

        comm.main()

        out = json.loads((tmp_path / "commentary.json").read_text())
        assert len(out) == 1
        entry = out[0]
        assert entry["text"] == MOCK_NOTE
        assert entry["model"] == "llama-3.3-70b-versatile"
        assert "prompt_hash" in entry
        assert "ts" in entry

    @patch("ml.commentary.requests.post")
    def test_main_falls_back_when_content_policy_violated(self, mock_post, tmp_path, monkeypatch):
        """audit fix: a Groq generation that ignores SYSTEM_PROMPT (banned word leaked)
        must NOT be written to commentary.json — main() falls back to the last good entry,
        the same path used for Groq API errors.
        """
        import ml.commentary as comm

        monkeypatch.setattr(comm, "DATA_DIR", tmp_path)
        monkeypatch.setenv("GROQ_API_KEY", "test-key")

        (tmp_path / "prices.json").write_text(json.dumps(SAMPLE_PRICES))
        (tmp_path / "forecast.json").write_text(json.dumps(SAMPLE_FORECAST))
        (tmp_path / "backtest.json").write_text(json.dumps(SAMPLE_BACKTEST))

        last_good = {
            "ts": "2026-05-08T00:00:00Z",
            "text": "Gold prices are steady near ₹7,180 per gram, about where they've been lately.",
            "model": "llama-3.3-70b-versatile",
            "prompt_hash": "priorhash001",
        }
        (tmp_path / "commentary.json").write_text(json.dumps([last_good]))

        violating_text = "Gold's bullish outlook, per the model, means prices may keep climbing."
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": violating_text}}]}
        mock_post.return_value = mock_resp

        with pytest.raises(SystemExit) as exc_info:
            comm.main()
        assert exc_info.value.code == 0

        out = json.loads((tmp_path / "commentary.json").read_text())
        # The last-good entry is re-surfaced (fallback=True); the violating text
        # is never written anywhere in commentary.json.
        assert violating_text not in [e["text"] for e in out]
        entry = out[-1]
        assert entry["text"] == last_good["text"]
        assert entry.get("fallback") is True
