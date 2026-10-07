"""scripts/build_timeliness_report.py: slots served inside their window, overnight window lateness."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IST = timezone(timedelta(hours=5, minutes=30))


def _load():
    spec = importlib.util.spec_from_file_location(
        "btr", ROOT / "scripts" / "build_timeliness_report.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_row(when_utc: datetime, event: str = "workflow_dispatch", ok: bool = True) -> dict:
    return {
        "createdAt": when_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": event,
        "conclusion": "success" if ok else "cancelled",
    }


def test_slot_served_only_inside_its_window(monkeypatch) -> None:
    mod = _load()
    now = datetime(2026, 10, 8, 0, 30, tzinfo=IST).astimezone(UTC)
    day = datetime(2026, 10, 7, tzinfo=IST)
    rows = []
    for hm in mod.SLOTS_IST:
        h, m = map(int, hm.split(":"))
        slot = day.replace(hour=h, minute=m)
        if hm == "10:40":  # served 2 hours late (laptop was off): outside the 45-minute window
            rows.append(_run_row((slot + timedelta(hours=2)).astimezone(UTC)))
        elif hm == "11:10":  # a push-triggered run does not count as a slot visit
            rows.append(_run_row(slot.astimezone(UTC), event="push"))
        elif hm == "15:35":  # cancelled by the concurrency group: not served
            rows.append(_run_row(slot.astimezone(UTC), ok=False))
        else:
            rows.append(_run_row((slot + timedelta(minutes=3)).astimezone(UTC)))
    monkeypatch.setattr(mod, "_run", lambda cmd: json.dumps(rows))
    rep = mod.slot_report(now, days=1)
    assert rep["slots"] == 6 and rep["served"] == 3 and rep["missed"] == 3
    assert {s[11:16] for s in rep["missed_slots_ist"]} == {"10:40", "11:10", "15:35"}


def test_window_still_open_is_not_counted_as_missed(monkeypatch) -> None:
    mod = _load()
    now = datetime(2026, 10, 8, 7, 40, tzinfo=IST).astimezone(UTC)  # 07:30 slot window still open
    monkeypatch.setattr(mod, "_run", lambda cmd: "[]")
    rep = mod.slot_report(now, days=1)
    assert not any(s.startswith("2026-10-08T07:30") for s in rep["missed_slots_ist"])


def test_overnight_window_lateness_and_fallback_nights(monkeypatch) -> None:
    mod = _load()
    now = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    # night of 10-07: model forecast 40 min after the close; night of 10-08: only hold modes
    commits = {
        "aaa": ("2026-10-07T22:55:00+00:00", "after_us_close"),
        "bbb": ("2026-10-08T23:10:00+00:00", "hold_latest_fix_pm_to_am"),
        "ccc": ("2026-10-08T12:00:00+00:00", "after_us_close"),  # outside any overnight window
    }
    log = "\n".join(f"{sha} {iso}" for sha, (iso, _) in commits.items())

    def fake(cmd):
        if cmd[:2] == ["git", "log"]:
            return log
        sha = cmd[2].split(":")[0]
        return json.dumps({"next_fix": {"mode": commits[sha][1]}})

    monkeypatch.setattr(mod, "_run", fake)
    rep = mod.window_report(now, days=7)
    assert rep["nights"] == 2 and rep["nights_with_model_forecast"] == 1
    assert rep["minutes_after_us_close"] == [40] and rep["nights_without_model_forecast"] == 1


def test_no_history_is_reported_not_guessed(monkeypatch) -> None:
    mod = _load()
    monkeypatch.setattr(mod, "_run", lambda cmd: "")
    assert mod.window_report(datetime(2026, 10, 9, tzinfo=UTC), 7)["available"] is False


def test_a_failing_source_is_recorded_and_never_raises(monkeypatch) -> None:
    mod = _load()

    def boom(cmd):
        raise OSError("gh not installed")

    monkeypatch.setattr(mod, "_run", boom)
    doc = mod.build(datetime(2026, 10, 9, tzinfo=UTC))
    assert (
        doc["tanishq_slots"]["available"] is False
        and "gh not installed" in doc["tanishq_slots"]["error"]
    )
    assert doc["overnight_window"]["available"] is False


# --- attribution of missed slots (what GitHub's run list can and cannot tell) -----------------------


def _row2(when_utc: datetime, conclusion: str, event: str = "workflow_dispatch") -> dict:
    return {
        "createdAt": when_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": event,
        "conclusion": conclusion,
        "status": "completed" if conclusion else "in_progress",
    }


def _slot(day: int, hm: str) -> datetime:
    h, m = map(int, hm.split(":"))
    return datetime(2026, 10, day, h, m, tzinfo=IST)


def _classes(rep: dict) -> dict[str, str]:
    return {p["slot_ist"][11:16]: p["class"] for p in rep["per_slot"]}


def test_missed_slots_are_attributed_to_a_cause(monkeypatch) -> None:
    mod = _load()
    now = datetime(2026, 10, 8, 0, 30, tzinfo=IST).astimezone(UTC)
    rows = [
        _row2((_slot(7, "01:40") + timedelta(minutes=2)).astimezone(UTC), "success"),  # served
        # 07:30: nothing at all -> no_dispatch (laptop off / scheduler idle)
        _row2((_slot(7, "10:40") + timedelta(minutes=5)).astimezone(UTC), "failure"),
        _row2((_slot(7, "11:10") + timedelta(minutes=5)).astimezone(UTC), "cancelled"),
        _row2((_slot(7, "15:35") + timedelta(hours=2)).astimezone(UTC), "success"),  # late
        _row2((_slot(7, "19:50") + timedelta(minutes=1)).astimezone(UTC), "timed_out"),
    ]
    monkeypatch.setattr(mod, "_run", lambda cmd: json.dumps(rows))
    rep = mod.slot_report(now, days=1)
    assert _classes(rep) == {
        "01:40": "served",
        "07:30": "no_dispatch",
        "10:40": "dispatched_failed",
        "11:10": "dispatched_cancelled",
        "15:35": "late",
        "19:50": "dispatched_failed",
    }
    assert rep["classification"] == {
        "no_dispatch": 1,
        "dispatched_failed": 2,
        "dispatched_cancelled": 1,
        "late": 1,
        "dispatched_other": 0,
        "unknown": 0,
    }
    assert sum(rep["classification"].values()) == rep["missed"]


def test_late_means_within_six_hours_only(monkeypatch) -> None:
    mod = _load()
    now = datetime(2026, 10, 8, 12, 0, tzinfo=IST).astimezone(UTC)
    # a success 7h after the slot is too late to be "caught up"
    rows = [_row2((_slot(7, "01:40") + timedelta(hours=7)).astimezone(UTC), "success")]
    monkeypatch.setattr(mod, "_run", lambda cmd: json.dumps(rows))
    assert _classes(mod.slot_report(now, days=1))["01:40"] == "no_dispatch"


def test_a_push_run_never_counts_as_a_dispatch_attribution(monkeypatch) -> None:
    mod = _load()
    now = datetime(2026, 10, 8, 0, 30, tzinfo=IST).astimezone(UTC)
    rows = [_row2(_slot(7, "07:30").astimezone(UTC), "failure", event="push")]
    monkeypatch.setattr(mod, "_run", lambda cmd: json.dumps(rows))
    assert _classes(mod.slot_report(now, days=1))["07:30"] == "no_dispatch"


def test_runs_are_listed_without_a_status_filter(monkeypatch) -> None:
    mod = _load()
    seen: list[list[str]] = []
    monkeypatch.setattr(mod, "_run", lambda cmd: seen.append(cmd) or "[]")
    mod.slot_report(datetime(2026, 10, 8, tzinfo=UTC), days=1)
    assert seen and all("--status" not in c for c in seen)
    assert all("createdAt,event,conclusion,status" in c for c in seen)


def test_slots_older_than_a_full_run_listing_are_unknown_not_no_dispatch(monkeypatch) -> None:
    mod = _load()
    now = datetime(2026, 10, 8, 0, 30, tzinfo=IST).astimezone(UTC)
    # the listing hit its limit and its oldest run is newer than the slots: nothing is knowable
    rows = [
        _row2((_slot(7, "20:00") + timedelta(minutes=i)).astimezone(UTC), "success")
        for i in range(mod.RUN_LIMIT)
    ]
    monkeypatch.setattr(mod, "_run", lambda cmd: json.dumps(rows))
    cls = _classes(mod.slot_report(now, days=1))
    assert cls["07:30"] == "unknown" and cls["01:40"] == "unknown"
