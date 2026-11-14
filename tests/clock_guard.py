"""Clock guard: find tests whose outcome silently depends on the real clock (report-only).

Why (2026-11-06): master `lint` was red ~9 h because a test called ml.notifications.main(), which
reads the real clock; inside the T8 digest windows (08:00-13:59 and 18:00-21:59 IST) main() really
sends a digest and the test asserted nothing was sent. The test passed at other hours. Fixed in
#3620 by pinning the clock; this guard makes the next one visible before it bites.

Design (simplest that is reliable): production modules read the clock as ``datetime.now()`` /
``date.today()`` through a module-level name. For every imported ``ml.*`` and ``scripts.*`` module
we swap that name for a thin subclass whose ``now`` / ``utcnow`` / ``today`` record
(test node id, module, caller file:line) and then delegate to the real clock. A test that pins the
clock (monkeypatches the module's ``datetime`` / ``date``, patches a ``_now()`` helper, or passes
``now=``) never reaches the guard class, so nothing is recorded for it. No frozen-time library, no
source scanning, no change in behaviour:
  * ``__new__`` returns REAL datetime/date objects, so ``type(x)``, arithmetic, pickling and pandas
    are unaffected; a metaclass keeps ``isinstance(x, datetime)`` true for real instances.
  * delegation is to the real class, so results are identical.
  * cost: one dict scan of each newly imported module (once), one frame lookup per clock read.

Sweep mode: CLOCK_GUARD_FIXED=<ISO instant with offset> makes every UNPINNED read return that
instant instead of the real clock (scripts/clock_sweep.py uses it to run the suite at several
instants and diff pass/fail). Without the variable the real clock is used.

Limits (what the guard cannot see): ``time.time()`` / ``time.monotonic()`` reads (pytest itself
uses them, so they are not swapped); ``import datetime`` then ``datetime.datetime.now()`` (the
repo does not use that shape in ml/ or scripts/; checked at design time); clock reads made by
test files themselves, by third-party code, or inside a child process (subprocess tests); reads
through ``from ml.x import datetime`` re-exports; a module imported only inside a test body is
swapped from the next test on, not the current one.

MODE is 'report': never fails a test. Flip to 'fail' only per the rule in docs/RUNBOOK.md
('Clock guard'): 5 clean real runs spanning >= 24 h, in a separate PR. tests/test_clock_guard.py
pins this constant.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

MODE = "report"
PREFIXES = ("ml.", "scripts.")
ALLOWLIST_FILE = Path(__file__).with_name("clock_guard_allowlist.txt")

_REAL_DATETIME = _dt.datetime
_REAL_DATE = _dt.date


@dataclass(frozen=True)
class Read:
    """One unpinned clock read."""

    nodeid: str
    module: str
    where: str


def _fixed_instant() -> _dt.datetime | None:
    raw = os.environ.get("CLOCK_GUARD_FIXED")
    if not raw:
        return None
    value = _REAL_DATETIME.fromisoformat(raw)
    if value.tzinfo is None:
        raise ValueError("CLOCK_GUARD_FIXED needs a UTC offset, e.g. 2026-11-09T09:00:00+00:00")
    return value


class ClockGuard:
    """Holds the recorded reads and installs the guard classes into modules."""

    def __init__(self) -> None:
        self.reads: list[Read] = []
        self.current = ""
        self._seen_modules: set[int] = set()
        self.datetime_cls, self.date_cls = self._make_classes()

    def _record(self, depth: int = 2) -> None:
        frame = sys._getframe(depth)
        module = str(frame.f_globals.get("__name__", "?"))
        where = f"{Path(frame.f_code.co_filename).name}:{frame.f_lineno}"
        self.reads.append(Read(self.current, module, where))

    def _make_classes(self) -> tuple[type, type]:
        guard = self

        class _DtMeta(type):
            def __instancecheck__(cls, obj: object) -> bool:
                return isinstance(obj, _REAL_DATETIME)

            def __subclasscheck__(cls, sub: type) -> bool:
                return issubclass(sub, _REAL_DATETIME)

        class _DateMeta(type):
            def __instancecheck__(cls, obj: object) -> bool:
                return isinstance(obj, _REAL_DATE)

            def __subclasscheck__(cls, sub: type) -> bool:
                return issubclass(sub, _REAL_DATE)

        class GuardDatetime(_REAL_DATETIME, metaclass=_DtMeta):
            def __new__(cls, *args, **kwargs):
                return _REAL_DATETIME(*args, **kwargs)

            @classmethod
            def now(cls, tz=None):
                guard._record()
                fixed = _fixed_instant()
                if fixed is None:
                    return _REAL_DATETIME.now(tz)
                if tz is None:
                    return fixed.astimezone().replace(tzinfo=None)
                return fixed.astimezone(tz)

            @classmethod
            def utcnow(cls):
                guard._record()
                fixed = _fixed_instant()
                if fixed is None:
                    return _REAL_DATETIME.now(_dt.UTC).replace(tzinfo=None)
                return fixed.astimezone(_dt.UTC).replace(tzinfo=None)

            @classmethod
            def today(cls):
                guard._record()
                fixed = _fixed_instant()
                if fixed is None:
                    return _REAL_DATETIME.now()
                return fixed.astimezone().replace(tzinfo=None)

        class GuardDate(_REAL_DATE, metaclass=_DateMeta):
            def __new__(cls, *args, **kwargs):
                return _REAL_DATE(*args, **kwargs)

            @classmethod
            def today(cls):
                guard._record()
                fixed = _fixed_instant()
                if fixed is None:
                    return _REAL_DATE.today()
                return fixed.astimezone().date()

        return GuardDatetime, GuardDate

    def swap_module(self, mod: ModuleType) -> int:
        """Replace every name in ``mod`` bound to the real datetime/date class; return the count."""
        n = 0
        for name, value in list(vars(mod).items()):
            if value is _REAL_DATETIME:
                setattr(mod, name, self.datetime_cls)
                n += 1
            elif value is _REAL_DATE:
                setattr(mod, name, self.date_cls)
                n += 1
        return n

    def install(self, prefixes: tuple[str, ...] = PREFIXES) -> None:
        """Swap newly imported modules (each module object once; cheap on later calls)."""
        for name, mod in list(sys.modules.items()):
            if mod is None or id(mod) in self._seen_modules:
                continue
            if name.startswith(prefixes) and isinstance(mod, ModuleType):
                self._seen_modules.add(id(mod))
                self.swap_module(mod)

    def begin_test(self, nodeid: str) -> None:
        self.current = nodeid
        self.install()

    # ---- reporting -------------------------------------------------------------------------

    def unreviewed(self, allow: set[tuple[str, str]] | None = None) -> list[Read]:
        allow = load_allowlist() if allow is None else allow
        return [r for r in self.reads if (r.nodeid.split("::")[0], r.module) not in allow]

    def table(self, reads: list[Read]) -> list[str]:
        by_mod: dict[str, list[Read]] = defaultdict(list)
        for r in reads:
            by_mod[r.module].append(r)
        lines = [f"{'module':44} {'tests':>5}  example"]
        for mod, items in sorted(by_mod.items()):
            tests = {r.nodeid for r in items}
            ex = items[0]
            lines.append(f"{mod:44} {len(tests):>5}  {ex.nodeid} ({ex.where})")
        return lines

    def finish(
        self, mode: str = MODE, allow: set[tuple[str, str]] | None = None
    ) -> tuple[list[str], bool]:
        """Return (report lines, should_fail). should_fail is only ever True in 'fail' mode."""
        dump = os.environ.get("CLOCK_GUARD_DUMP")  # measurement aid: every read, allowlist ignored
        if dump:
            Path(dump).write_text(
                "\n".join(f"{r.nodeid}\t{r.module}\t{r.where}" for r in self.reads) + "\n",
                encoding="utf-8",
            )
        reads = self.unreviewed(allow)
        if not reads:
            return [], False
        lines = self.table(reads)
        tests = {r.nodeid for r in reads}
        summary = (
            f"clock guard: {len(tests)} test(s) read the real clock unpinned in "
            f"{len({r.module for r in reads})} module(s); pin the clock or add the "
            f"test file + module to tests/clock_guard_allowlist.txt with a reason "
            f"(mode={mode})"
        )
        if os.environ.get("GITHUB_ACTIONS"):
            lines.append(f"::warning title=clock-guard::{summary}")
        else:
            lines.append(summary)
        return lines, mode == "fail"


def load_allowlist(path: Path = ALLOWLIST_FILE) -> set[tuple[str, str]]:
    """Entries: ``<tests/test_file.py> <module>  # reason`` (reason mandatory by convention)."""
    out: set[tuple[str, str]] = set()
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            parts = line.split()
            if len(parts) == 2:
                out.add((parts[0], parts[1]))
    return out
