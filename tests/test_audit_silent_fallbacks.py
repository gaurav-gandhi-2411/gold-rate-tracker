"""Tests for scripts/audit_silent_fallbacks.py -- the sweep itself, not the
codebase it scans. Verifies each category's detector on small synthetic
fixtures so the heuristics don't silently regress (e.g. start matching
nothing, or start matching everything)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import audit_silent_fallbacks as audit


def test_scan_dict_get_defaults_flags_substantive_default(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("x = d.get('key', 'normal')\n")
    findings = audit.scan_dict_get_defaults(f)
    assert len(findings) == 1
    assert findings[0].category == "dict.get-substantive-default"
    assert findings[0].line == 1


def test_scan_dict_get_defaults_ignores_none_default(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("x = d.get('key', None)\n")
    assert audit.scan_dict_get_defaults(f) == []


def test_scan_dict_get_defaults_ignores_empty_list_default(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("x = d.get('key', [])\n")
    assert audit.scan_dict_get_defaults(f) == []


def test_scan_dict_get_defaults_ignores_get_with_no_default(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("x = d.get('key')\n")
    assert audit.scan_dict_get_defaults(f) == []


def test_scan_swallowed_exceptions_flags_computed_return(tmp_path):
    f = tmp_path / "m.py"
    f.write_text(
        "def f():\n    try:\n        return risky()\n    except Exception:\n        return 0\n"
    )
    findings = audit.scan_swallowed_exceptions(f)
    assert len(findings) == 1
    assert findings[0].category == "except-swallows-and-continues"


def test_scan_swallowed_exceptions_ignores_reraise(tmp_path):
    f = tmp_path / "m.py"
    f.write_text(
        "def f():\n    try:\n        return risky()\n    except Exception:\n        raise\n"
    )
    assert audit.scan_swallowed_exceptions(f) == []


def test_scan_swallowed_exceptions_ignores_return_none(tmp_path):
    f = tmp_path / "m.py"
    f.write_text(
        "def f():\n"
        "    try:\n"
        "        return risky()\n"
        "    except Exception:\n"
        "        logger.warning('failed')\n"
        "        return None\n"
    )
    assert audit.scan_swallowed_exceptions(f) == []


def test_scan_js_defaults_tags_near_render(tmp_path):
    f = tmp_path / "app.js"
    f.write_text(
        "function render() {\n"
        "  const regime = volCtx.regime ?? 'normal';\n"
        "  el.innerHTML = t('note', { regime });\n"
        "}\n"
    )
    findings = audit.scan_js_defaults(f)
    assert len(findings) == 1
    assert findings[0].category == "js-default-near-render"


def test_scan_js_defaults_tags_other_when_far_from_render_hint(tmp_path):
    f = tmp_path / "app.js"
    lines = ["const x = a || b;"] + ["// filler"] * 10 + ["el.innerHTML = y;"]
    f.write_text("\n".join(lines) + "\n")
    findings = audit.scan_js_defaults(f)
    assert len(findings) == 1
    assert findings[0].category == "js-default-other"


def test_scan_workflow_continue_on_error_captures_step_name(tmp_path):
    f = tmp_path / "w.yml"
    f.write_text(
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - name: Run inference\n"
        "        continue-on-error: true\n"
        "        run: echo hi\n"
    )
    findings = audit.scan_workflow_continue_on_error(f)
    assert len(findings) == 1
    assert "Run inference" in findings[0].snippet


def test_scan_workflow_continue_on_error_ignores_false(tmp_path):
    f = tmp_path / "w.yml"
    f.write_text(
        "jobs:\n"
        "  build:\n"
        "    steps:\n"
        "      - name: Lint\n"
        "        continue-on-error: false\n"
        "        run: echo hi\n"
    )
    assert audit.scan_workflow_continue_on_error(f) == []


def test_render_report_includes_counts_table():
    findings = [
        audit.Finding("dict.get-substantive-default", "ml/x.py", 1, "d.get('a', 1)"),
        audit.Finding("dict.get-substantive-default", "ml/y.py", 2, "d.get('b', 2)"),
    ]
    report = audit.render_report(findings)
    assert "| dict.get-substantive-default | 2 |" in report
    assert "**total**" in report
    assert "ml/x.py:1" in report
