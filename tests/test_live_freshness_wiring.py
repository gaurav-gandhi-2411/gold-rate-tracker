"""ci-health runs scripts/live_freshness.py on every run, from the reliable triggers, with state."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _wf() -> dict:
    return yaml.safe_load((ROOT / ".github/workflows/ci-health.yml").read_text(encoding="utf-8"))


def test_ci_health_runs_it_every_time_with_state_carried_in_the_cache():
    wf = _wf()
    steps = wf["jobs"]["check"]["steps"]
    names = [s.get("name", "") for s in steps]
    i = names.index("Check that what visitors see is fresh (live site JSON)")
    restore, check, save = steps[i - 1], steps[i], steps[i + 1]
    assert "actions/cache/restore@" in restore["uses"] and "actions/cache/save@" in save["uses"]
    assert restore["with"]["path"] == save["with"]["path"] == ".live-freshness/state.json"
    assert restore["with"]["restore-keys"] == "live-freshness-"
    for s in (restore, check, save):
        assert s["if"].startswith("always()")
    assert "scripts/live_freshness.py" in check["run"] and "NTFY_TOPIC" in check["env"]
    assert "continue-on-error" not in check  # an incident shows red while it lasts
    # one run at a time, so overlapping triggers cannot send the same page twice
    assert wf["concurrency"] == {"group": "ci-health", "cancel-in-progress": False}
    on = wf.get("on", wf.get(True))
    assert on["workflow_dispatch"]["inputs"]["freshness_simulate"]["options"] == [
        "none",
        "stale",
        "fresh",
    ]


def test_the_reliable_triggers_start_ci_health():
    # check-price's starter and the laptop's side task both start ci-health (cron is the fallback)
    cp = (ROOT / ".github/workflows/check-price.yml").read_text(encoding="utf-8")
    assert "gh workflow run ci-health.yml" in cp
    laptop = (ROOT / "scripts/win/tanishq_dispatch.ps1").read_text(encoding="utf-8")
    assert "gh workflow run ci-health.yml" in laptop
