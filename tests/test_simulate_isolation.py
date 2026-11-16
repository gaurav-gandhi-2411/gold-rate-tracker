"""A [TEST] dispatch of ci-health touches no real alarm state, and a real incident still pages once.

2026-11-16: scheduled ci-health run 43707009550 went red and an earlier note blamed a simulate run.
The run's own log showed otherwise (a REAL stale forecast at 08:28Z on 11-15 opened a real incident,
and 11:03Z was its first fresh check). But reading the workflow turned up a real hole: a freshness
simulation still ran the share-card check for real (restore, judge without the freshness incident
file, page, save), and a share-card simulation ran the freshness check for real. The earlier wiring
tests looked at each check's own input only.

These tests evaluate the REAL workflow's `if:` conditions (parsed from ci-health.yml, not a copy) for
every combination of the two simulate inputs, then run scripts/live_freshness.py's main() against a
state file to show a simulation leaves it byte-for-byte alone and a real incident pages exactly once.
"""

from __future__ import annotations

import itertools
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import live_freshness

WF = ROOT / ".github" / "workflows" / "ci-health.yml"

FRESHNESS = {
    "restore": "Restore the live-freshness incident state",
    "check": "Check that what visitors see is fresh (live site JSON)",
    "save": "Save the live-freshness incident state",
}
CARD = {
    "restore": "Restore the share-card incident state",
    "check": "Check the published share card (og.png's sidecar against the live data)",
    "save": "Save the share-card incident state",
}


def _steps() -> dict[str, dict]:
    wf = yaml.safe_load(WF.read_text(encoding="utf-8"))
    return {s.get("name", ""): s for s in wf["jobs"]["check"]["steps"]}


def _truthy(expr: str, inputs: dict[str, str]) -> bool:
    """Evaluate a GitHub `if:` expression made of always(), hashFiles(), inputs.X, ||, &&, ==, !=."""
    py = re.sub(r"hashFiles\([^)]*\)", "'statefile'", expr)
    py = py.replace("always()", "True").replace("||", " or ").replace("&&", " and ")
    py = re.sub(r"inputs\.(\w+)", r"inp['\1']", py)
    # the harness cannot eval anything else: refuse an expression shape this evaluator does not know
    assert re.fullmatch(r"[\w\s'\[\]()=!.]+", py), f"unsupported expression: {expr}"
    return bool(eval(py, {"__builtins__": {}}, {"inp": inputs}))


FRESH_OPTS = ["", "stale", "fresh"]  # "" is a schedule or plain dispatch: no input at all
CARD_OPTS = ["", "bad", "good"]


def _inputs(f: str, c: str) -> dict[str, str]:
    return {"freshness_simulate": f, "share_card_simulate": c}


def test_the_dispatch_options_are_the_ones_this_test_enumerates() -> None:
    wf = yaml.safe_load(WF.read_text(encoding="utf-8"))
    on = wf.get("on", wf.get(True))["workflow_dispatch"]["inputs"]
    assert on["freshness_simulate"]["options"] == ["none", "stale", "fresh"]
    assert on["share_card_simulate"]["options"][0] == "none"
    assert set(on["share_card_simulate"]["options"][1:]) == set(CARD_OPTS[1:]), (
        "a new share-card simulate option exists: add it to CARD_OPTS so it is covered"
    )


@pytest.mark.parametrize(("f", "c"), list(itertools.product(FRESH_OPTS, CARD_OPTS)))
def test_a_simulated_run_runs_no_real_check_and_touches_no_real_state(f: str, c: str) -> None:
    steps = _steps()
    inp = _inputs(f, c)
    ran = {
        (kind, step): _truthy(steps[name]["if"], inp)
        for kind, group in (("freshness", FRESHNESS), ("card", CARD))
        for step, name in group.items()
    }
    simulating = bool(f or c)
    if not simulating:  # a real run (schedule, plain dispatch): everything runs, state carried
        assert all(ran.values()), ran
        return
    # no real state is restored or saved by either check
    for kind in ("freshness", "card"):
        assert not ran[(kind, "restore")], f"{kind} restores real state in a simulate run {f, c}"
        assert not ran[(kind, "save")], f"{kind} saves real state in a simulate run {f, c}"
    # a check runs only when it is the simulated one (never in real mode next to a simulation)
    assert ran[("freshness", "check")] == bool(f), (f, c, ran)
    assert ran[("card", "check")] == bool(c), (f, c, ran)


def test_the_conditions_are_enumerated_from_the_real_workflow_not_a_copy() -> None:
    steps = _steps()
    for group in (FRESHNESS, CARD):
        for name in group.values():
            assert name in steps and "if" in steps[name], name
    # and the evaluator really distinguishes: the pre-fix condition would have failed the test above
    old_card_check = "always()"
    assert _truthy(old_card_check, _inputs("stale", "")) is True  # i.e. a real card check ran


# ---- the script side: a simulation leaves the real state file alone; a real incident pages once ----

REAL_STATE = {"incident": {"opened_at": "2026-11-15T08:28:34+00:00", "last_paged_at": "x"}}


def _run(argv: list[str], monkeypatch: pytest.MonkeyPatch, payload=None):
    sent: list[dict] = []
    monkeypatch.setenv("NTFY_TOPIC", "t")
    monkeypatch.setattr(live_freshness, "send", lambda msg, topic: sent.append(msg) or True)
    if payload is not None:
        forecast, prices = payload
        monkeypatch.setattr(
            live_freshness,
            "fetch_json",
            lambda url: forecast if url.endswith("forecast.json") else prices,
        )
    rc = live_freshness.main(argv)
    return rc, sent


def test_a_simulation_leaves_an_open_real_incident_byte_for_byte_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = tmp_path / "state.json"
    state.write_text(json.dumps(REAL_STATE, sort_keys=True), encoding="utf-8")
    before = state.read_bytes()
    for kind in ("stale", "fresh"):
        _run(
            ["--simulate", kind, "--state-in", str(state), "--state-out", str(state)],
            monkeypatch,
        )
    assert state.read_bytes() == before


def test_a_real_incident_after_a_simulation_pages_exactly_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = tmp_path / "state.json"  # no real incident yet
    now = live_freshness.parse_ts("2026-11-17T12:00:00+00:00")
    stale_payload = live_freshness.simulated("stale", now)
    # a [TEST] run first: must not create, hide or consume anything
    _run(["--simulate", "stale", "--state-in", str(state), "--state-out", str(state)], monkeypatch)
    assert not state.exists()
    args = ["--state-in", str(state), "--state-out", str(state), "--now", now.isoformat()]
    rc1, sent1 = _run(args, monkeypatch, stale_payload)
    rc2, sent2 = _run(args, monkeypatch, stale_payload)
    assert len(sent1) == 1 and rc1 == 1, (rc1, sent1)  # the real incident pages once
    assert sent2 == [] and rc2 == 1  # still open, still red, not paged again
    assert "[TEST]" not in sent1[0]["title"]
