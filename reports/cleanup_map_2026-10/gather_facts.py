"""Analysis-only fact gatherer for the 2026-10 cleanup map (stdlib only, read-only).

Scope (rule 85b), syntactic shapes searched:
  Python AST: `import ml.x`, `from ml.x import y`, `from ml import x`, relative imports,
              `importlib.import_module("ml.x")` string literals.
  Text (YAML/ps1/Makefile/MD/JS/JSON/PY, any tracked file): `python -m ml.x`,
              `python path/x.py`, bare `ml/x.py` / `scripts/x.py` path strings, `ml.x` dotted names,
              `data/<file>` and `reports/<path>` path strings, `fetch('data/...')`, bare basenames.
NOT covered: dynamically built paths (f-strings with variables), `getattr` dispatch, paths
  assembled from `Path(a) / "b"` parts split over several literals (only the final-literal
  basename is matched), runtime-only references (secrets, remote dispatch payloads), non-tracked
  files, and git-history usage.
Writes facts.json next to this file's CWD-independent output path given as argv[1].
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.cwd()
OUT = Path(sys.argv[1])


def git_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line]


FILES = git_files()
TEXT_EXT = {
    ".py",
    ".js",
    ".mjs",
    ".json",
    ".md",
    ".yml",
    ".yaml",
    ".ps1",
    ".toml",
    ".html",
    ".txt",
    ".lock",
    ".jsonl",
    ".csv",
    ".webmanifest",
    ".cfg",
    ".ini",
    "",
}


def read(p: str) -> str | None:
    path = ROOT / p
    if path.suffix.lower() not in TEXT_EXT and path.name not in {"Makefile"}:
        return None
    try:
        if path.stat().st_size > 3_000_000:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


TEXT = {f: read(f) for f in FILES}
PY = [f for f in FILES if f.endswith(".py")]


def modname(f: str) -> str:
    m = f[:-3].replace("/", ".")
    return m[: -len(".__init__")] if m.endswith(".__init__") else m


MODS = {modname(f): f for f in PY}


def resolve(mod: str) -> list[str]:
    """Return tracked .py files for a dotted module and its parent packages."""
    out = []
    parts = mod.split(".")
    for i in range(1, len(parts) + 1):
        k = ".".join(parts[:i])
        if k in MODS:
            out.append(MODS[k])
    return out


imports: dict[str, set[str]] = defaultdict(set)
for f in PY:
    t = TEXT[f]
    if not t:
        continue
    try:
        tree = ast.parse(t)
    except SyntaxError:
        continue
    pkg = modname(f).rsplit(".", 1)[0] if "." in modname(f) else ""
    if f.endswith("__init__.py"):
        pkg = modname(f)
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                imports[f].update(resolve(a.name))
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                base = pkg.split(".")
                base = base[: len(base) - (n.level - 1)] if n.level > 1 else base
                mod = ".".join(base + ([n.module] if n.module else []))
            else:
                mod = n.module or ""
            imports[f].update(resolve(mod))
            for a in n.names:
                imports[f].update(resolve(f"{mod}.{a.name}"))
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            s = n.value
            if re.fullmatch(r"(ml|scripts)(\.[A-Za-z_0-9]+)+", s) and s in MODS:
                imports[f].add(MODS[s])
    imports[f].discard(f)

# Text references: python -m / python path
CLI_M = re.compile(r"python3?\s+-m\s+((?:ml|scripts)(?:\.[A-Za-z_0-9]+)+)")
CLI_P = re.compile(r"python3?\s+((?:ml|scripts)/[A-Za-z_0-9/\-]+\.py)")
NODE_P = re.compile(r"node\s+((?:scraper|scripts|worker-deadman|tests)/[A-Za-z_0-9/\-\.]+\.m?js)")
PATH_ANY = re.compile(
    r"(?<![A-Za-z0-9_])((?:ml|scripts|scraper|tests|data|reports|docs|config|archive|worker-deadman)/[A-Za-z0-9_\-\./]+)"
)

wf_files = [f for f in FILES if f.startswith(".github/")]
roots_wf: dict[str, set[str]] = defaultdict(set)  # file -> workflows referencing it
for w in [*wf_files, "Makefile"]:
    t = TEXT.get(w) or ""
    for m in CLI_M.finditer(t):
        for r in resolve(m.group(1)):
            roots_wf[r].add(w)
    for m in CLI_P.finditer(t):
        if m.group(1) in TEXT:
            roots_wf[m.group(1)].add(w)
    for m in NODE_P.finditer(t):
        if m.group(1) in TEXT:
            roots_wf[m.group(1)].add(w)
    for m in PATH_ANY.finditer(t):
        p = m.group(1).rstrip(".,:;)'\"")
        if p in TEXT:
            roots_wf[p].add(w)


# reachability from workflow roots over python import graph
def reach(roots: set[str]) -> set[str]:
    seen, stack = set(), list(roots)
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        stack.extend(imports.get(x, ()))
    return seen


wf_roots = {f for f in roots_wf if f.endswith(".py")}
prod_reach = reach(wf_roots)
test_files = [f for f in PY if f.startswith("tests/")]
test_reach = reach(set(test_files))


# who references a given path (any tracked text file), by category
def cat(f: str) -> str:
    if f.startswith(".github/"):
        return "workflow"
    if f.startswith("tests/"):
        return "test"
    if f.startswith("docs/adr/"):
        return "adr"
    if f.startswith("docs/"):
        return "docs"
    if f.startswith("reports/"):
        return "reports"
    if f.startswith("scripts/"):
        return "scripts"
    if f.startswith("ml/"):
        return "ml"
    if f.startswith("scraper/"):
        return "scraper"
    if f.startswith("worker-deadman/"):
        return "worker"
    if f.startswith("data/"):
        return "data"
    if "/" not in f:
        return "root"
    return "other"


def needles(f: str) -> list[str]:
    ns = [f]
    base = f.rsplit("/", 1)[-1]
    if f.endswith(".py"):
        ns.append(modname(f))
    # bare basenames are noisy for generic names, so require len>=8
    if len(base) >= 8 and base != "README.md":
        ns.append(base)
    return ns


refs: dict[str, dict[str, list[str]]] = {}
for f in FILES:
    if f.endswith(".png") or f.endswith(".woff2") or f.endswith(".parquet") or f.endswith(".pdf"):
        ns = needles(f)
    else:
        ns = needles(f)
    found: dict[str, list[str]] = defaultdict(list)
    for g in FILES:
        if g == f:
            continue
        t = TEXT.get(g)
        if not t:
            continue
        if any(n in t for n in ns):
            found[cat(g)].append(g)
    refs[f] = {k: v[:12] for k, v in found.items()} | {
        "_counts": {k: len(v) for k, v in found.items()}
    }  # type: ignore[dict-item]

# JS surface: script tags / shell list / fetches
index = TEXT.get("index.html") or ""
sw = TEXT.get("service-worker.js") or ""
fetches = set()
for f in (
    "app.js",
    "flags.js",
    "how-we-know.js",
    "i18n.js",
    "how-we-know-strings.js",
    "index.html",
    "how-we-know.html",
    "service-worker.js",
):
    for m in re.finditer(r"""['"`]((?:\./)?data/[A-Za-z0-9_\-\./]+)['"`]""", TEXT.get(f) or ""):
        fetches.add((f, m.group(1).lstrip("./") if m.group(1).startswith("./") else m.group(1)))
script_tags = re.findall(r"""<script[^>]+src=['"]([^'"]+)['"]""", index)

out = {
    "files": FILES,
    "imports": {k: sorted(v) for k, v in imports.items()},
    "roots_wf": {k: sorted(v) for k, v in roots_wf.items()},
    "prod_reach": sorted(prod_reach),
    "test_reach": sorted(test_reach),
    "refs": refs,
    "fetches": sorted(fetches),
    "script_tags": script_tags,
    "py_files": PY,
}
OUT.write_text(json.dumps(out, indent=1))
print(
    len(FILES),
    "files;",
    len(PY),
    "py;",
    len(prod_reach),
    "prod-reach;",
    len(test_reach),
    "test-reach",
)
