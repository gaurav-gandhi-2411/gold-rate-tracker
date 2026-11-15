"""Real-tool contract: every gh / git / jq flag our scripts and workflows use exists in the INSTALLED tool.

Why (2026-11-15): #3824 retried `gh api` with `--allow-escape-sequence`; gh's flag is
`--allow-escape-sequences`. The guard's tests mocked gh, so they passed and the guard stayed red on the
real PR. This test enumerates the flags used by .github/**/*.yml (shell `run:` text), scripts/*.sh,
scripts/*.ps1 and scripts/**/*.py (list literals such as ["gh", "pr", "view", "--json", ...]) and asks the
installed tool, so a wrong spelling fails here and not on a real PR.

Surface (stated, not implied): shell lines that start `gh|git|jq <sub> --flag`, and Python list/tuple
literals whose leading strings name one of those tools (or a gh/git subcommand in a file that runs that
tool). It does NOT see commands assembled from variables ("gh $SUB") or built with string concatenation;
such a flag is only covered when it also appears literally somewhere above. A tool that is not installed
(jq on a dev laptop) is skipped, so CI (where all three exist) is the authority.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GH_TOP = {"api", "pr", "run", "workflow", "issue", "repo", "release", "auth", "secret", "variable"}
GIT_SUBS = {
    "check-ignore", "checkout", "commit", "diff", "fetch", "log", "ls-files", "ls-remote",
    "merge-base", "patch-id", "push", "rev-parse", "rm", "show", "switch", "add", "status",
}  # fmt: skip
# Flags git documents only in the manual (not in -h / --git-completion-helper). Each was run against
# a real repo on 2026-11-15 (`git log --format=%h --since=1.day -1`, `git show --format=%h --name-only`,
# `git rev-parse --is-inside-work-tree --is-shallow-repository`, `git rev-parse --short HEAD`,
# `git ls-remote --heads`, `git ls-files --error-unmatch/--others`) and accepted.
GIT_MANUAL_ONLY = {
    ("log", "--format"), ("log", "--since"), ("show", "--format"), ("show", "--name-only"),
    ("rev-parse", "--is-inside-work-tree"), ("rev-parse", "--is-shallow-repository"),
    ("rev-parse", "--short"), ("ls-remote", "--heads"), ("ls-files", "--error-unmatch"),
    ("ls-files", "--others"), ("diff", "--name-only"), ("diff", "--name-status"), ("diff", "--stat"),
    ("diff", "--staged"), ("log", "--no-merges"), ("log", "--reverse"), ("log", "--until"),
}  # fmt: skip
# Flags that exist only in a newer gh than a dev laptop may have: skipped there, enforced on CI's runner.
NEWER_GH = {("api", "--allow-escape-sequences")}

SHELL_CMD = re.compile(
    r"(?<![\w./-])(gh|git|jq)((?:\s+(?!-)[a-z][a-z-]*){0,2})"
    r"((?:\s+(?:-[A-Za-z]|--[a-z][\w-]*)(?:[= ](?:\"[^\"]*\"|'[^']*'|[^\s|;&)<>-][^\s|;&)<>]*))?)*)"
)
FLAG = re.compile(r"(?<![\w-])(--[a-z][\w-]*|-[A-Za-z])(?![\w-])")


def _py_uses(path: Path, rel: str, out: dict) -> None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return
    src = path.read_text(encoding="utf-8")
    runs_gh, runs_git = '"gh"' in src, '"git"' in src
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and runs_gh
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and re.fullmatch(r"--[a-z][\w-]*", node.value.value)
            and any(isinstance(t, ast.Name) and "FLAG" in t.id for t in node.targets)
        ):
            # a named flag constant appended to a gh call (ESCAPE_FLAG); the call is an api call
            out[("gh", "api" if '"api"' in src else "")].add((node.value.value, rel))
        if not isinstance(node, ast.List | ast.Tuple):
            continue
        # only a list whose FIRST element is a literal counts: [binary, "git", ...] is gitleaks's own
        # `git` subcommand, not ours
        first = node.elts[0] if node.elts else None
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        toks = [
            e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
        tool = toks[0] if toks[0] in ("gh", "git", "jq") else None
        if tool:
            toks = toks[1:]
        elif toks[0] in GH_TOP and runs_gh:
            tool = "gh"
        elif toks[0] in GIT_SUBS and runs_git:
            tool = "git"
        if not tool:
            continue
        i = 0
        while tool == "git" and i + 1 < len(toks) and toks[i] in ("-c", "-C"):
            i += 2  # global options before the subcommand
        sub: list[str] = []
        while (
            i < len(toks)
            and len(sub) < (1 if tool == "git" else 2)
            and re.fullmatch(r"[a-z][a-z-]*", toks[i])
        ):
            sub.append(toks[i])
            i += 1
        for t in toks[i:]:
            if re.fullmatch(r"--?[A-Za-z][\w-]*(=.*)?", t):
                out[(tool, " ".join(sub))].add((t.split("=")[0], rel))


def _shell_uses(text: str, rel: str, out: dict) -> None:
    # drop comment lines first (their apostrophes and quotes are prose), then the CONTENT of
    # double-quoted strings (a --body text may span lines and contain "date -u" or a flag-like word)
    text = "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("#"))
    text = re.sub(r'"[^"]*"', '""', text)
    joined = re.sub(r"\\\r?\n\s*", " ", text)
    for line in joined.splitlines():
        if line.strip().startswith("#"):
            continue
        for m in SHELL_CMD.finditer(line):
            tool = m.group(1)
            sub = [] if tool == "jq" else m.group(2).split()
            for f in FLAG.findall(m.group(3)):
                out[(tool, " ".join(sub))].add((f, rel))


def enumerate_uses() -> dict[tuple[str, str], set[tuple[str, str]]]:
    out: dict = defaultdict(set)
    for p in (
        sorted((ROOT / ".github").rglob("*.yml"))
        + sorted((ROOT / "scripts").rglob("*.sh"))
        + sorted((ROOT / "scripts").rglob("*.ps1"))
    ):
        _shell_uses(p.read_text(encoding="utf-8", errors="replace"), str(p.relative_to(ROOT)), out)
    for p in sorted((ROOT / "scripts").rglob("*.py")):
        _py_uses(p, str(p.relative_to(ROOT)).replace("\\", "/"), out)
    return out


_HELP: dict[tuple[str, str], str] = {}


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )


def _help(tool: str, sub: str) -> str:
    key = (tool, sub)
    if key in _HELP:
        return _HELP[key]
    words = sub.split()
    text = ""
    if tool == "gh":
        # `gh api user` has an argument where a subcommand would be: try two words, then one
        for n in (2, 1, 0):
            r = _run(["gh", *words[:n], "--help"])
            if r.returncode == 0 and r.stdout.strip():
                text = r.stdout
                break
    elif tool == "git":
        text = _run(["git", *words[:1], "-h"]).stdout + _run(["git", *words[:1], "-h"]).stderr
        text += _run(["git", *words[:1], "--git-completion-helper"]).stdout
    else:
        text = _run(["jq", "--help"]).stdout
    _HELP[key] = text
    return text


def _known(tool: str, sub: str, flag: str) -> bool:
    text = _help(tool, sub)
    if re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w=-])", text) or f"{flag}=" in text:
        return True
    if flag.startswith("--no-") and f"--[no-]{flag[5:]}" in text:
        return True
    return tool == "git" and (sub.split()[:1] and (sub.split()[0], flag) in GIT_MANUAL_ONLY)


def _uses_for(tool: str):
    return [
        (tool, sub, flag, sorted({r for f, r in items if f == flag})[0])
        for (t, sub), items in sorted(enumerate_uses().items())
        if t == tool
        for flag in sorted({f for f, _ in items})
        if flag != "--" and not re.fullmatch(r"-\d+", flag)
    ]


@pytest.mark.parametrize("tool", ["gh", "git", "jq"])
def test_every_flag_we_use_exists_in_the_installed_tool(tool: str) -> None:
    if shutil.which(tool) is None:
        pytest.skip(f"{tool} is not installed here; CI's runner is the authority")
    uses = _uses_for(tool)
    assert uses, (
        f"the scan found no {tool} flags at all: the extractor is broken, not the repo clean"
    )
    bad = []
    for _, sub, flag, rel in uses:
        newer_only = tool == "gh" and sub.split()[:1] and (sub.split()[0], flag) in NEWER_GH
        if newer_only and "escape" not in _help("gh", "api"):
            continue  # this gh predates the flag; CI's newer gh is checked
        if not _known(tool, sub, flag):
            bad.append(f"{tool} {sub} {flag}  (used in {rel})")
    assert not bad, "flag(s) the installed tool does not know:\n" + "\n".join(bad)


def test_the_scan_sees_the_flags_that_motivated_it() -> None:
    uses = enumerate_uses()
    flags = {f for f, _ in uses[("gh", "api")]}
    assert "--allow-escape-sequences" in flags  # #3824's retry, the one a mock could not check
    assert any(f == "--json" for (t, s), it in uses.items() if t == "gh" for f, _ in it)


def test_a_misspelt_flag_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    if shutil.which("gh") is None:
        pytest.skip("gh is not installed here")
    assert _known("gh", "pr view", "--json")
    assert not _known("gh", "api", "--allow-escape-sequence")  # the singular that shipped in #3824
