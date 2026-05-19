"""Guard against modules in ml/ that fail to import.

Catches PR-H-style cleanup regressions where code in a module
references a deleted dependency that is only discovered at runtime.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from pathlib import Path

import pytest

_ML_ROOT = Path(__file__).resolve().parent.parent / "ml"


def _ml_module_names() -> list[str]:
    ml_pkg = importlib.import_module("ml")
    return [
        f"ml.{info.name}"
        for info in pkgutil.iter_modules(ml_pkg.__path__)
        if not info.name.startswith("_")
    ]


@pytest.mark.parametrize("module_name", _ml_module_names())
def test_module_imports_without_error(module_name: str) -> None:
    """Each ml.* module must be importable with no ImportError or SyntaxError."""
    if module_name in sys.modules:
        return  # already imported by a prior test — counts as passing
    try:
        importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        # A missing external (non-ml) package is an optional dependency, not a
        # dead-import bug.  Skip so CI without optional extras doesn't fail here.
        if not (exc.name or "").startswith("ml."):
            pytest.skip(f"optional dependency not installed: {exc.name}")
        raise
