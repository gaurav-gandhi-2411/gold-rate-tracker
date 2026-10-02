"""scripts/audit_silent_fallbacks.py — repeatable sweep for the "emits a
plausible value instead of failing" defect class (audit 2026-09).

Eight confirmed instances of this class were found by hand-reading code
during a production audit: (a) all ntfy alerts run inside the workflow they
monitor; (b) the coverage evaluator silently scored 45 of 65 windows; (c)
the direction gate's verdict cannot change; (d) the band silently fell back
to a Gaussian band already measured miscalibrated; (e) the hero price
rendered a stale value without the "~" marker; (f) `volCtx.regime ?? "normal"`
substitutes a named claim for a missing field; (g) a permanently dead runner
shows `ibja_calibrated` forever with zero on-page indication; (h)
bot-pr-sync's data-only allowlist guard (`.github/actions/bot-pr-sync/action.yml`)
rejected a commit outside its allowlist, correctly failing the step/job, but
with no page — the only other failure-detection step in every caller
("Alert on stuck bot PR") checks an open PR's age and this guard exits
before any PR exists, so it had nothing to find. Fixed (R2, audit
2026-09-04): the guard now also posts an ntfy alert when it rejects a diff
(`ntfy-topic` input, wired from every caller's `NTFY_TOPIC` secret) — a
failed run is no longer distinguishable only by noticing a red X among the
many `continue-on-error` steps in the same job. This script does NOT find
any of those eight — they required reading surrounding logic, not a pattern
match. What it does: turn the ad-hoc grep-and-read process used to find
(f)'s two siblings (see fix/vol-regime-fails-loud) into something that can
be re-run every time the codebase changes, so the next instance doesn't
require a full audit to surface as a candidate.

This is a REVIEW AID, not a correctness checker. False positives are
expected and normal — every category here also matches plenty of
legitimate, reasoned defaults (empty-list coalescing, safe div-by-zero
guards, control-flow guards). It does not judge CLAIM vs NEUTRAL; a human
does that per finding, the way P3b's table in fix/vol-regime-fails-loud did
by hand. Not wired into CI as a gate (see P6 in the audit brief) — run it
manually, read the report, triage by hand.

Usage: python scripts/audit_silent_fallbacks.py [--out PATH]
Writes a Markdown report to reports/silent_fallbacks_audit.md by default.
"""

from __future__ import annotations

import argparse
import ast
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ML_DIR = ROOT / "ml"
APP_JS = ROOT / "app.js"
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
DEFAULT_OUT = ROOT / "reports" / "silent_fallbacks_audit.md"

# Defaults that read as "no claim" on their own merits — kept narrow and
# literal (not a judgment call the script is making, just noise reduction on
# the most mechanical non-findings) so the report stays readable. Everything
# else with a non-None default is flagged; a human decides CLAIM vs NEUTRAL.
_NEUTRAL_GET_DEFAULT_AST_TYPES = (ast.List, ast.Dict, ast.Set)


@dataclass
class Finding:
    category: str
    file: str
    line: int
    snippet: str


def _rel(path: Path) -> str:
    """Path relative to ROOT for display; falls back to the path as given for
    files outside ROOT (e.g. a test's tmp_path fixture)."""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


# ---------------------------------------------------------------------------
# Category 1: ml/**/*.py — dict.get(key, default) where default != None and
# isn't an empty list/dict/set literal.
# ---------------------------------------------------------------------------


def _is_none_or_empty_default(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant) and node.value is None:
        return True
    return (
        isinstance(node, _NEUTRAL_GET_DEFAULT_AST_TYPES)
        and len(node.elts if hasattr(node, "elts") else node.keys) == 0
    )


def scan_dict_get_defaults(py_file: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    except (SyntaxError, UnicodeDecodeError):
        return findings

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "get"):
            continue
        if len(node.args) < 2:
            continue  # .get(key) with no default -- not this pattern
        default = node.args[1]
        if _is_none_or_empty_default(default):
            continue
        rel = _rel(py_file)
        snippet = ast.unparse(node) if hasattr(ast, "unparse") else "<.get(...) call>"
        findings.append(Finding("dict.get-substantive-default", rel, node.lineno, snippet[:120]))
    return findings


# ---------------------------------------------------------------------------
# Category 2: ml/**/*.py — except blocks that swallow and continue instead of
# propagating. Heuristic: no `raise` anywhere in the handler body, AND no
# bare `return None` / `return` with no value as the ONLY terminal statement.
# ---------------------------------------------------------------------------


def _handler_swallows(handler: ast.ExceptHandler) -> bool:
    has_raise = any(isinstance(n, ast.Raise) for n in ast.walk(handler))
    if has_raise:
        return False
    # A handler whose only effect is `return None` / bare `return` / `pass`
    # after a log call is the FAIL-CLOSED shape this class of bug should use
    # -- don't flag it. Anything else (a computed/literal return, an
    # assignment that isn't obviously re-raised) is a candidate.
    for stmt in handler.body:
        if isinstance(stmt, ast.Return):
            if stmt.value is None:
                continue
            if isinstance(stmt.value, ast.Constant) and stmt.value.value is None:
                continue
            return True  # returns something other than None
        if isinstance(stmt, ast.Assign):
            return True  # assigns a fallback value that presumably feeds forward
    return False


