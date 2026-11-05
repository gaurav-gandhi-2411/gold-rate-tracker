"""A check-price run that stops (e.g. a failed parquet round trip, #3316) must page GG through ci-health."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
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
    # 2026-11-06: ops pages go to the ops log, not ntfy
    assert "NTFY_TOPIC" not in step["env"] and "scripts/ops_log.py" in run


def test_ci_health_may_list_runs_and_stays_on_its_hourly_schedule():
    wf = _ci_health()
    assert wf["permissions"]["actions"] == "read"
    assert wf[True]["schedule"][0]["cron"] == "17 */1 * * *"  # PyYAML reads `on:` as True


def test_the_round_trip_step_this_alert_names_exists_in_check_price_or_is_pending():
    """The step name in the test fixtures is #3316's; once #3316 is on master it must match exactly."""
    text = (ROOT / ".github/workflows/check-price.yml").read_text(encoding="utf-8")
    if "parquet_roundtrip_check.py" in text:
        assert f"name: {ROUND_TRIP_STEP}" in text


# --- STALE needs a second read (false page 2026-11-02 20:10Z, run 41516713417) ------------------
STALE_LIST = [_run(1, "success", "2026-10-29T17:00:00Z")]  # 13 h before NOW
FRESH_LIST = [_run(2, "success", "2026-10-30T04:00:00Z"), *STALE_LIST]


def test_confirm_stale_clears_only_on_an_ok_second_read():
    first = cph.judge(STALE_LIST, [], NOW)
    assert first[0] == 1 and first[1].startswith("STALE")
    code, line = cph.confirm_stale(first, cph.judge(FRESH_LIST, [], NOW))
    assert code == 0 and line.startswith("STALE NOT CONFIRMED") and "2026-10-30T04:00" in line
    assert cph.confirm_stale(first, cph.judge(STALE_LIST, [], NOW)) == first
    assert cph.confirm_stale(first, cph.judge([], [], NOW)) == first  # unreadable keeps the page
    failing = [_run(3, "failure", "2026-10-30T05:00:00Z"), *STALE_LIST]
    assert cph.confirm_stale(first, cph.judge(failing, [], NOW)) == first


def test_confirm_stale_never_touches_a_failing_or_ok_verdict():
    ok = (0, "OK: x")
    failing = (1, "FAILING: check-price run 2 ended failure")
    assert cph.confirm_stale(failing, ok) == failing
    assert cph.confirm_stale(ok, (1, "STALE: y")) == ok
    assert cph.confirm_stale((2, "CANNOT JUDGE: z"), ok) == (2, "CANNOT JUDGE: z")


def _files(tmp_path: Path, first: object, second: object | str | None) -> list[str]:
    runs, jobs = tmp_path / "runs.json", tmp_path / "jobs.json"
    runs.write_text(json.dumps(first), encoding="utf-8")
    jobs.write_text("[]", encoding="utf-8")
    argv = ["--runs", str(runs), "--jobs", str(jobs), "--now", "2026-10-30T06:00:00Z"]
    if second is not None:
        p = tmp_path / "runs2.json"
        p.write_text(second if isinstance(second, str) else json.dumps(second), encoding="utf-8")
        argv += ["--second-runs", str(p)]
    return argv


def test_main_second_read(tmp_path, capsys):
    assert cph.main(_files(tmp_path, STALE_LIST, FRESH_LIST)) == 0
    assert capsys.readouterr().out.startswith("STALE NOT CONFIRMED")
    assert cph.main(_files(tmp_path, STALE_LIST, STALE_LIST)) == 1
    assert capsys.readouterr().out.startswith("STALE:")
    assert cph.main(_files(tmp_path, STALE_LIST, "unreadable")) == 1  # what the workflow writes
    assert capsys.readouterr().out.startswith("STALE:")
    assert cph.main(_files(tmp_path, STALE_LIST, None)) == 1  # no second read: as before
    capsys.readouterr()
    # a FAILING first read is never re-judged, whatever the second list says
    failing = [_run(3, "failure", "2026-10-30T05:00:00Z"), *FRESH_LIST]
    assert cph.main(_files(tmp_path, failing, FRESH_LIST)) == 1
    assert capsys.readouterr().out.startswith("FAILING")


# --- the step itself, executed in bash with stub gh / curl / sleep ------------------------------
BASH = shutil.which("bash")
JQ = shutil.which("jq")  # the step uses jq; ubuntu-latest has it, a Windows laptop may not


