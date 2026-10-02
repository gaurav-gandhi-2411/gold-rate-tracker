"""Tests for scripts/check_plain_language.py — synthetic fixture repo, no live data."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_plain_language.py"
_spec = importlib.util.spec_from_file_location("check_plain_language", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
cpl = importlib.util.module_from_spec(_spec)
sys.modules["check_plain_language"] = cpl
_spec.loader.exec_module(cpl)


_CLEAN_INDEX_HTML = """<!doctype html>
<html>
  <head><title>Gold Rate Today</title></head>
  <body>
    <button aria-label="Refresh data" title="Refresh data">refresh</button>
    <p>Today's price is on the low side for the month.</p>
    <!-- a dev comment mentioning calibration and coverage, never rendered -->
    <script>const model = "internal, never shown";</script>
  </body>
</html>
"""

_CLEAN_I18N_JS = """const STRINGS = {
  en: {
    // a comment mentioning "model" and "coverage" -- never a real value
    greeting: "Today's price is here",
    withParams: ({ price }) => `The price is ${price} today`,
  },
  hi: {
    greeting: "आज की कीमत यहाँ है",
  },
};
"""

_CLEAN_APP_JS = """const el = document.getElementById("model-signal-section");
el.textContent = "Todays price is here, no jargon at all";
const CLASS_NAME = "outlook-model-card"; // single-token identifier, no space
"""

_CLEAN_PUBLIC_COPY_PY = '''"""Copy for public notifications.

Standard: no jargon (no "model", "calibration", "coverage", "CI").
"""

from __future__ import annotations


def greet(name: str) -> str:
    """Docstring mentioning model and calibration -- never returned to a user."""
    return f"Hello {name}, welcome to Gold Tracker"
'''

_CLEAN_README_MD = """# Gold Rate Today

**Buying gold?** This tells you today's price in plain words.

## What you'll see

- A plain bullet with no jargon at all.

---

## What it won't tell you

Technical depth lives here: model accuracy, coverage 73% (n=63, 95% CI),
p=0.05, MAE, walk-forward validation. All exempt -- past the boundary.
"""

_POLLUTED_HOW_WE_KNOW_HTML = """<!doctype html>
<html><body>
  <p>Our model's coverage is 73% (n=63, 95% CI [61.0%, 82.4%]), p=0.003,
  MAE 249, walk-forward validated, no embargo leak, calibrated residuals,
  conformal band, ECE 0.02, volatility regime shadow baseline z-score.</p>
</body></html>
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A clean, minimal repo tree — each test overwrites the one file it cares
    about. how-we-know.html ships pre-polluted with every banned term in the
    baseline fixture itself, so every test in this file implicitly re-proves
    it's never scanned, not just the one dedicated test for it."""
    monkeypatch.setattr(cpl, "ROOT", tmp_path)
    (tmp_path / "index.html").write_text(_CLEAN_INDEX_HTML, encoding="utf-8")
    (tmp_path / "i18n.js").write_text(_CLEAN_I18N_JS, encoding="utf-8")
    (tmp_path / "app.js").write_text(_CLEAN_APP_JS, encoding="utf-8")
    (tmp_path / "README.md").write_text(_CLEAN_README_MD, encoding="utf-8")
    (tmp_path / "how-we-know.html").write_text(_POLLUTED_HOW_WE_KNOW_HTML, encoding="utf-8")
    ml_dir = tmp_path / "ml"
    ml_dir.mkdir()
    (ml_dir / "public_copy.py").write_text(_CLEAN_PUBLIC_COPY_PY, encoding="utf-8")
    return tmp_path


def test_clean_repo_produces_no_violations(repo):
    assert cpl.collect_violations() == []


def test_how_we_know_html_is_never_scanned(repo):
    # The baseline fixture's how-we-know.html is loaded with every banned term
    # (_POLLUTED_HOW_WE_KNOW_HTML) and the repo is otherwise clean -- zero
    # violations proves the file is structurally excluded, not just "happened
    # to be clean this run".
    violations = cpl.collect_violations()
    assert violations == []
    assert not any(v[0] == "how-we-know.html" for v in violations)


def test_moving_the_polluted_text_into_index_html_is_caught(repo):
    # Proves the exemption is about the FILE, not the text -- the exact same
    # polluted sentence, scanned via index.html instead of how-we-know.html,
    # must be flagged.
    (repo / "index.html").write_text(
        _CLEAN_INDEX_HTML.replace(
            "Today's price is on the low side for the month.",
            "Our model's coverage is 73% (n=63, 95% CI), calibrated.",
        ),
        encoding="utf-8",
    )
    violations = cpl.collect_violations()
    terms = {v[2] for v in violations}
    assert "model" in terms
    assert "coverage" in terms
    assert "n= (sample-size notation)" in terms
    assert "CI" in terms
    assert "calibrated/calibration" in terms