def scan_swallowed_exceptions(py_file: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    except (SyntaxError, UnicodeDecodeError):
        return findings

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if _handler_swallows(node):
            rel = _rel(py_file)
            first_stmt = node.body[0] if node.body else None
            snippet = (
                ast.unparse(first_stmt)[:120]
                if first_stmt and hasattr(ast, "unparse")
                else "<except block>"
            )
            findings.append(Finding("except-swallows-and-continues", rel, node.lineno, snippet))
    return findings


# ---------------------------------------------------------------------------
# Category 3: app.js — `??` / `||` defaults near text-rendering calls.
# Heuristic only (see P3b in the audit for the hand-done, judged version of
# this exact sweep): flags any `??`/`||` occurrence, tags it "near-render" if
# a t(/innerHTML/textContent call appears within 3 lines either side, else
# "other" (likely a control-flow guard, layout value, or numeric guard --
# still reported since false positives are expected, not filtered here).
# ---------------------------------------------------------------------------

_JS_DEFAULT_RE = re.compile(r"\?\?|\|\|")
_JS_RENDER_HINT_RE = re.compile(r"\bt\(|innerHTML|textContent")


def scan_js_defaults(js_file: Path) -> list[Finding]:
    findings: list[Finding] = []
    if not js_file.exists():
        return findings
    lines = js_file.read_text(encoding="utf-8").splitlines()
    rel = _rel(js_file)
    for i, line in enumerate(lines):
        if not _JS_DEFAULT_RE.search(line):
            continue
        window = lines[max(0, i - 3) : min(len(lines), i + 4)]
        near_render = any(_JS_RENDER_HINT_RE.search(w) for w in window)
        category = "js-default-near-render" if near_render else "js-default-other"
        findings.append(Finding(category, rel, i + 1, line.strip()[:120]))
    return findings


# ---------------------------------------------------------------------------
# Category 4: .github/workflows/*.yml — continue-on-error: true steps.
# ---------------------------------------------------------------------------

_CONTINUE_ON_ERROR_RE = re.compile(r"^\s*continue-on-error:\s*true\s*$")
_STEP_NAME_RE = re.compile(r"^\s*-\s*name:\s*(.+)$")


def scan_workflow_continue_on_error(yml_file: Path) -> list[Finding]:
    findings: list[Finding] = []
    lines = yml_file.read_text(encoding="utf-8").splitlines()
    rel = _rel(yml_file)
    last_step_name = "(unnamed step)"
    for i, line in enumerate(lines):
        m = _STEP_NAME_RE.match(line)
        if m:
            last_step_name = m.group(1).strip()
        if _CONTINUE_ON_ERROR_RE.match(line):
            findings.append(
                Finding("workflow-continue-on-error", rel, i + 1, f'step "{last_step_name}"')
            )
    return findings


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def run_audit() -> list[Finding]:
    findings: list[Finding] = []

    for py_file in sorted(ML_DIR.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        findings.extend(scan_dict_get_defaults(py_file))
        findings.extend(scan_swallowed_exceptions(py_file))

    findings.extend(scan_js_defaults(APP_JS))

    if WORKFLOWS_DIR.exists():
        for yml_file in sorted(WORKFLOWS_DIR.glob("*.yml")):
            findings.extend(scan_workflow_continue_on_error(yml_file))

    return findings


def render_report(findings: list[Finding]) -> str:
    by_category: dict[str, list[Finding]] = {}
    for f in findings:
        by_category.setdefault(f.category, []).append(f)

    lines = [
        "# Silent-fallback audit report",
        "",
        "Generated by `scripts/audit_silent_fallbacks.py`. Review aid, not a CI gate --",
        "false positives are expected. See the module docstring for what each category",
        "does and does not catch.",
        "",
        "## Counts",
        "",
        "| category | count |",
        "|---|---|",
    ]
    for cat in sorted(by_category):
        lines.append(f"| {cat} | {len(by_category[cat])} |")
    lines.append(f"| **total** | **{len(findings)}** |")
    lines.append("")

    for cat in sorted(by_category):
        lines.append(f"## {cat} ({len(by_category[cat])})")
        lines.append("")
        for f in by_category[cat]:
            lines.append(f"- `{f.file}:{f.line}` — {f.snippet}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    findings = run_audit()
    report = render_report(findings)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    # rstrip + one "\n": render_report()'s own "\n".join(lines) already ends in a
    # blank-line artifact from the last per-category lines.append(""), so
    # unconditionally appending "\n" here produced a double trailing newline that
    # failed pre-commit's end-of-file-fixer. newline="\n" also pins output to LF
    # regardless of platform -- write_text's default translates "\n" to os.linesep
    # on Windows (CRLF), which that same hook rejects too.
    args.out.write_text(report.rstrip("\n") + "\n", encoding="utf-8", newline="\n")

    by_category: dict[str, int] = {}
    for f in findings:
        by_category[f.category] = by_category.get(f.category, 0) + 1
    print(f"Wrote {_rel(args.out)} ({len(findings)} findings)")
    for cat, count in sorted(by_category.items()):
        print(f"  {cat}: {count}")


if __name__ == "__main__":
    main()
