"""ntfy carries gold-price information ONLY (owner decision 2026-11-06): every sender is enumerated.

The test scans .github/workflows/*.yml, .github/actions/**, scripts/**, ml/**, scraper/** and
worker-deadman/src/** for every shape that can put a message on ntfy:
  * the "ntfy.sh" URL in code (curl in a workflow `run:`/shell script, Python urlopen/requests,
    JS fetch), docstrings and comments excluded;
  * a run script that expands $NTFY_TOPIC, a Python string "NTFY_TOPIC"/"NTFY_TOPIC_PUBLIC" (reading
    the topic), JS `NTFY_TOPIC` in code;
  * `ntfy-topic:` inputs passed to a composite action;
  * calls to any Python function that itself contains one of those shapes (wrappers resolved to
    their callers, repo-wide).
Each hit becomes an identifier ("<file> :: <step or function> :: <shape>"); the set must equal KEEP
below, count for count. A new sender, or a second send added inside a listed one, fails and is named.
Adding to KEEP is an owner decision: the message must be a gold-price message (K1/K2/K5), say the
site shows a wrong or stale price (K3), or need an action only the owner can take (K4).

NTFY_KEEP_ROOT=<dir> points the scan (and the notifications/worker checks) at another tree; that is
how the test is shown to fail on origin/master's tree.
"""

from __future__ import annotations

import ast
import importlib
import os
import re
import sys
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

ROOT = Path(os.environ.get("NTFY_KEEP_ROOT") or Path(__file__).resolve().parent.parent)

# K1 daily summary, K2 Monday summary, K3 the site shows a wrong or stale price,
# K4 only the owner can fix it, K5 public subscriber price alerts (ml/notifications.py).
# KEEP[file] = {"<category> <identifier>": expected count}; identifiers come from scan() below.
KEEP: dict[str, dict[str, int]] = {}


def _k(category: str, file: str, ident: str, n: int = 1) -> None:
    KEEP.setdefault(file, {})[f"{category} {ident}"] = n


_NOTE = "step 'Send the gold-price note to GG's private topic'"
# K1 daily summary, K2 Monday summary (scripts/gg_digest.py: rates, change, range, accuracy)
_k("K1", ".github/workflows/daily-digest.yml", f"{_NOTE} :: ntfy-url")
_k("K1", ".github/workflows/daily-digest.yml", f"{_NOTE} :: topic-var", 2)
_k("K2", ".github/workflows/monday-push.yml", f"{_NOTE} :: ntfy-url")
_k("K2", ".github/workflows/monday-push.yml", f"{_NOTE} :: topic-var", 2)
# K3 the site shows a wrong or stale price (or does not render): visitors see no price
_k("K3", ".github/workflows/render-smoke.yml", "step 'Alert on render failure' :: ntfy-url")
_k("K3", ".github/workflows/render-smoke.yml", "step 'Alert on render failure' :: topic-var", 2)
_k("K3", "scripts/live_freshness.py", "send :: ntfy-url")
_k("K3", "scripts/live_freshness.py", "main :: topic-env")
_k("K3", "scripts/live_freshness.py", "main :: calls send")
_k("K3", "scripts/live_share_card.py", "main :: topic-env")
_k("K3", "scripts/live_share_card.py", "main :: calls send")
# K3 worker-deadman: only the forecast-staleness alert reaches deliver() (checked separately below)
_k("K3", "worker-deadman/src/index.mjs", "postToNtfy :: ntfy-url")
_k("K3", "worker-deadman/src/index.mjs", "postAlert :: topic-env", 2)
_k("K3", "worker-deadman/src/index.mjs", "deliver :: topic-env", 2)
_k("K3", "worker-deadman/src/index.mjs", "runCheck :: topic-env", 2)
# K4 only the owner can fix it: token expiry, and the 401 "data sync token rejected" page
_k("K4", "scripts/check_token_expiry.py", "send_ntfy :: ntfy-url")
_k("K4", "scripts/check_token_expiry.py", "run :: topic-env")
_BPS = "step 'Push, open/reuse PR, dispatch checks, and merge once green'"
_k("K4", ".github/actions/bot-pr-sync/action.yml", f"{_BPS} :: ntfy-url")
_k("K4", ".github/actions/bot-pr-sync/action.yml", f"{_BPS} :: topic-var", 2)
# K4 callers that hand the topic to bot-pr-sync (whose only send is the 401 page above)
for _wf, _step in (
    ("check-price.yml", "Sync data via bot PR"),
    ("eval-direction.yml", "Sync eval results via bot PR"),
    ("generate-og-image.yml", "Sync og.png via bot PR"),
    ("lighthouse.yml", "Sync data via bot PR"),
    ("monthly-ibja-backfill.yml", "Sync IBJA parquet via bot PR"),
    ("scrape-tanishq-selfhosted.yml", "Sync Tanishq reading via bot PR"),
    ("shadow-fusion.yml", "Sync data via bot PR"),
    ("weekly-backtest.yml", "Sync backtest results via bot PR"),
):
    _k(
        "K4",
        f".github/workflows/{_wf}",
        f"step '{_step}' :: uses ./.github/actions/bot-pr-sync :: ntfy-topic input",
    )
