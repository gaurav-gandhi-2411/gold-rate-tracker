"""scripts/gg_digest.py: the daily digest and Monday push read only aggregates and never fail on a missing part."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("gg_digest", ROOT / "scripts" / "gg_digest.py")
gd = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(gd)

NOW = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)
LIVE = {
    "n": 6,
    "last_day": "2026-10-15",
    "mae_model": 76.4,
    "mae_hold": 87.9,
    "direction_hit": 0.833,
    "direction_n": 6,
    "coverage": 0.667,
    "coverage_n": 6,
}


def _repo(tmp_path: Path, last_reading: str = "2026-10-23T14:00:00Z") -> Path:
    data = tmp_path / "data"
    data.mkdir()
    (tmp_path / "docs").mkdir()
    (data / "tanishq_scrape_outcomes.jsonl").write_text(
        json.dumps({"timestamp": "2026-10-20T05:41:10Z", "outcome": "success"})
        + "\n"
        + json.dumps({"timestamp": last_reading, "outcome": "success"})
        + "\n"
        + json.dumps({"timestamp": "2026-10-23T14:30:00Z", "outcome": "failure"})
        + "\n",
        encoding="utf-8",
    )
    (data / "forecast.json").write_text(
        json.dumps(
            {
                "predicted_at": "2026-10-23T14:10:00+00:00",
                "next_fix": {
                    "champion": {"id": "p3"},
                    "demotion": {
                        "live_monitor": {
                            "checked": True,
                            "n_live": 9,
                            "error_breach": False,
                            "coverage_alert": False,
                        }
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (data / "tanishq_scrape_success_rate.json").write_text(
        json.dumps({"n": 26, "n_success": 26}), encoding="utf-8"
    )
    (data / "model_status_weekly.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-10-18T06:50:00+00:00",
                "live": LIVE,
                "challengers": {
                    "ensemble": {"n": 6, "first_look_day": 20},
                    "p3_roll60": {"n": 6, "first_look_day": 20, "promotable": True},
                },
            }
        ),
        encoding="utf-8",
    )
    (data / "model_demotion_state.json").write_text(
        json.dumps({"demoted": False}), encoding="utf-8"
    )
    (tmp_path / "docs" / "FOR_GG.md").write_text(
        "# x\n## 0. Tokens\n| 1 | not counted |\n## 2. Optional\n| # | Item |\n|---|---|\n| 1 | a |\n| 2 | b |\n",
        encoding="utf-8",
    )
    return tmp_path


def test_daily_digest_lines(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    merged = [
        {"number": 3060, "title": "feat: digest", "headRefName": "feat/x"},
        {"number": 3061, "title": "chore: update gold prices", "headRefName": "bot/data-sync"},
    ]
    text = gd.daily(root, NOW, merged)
    lines = text.splitlines()
    assert lines[0] == "Data: fresh, last Tanishq reading 1.0 h ago; forecast 0.8 h old."
    assert lines[1] == "Scrapes (7 days): 26 of 26 succeeded."
    # The weekly file's date is named, so a mid-week reader knows the record is not stuck.
    assert lines[2].startswith("P3 live record (weekly check of 2026-10-18: 6 days, to 2026-10-15)")
    assert "average miss Rs.76 vs Rs.88" in lines[2] and "direction right 5 of 6" in lines[2]
    assert "range held 4 of 6 (target 80%)" in lines[2] and lines[2].endswith(
        "Too few days to judge."
    )
    assert lines[3] == (
        "Models: champion p3; not demoted; live monitor 9 live days, error breach no, range alert no."
    )
    assert lines[4] == "Merged today: #3060 feat: digest."
    assert lines[5] == "Parked for GG: 2 item(s) in docs/FOR_GG.md section 2."


def test_stale_data_is_flagged_and_failures_do_not_count_as_readings(tmp_path: Path) -> None:
    root = _repo(tmp_path, last_reading="2026-10-23T05:00:00Z")
    assert gd.freshness_line(root, NOW).startswith("Data: STALE, last Tanishq reading 10.0 h ago")


def test_a_missing_file_degrades_to_unknown_and_the_rest_still_builds(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "data" / "model_status_weekly.json").unlink()
    text = gd.daily(root, NOW, None)
    assert "unknown (FileNotFoundError)" in text
    assert "Merged today: unknown (no list)." in text
    assert text.splitlines()[0].startswith("Data: fresh")


def test_live_line_without_a_generated_date_says_so(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    p = root / "data" / "model_status_weekly.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    del d["generated_at"]
    p.write_text(json.dumps(d), encoding="utf-8")
    assert gd.live_line(root).startswith("P3 live record (weekly check of unknown date: 6 days")


def test_monday_note(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    text = gd.monday(root, NOW)
    assert "Challengers: ensemble 6 days (first look at 20); p3_roll60 PROMOTABLE." in text
    assert "Next: 2026-10-26: Monday push and visitor line;" in text
    assert text.endswith("Visitor line follows separately (visitors-weekly).")


def test_past_dates_drop_out() -> None:
    late = datetime(2026, 12, 20, tzinfo=UTC)
    assert gd.next_dates_line(late) == "Next: none listed."


def test_no_price_values_in_the_text(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    text = gd.daily(root, NOW, []) + gd.monday(root, NOW)
    assert (
        "13," not in text and "per gram" not in text
    )  # misses in Rs. are public averages, not prices


def test_main_writes_the_file(tmp_path: Path, capsys) -> None:
    root = _repo(tmp_path)
    out = tmp_path / "o.txt"
    assert gd.main(["daily", "--out", str(out), "--now", NOW.isoformat(), "--root", str(root)]) == 0
    assert out.read_text(encoding="utf-8").startswith("Data: fresh")


def test_workflows_guard_against_double_sends_and_have_dispatch() -> None:
    for name in ("daily-digest.yml", "monday-push.yml"):
        wf = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
        on = wf.get("on", wf.get(True))
        assert "workflow_dispatch" in on and "schedule" in on
        steps = wf["jobs"][next(iter(wf["jobs"]))]["steps"]
        guard = next(s for s in steps if s.get("id") == "guard")
        assert "status=success" in guard["run"] and "GITHUB_RUN_ID" in guard["run"]
        send = next(s for s in steps if "Send it" in s.get("name", ""))
        assert send["if"] == "steps.guard.outputs.send == 'true'"
    check_price = (ROOT / ".github" / "workflows" / "check-price.yml").read_text(encoding="utf-8")
    assert "start_if_not_run_today daily-digest.yml" in check_price
    assert "start_if_not_run_today monday-push.yml" in check_price
