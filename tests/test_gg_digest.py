"""scripts/gg_digest.py: the daily and Monday notes are gold-price information only, and never fail on a missing part."""

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

NOW = datetime(2026, 11, 5, 21, 0, tzinfo=UTC)  # 02:30 IST on 6 Nov
LIVE = {
    "n": 16,
    "since": "2026-10-07",
    "last_day": "2026-10-30",
    "mae_model": 80.6,
    "mae_hold": 99.3,
    "direction_hit": 0.812,
    "direction_n": 16,
    "coverage": 0.75,
    "coverage_n": 16,
}
# One row per IST day at 11:30Z (17:00 IST), like data/ibja_derived_prices.json.
IBJA = {
    "2026-10-27": 13000,
    "2026-10-28": 13100,
    "2026-10-29": 13200,
    "2026-10-30": 13150,
    "2026-10-31": 13250,
    "2026-11-01": 13300,
    "2026-11-02": 13350,
    "2026-11-03": 13300,
    "2026-11-04": 13167,
    "2026-11-05": 13400,
}
TANISHQ_ROW = {
    "timestamp": "2026-11-05T20:11:32.160Z",
    "22k": 13435,
    "24k": 14656,
    "18k": 10992,
    "source": "https://www.tanishq.co.in/gold-rate.html?lang=en_IN",
}


def _write(root: Path, name: str, obj: object) -> None:
    (root / "data" / name).write_text(json.dumps(obj), encoding="utf-8")


