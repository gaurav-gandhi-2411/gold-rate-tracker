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


def test_a_simulated_run_never_reads_or_writes_the_real_incident_state():
    # A [TEST] dispatch must not restore the real state (it would judge a made-up payload against a
    # real open incident) nor save over it (the next real run would forget, or re-page, an incident).
    steps = _wf()["jobs"]["check"]["steps"]
    names = [s.get("name", "") for s in steps]
    i = names.index("Check that what visitors see is fresh (live site JSON)")
    restore, check, save = steps[i - 1], steps[i], steps[i + 1]
    real_only = "(inputs.freshness_simulate || 'none') == 'none'"
    assert real_only in restore["if"] and real_only in save["if"]
    # schedule runs have no inputs: the `|| 'none'` default makes them real runs
    assert check["env"]["SIMULATE"] == "${{ inputs.freshness_simulate || 'none' }}"
    real, _, simulated = check["run"].partition("else")
    assert 'if [ "$SIMULATE" = "none" ]' in real
    assert "--state-in .live-freshness/state.json --state-out .live-freshness/state.json" in real
    assert '--simulate "$SIMULATE"' in simulated and "--state" not in simulated
    # one key per run attempt: cache keys are immutable, so a fixed key would save only once
    assert save["with"]["key"] == "live-freshness-${{ github.run_id }}-${{ github.run_attempt }}"
    assert "hashFiles('.live-freshness/state.json') != ''" in save["if"]


def test_the_reliable_triggers_start_ci_health():
    # check-price's starter and the laptop's side task both start ci-health (cron is the fallback)
    cp = (ROOT / ".github/workflows/check-price.yml").read_text(encoding="utf-8")
    assert "gh workflow run ci-health.yml" in cp
    laptop = (ROOT / "scripts/win/tanishq_dispatch.ps1").read_text(encoding="utf-8")
    assert "gh workflow run ci-health.yml" in laptop
