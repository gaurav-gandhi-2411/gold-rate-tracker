"""scripts/check_plain_language.py -- fails when a banned statistical/ML jargon
term appears in text an ordinary Indian gold buyer would actually read (U1/U3,
docs/PLAIN_LANGUAGE_AUDIT.md).

Why this exists: the site used to show a general buyer things like "coverage
73% (n=63, 95% CI [61.0%, 82.4%])" where a plain "usually right about 7 times
out of 10" would do. A one-time audit pass fixed the strings that existed on
2026-09-23 (docs/PLAIN_LANGUAGE_AUDIT.md); this script is what keeps a future
PR from quietly reintroducing the same pattern in a new string.

Scope (what is scanned, and how):
  * index.html    -- visible text nodes and aria-label/title attribute values.
                      <script>/<style> bodies and ALL HTML comments (including
                      the invisible dev-notes ones) are stripped first, so
                      neither counts as "visible".
  * i18n.js        -- string/template-literal VALUES inside STRINGS.en{...} and
                      STRINGS.hi{...} only (found by brace-depth matching, not
                      the whole file) -- every value in those two blocks is a
                      real user-facing string, so none of app.js's
                      identifier-filtering heuristic (below) is needed or
                      applied here. `${...}` interpolations are stripped
                      before matching (a variable name should never itself
                      trip a banned-term match).
  * app.js         -- string/template-literal content anywhere in the file,
                      but ONLY literals containing at least one whitespace
                      character. app.js has zero raw English UI sentences
                      outside i18n.js's t() catalogue (verified 2026-09-23 --
                      every .textContent/.innerHTML assignment either uses
                      t(...) or a bare symbol like "↻"/"—") but its string
                      literals are otherwise dense with element ids, CSS
                      classes, data-file paths and i18n key names, all of
                      which are single tokens with no internal whitespace and
                      none of which are real prose -- filtering on "contains a
                      space" removes that entire false-positive class for
                      free, at the cost of being unable to catch a
                      single-WORD violation in a raw app.js literal (accepted:
                      app.js has none today and the whole point of routing
                      real strings through i18n.js is that it shouldn't).
  * ml/public_copy.py -- string literals actually returned as ntfy title/body
                      text, found via `ast` (never a hand-rolled parser for
                      Python -- the stdlib one is exact). Module/function/class
                      DOCSTRINGS are explicitly excluded: public_copy.py's own
                      docstring intentionally names several of these terms
                      ("no jargon (no ... 'model', 'calibration' ...)") to
                      describe the rule, which is not the same as violating it.
                      ml/notifications.py, ml/cadence_digest.py,
                      ml/notification_routing.py, and ml/inference.py are
                      DELIBERATELY NOT scanned -- see docs/PLAIN_LANGUAGE_
                      AUDIT.md's "Notification files" section: every trigger
                      that can reach the public ntfy topic (T1-T4, T8_MORNING,
                      T8_EVENING; notification_routing.py's own
                      PUBLIC_ALLOWLIST) builds its title/body exclusively via
                      public_copy.* calls (verified 2026-09-23, no hardcoded
                      text of its own in notifications.py's public-trigger
                      functions) -- every other trigger id in those four files
                      is OPS-only (routes to the owner's private topic per
                      notification_routing.py's KNOWN_OPS set and
                      weekly-backtest.yml's NTFY_TOPIC env), i.e. not the
                      "ordinary Indian gold buyer" audience this script
                      exists to protect, and is legitimately allowed to be
                      technical.
  * README.md      -- the "first screen" only: from the top of the file
                      through the line BEFORE the SECOND `## `-level heading
                      (or a literal `---` line, whichever comes first).
                      Chosen deliberately over "before the first H2" --
                      "## What you'll see" (the first H2) is itself the
                      buyer-facing summary this script exists to keep plain;
                      "## What it won't tell you" (the second H2) is where the
                      README's own honest technical-limitations table starts,
                      and stays exempt on purpose (same "technical depth stays
                      lower down" split as the main page/how-we-know.html).
                      METRIC/FROZEN marker DELIMITERS (docs on
                      scripts/inject_metrics.py) are stripped but their
                      rendered inner content is kept (it's real visible text
                      on the published page); every other HTML comment is
                      stripped entirely, delimiters and content both.
  * how-we-know.html, how-we-know.js, how-we-know-strings.js are the
    designated technical-detail surface (U2) and are NEVER scanned -- they
    are simply absent from SCANNED targets below, no per-term exemption
    needed.

Known blind spots -- state plainly, not hidden (rule 85a):
  * The app.js/i18n.js/README tokenizers are hand-written, not a real
    JS/Markdown parser. The JS tokenizer correctly walks `//`/`/* */`
    comments and `"`/`'`/`` ` `` strings (including multi-line template
    literals) character-by-character, but does NOT understand regex literals
    -- a `/regex/` containing a literal `//` inside it would be misread as a
    comment start partway through. Verified 2026-09-23 that neither app.js
    nor i18n.js currently contains such a regex; this is a structural
    limitation, not a swept-and-forgotten gap.
  * IBJA is deliberately NOT a banned term here (U1: "explained once, or
    avoided" is a judgement call about CONTEXT a regex can't make -- a
    correctly-glossed mention and a bare unexplained one look identical to a
    pattern match). Audited by hand instead; see docs/PLAIN_LANGUAGE_AUDIT.md.
  * "General unexplained acronym" (U1's broader instruction) is not
    automated at all -- only the specific fixed BANNED_TERMS list below is
    checked. A new acronym introduced in a future PR needs a human audit
    pass, same as it always did.

Usage:
    python scripts/check_plain_language.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── Banned terms ─────────────────────────────────────────────────────────────
# (label, compiled pattern). Word-boundary, case-insensitive throughout except
# the bare-acronym ones (CI/ECE/MAE) which stay case-sensitive on purpose --
# their lowercase forms ("ci", "ece", "mae") are far more likely to be
# incidental substrings of ordinary words than the all-caps acronym is to
# appear by accident.
BANNED_TERMS: list[tuple[str, re.Pattern[str]]] = [
    ("brier", re.compile(r"\bbrier\b", re.IGNORECASE)),
    ("calibrated/calibration", re.compile(r"\bcalibrat\w*\b", re.IGNORECASE)),
    ("coverage", re.compile(r"\bcoverage\b", re.IGNORECASE)),
    ("confidence interval", re.compile(r"\bconfidence interval\b", re.IGNORECASE)),
    ("CI", re.compile(r"\bCI\b")),
    ("n= (sample-size notation)", re.compile(r"\bn=")),
    ("p= (p-value notation)", re.compile(r"\bp=")),
    ("p-value", re.compile(r"\bp-values?\b", re.IGNORECASE)),
    ("model", re.compile(r"\bmodels?\b", re.IGNORECASE)),
    ("baseline", re.compile(r"\bbaselines?\b", re.IGNORECASE)),
    ("regime", re.compile(r"\bregimes?\b", re.IGNORECASE)),
    ("embargo", re.compile(r"\bembargoe?d?\b", re.IGNORECASE)),
    ("walk-forward", re.compile(r"\bwalk[- ]forward\b", re.IGNORECASE)),
    ("shadow", re.compile(r"\bshadow\w*\b", re.IGNORECASE)),
    ("residual", re.compile(r"\bresiduals?\b", re.IGNORECASE)),
    ("percentile", re.compile(r"\bpercentiles?\b", re.IGNORECASE)),
    ("conformal", re.compile(r"\bconformal\b", re.IGNORECASE)),
    ("ECE", re.compile(r"\bECE\b")),
    ("MAE", re.compile(r"\bMAE\b")),
    ("volatility/volatile", re.compile(r"\bvolatil(?:e|ity)\b", re.IGNORECASE)),
    ("z-score", re.compile(r"\bz[- ]score\b", re.IGNORECASE)),
    ("sigma (\u03c3)", re.compile("\u03c3")),
]

# Explicit, commented allowlist for unavoidable cases -- a term inside a URL,
# a code identifier, or a CSS class name that genuinely can't be reworded.
# (relative POSIX path, exact matched snippet). The U1 audit pass fixed every
# other real violation rather than exempting it; add an entry here ONLY when a
# term is structurally unavoidable, with a comment explaining why.
ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        # app.js's renderModelSignal() builds one multi-line template literal
        # covering a whole HTML fragment (real rendered text + CSS class
        # names together) -- the whitespace filter that excludes pure
        # identifiers elsewhere can't apply here since the literal
        # legitimately contains real prose too. "outlook-volatility"/
        # "-note" are CSS class names only; the actual displayed text in
        # that fragment is t()-built (volNoteElevated/Calm/Normal/Fallback,
        # already reworded off "volatile" in i18n.js).
        ("app.js", "volatility"),
    }
)

Violation = tuple[str, int, str, str]  # (rel_path, line_no, term_label, snippet)


def _rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _check_text(text: str, path: Path, line_no: int) -> list[Violation]:
    rel = _rel(path)
    out: list[Violation] = []
    for term_label, pattern in BANNED_TERMS:
        for m in pattern.finditer(text):
            snippet = m.group(0)
            if (rel, snippet) in ALLOWLIST:
                continue
            out.append((rel, line_no, term_label, snippet))
    return out


_INTERPOLATION_RE = re.compile(r"\$\{[^{}]*\}")


def _strip_js_interpolations(content: str) -> str:
    """Removes `${...}` segments from a template-literal's extracted content --
    a variable/expression name (e.g. `${coverage}`) should never itself be
    able to trip a banned-term match; only the surrounding literal text can."""
    return _INTERPOLATION_RE.sub(" ", content)


def extract_js_string_literals(text: str) -> list[tuple[int, str]]:
    """Character-by-character JS tokenizer: returns [(start_line, content), ...]
    for every `"`, `'`, and `` ` ``-delimited string/template literal in
    `text`, correctly skipping `//` and `/* */` comments (so a quoted phrase
    inside a comment is never mistaken for a real string) and correctly
    handling multi-line template literals. See the module docstring's "Known
    blind spots" for what this does NOT handle (regex literals)."""
    results: list[tuple[int, str]] = []
    i = 0
    n = len(text)
    line = 1
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j == -1 else j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            end = n if j == -1 else j + 2
            line += text.count("\n", i, end)
            i = end
            continue
        if c in ('"', "'", "`"):
            quote = c
            start_line = line
            j = i + 1
            buf: list[str] = []
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j : j + 2])
                    if text[j + 1] == "\n":
                        line += 1
                    j += 2
                    continue
                if text[j] == quote:
                    j += 1
                    break
                if text[j] == "\n":
                    line += 1
                    if quote != "`":
                        # Unterminated single/double-quoted string across a
                        # line -- shouldn't happen in valid JS; stop
                        # collecting rather than run away across the file.
                        break
                buf.append(text[j])
                j += 1
            results.append((start_line, "".join(buf)))
            i = j
            continue
        i += 1
    return results