def _repo(tmp_path: Path, rows: list[dict] | None = None, forecast: dict | None = None) -> Path:
    (tmp_path / "data").mkdir()
    (tmp_path / "docs").mkdir()
    _write(tmp_path, "prices.json", rows if rows is not None else [TANISHQ_ROW])
    _write(
        tmp_path,
        "forecast.json",
        forecast
        if forecast is not None
        else {
            "predicted_at": "2026-11-05T20:48:15.263830+00:00",
            "current_22k": 13435,
            "price_source": "tanishq_scrape",
            "scraped_at": "2026-11-05T20:11:32.160Z",
            "target_time": "2026-11-06T00:00:00+00:00",
            "headline": {"predicted_22k": 13435, "lower": 13254, "upper": 13616},
            "next_fix": {
                "champion": {"id": "p3"},
                "demotion": {"live_monitor": {"checked": True, "n_live": 19}},
            },
        },
    )
    _write(
        tmp_path,
        "ibja_derived_prices.json",
        [{"timestamp": f"{d}T11:30:00.000Z", "22k": v} for d, v in IBJA.items()],
    )
    _write(
        tmp_path,
        "model_status_weekly.json",
        {
            "generated_at": "2026-11-03T07:19:19+00:00",
            "live": LIVE,
            "challengers": {"ensemble": {"n": 6, "first_look_day": 20}},
        },
    )
    _write(tmp_path, "model_demotion_state.json", {"demoted": False})
    _write(tmp_path, "tanishq_scrape_success_rate.json", {"n": 26, "n_success": 25})
    (tmp_path / "data" / "tanishq_scrape_outcomes.jsonl").write_text(
        json.dumps({"timestamp": "2026-11-05T20:11:32Z", "outcome": "success"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "FOR_GG.md").write_text(
        "## 2. Optional\n| # | Item |\n|---|---|\n| 1 | a |\n", encoding="utf-8"
    )
    return tmp_path


def test_daily_note_is_exactly_the_five_gold_price_lines(tmp_path: Path) -> None:
    lines = gd.daily(_repo(tmp_path), NOW).splitlines()
    assert lines == [
        "Gold rate per gram, Tanishq's listed rate as of 6 Nov, 01:41 IST: "
        "22K Rs.13,435, 24K Rs.14,656, 18K Rs.10,992.",
        "Change in the daily 22K benchmark rate (5 Nov, Rs.13,400): "
        "vs yesterday (4 Nov) +Rs.233 (+1.8%); vs last week (29 Oct) +Rs.200 (+1.5%).",
        "Shop-price equivalent of the next official rate: likely 22K Rs.13,254 to Rs.13,616, central estimate Rs.13,435 (for 6 Nov).",
        "Model accuracy as of 30 Oct, live since 7 Oct, 16 days: off by Rs.81 on average vs Rs.99 for "
        "'no change'; range held 12 of 16 days. Too few days to judge.",
        "Other models tested alongside the live one: none has reached its first look yet "
        "(20 days needed, the furthest has 6).",
    ]


def test_monday_note_adds_the_weeks_high_and_low(tmp_path: Path) -> None:
    lines = gd.monday(_repo(tmp_path), NOW).splitlines()
    assert len(lines) == 6
    assert lines[2] == "This week's 22K benchmark: high Rs.13,400 (5 Nov), low Rs.13,150 (30 Oct)."
    assert lines[3].startswith("Shop-price equivalent of the next official rate:")
    assert lines[4].startswith("Model accuracy")
    assert lines[5].startswith("Other models tested alongside the live one:")


def _challengers(tmp_path: Path, challengers: dict) -> str:
    root = _repo(tmp_path)
    _write(
        root,
        "model_status_weekly.json",
        {"generated_at": "2026-11-06T18:35:17+00:00", "live": LIVE, "challengers": challengers},
    )
    return gd.challengers_note(root)


def _started(promotable: bool = False, last_day: str = "2026-11-05") -> dict:
    return {
        "n": 20, "first_look_day": 20, "looks_started": True, "last_day": last_day,
        "promotable": promotable, "retired": False,
    }  # fmt: skip


def test_first_look_done_none_beats_the_live_model_the_live_one_stays(tmp_path: Path) -> None:
    """The real 2026-11-06 outcome (data/model_status_weekly.json, run 42271733212): all three at
    n = 20, lower bound did not clear for any, none promotable."""
    note = _challengers(
        tmp_path, {"ensemble": _started(), "p3_roll60": _started(), "p3_monday": _started()}
    )
    assert note == (
        "Other models tested alongside the live one: all 3 have now had their first look "
        "(20 days, as of 5 Nov); none beats the live model by enough to switch, so the live model stays."
    )


def test_a_model_that_clears_the_bar_is_named_and_left_to_a_person(tmp_path: Path) -> None:
    note = _challengers(tmp_path, {"ensemble": _started(), "p3_roll60": _started(promotable=True)})
    assert (
        "the model re-fitted on the last 60 days clears the bar to replace the live model" in note
    )
    assert note.endswith("A person decides whether to switch.")
    assert "the older blended model" not in note


def test_some_started_some_not_and_retired_ones_are_left_out(tmp_path: Path) -> None:
    waiting = {"n": 12, "first_look_day": 20, "looks_started": False, "retired": False}
    gone = {"n": 20, "first_look_day": 20, "looks_started": True, "retired": True,
            "last_day": "2026-11-05"}  # fmt: skip
    note = _challengers(tmp_path, {"ensemble": _started(), "p3_roll60": waiting, "x": gone})
    assert (
        "1 of 2 have had their first look (as of 5 Nov)" in note
        and "The rest need more days." in note
    )


def test_the_challengers_line_degrades_in_plain_words(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "data" / "model_status_weekly.json").unlink()
    assert gd.challengers_note(root) == (
        "Other models tested alongside the live one: not available right now."
    )


def test_the_challengers_line_uses_no_jargon_in_any_outcome(tmp_path: Path) -> None:
    outcomes = [
        {"ensemble": _started(), "p3_roll60": _started(), "p3_monday": _started()},
        {"ensemble": _started(promotable=True)},
        {"ensemble": {"n": 3, "first_look_day": 20, "looks_started": False, "retired": False}},
    ]
    for i, challengers in enumerate(outcomes):
        sub = tmp_path / f"case{i}"
        sub.mkdir()
        note = _challengers(sub, challengers)
        for jargon in ("challenger", "ensemble", "p3", "rule v7", "promot", "adr", "first_look"):
            assert jargon not in note.lower(), (jargon, note)


def test_note_has_no_system_facts(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    text = gd.daily(root, NOW) + gd.monday(root, NOW)
    for banned in ("Merged", "Parked", "Scrapes", "Challengers", "Next:", "Models:", "Visitor"):
        assert banned not in text


def test_a_stale_tanishq_reading_falls_back_to_the_estimate_like_the_hero(tmp_path: Path) -> None:
    fc = {
        "predicted_at": "2026-11-05T20:48:15+00:00",
        "current_22k": 13300,
        "price_source": "ibja_calibrated",
        "headline": {"lower": 13100, "upper": 13500},
    }
    root = _repo(tmp_path, forecast=fc)
    line = gd.rates_lines(root, NOW)[0]
    # Tanishq 13,435 is within 12% of the estimate and 0.8 h old, so it is still live
    assert "Tanishq's listed rate" in line and "22K Rs.13,435" in line
    late = datetime(2026, 11, 6, 8, 0, tzinfo=UTC)  # reading now 11.8 h old (> 8 h)
    line = gd.rates_lines(root, late)[0]
    assert "our estimate from the IBJA benchmark as of 6 Nov, 02:18 IST" in line
    assert "22K Rs.13,300" in line and "24K Rs.14,656" in line  # 24K/18K from the latest row


def test_24k_and_18k_follow_the_sites_karat_rule(tmp_path: Path) -> None:
    row = {"timestamp": "2026-11-05T20:11:32.160Z", "22k": 13200, "source": "x"}
    root = _repo(tmp_path, rows=[row])
    line = gd.rates_lines(root, NOW)[0]
    assert "24K Rs.14,400" in line and "18K Rs.10,800" in line  # round(13200 * k / 22)


def test_vs_last_week_uses_the_row_on_or_before_seven_days_earlier(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    assert "vs last week (29 Oct)" in gd.change_line(root)  # 5 Nov - 7 days = 29 Oct exactly
    short = [
        {"timestamp": "2026-11-05T11:30:00Z", "22k": 1},
        {"timestamp": "2026-11-04T11:30:00Z", "22k": 2},
    ]
    _write(root, "ibja_derived_prices.json", short)
    assert "vs last week unknown (series too short)" in gd.change_line(root)


def test_a_missing_file_degrades_to_unknown_and_the_rest_still_builds(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "data" / "model_status_weekly.json").unlink()
    (root / "data" / "ibja_derived_prices.json").unlink()
    lines = gd.daily(root, NOW).splitlines()
    assert lines[0].startswith("Gold rate per gram")
    assert lines[1] == "Change in the daily 22K benchmark rate: not available right now."
    assert lines[2].startswith("Shop-price equivalent of the next official rate")
    assert lines[3] == "Model accuracy: not available right now."
    assert "Error" not in "\n".join(lines)  # plain words, never a Python exception class


def test_ops_lines_hold_the_system_facts_for_the_job_summary(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    merged = [
        {"number": 3060, "title": "feat: digest", "headRefName": "feat/x"},
        {"number": 3061, "title": "chore: data", "headRefName": "bot/data-sync"},
    ]
    text = "\n".join(gd.ops_lines(root, NOW, merged))
    assert "Data: fresh" in text and "Scrapes (7 days): 25 of 26" in text
    assert "Merged today: #3060 feat: digest." in text and "Parked for GG: 1 item(s)" in text
    assert "Challengers: ensemble 6 days" in text and "Models: champion p3" in text


def test_main_writes_the_note_and_puts_system_facts_in_the_summary(
    tmp_path: Path, monkeypatch
) -> None:
    root = _repo(tmp_path)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    out = tmp_path / "o.txt"
    args = ["daily", "--out", str(out), "--now", NOW.isoformat(), "--root", str(root)]
    assert gd.main(args) == 0
    note = out.read_text(encoding="utf-8")
    assert note.startswith("Gold rate per gram") and "Parked" not in note
    assert "Parked for GG" in summary.read_text(encoding="utf-8")


def test_workflows_guard_against_double_sends_and_have_dispatch() -> None:
    for name in ("daily-digest.yml", "monday-push.yml"):
        wf = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
        on = wf.get("on", wf.get(True))
        assert "workflow_dispatch" in on and "schedule" in on
        steps = wf["jobs"][next(iter(wf["jobs"]))]["steps"]
        guard = next(s for s in steps if s.get("id") == "guard")
        assert "status=success" in guard["run"] and "GITHUB_RUN_ID" in guard["run"]
        send = next(s for s in steps if "Send the gold-price note" in s.get("name", ""))
        assert send["if"] == "steps.guard.outputs.send == 'true'"
    check_price = (ROOT / ".github" / "workflows" / "check-price.yml").read_text(encoding="utf-8")
    assert "start_if_not_run_today daily-digest.yml" in check_price
    assert "start_if_not_run_today monday-push.yml" in check_price
