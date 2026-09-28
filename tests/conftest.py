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
