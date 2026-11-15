"""Tests for tests/clock_guard.py: the mode is pinned, and the detector is proven on synthetic modules."""

from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests import clock_guard

ROOT = Path(__file__).resolve().parent.parent

SOURCE = """
from datetime import datetime


def unpinned():
    return datetime.now()


def explicit(now):
    return now
"""


def _module(guard: clock_guard.ClockGuard, name: str = "ml.synthetic_clock_mod") -> ModuleType:
    mod = ModuleType(name)
    exec(SOURCE, mod.__dict__)
    guard.swap_module(mod)
    return mod


def test_mode_is_report() -> None:
    """Flipping to 'fail' must be a visible, deliberate change (RUNBOOK 'Clock guard')."""
    assert clock_guard.MODE == "report"


def test_unpinned_read_is_flagged_in_fail_mode() -> None:
    guard = clock_guard.ClockGuard()
    mod = _module(guard)
    guard.current = "tests/test_x.py::test_a"
    mod.unpinned()
    lines, should_fail = guard.finish("fail", allow=set())
    assert should_fail is True
    assert any("ml.synthetic_clock_mod" in line for line in lines)


def test_pinned_and_explicit_now_are_not_flagged() -> None:
    guard = clock_guard.ClockGuard()
    mod = _module(guard)
    guard.current = "tests/test_x.py::test_b"
    fixed = dt.datetime(2026, 11, 9, 9, 0, tzinfo=dt.UTC)

    class Pinned(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    mod.datetime = Pinned  # what monkeypatch.setattr(module, "datetime", ...) does
    assert mod.unpinned() == fixed
    assert mod.explicit(fixed) == fixed
    assert guard.finish("fail", allow=set()) == ([], False)


def test_allowlisted_file_and_module_pair_is_not_reported() -> None:
    guard = clock_guard.ClockGuard()
    mod = _module(guard)
    guard.current = "tests/test_x.py::test_c"
    mod.unpinned()
    assert guard.finish("fail", allow={("tests/test_x.py", "ml.synthetic_clock_mod")}) == (
        [],
        False,
    )


def test_report_mode_never_fails_even_with_reads() -> None:
    guard = clock_guard.ClockGuard()
    mod = _module(guard)
    guard.current = "tests/test_x.py::test_d"
    mod.unpinned()
    lines, should_fail = guard.finish(clock_guard.MODE, allow=set())
    assert lines and should_fail is False


def test_guard_does_not_change_behaviour() -> None:
    guard = clock_guard.ClockGuard()
    mod = _module(guard)
    before = dt.datetime.now()
    got = mod.unpinned()
    assert type(got) is dt.datetime  # real class, not the guard subclass
    assert before <= got <= dt.datetime.now()
    assert isinstance(got, mod.datetime)
    made = mod.datetime(2026, 1, 31, 12, 0)
    assert type(made) is dt.datetime and (made + dt.timedelta(days=1)).month == 2


def test_fixed_instant_sweep_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOCK_GUARD_FIXED", "2026-11-09T09:00:00+00:00")
    guard = clock_guard.ClockGuard()
    mod = _module(guard)
    got = mod.datetime.now(dt.UTC)
    assert got == dt.datetime(2026, 11, 9, 9, 0, tzinfo=dt.UTC)


def test_guard_in_report_mode_does_not_touch_data_only_selection() -> None:
    """The data-only lint path selects tests by what they read; the guard must not enter it."""
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "select_data_tests.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert out.returncode == 0
    selected = out.stdout.splitlines()
    assert "tests/test_clock_guard.py" not in selected
    measured = (ROOT / "tests" / "data_reading_tests.txt").read_text(encoding="utf-8")
    assert "test_clock_guard" not in measured
    assert clock_guard.MODE == "report"  # in report mode finish() can never fail a session