def _extract_lang_block(text: str, lang_key: str) -> tuple[str, int]:
    """Returns (block_text, start_line) for `<lang_key>: { ... }` inside
    `const STRINGS = { ... }`, found by brace-depth counting from the matching
    opening `{` -- NOT a regex-bounded "until the next `lang: {`", which would
    silently include/exclude content if key order or nesting ever changes.
    Skips over string-literal content while counting depth so a `{`/`}`
    inside a value (template-literal interpolations) can't desync it."""
    m = re.search(rf"\b{re.escape(lang_key)}:\s*\{{", text)
    if not m:
        return "", 0
    start = m.end() - 1
    depth = 0
    i = start
    n = len(text)
    while i < n:
        c = text[i]
        if c in ('"', "'", "`"):
            quote = c
            i += 1
            while i < n:
                if text[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1], text.count("\n", 0, start)
        i += 1
    return text[start:], text.count("\n", 0, start)


def scan_i18n_js(path: Path) -> list[Violation]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    out: list[Violation] = []
    for lang in ("en", "hi"):
        block, start_line = _extract_lang_block(text, lang)
        if not block:
            continue
        for rel_line, content in extract_js_string_literals(block):
            content = _strip_js_interpolations(content)
            out.extend(_check_text(content, path, start_line + rel_line))
    return out


