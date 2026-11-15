"""scripts/gg_digest.py: the daily and Monday notes are gold-price information only, and never fail on a missing part."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
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
                "active": True,  # the model is live: the daily note then adds nothing about it
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


def _forecast_with(next_fix: dict | None, predicted_at: str = "2026-11-05T20:48:15+00:00") -> dict:
    fc = {
        "predicted_at": predicted_at,
        "current_22k": 13435,
        "price_source": "tanishq_scrape",
        "scraped_at": "2026-11-05T20:11:32.160Z",
        "target_time": "2026-11-06T00:00:00+00:00",
        "headline": {"predicted_22k": 13435, "lower": 13254, "upper": 13616},
    }
    if next_fix is not None:
        fc["next_fix"] = next_fix
    return fc


PAUSED = (
    "The price forecast has been paused since 5 Nov, 13:10 IST: "
    "the range above is a plain 'no change' range, not a forecast."
)


def test_a_paused_forecast_is_one_plain_sentence_with_since_when(tmp_path: Path) -> None:
    fc = _forecast_with(
        {"active": False, "reason": "error: X", "down_since": "2026-11-05T07:40:00Z"}
    )
    lines = gd.daily(_repo(tmp_path, forecast=fc), NOW).splitlines()
    assert len(lines) == 6 and lines[3] == PAUSED  # right under the range it qualifies
    assert lines[2].startswith("Shop-price equivalent")


def test_a_working_forecast_adds_nothing_including_a_hold_window(tmp_path: Path) -> None:
    # holding the latest fix is the model working: next_fix.active is true, headline method "hold_..."
    fc = _forecast_with({"active": True, "mode": "after_morning_rate"})
    fc["headline"]["method"] = "hold_latest_fix_am_to_pm"
    root = _repo(tmp_path, forecast=fc)
    assert gd.model_pause_lines(root, NOW) == []
    assert len(gd.daily(root, NOW).splitlines()) == 5


@pytest.mark.parametrize("next_fix", [None, {"active": "true"}, {}, {"active": False}])
def test_anything_but_the_boolean_true_counts_as_paused(tmp_path: Path, next_fix) -> None:
    root = _repo(tmp_path, forecast=_forecast_with(next_fix))
    (line,) = gd.model_pause_lines(root, NOW)
    # no down_since, not a git checkout: the file's own predicted_at is the earliest it can say
    assert line.startswith("The price forecast has been paused since 6 Nov, 02:18 IST:")


def test_a_forecast_that_stopped_updating_says_so_with_the_last_update(tmp_path: Path) -> None:
    # inference refused to write a non-positive forecast, so forecast.json keeps ageing with the
    # last run's next_fix.active still true
    fc = _forecast_with({"active": True}, predicted_at="2026-11-05T10:00:00+00:00")
    root = _repo(tmp_path, forecast=fc)  # NOW is 11 h later
    assert gd.model_pause_lines(root, NOW) == [
        "The price forecast has not updated since 5 Nov, 15:30 IST, "
        "so the range above may be out of date."
    ]
    fresh = datetime(2026, 11, 5, 18, 50, tzinfo=UTC)  # 8.8 h after predicted_at: normal
    assert gd.model_pause_lines(root, fresh) == []


def test_the_stale_limit_is_the_one_the_freshness_alarm_uses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import live_freshness

    assert live_freshness.LIMITS_H["forecast"] == gd.FORECAST_STALE_H


def test_the_monday_note_says_it_too_and_an_unreadable_forecast_adds_nothing(
    tmp_path: Path,
) -> None:
    fc = _forecast_with({"active": False, "down_since": "2026-11-05T07:40:00Z"})
    root = _repo(tmp_path, forecast=fc)
    assert PAUSED in gd.monday(root, NOW).splitlines()
    (root / "data" / "forecast.json").write_text("{not json", encoding="utf-8")
    assert gd.model_pause_lines(root, NOW) == []
    assert "Error" not in gd.daily(root, NOW)


def test_the_streak_start_is_read_from_history_when_the_key_is_absent(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]

    def commit(next_fix: dict, predicted_at: str) -> None:
        path = root / "data" / "forecast.json"
        path.write_text(json.dumps(_forecast_with(next_fix, predicted_at)), encoding="utf-8")
        subprocess.run([*git, "add", "data/forecast.json"], check=True, capture_output=True)
        subprocess.run(
            [*git, "commit", "-q", "-m", "t", "--no-gpg-sign"], check=True, capture_output=True
        )

    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
    commit({"active": True}, "2026-11-05T04:00:00+00:00")
    commit({"active": False}, "2026-11-05T07:40:00+00:00")  # the first paused forecast
    commit({"active": False}, "2026-11-05T14:00:00+00:00")
    (line,) = gd.model_pause_lines(root, NOW)
    assert "paused since 5 Nov, 13:10 IST" in line  # 07:40Z, not the latest 14:00Z


def test_the_pause_sentences_use_none_of_the_banned_jargon(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "check_plain_language", ROOT / "scripts" / "check_plain_language.py"
    )
    cpl = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(cpl)
    stale = gd.model_pause_lines(
        _repo(tmp_path, forecast=_forecast_with({"active": True}, "2026-11-05T10:00:00+00:00")), NOW
    )
    for text in [PAUSED, *stale]:
        assert [n for n, rx in cpl.BANNED_TERMS if rx.search(text)] == [], text


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
