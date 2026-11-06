"""pytest plugin: record, per test, whether it read the committed data (data/) and how it ended.

FOR_GG item 11 (GG decision 2026-11-03, option C refined). On a data-only PR the tests that read
data/ decide; which tests those are is MEASURED here at run time, not kept in a list: an audit hook
(``sys.addaudithook``, event "open") sees every file a test opens, and a ``git show <rev>:data/...``
run as a subprocess is seen through the "subprocess.Popen" event. A file read by a child process
is NOT seen; scripts/data_test_decision.py covers that by also selecting, statically, every test
file whose source names data/ (scripts/select_data_tests.py).

Recording only: every hook swallows its own errors, so this plugin can never fail a test or the run.
It is registered by tests/conftest.py only when DATA_READS_OUT is set (the lint job sets it on
data-only PRs); otherwise nothing here runs. Output (JSON, at DATA_READS_OUT):
  {"version": 1, "exitstatus": int, "collect_errors": [nodeid, ...],
   "collect_reads": {test_file: [path, ...]},   # reads while that file was imported/collected
   "tests": {nodeid: {"outcome": "passed" | "failed" | "skipped", "data_reads": [path, ...]}}}
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

MAX_PATHS = 5  # per test; enough to say what it read
COLLECT = "collect::"  # key prefix for reads made while a test file is collected (imported)


class DataReads:
    def __init__(self, root: Path, out: Path) -> None:
        self.data = os.path.normcase(str((root / "data").resolve()))
        self.out = out
        self.current: str | None = None
        self.reads: dict[str, set[str]] = {}
        self.outcomes: dict[str, str] = {}
        self.collect_errors: list[str] = []
        sys.addaudithook(self._hook)  # cannot be removed; inert once `current` is None

    def _hook(self, event: str, args: tuple[Any, ...]) -> None:
        node = self.current
        if node is None:
            return
        try:
            if event == "open":
                p = args[0]
                if isinstance(p, int):
                    return
                s = os.path.normcase(os.path.abspath(os.fsdecode(p)))
                if s == self.data or s.startswith(self.data + os.sep):
                    self._add(node, "data" + s[len(self.data) :].replace(os.sep, "/"))
            elif event == "subprocess.Popen":
                argv = args[1]
                joined = " ".join(map(str, argv)) if isinstance(argv, (list, tuple)) else str(argv)
                if ":data/" in joined:
                    self._add(node, "git:" + joined[:120])
        except Exception:
            pass

    def _add(self, node: str, path: str) -> None:
        got = self.reads.setdefault(node, set())
        if len(got) < MAX_PATHS:
            got.add(path)

    @pytest.hookimpl(hookwrapper=True)
    def pytest_make_collect_report(self, collector: pytest.Collector):  # type: ignore[no-untyped-def]
        # a read while a test FILE is imported/collected (a module-level load) belongs to that file
        prev = self.current
        try:
            nodeid = collector.nodeid.split("::", 1)[0]
            if nodeid.endswith(".py"):
                self.current = COLLECT + nodeid
        except Exception:
            pass
        try:
            yield
        finally:
            self.current = prev

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(self, item: pytest.Item, nextitem: pytest.Item | None):  # type: ignore[no-untyped-def]
        self.current = item.nodeid
        try:
            yield
        finally:
            self.current = None

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        try:
            if report.failed:
                self.outcomes[report.nodeid] = "failed"
            elif report.skipped and report.nodeid not in self.outcomes:
                self.outcomes[report.nodeid] = "skipped"
            elif report.when == "call" and report.nodeid not in self.outcomes:
                self.outcomes[report.nodeid] = "passed"
        except Exception:
            pass

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        try:
            if report.failed:
                self.collect_errors.append(report.nodeid or "<collection>")
        except Exception:
            pass

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        try:
            tests = {
                n: {"outcome": o, "data_reads": sorted(self.reads.get(n, ()))}
                for n, o in sorted(self.outcomes.items())
            }
            collect_reads = {
                n[len(COLLECT) :]: sorted(v)
                for n, v in sorted(self.reads.items())
                if n.startswith(COLLECT)
            }
            doc = {
                "version": 1,
                "exitstatus": int(exitstatus),
                "collect_errors": self.collect_errors,
                "collect_reads": collect_reads,
                "tests": tests,
            }
            self.out.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        except Exception as exc:  # recording only: a missing file reads as "error" downstream
            print(f"data_reads_plugin: could not write {self.out}: {exc}", file=sys.stderr)