# ── Each banned term fails when it appears in real visible text ─────────────

_BANNED_TERM_CASES = [
    ("brier", "The Brier loss was low this week."),
    ("calibrated/calibration", "The estimate is calibrated to match shop prices."),
    ("coverage", "Our coverage was strong this week."),
    ("confidence interval", "This has a wide confidence interval."),
    ("CI", "See the CI report for details."),
    ("n= (sample-size notation)", "Checked n=63 times so far."),
    ("p= (p-value notation)", "The result was p=0.003 significant."),
    ("p-value", "The p-value was small."),
    ("model", "Our model predicts the price."),
    ("baseline", "This beats the baseline easily."),
    ("regime", "Prices are calm in this regime."),
    ("embargo", "No embargo leak was found."),
    ("walk-forward", "Tested with walk-forward validation."),
    ("shadow", "Running in shadow mode for now."),
    ("residual", "The residual was small."),
    ("percentile", "This is the 90th percentile."),
    ("conformal", "Uses a conformal band."),
    ("ECE", "The ECE score improved."),
    ("MAE", "The MAE was 249 rupees."),
    ("volatility/volatile", "Gold has been volatile lately."),
    ("z-score", "The z-score was high."),
]


@pytest.mark.parametrize(
    "term_label,sentence", _BANNED_TERM_CASES, ids=[c[0] for c in _BANNED_TERM_CASES]
)
def test_each_banned_term_fails(repo, term_label, sentence):
    (repo / "index.html").write_text(
        _CLEAN_INDEX_HTML.replace("Today's price is on the low side for the month.", sentence),
        encoding="utf-8",
    )
    violations = cpl.collect_violations()
    assert any(v[2] == term_label for v in violations), (
        f"expected {term_label!r} to be flagged, got {violations!r}"
    )


def test_sigma_symbol_fails(repo):
    (repo / "index.html").write_text(
        _CLEAN_INDEX_HTML.replace(
            "Today's price is on the low side for the month.",
            "The move is about 340\u03c3 today.",
        ),
        encoding="utf-8",
    )
    violations = cpl.collect_violations()
    assert any(v[2].startswith("sigma") for v in violations)


# ── Allowlist ─────────────────────────────────────────────────────────────────


def test_allowlist_excludes_a_listed_snippet_but_not_others(repo, monkeypatch):
    monkeypatch.setattr(cpl, "ALLOWLIST", frozenset({("index.html", "model")}))
    (repo / "index.html").write_text(
        _CLEAN_INDEX_HTML.replace(
            "Today's price is on the low side for the month.",
            "Our model predicts nothing; see the model card.",
        ),
        encoding="utf-8",
    )
    violations = cpl.collect_violations()
    # Both occurrences match the SAME (path, snippet) pair, so the allowlist
    # exempts both -- an allowlist entry is keyed on the matched text, not a
    # single occurrence.
    assert not any(v[2] == "model" for v in violations)


def test_allowlist_does_not_exempt_a_different_file(repo, monkeypatch):
    monkeypatch.setattr(cpl, "ALLOWLIST", frozenset({("app.js", "model")}))
    (repo / "index.html").write_text(
        _CLEAN_INDEX_HTML.replace(
            "Today's price is on the low side for the month.",
            "Our model predicts the price.",
        ),
        encoding="utf-8",
    )
    violations = cpl.collect_violations()
    assert any(v[2] == "model" and v[0] == "index.html" for v in violations)


# ── index.html scoping: aria-label/title + comments/scripts excluded ────────


def test_aria_label_attribute_is_scanned(repo):
    html = _CLEAN_INDEX_HTML.replace('aria-label="Refresh data"', 'aria-label="Model confidence"')
    (repo / "index.html").write_text(html, encoding="utf-8")
    violations = cpl.collect_violations()
    assert any(v[2] == "model" and v[0] == "index.html" for v in violations)


def test_html_comment_text_is_not_scanned(repo):
    # The clean fixture's HTML comment already says "calibration and coverage"
    # -- if comments were scanned, the clean-repo test above would already
    # fail. This test makes the claim explicit and independent of that one.
    violations = cpl.collect_violations()
    assert not any(v[0] == "index.html" for v in violations)


