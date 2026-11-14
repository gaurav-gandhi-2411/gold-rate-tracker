"""Shared pytest fixtures and session-level setup."""

import os
import sys

# Force UTF-8 stdout/stderr so MLflow's emoji output (🏃 View run at: ...) doesn't
# crash on Windows terminals that default to CP1252.
if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


import pytest


@pytest.fixture(autouse=True)
def _no_ops_log_file_writes(monkeypatch):
    """scripts/ops_log.py appends to a branch of the real repo inside Actions; never from a test."""
    monkeypatch.setenv("OPS_LOG_NO_FILE", "1")


def pytest_configure(config):
    """FOR_GG item 11: record which tests read data/, only when DATA_READS_OUT is set (the lint
    job sets it on data-only PRs). Recording only; a failure to register is a printed warning."""
    out = os.environ.get("DATA_READS_OUT")
    if not out:
        return
    try:
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(root / "scripts"))
        try:
            import data_reads_plugin
        finally:
            sys.path.remove(str(root / "scripts"))
        config.pluginmanager.register(
            data_reads_plugin.DataReads(root, Path(out)), "data_reads_plugin"
        )
    except Exception as exc:
        print(f"data_reads_plugin not registered ({exc}); recording off", file=sys.stderr)


@pytest.fixture(autouse=True)
def _polite_http_no_real_sleep():
    """ADR 059: the retailer HTTP policy keeps per-host state and really sleeps
    between same-host requests. Tests must neither wait on it nor leak a host
    cool-off from one test into the next, so every test starts with a clean
    policy whose sleep is a no-op (the calls are still recorded for assertions).
    """
    from ml.sources import polite_http

    policy = polite_http.DEFAULT_POLICY
    original_sleep = policy.sleep
    slept: list[float] = []
    policy.sleep = slept.append
    policy.reset()
    yield slept
    policy.sleep = original_sleep
    policy.reset()


# Clock guard (report-only, see tests/clock_guard.py): records unpinned real-clock reads.
from tests import clock_guard as _clock_guard

_CLOCK_GUARD = _clock_guard.ClockGuard()


@pytest.fixture(autouse=True)
def _clock_guard_track(request):
    _CLOCK_GUARD.begin_test(request.node.nodeid)
    yield


def pytest_terminal_summary(terminalreporter):
    lines, _ = _CLOCK_GUARD.finish(_clock_guard.MODE)
    if lines:
        terminalreporter.section("clock guard (report-only)")
        for line in lines:
            terminalreporter.write_line(line)
