"""Tests for scripts/check_computed_claims.py — synthetic fixture repo, no live data.

Model: tests/test_check_plain_language.py's fixture-repo pattern (monkeypatch ROOT to
a tmp_path, write only the files a given test cares about).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_computed_claims.py"
_spec = importlib.util.spec_from_file_location("check_computed_claims", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
ccc = importlib.util.module_from_spec(_spec)
sys.modules["check_computed_claims"] = ccc
_spec.loader.exec_module(ccc)


_CLEAN_INDEX_HTML = """<!doctype html>
<html>
  <head><title>Gold Rate Today</title></head>
  <body>
    <button aria-label="Refresh data" title="Refresh data">refresh</button>
    <p>Today's price is on the low side for the month.</p>
    <!-- a dev comment mentioning 4 times out of 5, never rendered -->
    <script>const claim = "9 out of 10 -- never shown, script body stripped";</script>
  </body>
</html>
"""

_CLEAN_I18N_JS = """const STRINGS = {
  en: {
    greeting: "Today's price is here",
    withParams: ({ price }) => `The price is ${price} today`,
    // a comment mentioning right 90% of the time -- never a real value
  },
  hi: {
    greeting: "आज की कीमत यहाँ है",
  },
};
"""

_CLEAN_HWK_STRINGS_JS = """const STRINGS_HWK = {
  en: {
    methAccurateP2: ({ coverageText }) => `Our range has been right ${coverageText}`,
  },
  hi: {},
};
"""

_CLEAN_HOW_WE_KNOW_HTML = """<!doctype html>
<html><body><p>Technical detail page, computed numbers only.</p></body></html>
"""

_CLEAN_APP_JS = """const el = document.getElementById("claim-section");
el.textContent = "Todays price is here, no hand-typed claim at all";
const CLASS_NAME = "outlook-claim-card"; // single-token identifier, no space
"""

_CLEAN_README_MD = """# Gold Rate Today

**Buying gold?** This tells you today's price in plain words.

## What you'll see

- It's aimed at being right <!--METRIC:data/x.json#pct:int-->80<!--/METRIC-->% of the time;
  it has landed inside the range <!--METRIC:data/x.json#cov:frac10-->about 7 times out of 10<!--/METRIC--> so far.
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(ccc, "ROOT", tmp_path)
    (tmp_path / "index.html").write_text(_CLEAN_INDEX_HTML, encoding="utf-8")
    (tmp_path / "how-we-know.html").write_text(_CLEAN_HOW_WE_KNOW_HTML, encoding="utf-8")
    (tmp_path / "i18n.js").write_text(_CLEAN_I18N_JS, encoding="utf-8")
    (tmp_path / "how-we-know-strings.js").write_text(_CLEAN_HWK_STRINGS_JS, encoding="utf-8")
    (tmp_path / "app.js").write_text(_CLEAN_APP_JS, encoding="utf-8")
    (tmp_path / "README.md").write_text(_CLEAN_README_MD, encoding="utf-8")
    return tmp_path


def test_clean_repo_produces_no_violations(repo):
    assert ccc.collect_violations() == []


# ── Computed templates pass ──────────────────────────────────────────────────


def test_js_interpolation_placeholder_is_not_a_violation(repo):
    # ${coverageText} is a template placeholder, not a literal digit -- must
    # never trip "right N%" even though the surrounding words match.
    assert ccc.collect_violations() == []


def test_readme_metric_marker_content_is_not_a_violation(repo):
    # The fixture's README already contains "right 80% of the time" and
    # "7 times out of 10" -- both INSIDE METRIC marker spans (computed).
    # Zero violations proves the whole span (not just delimiters) is excluded.
    violations = ccc.collect_violations()
    assert violations == []
    assert "README.md" not in {v[0] for v in violations}


def test_moving_the_metric_text_outside_the_marker_is_caught(repo):
    # Proves the exemption is about being INSIDE a METRIC span, not about the
    # text itself -- the identical rendered text, hand-typed with no marker,
    # must be flagged.
    (repo / "README.md").write_text(
        _CLEAN_README_MD.replace(
            "it has landed inside the range <!--METRIC:data/x.json#cov:frac10-->about 7 times out of 10<!--/METRIC--> so far.",
            "it has landed inside the range about 7 times out of 10 so far.",
        ),
        encoding="utf-8",
    )
    violations = ccc.collect_violations()
    labels = {v[2] for v in violations}
    assert "N times out of M" in labels


# ── Planted violations (the exact bug class this script exists to catch) ────