def test_script_block_text_is_not_scanned(repo):
    # The clean fixture's inline <script> already declares `const model = ...`
    # -- same "already proven by the clean-repo test" pattern, made explicit.
    violations = cpl.collect_violations()
    assert not any(v[0] == "index.html" and v[2] == "model" for v in violations)


# ── i18n.js scoping: only STRINGS.en/hi block values, ${} interpolations stripped ──


def test_i18n_js_comment_inside_strings_block_is_not_scanned(repo):
    # The clean fixture's own comment ("model" and "coverage") inside the en
    # block already proves this if collect_violations() is empty -- explicit
    # test for clarity.
    violations = cpl.collect_violations()
    assert not any(v[0] == "i18n.js" for v in violations)


def test_i18n_js_value_is_scanned(repo):
    js = _CLEAN_I18N_JS.replace(
        'greeting: "Today\'s price is here",', 'greeting: "Our model says so",'
    )
    (repo / "i18n.js").write_text(js, encoding="utf-8")
    violations = cpl.collect_violations()
    assert any(v[0] == "i18n.js" and v[2] == "model" for v in violations)


def test_i18n_js_interpolation_variable_name_is_not_matched(repo):
    js = _CLEAN_I18N_JS.replace(
        "withParams: ({ price }) => `The price is ${price} today`,",
        "withParams: ({ model }) => `The price is ${model} today`,",
    )
    (repo / "i18n.js").write_text(js, encoding="utf-8")
    violations = cpl.collect_violations()
    # "model" only appears as a destructured parameter name / ${} interpolation
    # placeholder, never in the literal template text -- must not be flagged.
    assert not any(v[0] == "i18n.js" and v[2] == "model" for v in violations)


# ── app.js scoping: whitespace-containing literals only ──────────────────────


def test_app_js_single_token_identifier_is_not_scanned(repo):
    # The clean fixture already has `"outlook-model-card"` (no whitespace) --
    # explicit test for clarity.
    violations = cpl.collect_violations()
    assert not any(v[0] == "app.js" for v in violations)


def test_app_js_phrase_with_whitespace_is_scanned(repo):
    js = _CLEAN_APP_JS.replace(
        'el.textContent = "Todays price is here, no jargon at all";',
        'el.textContent = "Our model says the price will rise";',
    )
    (repo / "app.js").write_text(js, encoding="utf-8")
    violations = cpl.collect_violations()
    assert any(v[0] == "app.js" and v[2] == "model" for v in violations)


# ── Python scoping: docstrings excluded, returned literals scanned ──────────


def test_python_docstring_is_not_scanned(repo):
    # The clean fixture's module AND function docstrings both say "model"/
    # "calibration" -- explicit test for clarity.
    violations = cpl.collect_violations()
    assert not any(v[0] == "ml/public_copy.py" for v in violations)


def test_python_returned_string_is_scanned(repo):
    py = _CLEAN_PUBLIC_COPY_PY.replace(
        'return f"Hello {name}, welcome to Gold Tracker"',
        'return f"Hello {name}, our model says so"',
    )
    (repo / "ml" / "public_copy.py").write_text(py, encoding="utf-8")
    violations = cpl.collect_violations()
    assert any(v[0] == "ml/public_copy.py" and v[2] == "model" for v in violations)


# ── README.md boundary ───────────────────────────────────────────────────────


def test_readme_first_screen_is_scanned(repo):
    md = _CLEAN_README_MD.replace(
        "- A plain bullet with no jargon at all.",
        "- Our model's coverage was strong.",
    )
    (repo / "README.md").write_text(md, encoding="utf-8")
    violations = cpl.collect_violations()
    terms = {v[2] for v in violations if v[0] == "README.md"}
    assert "model" in terms
    assert "coverage" in terms


def test_readme_past_second_h2_is_exempt(repo):
    # The clean fixture's own "What it won't tell you" section already has
    # model/coverage/n=/CI/p=/MAE/walk-forward -- explicit test for clarity.
    violations = cpl.collect_violations()
    assert not any(v[0] == "README.md" for v in violations)


# ── CLI entrypoint ────────────────────────────────────────────────────────────


def test_main_returns_zero_on_clean_repo(repo, capsys):
    assert cpl.main() == 0
    out = capsys.readouterr().out
    assert "OK" in out


def test_main_returns_one_and_reports_file_line_term(repo, capsys):
    (repo / "index.html").write_text(
        _CLEAN_INDEX_HTML.replace(
            "Today's price is on the low side for the month.",
            "Our model predicts the price.",
        ),
        encoding="utf-8",
    )
    assert cpl.main() == 1
    err = capsys.readouterr().err
    assert "index.html:" in err
    assert "model" in err
