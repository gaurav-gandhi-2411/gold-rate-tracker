"""Tests for scripts/inject_metrics.py — no live requests, synthetic JSON fixtures."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "inject_metrics.py"
_spec = importlib.util.spec_from_file_location("inject_metrics", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
inject_metrics = importlib.util.module_from_spec(_spec)
sys.modules["inject_metrics"] = inject_metrics
_spec.loader.exec_module(inject_metrics)


@pytest.fixture(autouse=True)
def _clear_cache():
    inject_metrics._load_json.cache_clear()
    yield
    inject_metrics._load_json.cache_clear()


@pytest.fixture
def fixture_json(tmp_path, monkeypatch):
    monkeypatch.setattr(inject_metrics, "ROOT", tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    payload = {
        "coverage": 0.8854,
        "n": 96,
        "generated_at_utc": "2026-08-23T02:54:42.208269+00:00",
        "nested": {"deep": {"value": 0.975}},
        "wilson_ci_low": 0.5494,
        "wilson_ci_high": 0.8367,
        "resolvable_at_n": False,
        "resolvable_true": True,
    }
    (data_dir / "metrics.json").write_text(json.dumps(payload))
    return payload


def _marker(field, fmt, mods="", stale="STALE"):
    return f"<!--METRIC:data/metrics.json#{field}:{fmt}{mods}-->{stale}<!--/METRIC-->"


def test_resolves_simple_field_and_replaces_stale_content(fixture_json):
    text = f"Coverage: {_marker('coverage', 'pct1')}."
    rendered, errors = inject_metrics.render_file(text)
    assert errors == []
    assert rendered == (
        "Coverage: <!--METRIC:data/metrics.json#coverage:pct1-->88.5%<!--/METRIC-->."
    )


def test_markers_survive_rerender_check_mode_stable(fixture_json):
    """Rendering an already-current file must be a no-op -- the markers persist
    across every run, unlike a one-shot placeholder (the bug this design fixes)."""
    text = f"Coverage: {_marker('coverage', 'pct1', stale='88.5%')}."
    rendered_once, errors1 = inject_metrics.render_file(text)
    rendered_twice, errors2 = inject_metrics.render_file(rendered_once)
    assert errors1 == errors2 == []
    assert rendered_once == rendered_twice
    assert "<!--METRIC:" in rendered_once
    assert "<!--/METRIC-->" in rendered_once


def test_resolves_with_n_and_asof(fixture_json):
    text = _marker("coverage", "pct1", "|n=n|asof=generated_at_utc")
    rendered, errors = inject_metrics.render_file(text)
    assert errors == []
    assert "88.5% (n=96, as of 2026-08-23)" in rendered


def test_resolves_nested_field_path(fixture_json):
    text = _marker("nested.deep.value", "num3")
    rendered, errors = inject_metrics.render_file(text)
    assert errors == []
    assert "0.975" in rendered


def test_missing_file_reports_error_and_leaves_content_untouched(fixture_json):
    text = "<!--METRIC:data/nonexistent.json#foo:pct1-->STALE<!--/METRIC-->"
    rendered, errors = inject_metrics.render_file(text)
    assert rendered == text  # unresolved markers keep their old content verbatim
    assert len(errors) == 1
    assert "does not exist" in errors[0]


def test_missing_field_reports_error(fixture_json):
    text = _marker("nope", "pct1")
    rendered, errors = inject_metrics.render_file(text)
    assert rendered == text
    assert len(errors) == 1
    assert "no such field" in errors[0]


def test_frozen_block_multiline_untouched(fixture_json):
    text = (
        '<!--FROZEN reason="pinned"-->\n'
        f"Static number: {_marker('coverage', 'pct1')}\n"
        "<!--/FROZEN-->"
    )
    rendered, errors = inject_metrics.render_file(text)
    assert rendered == text
    assert errors == []


def test_frozen_block_inline_in_table_cell_untouched(fixture_json):
    text = f'| a | <!--FROZEN reason="x"-->{_marker("coverage", "pct1")}<!--/FROZEN--> | b |'
    rendered, errors = inject_metrics.render_file(text)
    assert rendered == text
    assert errors == []


def test_marker_outside_frozen_still_resolves(fixture_json):
    text = f'<!--FROZEN reason="x"-->frozen text<!--/FROZEN--> live: {_marker("coverage", "pct1")}'
    rendered, errors = inject_metrics.render_file(text)
    assert errors == []
    assert "88.5%" in rendered
    assert "frozen text" in rendered


def test_unclosed_frozen_block_raises(fixture_json):
    text = '<!--FROZEN reason="x"-->never closed'
    with pytest.raises(inject_metrics.MetricError, match="unclosed FROZEN block"):
        inject_metrics.render_file(text)


@pytest.mark.parametrize(
    "fmt,expected",
    [
        ("pct1", "88.5%"),
        ("pct2", "88.54%"),
        ("int", "0"),  # 0.8854 truncates to 0 -- exercises the int formatter, not a realistic use
    ],
)
def test_format_specifiers(fixture_json, fmt, expected):
    text = _marker("coverage", fmt)
    rendered, errors = inject_metrics.render_file(text)
    assert errors == []
    assert expected in rendered


def test_unknown_format_reports_error(fixture_json):
    text = _marker("coverage", "bogus")
    rendered, errors = inject_metrics.render_file(text)
    assert rendered == text
    assert len(errors) == 1
    assert "unknown format" in errors[0]


def test_non_numeric_value_with_numeric_format_reports_error(tmp_path, monkeypatch):
    monkeypatch.setattr(inject_metrics, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "m.json").write_text(json.dumps({"label": "not a number"}))
    text = "<!--METRIC:data/m.json#label:pct1-->STALE<!--/METRIC-->"
    rendered, errors = inject_metrics.render_file(text)
    assert rendered == text
    assert len(errors) == 1
    assert "not numeric" in errors[0]


def test_raw_format_passes_through_non_numeric(tmp_path, monkeypatch):
    monkeypatch.setattr(inject_metrics, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "m.json").write_text(json.dumps({"label": "naive_flat_hold"}))
    text = "<!--METRIC:data/m.json#label:raw-->STALE<!--/METRIC-->"
    rendered, errors = inject_metrics.render_file(text)
    assert errors == []
    assert "naive_flat_hold" in rendered


def test_asof_truncates_iso_timestamp_to_date(fixture_json):
    text = _marker("coverage", "pct1", "|asof=generated_at_utc")
    rendered, _ = inject_metrics.render_file(text)
    assert "2026-08-23" in rendered
    assert "T02:54:42" not in rendered


def test_check_mode_detects_stale_committed_text(fixture_json, tmp_path):
    stale_file = tmp_path / "README.md"
    stale_file.write_text(_marker("coverage", "pct1", stale="99.9% (stale)"), encoding="utf-8")
    rendered, errors = inject_metrics.render_file(stale_file.read_text(encoding="utf-8"))
    assert errors == []
    assert rendered != stale_file.read_text(encoding="utf-8")  # would need rewriting


def test_ci_modifier_renders_wilson_bounds(fixture_json):
    text = _marker("coverage", "pct1", "|ci=wilson_ci_low,wilson_ci_high")
    rendered, errors = inject_metrics.render_file(text)
    assert errors == []
    assert "95% CI [54.9%, 83.7%]" in rendered


def test_ci_modifier_rejects_non_percent_format(fixture_json):
    text = _marker("nested.deep.value", "num3", "|ci=wilson_ci_low,wilson_ci_high")
    rendered, errors = inject_metrics.render_file(text)
    assert rendered == text
    assert len(errors) == 1
    assert "only supports pct1/pct2" in errors[0]


def test_unresolved_if_appends_note_when_false(fixture_json):
    text = _marker("coverage", "pct1", "|unresolved_if=resolvable_at_n")
    rendered, errors = inject_metrics.render_file(text)
    assert errors == []
    assert "not yet resolvable at this sample size" in rendered


def test_unresolved_if_silent_when_true(fixture_json):
    text = _marker("coverage", "pct1", "|unresolved_if=resolvable_true")
    rendered, errors = inject_metrics.render_file(text)
    assert errors == []
    assert "not yet resolvable" not in rendered


def test_all_modifiers_combined(fixture_json):
    text = _marker(
        "coverage",
        "pct1",
        "|n=n|ci=wilson_ci_low,wilson_ci_high|asof=generated_at_utc|unresolved_if=resolvable_at_n",
    )
    rendered, errors = inject_metrics.render_file(text)
    assert errors == []
    assert "88.5% (n=96, 95% CI [54.9%, 83.7%], as of 2026-08-23) — not yet resolvable" in rendered
