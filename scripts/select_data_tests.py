"""List the test files whose SOURCE names the committed data (data/): the static half of the guard.

FOR_GG item 11 (GG decision 2026-11-03, option C refined): on a data-only PR the tests that read
the committed data decide. The main signal is MEASURED at run time (scripts/data_reads_plugin.py
sees every file a test opens). This scan complements it: it also selects a test that reads data/
through a child process, which the measurement cannot see. A failed test blocks when either signal
selects it (scripts/data_test_decision.py). Both are computed at run time, so no list to keep.

Measured on 2026-11-03 (full suite, 3127 tests): 31 test files read data/; this scan alone selects
58 files and misses 6 of the 31 (they read through a module's default path), which is why it is
the complement, not the guard.

A test file is selected when its source (comments and docstrings excluded) contains any of:
  * a string literal naming a data/ path: "data/...", "...:data/..." (git show), "data\\..."
  * a path join onto "data": ``/ "data"``, ``joinpath("data")``, ``"data" /``, ``os.path.join(..., "data"``
  * ``DATA_DIR`` / ``DATA_PATH`` / ``DATA_ROOT``, or a module-level constant of ml/, scripts/ or
    scraper/ whose value names data/ (found by scanning those modules the same way).

Over-selecting is harmless (it only widens what can block); every rule errs toward selecting.

Usage: python scripts/select_data_tests.py [--root DIR]   -> one test path per line, sorted.
Exit 0 with the list; exit 2 if tests/ cannot be read (the caller then runs the full suite).
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

DATA_STRING = re.compile(r"(?:^|[\s:'\"(/=])data[/\\](?!\s)")
JOIN = re.compile(
    r"""/\s*["']data["']|["']data["']\s*/|joinpath\(\s*["']data["']|path\.join\([^)]*["']data["']"""
)
DATA_NAME = re.compile(r"\bDATA_(?:DIR|PATH|ROOT)\b")


def _code_only(src: str) -> str:
    """The source with comments and docstrings blanked, so prose about data/ selects nothing."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src  # unparseable: scan everything (err toward selecting)
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                doc_lines.update(range(body[0].lineno, (body[0].end_lineno or 0) + 1))
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT or tok.start[0] in doc_lines:
                continue
            out.append(tok.string)
    except (tokenize.TokenError, IndentationError):
        return src
    return " ".join(out)


def reads_data(src: str) -> bool:
    code = _code_only(src)
    return bool(DATA_STRING.search(code) or JOIN.search(code) or DATA_NAME.search(code))


def _data_constants(root: Path) -> set[str]:
    """Module-level NAME = ... assignments, in the repo's own packages, that point into data/."""
    names: set[str] = set()
    for pkg in ("ml", "scripts", "scraper"):
        for p in sorted((root / pkg).glob("*.py")):
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            for node in tree.body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                    seg = ast.get_source_segment(p.read_text(encoding="utf-8"), node.value) or ""
                    if DATA_STRING.search(" " + seg) or JOIN.search(seg) or DATA_NAME.search(seg):
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        names.update(t.id for t in targets if isinstance(t, ast.Name))
    return names


def select(root: Path) -> list[str]:
    tests = sorted((root / "tests").glob("test_*.py"))
    if not tests:
        raise FileNotFoundError(f"no test files under {root / 'tests'}")
    consts = _data_constants(root)
    const_re = (
        re.compile(r"\b(" + "|".join(map(re.escape, sorted(consts))) + r")\b") if consts else None
    )
    out = []
    for p in tests:
        src = p.read_text(encoding="utf-8")
        code = _code_only(src)
        if reads_data(src) or (const_re is not None and const_re.search(code)):
            out.append(p.relative_to(root).as_posix())
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = ap.parse_args(argv)
    try:
        chosen = select(args.root)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"cannot read the tests ({exc}); run the full suite", file=sys.stderr)
        return 2
    print("\n".join(chosen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
