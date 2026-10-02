"""scripts/check_computed_claims.py -- fails when user-facing text contains a
hand-typed accuracy/coverage/error claim as a LITERAL digit, instead of a value
computed from the data the page already loads.

Why this exists: "Right about 4 times out of 5" shipped as a hand-typed string
in how-we-know-strings.js (fixed alongside this script, same PR) -- a number
that reads as measured but was never derived from data/*.json at all, and would
have silently drifted the moment the real accuracy changed. This is the same
failure class rule 65c already names for README/site metric copy ("a metric
without provenance doesn't exist"), generalized into a CI gate: any literal
digit sitting inside one of the fixed claim SHAPES below is refused, full stop,
unless explicitly allowlisted with a stated reason for why it is NOT actually
an accuracy/coverage/error claim (a karat-purity ratio, a making-charge range,
a fixed rule threshold, etc.).

What counts as a "claim shape" (CLAIM_PATTERNS below) is deliberately narrow
and enumerated, not a general "any digit near '%' is suspicious" rule -- a
false-positive-heavy checker gets disabled or bulk-allowlisted, which defeats
the point. Each pattern requires an ACTUAL digit character next to the words
that make it a claim ("N times out of M", "right N%", "coverage N%", ...);
a template placeholder (`${...}` in JS, an f-string `{expr}` segment in Python,
which never survives into an ast.Constant's string value in the first place)
has no digit characters to match, so it passes through untouched with no
special-case stripping needed -- the one exception is JS `${...}` interpolation
bodies that might themselves *contain* a literal digit (e.g. `${1 + 1}`, or a
number embedded directly in the expression), which are stripped before matching
the same way scripts/check_plain_language.py strips them for the same reason.

Scope (what is scanned, and how) -- deliberately DIFFERENT from
check_plain_language.py's scope, which exists to keep GENERAL JARGON off the
plain-language main-page surface and explicitly EXEMPTS how-we-know.* as the
"designated technical-detail surface". This checker has the opposite shape: a
hand-typed accuracy claim is a defect on EVERY user-facing surface, technical
or not -- how-we-know.html is exactly where a p-value or a coverage number
belongs, but it must still be the real, computed number, never typed by hand.
  * index.html, how-we-know.html   -- visible text nodes and aria-label/title
                                       attribute values (script/style bodies
                                       and HTML comments stripped first).
  * i18n.js, how-we-know-strings.js -- string/template-literal VALUES inside
                                       STRINGS.en{...}/STRINGS.hi{...} (or
                                       STRINGS_HWK's equivalent -- the extractor
                                       matches `en:`/`hi:` by name, not by the
                                       enclosing object's own name, so it works
                                       for either file unmodified).
  * app.js                          -- string/template-literal content anywhere
                                       in the file, but only literals containing
                                       at least one whitespace character (same
                                       identifier/CSS-class/id filter
                                       check_plain_language.py uses, and for the
                                       same reason: app.js has no raw prose
                                       outside i18n.js's catalogue).
  * README.md                       -- the WHOLE file, MINUS every METRIC
                                       marker's full span (open comment +
                                       rendered content + close comment) --
                                       not just the delimiters the way
                                       check_plain_language.py strips them.
                                       A METRIC-injected number is BY
                                       DEFINITION the computed value this
                                       checker exists to require, so its
                                       content must never itself trip a
                                       violation (see scripts/inject_metrics.py
                                       for the marker syntax). Everything
                                       else in README.md -- not just the
                                       "first screen" -- is in scope: a
                                       hand-typed claim in the technical
                                       detail lower down is exactly as much
                                       a defect as one in the opening bullets.

Not scanned, and why: docs/*.md and docs/adr/*.md (ADRs are allowed to cite a
frozen historical measurement by hand, same reasoning as README's own FROZEN
block convention -- see inject_metrics.py's FROZEN docs); ml/public_copy.py and
other Python notification copy (out of scope for THIS PR; scripts/
check_plain_language.py already covers that surface for jargon, and a hand-typed
digit claim there would need a follow-up pass, not silently added here).

Allowlist mechanism: ALLOWLIST entries are (path, exact matched snippet,
reason) -- a snippet is allowlisted only in the file it was found in, and only
with a human-readable reason a reviewer can judge (never a bare path/snippet
pair). Keep this list small: every entry here is a claim-SHAPED string that is
NOT actually an accuracy/coverage/error claim (a karat-purity definition, a
making-charge percentage range, GST, a fixed rule threshold) -- if a real
claim is found, FIX it (compute it from data, same pattern as how-we-know.js's
range label using coverage via fractionOutOf10Phrase()), don't allowlist it.

Usage:
    python scripts/check_computed_claims.py
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── Claim shape patterns ─────────────────────────────────────────────────────
# (label, compiled pattern). All require a literal ASCII digit -- a template
# placeholder (`${n}`, `{n}`) has none, so it can never match these on its own.
CLAIM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("N times out of M", re.compile(r"\b\d+\s+times\s+out\s+of\s+\d+\b", re.IGNORECASE)),
    ("N out of M", re.compile(r"\b\d+\s+out\s+of\s+\d+\b", re.IGNORECASE)),
    ("N in M", re.compile(r"\b\d+\s+in\s+\d+\b", re.IGNORECASE)),
    ("N% of the time", re.compile(r"\b\d+%\s+of\s+the\s+time\b", re.IGNORECASE)),
    ("right N%", re.compile(r"\bright\s+\d+%", re.IGNORECASE)),
    ("N% accurate", re.compile(r"\b\d+%\s+accurate\b", re.IGNORECASE)),
    ("accurate to", re.compile(r"\baccurate\s+to\b", re.IGNORECASE)),
    ("within ₹N (accuracy/error)", re.compile(r"\bwithin\s+₹\d+\b", re.IGNORECASE)),
    ("coverage N%", re.compile(r"\bcoverage\s+\d+%", re.IGNORECASE)),
    # Hindi: "<M> में से <N> बार" -- "N times out of M" (e.g. "10 में से 7 बार").
    ("Hindi N में से M बार", re.compile(r"\d+\s*में\s*से\s*\d+\s*बार")),
]


# Explicit, commented allowlist for genuine non-claims that happen to match one
# of the shapes above (unit definitions, fixed config ranges, rule thresholds)
# -- see module docstring. (relative POSIX path, exact matched snippet, reason).
@dataclass(frozen=True)
class AllowlistEntry:
    path: str
    snippet: str
    reason: str


ALLOWLIST: tuple[AllowlistEntry, ...] = (
    AllowlistEntry(
        path="how-we-know-strings.js",
        snippet="within ₹100",
        reason=(
            "Fixed RULE THRESHOLD defining the 'Steady' direction-signal label "
            "(movement of <=₹100 either way counts as 'Steady'), not a measured "
            "accuracy/error claim -- the number is a config constant for how the "
            "label is assigned, never a statistic about how often it's right."
        ),
    ),
)
_ALLOWLIST_SET = frozenset((e.path, e.snippet) for e in ALLOWLIST)

Violation = tuple[str, int, str, str]  # (rel_path, line_no, pattern_label, snippet)


def _rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _check_text(text: str, path: Path, line_no: int) -> list[Violation]:
    rel = _rel(path)
    out: list[Violation] = []
    for label, pattern in CLAIM_PATTERNS:
        for m in pattern.finditer(text):
            snippet = m.group(0)
            if (rel, snippet) in _ALLOWLIST_SET:
                continue
            out.append((rel, line_no, label, snippet))
    return out


# ── JS string-literal tokenizer (self-contained; see module docstring for why
# this is not imported from scripts/check_plain_language.py -- no check_*.py
# script in this repo imports another, and this checker's scope differs enough
# from that one's that sharing beyond "the algorithm shape" would be a false
# economy) ────────────────────────────────────────────────────────────────────

_INTERPOLATION_RE = re.compile(r"\$\{[^{}]*\}")


def _strip_js_interpolations(content: str) -> str:
    """Removes `${...}` segments -- an interpolated expression (which may
    itself contain a literal digit, e.g. `${maePctWorse}` or `${1+1}`) must
    never be mistaken for a hand-typed digit in the surrounding literal text."""
    return _INTERPOLATION_RE.sub(" ", content)


def extract_js_string_literals(text: str) -> list[tuple[int, str]]:
    """Character-by-character JS tokenizer: [(start_line, content), ...] for
    every `"`, `'`, and `` ` ``-delimited string/template literal, skipping
    `//` and `/* */` comments. Does not understand regex literals (same stated
    limitation as check_plain_language.py's identical tokenizer; verified
    2026-09-24 that none of the files this script scans contain one)."""
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
                        break
                buf.append(text[j])
                j += 1
            results.append((start_line, "".join(buf)))
            i = j
            continue
        i += 1
    return results


def _extract_lang_block(text: str, lang_key: str) -> tuple[str, int]:
    """Returns (block_text, start_line) for `<lang_key>: { ... }` found by
    brace-depth counting from the matching opening `{`, skipping over string
    literals while counting depth. Works regardless of the enclosing object's
    own name (`STRINGS` in i18n.js, `STRINGS_HWK` in how-we-know-strings.js)."""
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


def scan_js_strings_catalogue(path: Path) -> list[Violation]:
    """Scans the en/hi STRINGS blocks of an i18n-style catalogue file (i18n.js,
    how-we-know-strings.js)."""
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
            continue  # identifier/URL/CSS-class/i18n-key -- not prose
        content = _strip_js_interpolations(content)
        out.extend(_check_text(content, path, line_no))
    return out


# ── HTML scanning ────────────────────────────────────────────────────────────

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_SCRIPT_BLOCK_RE = re.compile(r"<script\b.*?</script>", re.DOTALL | re.IGNORECASE)
_STYLE_BLOCK_RE = re.compile(r"<style\b.*?</style>", re.DOTALL | re.IGNORECASE)
_ARIA_TITLE_ATTR_RE = re.compile(r'\b(?:aria-label|title)="([^"]*)"')
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_preserving_lines(pattern: re.Pattern[str], text: str) -> str:
    return pattern.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def scan_html(path: Path) -> list[Violation]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
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


# ── README.md scanning ───────────────────────────────────────────────────────

# Full span (open comment + rendered content + close comment) -- see module
# docstring: a METRIC-injected number is the computed value this checker
# exists to require, so it (and any claim-shaped text sitting inside it, e.g.
# frac10's own "about 7 times out of 10") must never itself be flagged.
_README_METRIC_SPAN_RE = re.compile(r"<!--METRIC:.*?-->.*?<!--/METRIC-->", re.DOTALL)


def scan_readme(path: Path) -> list[Violation]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    scope = _strip_preserving_lines(_README_METRIC_SPAN_RE, text)
    out: list[Violation] = []
    for line_no, line in enumerate(scope.splitlines(), start=1):
        out.extend(_check_text(line, path, line_no))
    return out


def collect_violations() -> list[Violation]:
    violations: list[Violation] = []
    violations += scan_html(ROOT / "index.html")
    violations += scan_html(ROOT / "how-we-know.html")
    violations += scan_js_strings_catalogue(ROOT / "i18n.js")
    violations += scan_js_strings_catalogue(ROOT / "how-we-know-strings.js")
    violations += scan_app_js(ROOT / "app.js")
    violations += scan_readme(ROOT / "README.md")
    return violations


def main() -> int:
    violations = collect_violations()
    if violations:
        for rel, line_no, label, snippet in sorted(violations):
            print(
                f"{rel}:{line_no}: hand-typed claim [{label}] found: {snippet!r}", file=sys.stderr
            )
        print(
            f"\nFAIL: {len(violations)} hand-typed computed-claim violation(s). "
            "Compute the number from data instead, or add a justified ALLOWLIST "
            "entry in scripts/check_computed_claims.py if it is genuinely not an "
            "accuracy/coverage/error claim.",
            file=sys.stderr,
        )
        return 1
    print("OK: no hand-typed computed claims found in user-facing text.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
