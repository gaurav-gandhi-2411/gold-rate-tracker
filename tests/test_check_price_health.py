"""A check-price run that stops (e.g. a failed parquet round trip, #3316) must page GG through ci-health."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_price_health as cph

NOW = datetime(2026, 10, 30, 6, 0, tzinfo=UTC)
ROUND_TRIP_STEP = "Parquet round trip with the installed pyarrow"


def _run(i: int, conclusion: str, created: str) -> dict:
    return {"id": i, "conclusion": conclusion, "created_at": created}


def _jobs_failed_at(step: str) -> list[dict]:
    return [
        {
            "name": "check",
            "steps": [
                {"name": "Decrypt raw data files (ADR 060)", "conclusion": "success"},
                {"name": step, "conclusion": "failure"},
                {"name": "Fetch macro features", "conclusion": "skipped"},
            ],
        }
    ]


def test_a_failed_round_trip_is_failing_and_names_the_step():
    runs = [_run(2, "failure", "2026-10-30T04:37:00Z"), _run(1, "success", "2026-10-30T01:37:00Z")]
    code, line = cph.judge(runs, _jobs_failed_at(ROUND_TRIP_STEP), NOW)
    assert code == 1 and line.startswith("FAILING:")
    assert ROUND_TRIP_STEP in line and "not updated" in line


def test_a_timed_out_run_is_failing_even_without_step_detail():
    runs = [
        _run(2, "timed_out", "2026-10-30T04:37:00Z"),
        _run(1, "success", "2026-10-30T01:37:00Z"),
    ]
    code, line = cph.judge(runs, [], NOW)
    assert code == 1 and "step not identified" in line


def test_a_cancelled_newest_run_is_not_a_page_while_a_recent_run_succeeded():
    runs = [
        _run(2, "cancelled", "2026-10-30T04:37:00Z"),
        _run(1, "success", "2026-10-30T01:37:00Z"),
    ]
    code, line = cph.judge(runs, [], NOW)
    assert code == 0 and line.startswith("OK")


def test_no_success_for_more_than_nine_hours_is_stale():
    runs = [
        _run(2, "cancelled", "2026-10-29T22:37:00Z"),
        _run(1, "success", "2026-10-29T20:37:00Z"),
    ]
    code, line = cph.judge(runs, [], NOW)  # last success 9 h 23 min before NOW
    assert code == 1 and line.startswith("STALE:")


def test_a_recent_success_is_ok():
    runs = [_run(1, "success", "2026-10-30T04:37:00Z")]
    assert cph.judge(runs, [], NOW)[0] == 0


def test_no_runs_or_a_run_without_conclusion_cannot_be_judged():
    assert cph.judge([], [], NOW)[0] == 2
    assert cph.judge([{"id": 1, "created_at": "2026-10-30T04:37:00Z"}], [], NOW)[0] == 2


def test_main_fails_closed_on_an_unreadable_file(tmp_path, capsys):
    jobs = tmp_path / "jobs.json"
    jobs.write_text("[]", encoding="utf-8")
    code = cph.main(
        [
            "--runs",
            str(tmp_path / "missing.json"),
            "--jobs",
            str(jobs),
            "--now",
            "2026-10-30T06:00:00Z",
        ]
    )
    assert code == 2 and "CANNOT JUDGE" in capsys.readouterr().out


def test_main_reads_the_files_the_workflow_writes(tmp_path, capsys):
    runs = tmp_path / "runs.json"
    jobs = tmp_path / "jobs.json"
    runs.write_text(json.dumps([_run(2, "failure", "2026-10-30T04:37:00Z")]), encoding="utf-8")
    jobs.write_text(json.dumps(_jobs_failed_at(ROUND_TRIP_STEP)), encoding="utf-8")
    code = cph.main(["--runs", str(runs), "--jobs", str(jobs), "--now", "2026-10-30T06:00:00Z"])
    assert code == 1 and ROUND_TRIP_STEP in capsys.readouterr().out


# --- the wiring --------------------------------------------------------------------------------
def _ci_health() -> dict:
    return yaml.safe_load((ROOT / ".github/workflows/ci-health.yml").read_text(encoding="utf-8"))


def _step() -> dict:
    steps = _ci_health()["jobs"]["check"]["steps"]
    return next(
        s
        for s in steps
        if s.get("name") == "Check that check-price is still running and succeeding"
    )


def test_ci_health_runs_the_check_even_after_its_lint_check_failed_and_pages_on_any_non_zero():
    step = _step()
    assert step["if"] == "always()"
    run = step["run"]
    assert "scripts/check_price_health.py" in run
    assert "check-price.yml/runs?branch=master&status=completed" in run
    assert 'if [ "$STATUS" -ne 0 ]' in run and 'page "${RESULT}' in run
    assert "NTFY_TOPIC" in step["env"]


def test_ci_health_may_list_runs_and_stays_on_its_hourly_schedule():
    wf = _ci_health()
    assert wf["permissions"]["actions"] == "read"
    assert wf[True]["schedule"][0]["cron"] == "17 */1 * * *"  # PyYAML reads `on:` as True


def test_the_round_trip_step_this_alert_names_exists_in_check_price_or_is_pending():
    """The step name in the test fixtures is #3316's; once #3316 is on master it must match exactly."""
    text = (ROOT / ".github/workflows/check-price.yml").read_text(encoding="utf-8")
    if "parquet_roundtrip_check.py" in text:
        assert f"name: {ROUND_TRIP_STEP}" in text