def test_planted_violation_right_about_n_times_out_of_m_is_caught(repo):
    # Exactly the bug that shipped: a hand-typed "Right about 4 times out of 5"
    # in how-we-know-strings.js's methAccurateP2Strong-style key.
    (repo / "how-we-know-strings.js").write_text(
        """const STRINGS_HWK = {
  en: {
    methAccurateP2Strong: "Right about 4 times out of 5",
  },
  hi: {},
};
""",
        encoding="utf-8",
    )
    violations = ccc.collect_violations()
    assert any(
        v[0] == "how-we-know-strings.js"
        and v[2] == "N times out of M"
        and "4 times out of 5" in v[3]
        for v in violations
    ), violations


def test_planted_violation_right_n_pct_of_the_time_is_caught(repo):
    (repo / "index.html").write_text(
        _CLEAN_INDEX_HTML.replace(
            "Today's price is on the low side for the month.",
            "Our range is right 73% of the time.",
        ),
        encoding="utf-8",
    )
    violations = ccc.collect_violations()
    labels = {v[2] for v in violations}
    assert "right N%" in labels
    assert "N% of the time" in labels


def test_planted_violation_hindi_n_mein_se_m_baar_is_caught(repo):
    (repo / "i18n.js").write_text(
        """const STRINGS = {
  en: {},
  hi: {
    claim: "10 में से 7 बार सही",
  },
};
""",
        encoding="utf-8",
    )
    violations = ccc.collect_violations()
    assert any(v[0] == "i18n.js" and v[2] == "Hindi N में से M बार" for v in violations), violations


def test_planted_violation_coverage_pct_is_caught(repo):
    (repo / "app.js").write_text(
        'const s = "our coverage 73% figure, hardcoded by mistake";\n',
        encoding="utf-8",
    )
    violations = ccc.collect_violations()
    assert any(v[0] == "app.js" and v[2] == "coverage N%" for v in violations), violations


def test_planted_violation_accurate_to_is_caught(repo):
    (repo / "how-we-know.html").write_text(
        "<!doctype html><html><body><p>Accurate to within a rupee.</p></body></html>",
        encoding="utf-8",
    )
    violations = ccc.collect_violations()
    assert any(v[0] == "how-we-know.html" and v[2] == "accurate to" for v in violations), violations


# ── Allowlist mechanism ──────────────────────────────────────────────────────


def test_allowlisted_snippet_is_not_flagged_in_its_own_file(repo, monkeypatch):
    entry = ccc.AllowlistEntry(
        path="how-we-know-strings.js",
        snippet="within ₹50",
        reason="test-only: fixed threshold, not an accuracy claim",
    )
    monkeypatch.setattr(ccc, "_ALLOWLIST_SET", frozenset({(entry.path, entry.snippet)}))
    (repo / "how-we-know-strings.js").write_text(
        """const STRINGS_HWK = {
  en: {
    rule: "Steady: movement within ₹50 either way",
  },
  hi: {},
};
""",
        encoding="utf-8",
    )
    assert ccc.collect_violations() == []


def test_allowlist_is_scoped_to_its_own_file_not_globally(repo, monkeypatch):
    entry = ccc.AllowlistEntry(
        path="how-we-know-strings.js",
        snippet="within ₹50",
        reason="test-only: fixed threshold, not an accuracy claim",
    )
    monkeypatch.setattr(ccc, "_ALLOWLIST_SET", frozenset({(entry.path, entry.snippet)}))
    # The identical snippet, in a DIFFERENT file than the allowlist entry names,
    # must still be flagged -- the allowlist is (path, snippet), never snippet alone.
    (repo / "index.html").write_text(
        _CLEAN_INDEX_HTML.replace(
            "Today's price is on the low side for the month.",
            "Steady: movement within ₹50 either way.",
        ),
        encoding="utf-8",
    )
    violations = ccc.collect_violations()
    assert any(v[0] == "index.html" and v[2] == "within ₹N (accuracy/error)" for v in violations), (
        violations
    )


def test_real_allowlist_entry_is_load_bearing(repo, monkeypatch):
    # The one real ALLOWLIST entry (how-we-know-strings.js's "within ₹100" rule
    # threshold) must actually be necessary -- disabling it must produce exactly
    # that violation, proving the entry isn't dead weight.
    monkeypatch.setattr(ccc, "_ALLOWLIST_SET", frozenset())
    (repo / "how-we-know-strings.js").write_text(
        """const STRINGS_HWK = {
  en: {
    methRuleSteady: "Steady: movement within ₹100 either way",
  },
  hi: {},
};
""",
        encoding="utf-8",
    )
    violations = ccc.collect_violations()
    assert any(
        v[0] == "how-we-know-strings.js" and v[2] == "within ₹N (accuracy/error)"
        for v in violations
    ), violations
