"""pwa-headless must outlast a slow Playwright browser install.

2026-10-22: 6 of the 7 non-green pwa-headless runs in 55 were the 10-minute job timeout firing while
`npx playwright install --with-deps chromium` took 7.7-10.0 minutes (normally 0.3-0.6); the headless tests
themselves take about 2.5. A limit below about 13 minutes turns a slow mirror into a red check."""

from __future__ import annotations

from pathlib import Path

import yaml

LINT = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "lint.yml"


def test_pwa_headless_timeout_covers_a_slow_browser_install() -> None:
    job = yaml.safe_load(LINT.read_text(encoding="utf-8"))["jobs"]["pwa-headless"]
    assert job["timeout-minutes"] >= 15
    steps = [s.get("name", "") for s in job["steps"]]
    assert "Install Playwright browsers" in steps
