"""ci-health runs scripts/live_share_card.py on every run, from the reliable triggers, with state."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CHECK = "Check the published share card (og.png's sidecar against the live data)"


def _wf() -> dict:
    return yaml.safe_load((ROOT / ".github/workflows/ci-health.yml").read_text(encoding="utf-8"))


def _steps() -> tuple[dict, dict, dict]:
    steps = _wf()["jobs"]["check"]["steps"]
    names = [s.get("name", "") for s in steps]
    i = names.index(CHECK)
    return steps[i - 1], steps[i], steps[i + 1]


def test_ci_health_runs_it_every_time_with_state_carried_in_the_cache():
    restore, check, save = _steps()
    assert "actions/cache/restore@" in restore["uses"] and "actions/cache/save@" in save["uses"]
    assert restore["with"]["path"] == save["with"]["path"] == ".share-card/state.json"
    assert restore["with"]["restore-keys"] == "share-card-"
    # its own key prefix: sharing live-freshness- would restore the other check's state
    assert not restore["with"]["restore-keys"].startswith("live-freshness")
    for s in (restore, check, save):
        assert s["if"].startswith("always()")
    assert "scripts/live_share_card.py" in check["run"] and "NTFY_TOPIC" in check["env"]
    assert "continue-on-error" not in check  # an incident shows red while it lasts
    wf = _wf()
    assert wf["concurrency"] == {"group": "ci-health", "cancel-in-progress": False}
    on = wf.get("on", wf.get(True))
    assert on["workflow_dispatch"]["inputs"]["share_card_simulate"]["options"] == [
        "none",
        "bad",
        "good",
    ]


def test_a_simulated_run_never_reads_or_writes_the_real_incident_state():
    restore, check, save = _steps()
    real_only = "(inputs.share_card_simulate || 'none') == 'none'"
    assert real_only in restore["if"] and real_only in save["if"]
    assert check["env"]["SIMULATE"] == "${{ inputs.share_card_simulate || 'none' }}"
    real, _, simulated = check["run"].partition("else")
    assert 'if [ "$SIMULATE" = "none" ]' in real
    assert "--state-in .share-card/state.json --state-out .share-card/state.json" in real
    assert '--simulate "$SIMULATE"' in simulated and "--state" not in simulated
    assert save["with"]["key"] == "share-card-${{ github.run_id }}-${{ github.run_attempt }}"
    assert "hashFiles('.share-card/state.json') != ''" in save["if"]


def test_it_runs_after_the_freshness_check_and_never_before_it():
    # A share-card failure must not keep the freshness check (the older, broader alarm) from running.
    steps = _wf()["jobs"]["check"]["steps"]
    names = [s.get("name", "") for s in steps]
    assert names.index("Check that what visitors see is fresh (live site JSON)") < names.index(
        CHECK
    )


def test_the_script_is_stdlib_only():
    # ci-health runs it on the runner's own python3 with nothing installed
    src = (ROOT / "scripts/live_share_card.py").read_text(encoding="utf-8")
    imports = {
        line.split()[1].split(".")[0]
        for line in src.splitlines()
        if line.startswith(("import ", "from ")) and not line.startswith("from __future__")
    }
    assert imports <= {
        "argparse",
        "json",
        "math",
        "os",
        "re",
        "sys",
        "datetime",
        "pathlib",
        "typing",
        "live_freshness",
    }, imports
