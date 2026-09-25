"""E2: a Tanishq block must reach GG through the EXISTING GitHub-side ntfy path.

The chain, each link exercised for real here (no notification is sent):
1. scraper/scrape.js exits non-zero on a Cloudflare block (exit 2) or any other failure.
2. scrape-tanishq-selfhosted.yml runs it under `set -e` with continue-on-error, so the
   step's `outcome` is "failure"; the health step's bash (executed below, verbatim from the
   workflow) increments data/tanishq_selfhosted_health.json's consecutive_job_failures.
3. ml.notifications reads that record and fires T12 at >= 3 consecutive failures.
4. ml.notification_routing sends T12 to the OPS topic (NTFY_TOPIC) -- GG, never the public
   topic. send_pending is driven with urlopen patched, so the request is captured, not sent.

Also pins the takedown fix: a deliberate takedown (switch enabled=false) must not count as a
failure (it would otherwise raise a false T12 every day), while a switch/config error still does.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from ml import notifications
from ml.notification_routing import OPS, audience_for
from ml.notifications import (
    NotificationState,
    check_triggers,
    compute_selfhosted_consecutive_failures,
    send_pending,
)

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "scrape-tanishq-selfhosted.yml"
SCRAPE_JS = ROOT / "scraper" / "scrape.js"
NOW_IST = datetime(2026, 9, 25, 14, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


def test_scrape_js_exits_nonzero_on_cloudflare_block():
    src = SCRAPE_JS.read_text(encoding="utf-8")
    block = re.search(r"if \(err\.isCFBlock\) \{(.*?)\}", src, re.S)
    assert block and "process.exit(2)" in block.group(1)
    assert "process.exit(1)" in src


def test_workflow_scrape_step_fails_its_outcome_on_a_nonzero_exit():
    wf = WORKFLOW.read_text(encoding="utf-8")
    step = wf.split("- name: Scrape Tanishq and update prices.json", 1)[1].split("- name:", 1)[0]
    assert "id: scrape" in step
    assert "continue-on-error: true" in step  # conclusion green, outcome still "failure"
    assert "set -e" in step and "node scrape.js" in step


def _health_script() -> str:
    """The workflow's own health-record bash, with the two expressions made variables."""
    wf = WORKFLOW.read_text(encoding="utf-8")
    start = wf.index('          HEALTH_FILE="data/tanishq_selfhosted_health.json"')
    end = wf.index('          } > "$HEALTH_FILE"') + len('          } > "$HEALTH_FILE"')
    lines = [ln[10:] if ln.startswith("          ") else ln for ln in wf[start:end].splitlines()]
    script = "\n".join(lines)
    script = script.replace("${{ steps.scrape.outcome }}", "$SCRAPE_OUTCOME")
    script = script.replace("${{ steps.switch.outputs.enabled }}", "$SWITCH_ENABLED")
    assert "${{" not in script, "unexpected GitHub expression left in the extracted script"
    return "set -e\n" + script + "\n"


def _run_cycle(workdir: Path, outcome: str, switch_enabled: str) -> int:
    bash = shutil.which("bash")
    subprocess.run(
        [bash, "-c", _health_script()],
        cwd=workdir,
        check=True,
        env={"SCRAPE_OUTCOME": outcome, "SWITCH_ENABLED": switch_enabled, "PATH": "/usr/bin:/bin"},
    )
    raw = json.loads((workdir / "data" / "tanishq_selfhosted_health.json").read_text())
    return int(raw["consecutive_job_failures"])


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    if shutil.which("bash") is None:
        pytest.skip("bash not available")
    (tmp_path / "data").mkdir()
    return tmp_path


def test_three_blocked_runs_reach_ops_via_t12_without_sending(workdir: Path, monkeypatch):
    counts = [_run_cycle(workdir, "failure", "true") for _ in range(3)]
    assert counts == [1, 2, 3]

    health = workdir / "data" / "tanishq_selfhosted_health.json"
    n = compute_selfhosted_consecutive_failures(health)
    assert n == 3
    alerts = check_triggers(
        {"price_source": "ibja_calibrated", "current_22k": 14100},
        {},
        [],
        {"folds": []},
        NotificationState(),
        NOW_IST,
        selfhosted_consecutive_failures=n,
    )
    t12 = [a for a in alerts if a.trigger_id == "T12"]
    assert len(t12) == 1
    assert audience_for("T12") == OPS

    captured = []

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured.append(req)
        return _Resp()

    monkeypatch.setattr(notifications.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("NTFY_TOPIC", "ops-topic-under-test")
    monkeypatch.setenv("NTFY_TOPIC_PUBLIC", "public-topic-under-test")
    send_pending(t12, NotificationState(), NOW_IST)
    assert len(captured) == 1
    assert captured[0].full_url.endswith("/ops-topic-under-test")
    assert "Tanishq" in captured[0].headers["Title"]


def test_success_resets_the_count(workdir: Path):
    _run_cycle(workdir, "failure", "true")
    _run_cycle(workdir, "failure", "true")
    assert _run_cycle(workdir, "success", "true") == 0


def test_takedown_skip_is_not_a_failure_but_a_switch_error_is(workdir: Path):
    assert _run_cycle(workdir, "failure", "true") == 1
    # Deliberate takedown: scrape skipped, switch said enabled=false -> count unchanged.
    assert _run_cycle(workdir, "skipped", "false") == 1
    assert _run_cycle(workdir, "skipped", "false") == 1
    # Switch step itself errored (no output) -> scrape skipped -> still a failure.
    assert _run_cycle(workdir, "skipped", "") == 2