# K5 public subscriber price alerts: T1 T2 T3 T4 T8_MORNING T8_EVENING T15 (see the runtime test)
_k("K5", "ml/notifications.py", "<module> :: ntfy-url")
_k("K5", "ml/notifications.py", "send_pending :: _NTFY_BASE")
_k("K5", "ml/notifications.py", "main :: calls send_pending")
_k("K5", "ml/notification_routing.py", "<module> :: topic-env", 2)


# =================================================================================================
# scanner
# =================================================================================================


def _files(root: Path) -> Iterator[Path]:
    globs = [
        ".github/workflows/*.yml",
        ".github/workflows/*.yaml",
        ".github/actions/**/*.yml",
        ".github/actions/**/*.yaml",
        "scripts/**/*.py",
        "scripts/**/*.sh",
        "scripts/**/*.js",
        "scripts/**/*.mjs",
        "ml/**/*.py",
        "scraper/**/*.py",
        "scraper/**/*.js",
        "scraper/**/*.mjs",
        "worker-deadman/src/**/*.mjs",
    ]
    seen: set[Path] = set()
    for g in globs:
        for p in sorted(root.glob(g)):
            if "node_modules" in p.parts or "__pycache__" in p.parts or p in seen:
                continue
            seen.add(p)
            yield p