def scan_app_js(path: Path) -> list[Violation]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    out: list[Violation] = []
    for line_no, content in extract_js_string_literals(text):
        if not re.search(r"\s", content):
            continue  # identifier/URL/CSS-class/i18n-key -- see module docstring
        content = _strip_js_interpolations(content)
        out.extend(_check_text(content, path, line_no))
    return out


def _strip_preserving_lines(pattern: re.Pattern[str], text: str) -> str:
    return pattern.sub(lambda m: "\n" * m.group(0).count("\n"), text)


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_SCRIPT_BLOCK_RE = re.compile(r"<script\b.*?</script>", re.DOTALL | re.IGNORECASE)
_STYLE_BLOCK_RE = re.compile(r"<style\b.*?</style>", re.DOTALL | re.IGNORECASE)
_ARIA_TITLE_ATTR_RE = re.compile(r'\b(?:aria-label|title)="([^"]*)"')
_TAG_RE = re.compile(r"<[^>]+>")


def scan_index_html(path: Path) -> list[Violation]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    # Comments first (invisible on the rendered page, never "visible text"),
    # then script/style bodies -- all preserving newline counts so every
    # subsequent line number stays accurate.
    cleaned = _strip_preserving_lines(_HTML_COMMENT_RE, text)
    cleaned = _strip_preserving_lines(_SCRIPT_BLOCK_RE, cleaned)
    cleaned = _strip_preserving_lines(_STYLE_BLOCK_RE, cleaned)

    out: list[Violation] = []
    for m in _ARIA_TITLE_ATTR_RE.finditer(cleaned):
        line_no = cleaned.count("\n", 0, m.start()) + 1
        out.extend(_check_text(m.group(1), path, line_no))

    text_only = _TAG_RE.sub(" ", cleaned)
    for line_no, line_text in enumerate(text_only.splitlines(), start=1):
        out.extend(_check_text(line_text, path, line_no))
    return out


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    nodes: list[ast.AST] = [tree]
    nodes.extend(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    )
    for node in nodes:
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


