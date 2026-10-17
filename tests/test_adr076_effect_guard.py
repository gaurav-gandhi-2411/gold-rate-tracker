"""ADR 076 guard: only the registered look may compute hourly-model effects on entries after the cut-off.

Two producers (the check-price summary and the weekly scorecard) once did, unnoticed, because nothing
listed who computes those effects. This test lists them by reading every non-test Python file of the
repository (AST), not from a hand-kept list:

* every function that calls an effect primitive of the hourly model (``ml.nextfix_intraday.score`` or
  ``_cluster_ci``), with calls resolved through the file's imports, and
* every function (and module body) that touches the hourly shadow log or the hourly backtest file.

Each one found must be classified below. A new one fails this test until someone reads it and decides
whether it is the look, is frozen at the cut-off, or computes nothing. A frozen one must name the cut-off
in its source and name a test that exercises the freeze. The look itself must compute its effects only
after the closed-look returns of ``evaluate_window_076``."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
SKIP_DIRS = {"tests", "node_modules", "__pycache__", "site-packages"}
PRIMITIVES = {
    ("ml/nextfix_intraday.py", "score"),
    ("ml/nextfix_intraday.py", "_cluster_ci"),
}
SHADOW_NAMES = {"SHADOW_PATH", "BACKTEST_PATH", "load_shadow"}
SHADOW_FILES = ("nextfix_intraday_shadow", "nextfix_intraday_backtest")
CUTOFF_LITERAL = "2026-10-16T17:38:00Z"
MODULE_BODY = "<module>"

# -- the registry: everything the enumeration finds must be here, with a reason -------------------

LOOK = "look"  # the registered ADR 076 look (scripts/check_adr066_promotion.py --adr 076)
FROZEN = "frozen"  # computes effects, but only on data up to the cut-off; a test pins it
NO_EFFECT = "no_effect"  # touches the data without computing any effect
PRIMITIVE = "primitive"  # an effect primitive itself

_CHECK = "scripts/check_adr066_promotion.py"
_INTRA = "ml/nextfix_intraday.py"
_CARD = "scripts/build_model_scorecard.py"
REGISTRY: dict[str, tuple[str, str, tuple[str, ...]]] = {
    f"{_INTRA}::score": (
        PRIMITIVE,
        "MAE change, cluster bootstrap CI and DM p of the hourly model",
        (),
    ),
    f"{_INTRA}::_cluster_ci": (PRIMITIVE, "cluster bootstrap interval", ()),
    f"{_INTRA}::run": (
        FROZEN,
        "check-price summary: scores only entries logged at or before the cut-off",
        ("test_run_scores_only_entries_up_to_the_adr076_cutoff",),
    ),
    f"{_INTRA}::backtest": (
        FROZEN,
        "replayed hourly decisions: scored only when the target fix was published by the cut-off",
        ("test_backtest_scores_only_decisions_resolved_by_the_adr076_cutoff",),
    ),
    f"{_INTRA}::load_shadow": (NO_EFFECT, "reads the log", ()),
    f"{_INTRA}::save_shadow": (NO_EFFECT, "writes the log and the summary it is given", ()),
    f"{_CARD}::_intraday_rows": (
        FROZEN,
        "weekly scorecard rows of the hourly shadow: entries up to the cut-off only",
        ("test_intraday_rows_are_frozen_at_the_adr076_cutoff",),
    ),
    f"{_CHECK}::run_check": (
        FROZEN,
        "ADR 066 check: shadow entries and replayed decisions up to the cut-off only",
        ("test_the_adr066_check_ignores_everything_after_the_adr076_cutoff",),
    ),
    f"{_CHECK}::load_backtest": (
        FROZEN,
        "ADR 066 backtest scores: replayed decisions resolved by the cut-off only",
        ("test_the_adr066_check_ignores_everything_after_the_adr076_cutoff",),
    ),
    f"{_CHECK}::shadow_window_stats": (
        NO_EFFECT,
        "scores the entries it is given; its only caller, run_check, passes entries up to the cut-off",
        (),
    ),
    f"{_CHECK}::adr076_effect": (LOOK, "conditions a to c of the look", ()),
    f"{_CHECK}::run_adr076": (NO_EFFECT, "loads the log and the frozen file, calls the look", ()),
    f"{_INTRA}::<module>": (NO_EFFECT, "path constants of the log and the backtest file", ()),
    "scripts/data_crypt.py::<module>": (NO_EFFECT, "encryption manifest naming the log", ()),
}


# -- enumeration (pure functions over {relative path: source}) -------------------------------------


def repo_sources() -> dict[str, str]:
    out = {}
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT)
        if set(rel.parts[:-1]) & SKIP_DIRS or any(x.startswith(".") for x in rel.parts[:-1]):
            continue
        out[rel.as_posix()] = p.read_text(encoding="utf-8")
    return out


def _module_names(rel: str) -> set[str]:
    dotted = rel[:-3].replace("/", ".")
    names = {dotted}
    if rel.startswith("scripts/"):
        names.add(Path(rel).stem)  # scripts are imported by bare name from scripts/
    return names


class Repo:
    def __init__(self, sources: dict[str, str]):
        self.trees = {rel: ast.parse(src) for rel, src in sources.items()}
        self.modules = {n: rel for rel in sources for n in _module_names(rel)}
        self.defs: dict[str, dict[str, list[ast.AST]]] = {}
        for rel, tree in self.trees.items():
            d: dict[str, list[ast.AST]] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    d.setdefault(node.name, []).append(node)
            self.defs[rel] = d
        self.aliases = {rel: self._aliases(rel, tree) for rel, tree in self.trees.items()}

    def _aliases(self, rel: str, tree: ast.AST) -> dict[str, tuple[str, str | None]]:
        """local name -> (module path, function name or None)."""
        out: dict[str, tuple[str, str | None]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in self.modules:
                        out[a.asname or a.name.split(".")[0]] = (self.modules[a.name], None)
            elif isinstance(node, ast.ImportFrom) and node.module is not None and not node.level:
                for a in node.names:
                    local = a.asname or a.name
                    if f"{node.module}.{a.name}" in self.modules:
                        out[local] = (self.modules[f"{node.module}.{a.name}"], None)
                    elif node.module in self.modules:
                        out[local] = (self.modules[node.module], a.name)
        return out

    def functions(self):
        """(qualified name, node) for every function and for each module body."""
        for rel, tree in self.trees.items():
            yield f"{rel}::{MODULE_BODY}", tree
            for name, nodes in self.defs[rel].items():
                for n in nodes:
                    yield f"{rel}::{name}", n

    def resolve(self, rel: str, call: ast.Call) -> tuple[str, str] | None:
        f = call.func
        if isinstance(f, ast.Name):
            if f.id in self.defs[rel]:
                return rel, f.id
            mod, fn = self.aliases[rel].get(f.id, (None, None))
            if mod and fn and fn in self.defs[mod]:
                return mod, fn
        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            mod, fn = self.aliases[rel].get(f.value.id, (None, None))
            if mod and fn is None and f.attr in self.defs[mod]:
                return mod, f.attr
        return None

    def own_nodes(self, node: ast.AST):
        """Nodes of a function, or of a module body without its function bodies."""
        stack = [node]
        while stack:
            n = stack.pop()
            for child in ast.iter_child_nodes(n):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) and not isinstance(
                    node, ast.FunctionDef | ast.AsyncFunctionDef
                ):
                    continue  # a module body does not own its functions' bodies
                yield child
                stack.append(child)

    def callers_of(self, targets: set[tuple[str, str]]) -> set[str]:
        found = set()
        for qual, node in self.functions():
            rel = qual.split("::")[0]
            for n in self.own_nodes(node):
                if isinstance(n, ast.Call) and self.resolve(rel, n) in targets:
                    found.add(qual)
        return found

    def touches_hourly_data(self) -> set[str]:
        """Functions that name the hourly log/backtest file, or its path constants/loader of the hourly
        module (only in files that use that module: other modules have their own BACKTEST_PATH)."""
        found = set()
        for qual, node in self.functions():
            rel = qual.split("::")[0]
            uses_hourly = rel == _INTRA or any(m == _INTRA for m, _ in self.aliases[rel].values())
            doc_strings = {
                id(n.value)
                for n in ast.walk(node)
                if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
            }  # docstrings and bare strings are prose, not reads
            for n in self.own_nodes(node):
                if isinstance(n, ast.Name | ast.Attribute):
                    name = n.id if isinstance(n, ast.Name) else n.attr
                    if uses_hourly and name in SHADOW_NAMES:
                        found.add(qual)
                elif isinstance(n, ast.Constant) and isinstance(n.value, str):
                    if id(n) not in doc_strings and any(s in n.value for s in SHADOW_FILES):
                        found.add(qual)
        return found

    def source_of(self, sources: dict[str, str], qual: str) -> str:
        rel, name = qual.split("::")
        if name == MODULE_BODY:
            return sources[rel]
        return "\n".join(
            ast.get_source_segment(sources[rel], n) or "" for n in self.defs[rel][name]
        )


def producers(repo: Repo) -> set[str]:
    prim = {(r, n) for r, n in PRIMITIVES}
    return repo.callers_of(prim) | repo.touches_hourly_data() | {f"{r}::{n}" for r, n in prim}


# -- the tests -------------------------------------------------------------------------------------

SOURCES = repo_sources()
REPO = Repo(SOURCES)


def test_every_producer_of_hourly_effects_or_reader_of_the_hourly_data_is_classified():
    found = producers(REPO)
    unclassified = sorted(found - REGISTRY.keys())
    assert not unclassified, (
        "These functions compute hourly-model effects or read the hourly shadow/backtest data and are "
        "not classified in tests/test_adr076_effect_guard.py (ADR 076: nothing but the registered look "
        f"may compute effects on entries after the cut-off): {unclassified}"
    )


def test_the_registry_has_no_stale_entries():
    found = producers(REPO)
    stale = sorted(set(REGISTRY) - found)
    assert not stale, f"classified but no longer found (remove them): {stale}"


def test_a_frozen_producer_names_the_cutoff_and_a_test_that_pins_it():
    tests_text = "\n".join(p.read_text(encoding="utf-8") for p in TESTS.glob("test_*.py"))
    problems = []
    for qual, (status, why, pinned) in REGISTRY.items():
        if status != FROZEN:
            continue
        src = REPO.source_of(SOURCES, qual)
        if "ADR076_CUTOFF" not in src and CUTOFF_LITERAL not in src:
            problems.append(f"{qual} ({why}): its source does not name the cut-off")
        if not pinned:
            problems.append(f"{qual} is frozen but cites no test")
        problems += [
            f"{qual}: pinning test {name} does not exist"
            for name in pinned
            if f"def {name}(" not in tests_text
        ]
    assert not problems, "frozen producers that are not frozen:\n" + "\n".join(problems)


def test_a_no_effect_producer_calls_no_effect_primitive():
    direct = REPO.callers_of(set(PRIMITIVES))
    for qual, (status, why, _) in REGISTRY.items():
        if status == NO_EFFECT:
            assert qual not in direct or qual.endswith("::shadow_window_stats"), (qual, why)


def test_the_primitives_are_called_only_from_classified_places():
    callers = REPO.callers_of(set(PRIMITIVES))
    allowed = {q for q, (s, _, _) in REGISTRY.items() if s in (FROZEN, LOOK, PRIMITIVE)} | {
        f"{_CHECK}::shadow_window_stats"
    }
    assert callers <= allowed, sorted(callers - allowed)


def test_the_look_effects_have_one_caller_and_come_after_every_closed_return():
    look_fns = {(_CHECK, "adr076_effect")}
    assert REPO.callers_of(look_fns) == {f"{_CHECK}::evaluate_window_076"}
    (fn,) = REPO.defs[_CHECK]["evaluate_window_076"]
    calls = [
        n.lineno
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "adr076_effect"
    ]
    closed_returns = [
        n.end_lineno
        for n in ast.walk(fn)
        if isinstance(n, ast.If)
        and any(isinstance(b, ast.Return) for b in n.body)
        and ast.unparse(n.test) in ("day is None", "folds is None")
    ]
    assert len(calls) == 1 and len(closed_returns) == 2
    assert all(c > max(closed_returns) for c in calls), (calls, closed_returns)


def test_cond_c_and_the_one_sided_dm_are_reachable_only_from_the_look():
    for name in ("cond_c_076", "dm_p_one_sided"):
        assert REPO.callers_of({(_CHECK, name)}) <= {f"{_CHECK}::adr076_effect"}, name


# -- the enumeration itself must see a new producer (synthetic sources) ----------------------------


def _repo(**files: str) -> Repo:
    base = {k: v for k, v in SOURCES.items() if k in (_INTRA,)}
    return Repo({**base, **{k.replace("__", "/") + ".py": v for k, v in files.items()}})


def test_a_new_caller_of_score_through_a_module_alias_is_found():
    r = _repo(
        scripts__sneaky="from ml import nextfix_intraday as ni\n\ndef f(rows):\n    return ni.score(rows)\n"
    )
    assert "scripts/sneaky.py::f" in producers(r)


def test_a_new_caller_of_score_through_a_from_import_is_found():
    r = _repo(
        ml__other="from ml.nextfix_intraday import score as s\n\ndef g(rows):\n    return s(rows)\n"
    )
    assert "ml/other.py::g" in producers(r)


def test_a_new_reader_of_the_shadow_log_is_found_even_without_calling_score():
    r = _repo(
        scripts__mine="import json\n\ndef h():\n    return json.load(open('data/nextfix_intraday_shadow.json'))\n"
    )
    assert "scripts/mine.py::h" in producers(r)


def test_an_unrelated_score_function_is_not_mistaken_for_the_hourly_one():
    r = _repo(ml__elsewhere="def score(x):\n    return x\n\ndef k(x):\n    return score(x)\n")
    assert "ml/elsewhere.py::k" not in producers(r)