def _strip_hash_comments(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def _yaml_steps(doc: dict) -> Iterator[dict]:
    for job in (doc.get("jobs") or {}).values():
        yield from job.get("steps") or []
    yield from (doc.get("runs") or {}).get("steps") or []


def _scan_yaml(path: Path, rel: str, out: Counter[tuple[str, str]]) -> None:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for step in _yaml_steps(doc):
        name = step.get("name") or step.get("uses") or step.get("id") or "?"
        run = _strip_hash_comments(step.get("run") or "")
        n = len(re.findall(r"ntfy\.sh", run))
        if n:
            out[(rel, f"step '{name}' :: ntfy-url")] += n
        v = len(re.findall(r"\$\{?NTFY_TOPIC\b", run))
        if v:
            out[(rel, f"step '{name}' :: topic-var")] += v
        if "ntfy-topic" in (step.get("with") or {}):
            out[(rel, f"step '{name}' :: uses {step.get('uses')} :: ntfy-topic input")] += 1


def _scan_sh(path: Path, rel: str, out: Counter[tuple[str, str]]) -> None:
    text = _strip_hash_comments(path.read_text(encoding="utf-8"))
    n = len(re.findall(r"ntfy\.sh", text))
    if n:
        out[(rel, "script :: ntfy-url")] += n
    v = len(re.findall(r"\$\{?NTFY_TOPIC\b", text))
    if v:
        out[(rel, "script :: topic-var")] += v


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


class _PyVisitor(ast.NodeVisitor):
    def __init__(self, docs: set[int]) -> None:
        self.docs = docs
        self.stack: list[str] = []
        self.hits: Counter[str] = Counter()
        self.calls: list[tuple[str, str]] = []  # (enclosing, called name)

    @property
    def where(self) -> str:
        return self.stack[-1] if self.stack else "<module>"

    def _func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    # ast.NodeVisitor dispatches on these exact names
    visit_FunctionDef = _func  # noqa: N815
    visit_AsyncFunctionDef = _func  # noqa: N815

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and id(node) not in self.docs:
            if "ntfy.sh" in node.value:
                self.hits[f"{self.where} :: ntfy-url"] += 1
            if re.fullmatch(r"NTFY_TOPIC(_PUBLIC)?", node.value):
                self.hits[f"{self.where} :: topic-env"] += 1

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "_NTFY_BASE" and isinstance(node.ctx, ast.Load):
            self.hits[f"{self.where} :: _NTFY_BASE"] += 1

    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
        if name:
            self.calls.append((self.where, name))
        self.generic_visit(node)


def _imported_stems(tree: ast.AST) -> set[str]:
    stems: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            stems |= {a.name.split(".")[-1] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            stems |= {(n.module or "").split(".")[-1], *(a.name for a in n.names)}
    return stems


def _scan_py(path: Path) -> tuple[Counter[str], list[tuple[str, str]], set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    v = _PyVisitor(_docstring_nodes(tree))
    v.visit(tree)
    return v.hits, v.calls, _imported_stems(tree)


def _strip_js_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("//"))


_JS_FUNC = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)|^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\("
)


def _scan_js(path: Path, rel: str, out: Counter[tuple[str, str]]) -> None:
    where = "<module>"
    for ln in _strip_js_comments(path.read_text(encoding="utf-8")).splitlines():
        m = _JS_FUNC.match(ln)
        if m and not ln.startswith("  " * 2):
            where = m.group(1) or m.group(2)
        if "ntfy.sh" in ln:
            out[(rel, f"{where} :: ntfy-url")] += 1
        if "NTFY_TOPIC" in ln:
            out[(rel, f"{where} :: topic-env")] += 1


# Directories the repo-wide sweep skips, each for a stated reason: tests and fixtures only ASSERT about
# ntfy (and the worker tests contain fake ntfy URLs); docs and reports are prose; data/ and
# reports/ are generated records; the rest is tooling or vendored.
SWEEP_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "tests", "test", "docs", "reports",
    "data", ".pytest_cache", ".ruff_cache", ".mypy_cache", "playwright-report", "test-results",
}  # fmt: skip
SWEEP_SKIP_SUFFIXES = {".md", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".pdf", ".lock",
                       ".parquet", ".bundle", ".woff", ".woff2", ".enc", ".zip"}  # fmt: skip
SWEEP_MAX_BYTES = 1_000_000