def _step_run(tmp_path: Path, first: list, second: list | None) -> tuple[int, str, str]:
    """Run the workflow step's own script. gh prints the post---jq list for each query; the
    status-filtered query (the first read) returns `first`, the unfiltered one `second`."""
    t = tmp_path.as_posix()
    (tmp_path / "first.json").write_text(json.dumps(first), encoding="utf-8")
    (tmp_path / "second.json").write_text(json.dumps(second or []), encoding="utf-8")
    second_fails = "1" if second is None else "0"
    stubs = f"""
gh() {{
  echo "gh $*" >> "{t}/gh.log"
  case "$*" in
    *status=completed*) cat "{t}/first.json" ;;
    *per_page=50*) if [ "{second_fails}" = 1 ]; then echo "HTTP 502" >&2; return 1; fi
                   cat "{t}/second.json" ;;
    *) echo "[]" ;;
  esac
}}
curl() {{ echo "curl $*" >> "{t}/page.log"; return 0; }}
sleep() {{ echo "sleep $*" >> "{t}/sleep.log"; }}
python3() {{ "{Path(sys.executable).as_posix()}" "$@"; }}
export -f gh curl sleep python3
"""
    script = stubs + f'cd "{ROOT.as_posix()}"\n' + _step()["run"]
    f = tmp_path / "step.sh"
    f.write_bytes(script.encode("utf-8"))
    env = {
        **os.environ,
        "GH_TOKEN": "x",
        "NTFY_TOPIC": "t",
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_WORKSPACE": str(ROOT),
        "RUNNER_TEMP": str(tmp_path),
        "CI_RETRY_BASE": "0",
    }
    r = subprocess.run(
        [BASH or "bash", f.as_posix()], capture_output=True, text=True, timeout=120, env=env
    )
    page = tmp_path / "page.log"
    return r.returncode, r.stdout, page.read_text() if page.exists() else ""


def _recent(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.mark.skipif(BASH is None or JQ is None, reason="needs bash and jq (CI has both)")
def test_step_a_stale_first_read_that_a_second_read_contradicts_does_not_page(tmp_path):
    first = [_run(1, "success", _recent(50))]  # the 2026-11-02 shape: an out-of-date list
    second = [_run(2, "success", _recent(4.6)), *first]
    code, out, page = _step_run(tmp_path, first, second)
    assert code == 0, out
    assert "STALE NOT CONFIRMED" in out and page == ""
    assert (tmp_path / "sleep.log").read_text().strip() == "sleep 60"


@pytest.mark.skipif(BASH is None or JQ is None, reason="needs bash and jq (CI has both)")
def test_step_a_real_stop_still_pages(tmp_path):
    first = [_run(1, "success", _recent(12))]
    code, out, page = _step_run(tmp_path, first, first)
    assert code == 1 and "STALE: no successful check-price run" in out
    assert "check-price stopped" in page


@pytest.mark.skipif(BASH is None or JQ is None, reason="needs bash and jq (CI has both)")
def test_step_an_unreadable_second_read_still_pages(tmp_path):
    first = [_run(1, "success", _recent(12))]
    code, out, page = _step_run(tmp_path, first, None)
    assert code == 1 and "check-price stopped" in page
    assert "STALE: no successful check-price run" in out and "NOT CONFIRMED" not in out


@pytest.mark.skipif(BASH is None or JQ is None, reason="needs bash and jq (CI has both)")
def test_step_a_healthy_list_reads_once_and_never_sleeps(tmp_path):
    code, out, page = _step_run(tmp_path, [_run(1, "success", _recent(1))], None)
    assert code == 0 and out.startswith("OK") and page == ""
    assert not (tmp_path / "sleep.log").exists()
    assert "per_page=50" not in (tmp_path / "gh.log").read_text()


@pytest.mark.skipif(BASH is None or JQ is None, reason="needs bash and jq (CI has both)")
def test_step_a_failed_run_pages_at_once_without_a_second_read(tmp_path):
    # a failed round trip is a real stop: no minute's wait, no re-read
    first = [_run(2, "failure", _recent(0.5)), _run(1, "success", _recent(3))]
    code, out, page = _step_run(tmp_path, first, None)
    assert code == 1 and "FAILING" in out and "check-price stopped" in page
    assert not (tmp_path / "sleep.log").exists()
    assert "per_page=50" not in (tmp_path / "gh.log").read_text()


def _queries() -> list[tuple[str, str]]:
    """(url, jq expression) of each run-list query in the step, in order."""
    run = _step()["run"]
    return re.findall(r'gh api "([^"]*check-price\.yml/runs[^"]*)" \\\s*--jq \'([^\']*)\'', run)


def test_the_second_read_is_a_different_wider_query():
    (first_url, _), (second_url, _) = _queries()
    assert "status=completed" in first_url and "per_page=30" in first_url
    assert "status=" not in second_url and "per_page=50" in second_url
    assert "branch=master" in second_url


@pytest.mark.skipif(JQ is None, reason="needs jq (CI has it)")
def test_the_second_reads_jq_keeps_only_completed_runs():
    # stubbed gh never applies --jq; this runs the workflow's own expression through real jq on an
    # API-shaped page with a run still in progress (conclusion null), which judge() cannot read
    _, expr = _queries()[1]
    page = {
        "workflow_runs": [
            {
                "id": 3,
                "status": "in_progress",
                "conclusion": None,
                "created_at": "2026-11-03T01:50:00Z",
            },
            {
                "id": 2,
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-11-03T00:44:32Z",
            },
            {"id": 1, "status": "queued", "conclusion": None, "created_at": "2026-11-03T01:55:00Z"},
        ]
    }
    out = subprocess.run(
        [JQ or "jq", expr], input=json.dumps(page), capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 0, out.stderr
    rows = json.loads(out.stdout)
    assert rows == [{"id": 2, "conclusion": "success", "created_at": "2026-11-03T00:44:32Z"}]
    assert cph.judge(rows, [], datetime(2026, 11, 3, 2, 0, tzinfo=UTC))[0] == 0
