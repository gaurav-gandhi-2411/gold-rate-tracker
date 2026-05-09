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
