"""Tests for scripts/check_direction_signal_not_wired_without_promotion.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "check_direction_signal_not_wired_without_promotion.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("check_direction_signal_guard", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load_module()


class TestFindReferences:
    def test_no_match_in_unrelated_file(self, tmp_path: Path) -> None:
        f = tmp_path / "app.js"
        f.write_text("function render() { return direction; }")
        assert guard.find_references(f) == []

    def test_matches_probability_gate(self, tmp_path: Path) -> None:
        f = tmp_path / "app.js"
        f.write_text("if (data.probability_gate.ship) { show(); }")
        hits = guard.find_references(f)
        assert len(hits) == 1
        assert hits[0][0] == 1

    def test_matches_decide_direction_signal_call(self, tmp_path: Path) -> None:
        f = tmp_path / "notifications.py"
        f.write_text("result = decide_direction_signal(baseline)\n")
        hits = guard.find_references(f)
        assert len(hits) == 1

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert guard.find_references(tmp_path / "does-not-exist.js") == []

    def test_word_boundary_does_not_false_positive_on_similar_names(self, tmp_path: Path) -> None:
        f = tmp_path / "app.js"
        f.write_text("const timing_gate_wrapper_unrelated = 1;\n")
        # "timing_gate" IS a substring here but \b matches at the underscore
        # boundary too (word chars include _) -- this specific name should
        # NOT match because "timing_gate_wrapper..." has no word boundary
        # immediately after "timing_gate" (the underscore continues the word).
        hits = guard.find_references(f)
        assert hits == []


class TestMainIntegration:
    def test_pass_when_no_references_and_no_record(self, tmp_path: Path, monkeypatch) -> None:
        surface = tmp_path / "app.js"
        surface.write_text("console.log('nothing to see here');")
        record = tmp_path / "direction_promotion_record.json"

        monkeypatch.setattr(guard, "SURFACE_FILES", [surface])
        monkeypatch.setattr(guard, "PROMOTION_RECORD_PATH", record)
        monkeypatch.setattr(guard, "ROOT", tmp_path)
        assert guard.main() == 0

    def test_fail_when_reference_exists_without_record(self, tmp_path: Path, monkeypatch) -> None:
        surface = tmp_path / "app.js"
        surface.write_text("if (fc.probability_gate.ship) { showSignal(); }")
        record = tmp_path / "direction_promotion_record.json"

        monkeypatch.setattr(guard, "SURFACE_FILES", [surface])
        monkeypatch.setattr(guard, "PROMOTION_RECORD_PATH", record)
        monkeypatch.setattr(guard, "ROOT", tmp_path)
        assert guard.main() == 1

    def test_pass_when_reference_exists_with_record(self, tmp_path: Path, monkeypatch) -> None:
        surface = tmp_path / "app.js"
        surface.write_text("if (fc.probability_gate.ship) { showSignal(); }")
        record = tmp_path / "direction_promotion_record.json"
        record.write_text('{"approved_by": "GG"}')

        monkeypatch.setattr(guard, "SURFACE_FILES", [surface])
        monkeypatch.setattr(guard, "PROMOTION_RECORD_PATH", record)
        monkeypatch.setattr(guard, "ROOT", tmp_path)
        assert guard.main() == 0

    def test_real_repo_currently_passes(self) -> None:
        """The actual, unmocked check against the real repo files — this is
        the live regression guard, not just a unit test of the logic."""
        assert guard.main() == 0