def _sweep(root: Path, covered: set[Path], out: Counter[tuple[str, str]]) -> None:
    """Every other text file in the repo that mentions ntfy.sh or NTFY_TOPIC in non-comment code.

    This is the net under the structured scan: a new tool at the repo root, a workflow with an
    unusual extension, a script type the scan does not parse. Comment lines (# or //) are ignored.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SWEEP_SKIP_DIRS]
        for name in filenames:
            p = Path(dirpath) / name
            if p in covered or p.suffix.lower() in SWEEP_SKIP_SUFFIXES:
                continue
            try:
                if p.stat().st_size > SWEEP_MAX_BYTES:
                    continue
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue  # binary or unreadable: not a text sender
            code = [ln for ln in text.splitlines() if not ln.lstrip().startswith(("#", "//"))]
            n = sum(len(re.findall(r"ntfy\.sh|NTFY_TOPIC", ln)) for ln in code)
            if n:
                out[(p.relative_to(root).as_posix(), "sweep :: ntfy-mention")] += n


def scan(root: Path) -> Counter[tuple[str, str]]:
    """Every ntfy send shape under the scanned dirs, as {(file, identifier): count}."""
    out: Counter[tuple[str, str]] = Counter()
    covered: set[Path] = set()
    py_hits: dict[str, Counter[str]] = {}
    py_calls: dict[str, list[tuple[str, str]]] = {}
    py_imports: dict[str, set[str]] = {}
    for p in _files(root):
        rel = p.relative_to(root).as_posix()
        covered.add(p)
        if p.suffix in (".yml", ".yaml"):
            _scan_yaml(p, rel, out)
        elif p.suffix == ".sh":
            _scan_sh(p, rel, out)
        elif p.suffix == ".py":
            py_hits[rel], py_calls[rel], py_imports[rel] = _scan_py(p)
        else:
            _scan_js(p, rel, out)
    # Python wrappers: a function that contains a send shape. Its callers are senders too: calls
    # in the defining file, or in a file that imports the defining module (not a same-named method).
    wrappers: dict[str, set[str]] = {}
    for rel, hits in py_hits.items():
        for ident in hits:
            if ident.endswith(("ntfy-url", "_NTFY_BASE")) and not ident.startswith("<module>"):
                wrappers.setdefault(ident.split(" :: ")[0], set()).add(Path(rel).stem)
    for rel, hits in py_hits.items():
        for ident, n in hits.items():
            out[(rel, ident)] += n
        for where, called in py_calls[rel]:
            homes = wrappers.get(called, set())
            if where != called and (Path(rel).stem in homes or homes & py_imports[rel]):
                out[(rel, f"{where} :: calls {called}")] += 1
    _sweep(root, covered, out)
    return out


def _keep_counter() -> Counter[tuple[str, str]]:
    c: Counter[tuple[str, str]] = Counter()
    for f, idents in KEEP.items():
        for ident, n in idents.items():
            c[(f, ident.split(" ", 1)[1])] += n
    return c


# =================================================================================================
# tests
# =================================================================================================


def test_the_scan_finds_the_senders_it_is_meant_to_find() -> None:
    found = scan(ROOT)
    files = {f for f, _ in found}
    # guards against the scan silently matching nothing (rule 85b: name the surface covered)
    assert ".github/actions/bot-pr-sync/action.yml" in files
    assert "ml/notifications.py" in files and "worker-deadman/src/index.mjs" in files
    assert "scripts/live_freshness.py" in files and ".github/workflows/daily-digest.yml" in files


def violations(found: Counter[tuple[str, str]], keep: Counter[tuple[str, str]]) -> list[str]:
    """Everything wrong between a scan result and the KEEP list, as readable lines."""
    extra = [
        f"NOT on the KEEP list: {f} :: {ident} (x{n})"
        for (f, ident), n in sorted(found.items())
        if (f, ident) not in keep
    ]
    grown = [
        f"send count changed: {f} :: {ident}: found {n}, KEEP allows {keep[(f, ident)]}"
        for (f, ident), n in sorted(found.items())
        if (f, ident) in keep and n != keep[(f, ident)]
    ]
    stale = [
        f"KEEP entry no longer exists (remove it): {f} :: {ident}"
        for (f, ident) in sorted(keep)
        if (f, ident) not in found
    ]
    return extra + grown + stale


def test_every_ntfy_sender_is_on_the_keep_list() -> None:
    bad = violations(scan(ROOT), _keep_counter())
    assert not bad, "ntfy carries gold-price information only: " + "; ".join(bad)


def _tmp_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def test_a_new_yaml_extension_workflow_is_caught(tmp_path: Path) -> None:
    """A workflow named zz.yaml (not .yml) once bypassed the scan."""
    root = _tmp_tree(
        tmp_path,
        {
            ".github/workflows/zz.yaml": "jobs:\n  j:\n    steps:\n      - name: spam\n"
            '        run: curl -d hi "https://ntfy.sh/${NTFY_TOPIC}"\n',
            ".github/actions/x/action.yaml": "runs:\n  using: composite\n  steps:\n"
            '    - name: spam2\n      run: curl "https://ntfy.sh/t"\n',
        },
    )
    found = scan(root)
    assert (".github/workflows/zz.yaml", "step 'spam' :: ntfy-url") in found
    assert (".github/actions/x/action.yaml", "step 'spam2' :: ntfy-url") in found
    assert any("zz.yaml" in v for v in violations(found, Counter()))


def test_a_root_level_tool_outside_the_scanned_dirs_is_caught(tmp_path: Path) -> None:
    """A tools_g.py at the repo root once bypassed the scan; the repo-wide sweep catches it."""
    root = _tmp_tree(
        tmp_path,
        {
            "tools_g.py": 'import urllib.request\nurllib.request.urlopen("https://ntfy.sh/mytopic")\n',
            "notes.sh": "# curl https://ntfy.sh/x  (a comment is ignored)\necho ok\n",
            "docs/x.txt": "ntfy.sh is prose here and docs/ is skipped\n",
            "tests/t.py": 'X = "ntfy.sh"\n',
        },
    )
    found = scan(root)
    assert ("tools_g.py", "sweep :: ntfy-mention") in found
    assert not any(f in ("notes.sh", "docs/x.txt", "tests/t.py") for f, _ in found)
    assert any("tools_g.py" in v for v in violations(found, Counter()))


# ---- ml/notifications.py: the real function with a fake opener ---------------------------------

ALLOWED_TRIGGERS = {"T1", "T2", "T3", "T4", "T8_MORNING", "T8_EVENING", "T15"}


class _Resp:
    status = 200

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        return None


def _import_notifications_from(root: Path):
    saved = {k: v for k, v in sys.modules.items() if k == "ml" or k.startswith("ml.")}
    for k in saved:
        del sys.modules[k]
    sys.path.insert(0, str(root))
    try:
        return importlib.import_module("ml.notifications"), saved
    except Exception:
        sys.path.remove(str(root))
        sys.modules.update(saved)
        raise


def _emitted_ids(root: Path) -> set[str]:
    tree = ast.parse((root / "ml" / "notifications.py").read_text(encoding="utf-8"))
    return {
        c.args[0].value
        for c in ast.walk(tree)
        if isinstance(c, ast.Call)
        and getattr(c.func, "id", "") == "_make_alert"
        and c.args
        and isinstance(c.args[0], ast.Constant)
    }


def test_ml_notifications_can_only_post_the_allowed_trigger_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _emitted_ids(ROOT) | {"T99_UNLISTED"}
    assert len(ids) >= 15, "discovery found too few trigger ids"
    saved: dict = {}
    mod, saved = _import_notifications_from(ROOT)
    try:
        monkeypatch.setenv("NTFY_TOPIC", "ops-topic-aaaa")
        monkeypatch.setenv("NTFY_TOPIC_PUBLIC", "public-topic-bbbb")
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", os.devnull)
        now = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
        posted: set[str] = set()
        for tid in sorted(ids):
            urls: list[str] = []
            monkeypatch.setattr(
                mod.urllib.request,
                "urlopen",
                lambda req, timeout=0, _u=urls: (_u.append(req.full_url), _Resp())[1],
            )
            alert = mod._make_alert(tid, "title", "body", 3, ["x"], now)
            mod.send_pending([alert], mod.NotificationState(), now)
            if urls:
                posted.add(tid)
        assert posted <= ALLOWED_TRIGGERS, (
            f"ml/notifications.py can POST these ops/unlisted trigger ids: {sorted(posted - ALLOWED_TRIGGERS)}"
        )
        assert posted == ALLOWED_TRIGGERS, (
            f"allowed price alerts stopped posting: {sorted(ALLOWED_TRIGGERS - posted)}"
        )
    finally:
        for k in [k for k in sys.modules if k == "ml" or k.startswith("ml.")]:
            del sys.modules[k]
        sys.modules.update(saved)
        if str(ROOT) in sys.path:
            sys.path.remove(str(ROOT))


# ---- worker-deadman: postAlert only for forecast staleness -------------------------------------


def test_worker_post_alert_is_only_reached_for_forecast_staleness() -> None:
    text = _strip_js_comments(
        (ROOT / "worker-deadman" / "src" / "index.mjs").read_text(encoding="utf-8")
    )
    # postToNtfy: defined once, called once, from postAlert
    assert len(re.findall(r"\bpostToNtfy\(", text)) == 2  # definition + the call in postAlert
    # postAlert: defined once, called once, from deliver()
    post_alert_calls = re.findall(r"\bpostAlert\(([^)]*)\)", text)
    assert len(post_alert_calls) == 2, post_alert_calls  # definition + the call in deliver()
    assert re.search(r"const deliver = async \(toSend\) => \{\s*const r = await postAlert\(", text)
    # deliver(): its only call sites pass the forecast-staleness alert built by decideAction
    calls = [m.group(1).strip() for m in re.finditer(r"\bawait deliver\(([^)]*)\)", text)]
    assert calls == ["alert"], (
        f"deliver() reached with something other than the staleness alert: {calls}"
    )
    assert re.search(
        r"const \{ send, alert, nextState \} = decideAction\(current, previousState, nowMs\)", text
    )