def scan_python_file(path: Path) -> list[Violation]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    skip_ids = _docstring_node_ids(tree)
    out: list[Violation] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skip_ids
        ):
            out.extend(_check_text(node.value, path, node.lineno))
    return out


_README_METRIC_OPEN_RE = re.compile(r"<!--METRIC:[^>]*-->")
_README_METRIC_CLOSE_RE = re.compile(r"<!--/METRIC-->")
_README_FROZEN_OPEN_RE = re.compile(r'<!--FROZEN reason="[^"]*"-->')
_README_FROZEN_CLOSE_RE = re.compile(r"<!--/FROZEN-->")


def _readme_first_screen(text: str) -> str:
    """See the module docstring's README.md entry for exactly what "first
    screen" means and why the boundary sits where it does."""
    lines = text.splitlines()
    h2_count = 0
    end = len(lines)
    for i, line in enumerate(lines):
        if line.strip() == "---":
            end = i
            break
        if line.startswith("## "):
            h2_count += 1
            if h2_count == 2:
                end = i
                break
    return "\n".join(lines[:end])


def scan_readme(path: Path) -> list[Violation]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    scope = _readme_first_screen(text)
    # METRIC/FROZEN marker DELIMITERS only -- their rendered inner content is
    # real visible text on the published page and must stay in scope.
    scope = _README_METRIC_OPEN_RE.sub("", scope)
    scope = _README_METRIC_CLOSE_RE.sub("", scope)
    scope = _README_FROZEN_OPEN_RE.sub("", scope)
    scope = _README_FROZEN_CLOSE_RE.sub("", scope)
    # Any OTHER HTML comment is genuinely invisible -- strip delimiters AND content.
    scope = _strip_preserving_lines(_HTML_COMMENT_RE, scope)

    out: list[Violation] = []
    for line_no, line in enumerate(scope.splitlines(), start=1):
        out.extend(_check_text(line, path, line_no))
    return out


def collect_violations() -> list[Violation]:
    violations: list[Violation] = []
    violations += scan_index_html(ROOT / "index.html")
    violations += scan_i18n_js(ROOT / "i18n.js")
    violations += scan_app_js(ROOT / "app.js")
    violations += scan_python_file(ROOT / "ml" / "public_copy.py")
    violations += scan_readme(ROOT / "README.md")
    return violations


def main() -> int:
    violations = collect_violations()
    if violations:
        for rel, line_no, term_label, snippet in sorted(violations):
            print(
                f"{rel}:{line_no}: banned term [{term_label}] found: {snippet!r}", file=sys.stderr
            )
        print(
            f"\nFAIL: {len(violations)} plain-language violation(s). "
            "See docs/PLAIN_LANGUAGE_AUDIT.md.",
            file=sys.stderr,
        )
        return 1
    print("OK: no banned jargon terms found in user-facing text.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
