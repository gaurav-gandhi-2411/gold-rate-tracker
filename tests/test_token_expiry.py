"""scripts/check_token_expiry.py: pages once each at 30, 14 and 7 days and at once on a 401; never prints
a token; state is keyed by expiry date so a renewed token starts afresh."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_token_expiry as cte

NOW = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)
SECRET = "ghp_SENTINEL_SECRET_VALUE_1234567890"


def _hdr(days: float) -> str:
    return (NOW + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S UTC")


def test_parse_expiry_formats():
    assert cte.parse_expiry("2026-11-14 10:20:00 UTC") == datetime(2026, 11, 14, 10, 20, tzinfo=UTC)
    assert cte.parse_expiry("2026-11-14T10:20:00+00:00") == datetime(
        2026, 11, 14, 10, 20, tzinfo=UTC
    )
    assert cte.parse_expiry(None) is None and cte.parse_expiry("not a date") is None


@pytest.mark.parametrize("days", [365, 31, 30.5])
def test_no_page_before_thirty_days(days):
    pages, state, line = cte.decide("T", 200, cte.parse_expiry(_hdr(days)), NOW, {})
    assert pages == [] and state == {} and "OK" in line


EXPIRY = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)


def _at(days_left: float) -> datetime:
    return EXPIRY - timedelta(days=days_left)


def test_pages_once_each_at_30_14_7_and_not_again():
    state: dict = {}
    seen = []
    for days in (29.5, 29.0, 20, 14.2, 13.5, 13, 8, 7.4, 6.5, 3, 1):
        pages, state, _ = cte.decide("T", 200, EXPIRY, _at(days), state)
        seen += [(days, p[0]) for p in pages]
    assert [d for d, _ in seen] == [29.5, 13.5, 6.5]  # first look under each threshold, once


def test_a_late_first_look_pages_once_and_marks_the_earlier_thresholds_done():
    pages, state, _ = cte.decide("T", 200, EXPIRY, _at(10), {})
    assert len(pages) == 1 and "10 days" in pages[0][0]
    again, _, _ = cte.decide("T", 200, EXPIRY, _at(9), state)
    assert again == []


def test_renewing_a_token_starts_a_fresh_set():
    _, state, _ = cte.decide("T", 200, cte.parse_expiry(_hdr(5)), NOW, {})
    pages, _, _ = cte.decide("T", 200, cte.parse_expiry(_hdr(364)), NOW, state)
    assert pages == []
    later = NOW + timedelta(days=340)
    pages2, _, _ = cte.decide("T", 200, cte.parse_expiry(_hdr(364)), later, state)
    assert len(pages2) == 1  # the new expiry date has its own 30-day page


def test_a_401_pages_immediately_then_once_a_day():
    pages, state, line = cte.decide("T", 401, None, NOW, {})
    assert len(pages) == 1 and "REJECTED" in line and "401" in pages[0][1]
    again, state, _ = cte.decide("T", 401, None, NOW + timedelta(hours=3), state)
    assert again == []
    next_day, _, _ = cte.decide("T", 401, None, NOW + timedelta(days=1), state)
    assert len(next_day) == 1


def test_an_expired_token_that_still_answers_is_reported_as_expired():
    pages, _, _ = cte.decide("T", 200, cte.parse_expiry(_hdr(-1)), NOW, {})
    assert len(pages) == 1 and "has expired" in pages[0][0]


def test_no_expiry_header_is_reported_not_paged():
    pages, _, line = cte.decide("T", 200, None, NOW, {})
    assert pages == [] and "no expiry" in line


def test_other_statuses_are_not_judged():
    pages, _, line = cte.decide("T", 502, None, NOW, {})
    assert pages == [] and "not judged" in line


def _run(tmp_path, env, probe, sent):
    state = tmp_path / "s.json"
    code = cte.run(
        ["A", "B"],
        state,
        env,
        NOW,
        probe_fn=probe,
        send_fn=lambda topic, title, msg: sent.append((topic, title, msg)) or True,
    )
    return code, state


def test_run_never_prints_or_sends_a_token_and_writes_state(tmp_path, capsys):
    sent: list = []
    env = {"A": SECRET, "B": SECRET + "-b", "NTFY_TOPIC": "topic"}
    code, state = _run(
        tmp_path, env, lambda tok: (200, _hdr(10) if tok == SECRET else _hdr(300)), sent
    )
    out = capsys.readouterr().out
    assert code == 0
    assert SECRET not in out and SECRET not in json.dumps(sent) and SECRET not in state.read_text()
    assert len(sent) == 1 and sent[0][0] == "topic" and "A" in sent[0][1]
    assert (
        "A: OK, expires 2026-10-25" in out
        and f"B: OK, expires {(NOW + timedelta(days=300)).date().isoformat()}" in out
    )
    # a second run the same day pages nothing more
    sent.clear()
    code, _ = _run(tmp_path, env, lambda tok: (200, _hdr(10) if tok == SECRET else _hdr(300)), sent)
    assert code == 0 and sent == []


def test_run_pages_on_401_and_fails_when_a_secret_is_missing(tmp_path, capsys):
    sent: list = []
    code, _ = _run(tmp_path, {"A": SECRET, "NTFY_TOPIC": "t"}, lambda tok: (401, None), sent)
    out = capsys.readouterr().out
    assert code == 1  # B is missing, so the run is red
    assert "B: secret is not available" in out and len(sent) == 1 and "rejected" in sent[0][1]
    assert SECRET not in out


def test_run_goes_red_when_the_probe_itself_fails(tmp_path, capsys):
    def boom(_tok):
        raise OSError("network down")

    code, _ = _run(tmp_path, {"A": SECRET, "B": SECRET, "NTFY_TOPIC": "t"}, boom, [])
    assert code == 1 and SECRET not in capsys.readouterr().out


def test_run_without_a_topic_says_so_instead_of_dropping_the_page(tmp_path, capsys):
    code, _ = _run(tmp_path, {"A": SECRET, "B": SECRET}, lambda tok: (401, None), [])
    assert "would page" in capsys.readouterr().out and code == 0


def test_corrupt_state_is_ignored(tmp_path):
    (tmp_path / "s.json").write_text("{not json", encoding="utf-8")
    code, _ = _run(tmp_path, {"A": SECRET, "B": SECRET}, lambda tok: (200, _hdr(100)), [])
    assert code == 0


# --- the workflow ----------------------------------------------------------------------------------
def test_workflow_triggers_state_and_secrets():
    text = (ROOT / ".github/workflows/token-expiry.yml").read_text(encoding="utf-8")
    wf = yaml.safe_load(text)
    on = wf.get("on") or wf.get(True)
    assert "schedule" in on and "workflow_dispatch" in on
    assert wf["permissions"] == {"contents": "read"}
    steps = wf["jobs"]["check"]["steps"]
    uses = [s.get("uses", "") for s in steps]
    assert any(u.startswith("actions/cache/restore@") for u in uses)
    assert any(u.startswith("actions/cache/save@") for u in uses)
    assert all("@" in u and len(u.split("@")[1].split()[0]) == 40 for u in uses if u)  # SHA-pinned
    run = next(s for s in steps if "check_token_expiry" in s.get("run", ""))
    assert {"CI_MERGE_PAT", "CI_HEALTH_PAT", "NTFY_TOPIC"} <= set(run["env"])
    assert (
        "echo" not in run["run"] and "set -x" not in run["run"]
    )  # nothing that could print a secret


def test_check_price_starts_the_check_when_it_has_not_run_lately():
    wf = yaml.safe_load((ROOT / ".github/workflows/check-price.yml").read_text(encoding="utf-8"))
    step = next(
        s for s in wf["jobs"]["check"]["steps"] if "token expiry check" in s.get("name", "")
    )
    assert step["if"] == "always()" and step["continue-on-error"] is True
    assert "gh workflow run token-expiry.yml" in step["run"] and "72000" in step["run"]
