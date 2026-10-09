"""scripts/laptop_attribution.py: the real 2026-10-05..07 timeline of the scheduler laptop (boot and
shutdown times read from its System event log, dispatcher log lines as written) classifies the 11
missed slots the way the evidence says, and each class is reachable."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import laptop_attribution as la

IST = timezone(timedelta(hours=5, minutes=30))


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=IST)


def _ev(*rows: tuple[str, str]) -> list[dict]:
    return sorted([{"t": _t(t), "kind": k, "ac": None} for t, k in rows], key=lambda e: e["t"])


# shutdown (13) and boot (12) times, IST, from the laptop's System log
REAL = _ev(
    ("2026-10-04T23:06:39", "shutdown"),
    ("2026-10-05T12:31:58", "boot"),
    ("2026-10-05T18:58:26", "shutdown"),
    ("2026-10-05T21:50:28", "boot"),
    ("2026-10-05T23:14:05", "shutdown"),
    ("2026-10-06T10:06:31", "boot"),
    ("2026-10-06T20:53:50", "shutdown"),
    ("2026-10-07T02:16:55", "boot"),
    ("2026-10-07T03:17:50", "shutdown"),
    ("2026-10-07T17:21:19", "boot"),
)
LOG = la.parse_dispatch_log(
    "\n".join(
        [
            "2026-10-05T13:01:44+05:30 slot=11:10 DISPATCHED",
            "2026-10-05T21:55:29+05:30 slot=19:50 DISPATCHED",
            "2026-10-06T10:16:34+05:30 slot=07:30 DISPATCHED",
            "2026-10-06T10:16:34+05:30 slot=01:40 DISPATCHED",
            "2026-10-07T17:26:23+05:30 slot=15:35 DISPATCHED",
            "2026-10-07T17:26:23+05:30 slot=10:40 DISPATCHED",
            "2026-10-07T17:26:23+05:30 slot=11:10 DISPATCHED",
            "2026-10-07T17:26:23+05:30 slot=07:30 DISPATCHED",
        ]
    )
)
INSTALLED = _t("2026-10-05T12:59:58")
MISSED = [
    "2026-10-05T01:40", "2026-10-05T07:30", "2026-10-05T10:40", "2026-10-05T11:10",
    "2026-10-05T19:50", "2026-10-06T01:40", "2026-10-06T07:30", "2026-10-07T07:30",
    "2026-10-07T10:40", "2026-10-07T11:10", "2026-10-07T15:35",
]  # fmt: skip


def test_the_eleven_real_misses_are_attributed() -> None:
    got = {m: la.classify_slot(_t(m), REAL, LOG, INSTALLED) for m in MISSED}
    classes = [got[m]["class"] for m in MISSED]
    assert classes == ["before_schedule_installed"] * 4 + ["laptop_off"] * 7
    assert all("shut down" in got[m]["detail"] for m in MISSED[3:])
    assert got["2026-10-05T11:10"]["dispatch_delay_min"] == 112  # caught up at registration
    assert got["2026-10-06T01:40"]["dispatch_delay_min"] == 517
    assert got["2026-10-05T01:40"]["dispatch_delay_min"] is None  # nothing logged for that slot


def test_every_class_is_reachable() -> None:
    slot = _t("2026-10-06T12:00")
    base = _ev(("2026-10-06T08:00", "boot"))
    on_log = la.parse_dispatch_log("2026-10-06T12:00:05+05:30 slot=12:00 DISPATCHED")
    skip = la.parse_dispatch_log("2026-10-06T12:00:05+05:30 slot=12:00 SKIP cool-off after blocked")
    c = lambda ev, lg: la.classify_slot(slot, ev, lg, INSTALLED)["class"]  # noqa: E731
    assert c(base, []) == "task_did_not_run_while_on"
    assert c(base, skip) == "task_ran_dispatch_skipped"
    assert la.classify_slot(slot, base, skip, INSTALLED)["detail"].startswith("SKIP cool-off")
    assert c(base, on_log) == "dispatched_but_run_late"
    battery = [*base, {"t": _t("2026-10-06T11:00"), "kind": "sleep", "ac": None}]
    battery.insert(1, {"t": _t("2026-10-06T10:00"), "kind": "power", "ac": False})
    assert c(sorted(battery, key=lambda e: e["t"]), []) == "asleep_modern_standby_on_battery"
    mains = [dict(e, ac=True) if e["kind"] == "power" else e for e in battery]
    assert c(sorted(mains, key=lambda e: e["t"]), []) == "asleep_wake_timer_failed"
    assert c(_ev(("2026-10-07T08:00", "boot")), []) == "unknown"  # log starts after the slot
    assert c([], []) == "unknown"


def test_merge_keeps_history_and_counts() -> None:
    a = la.classify_slot(_t(MISSED[0]), REAL, LOG, INSTALLED)
    b = la.classify_slot(_t(MISSED[5]), REAL, LOG, INSTALLED)
    first = la.merge(None, [a])
    both = la.merge(first, [b, a])
    assert [r["slot_ist"] for r in both["slots"]] == sorted([a["slot_ist"], b["slot_ist"]])
    assert both["counts"] == {"before_schedule_installed": 1, "laptop_off": 1}


def test_parse_events_maps_ids_and_power_flag() -> None:
    raw = [
        {
            "t": "2026-10-07T17:21:19+05:30",
            "p": "Microsoft-Windows-Kernel-General",
            "id": 12,
            "ac": "",
        },
        {
            "t": "2026-10-07T17:32:15+05:30",
            "p": "Microsoft-Windows-Kernel-Power",
            "id": 105,
            "ac": "True",
        },
        {
            "t": "2026-10-07T17:40:00+05:30",
            "p": "Microsoft-Windows-Kernel-Power",
            "id": 999,
            "ac": "",
        },
    ]
    ev = la.parse_events(raw)
    assert [e["kind"] for e in ev] == ["boot", "power"] and ev[1]["ac"] is True


def test_a_cancelled_or_failed_run_is_not_labelled_late() -> None:
    slot = _t("2026-10-06T12:00")
    base = _ev(("2026-10-06T08:00", "boot"))
    on_log = la.parse_dispatch_log("2026-10-06T12:00:05+05:30 slot=12:00 DISPATCHED")
    key = slot.isoformat()
    cancelled = la.classify_slot(slot, base, on_log, INSTALLED, {key: "dispatched_cancelled"})
    assert cancelled["class"] == "dispatched_run_cancelled"
    assert "cancelled" in cancelled["detail"]
    failed = la.classify_slot(slot, base, on_log, INSTALLED, {key: "dispatched_failed"})
    assert failed["class"] == "dispatched_run_failed"
    assert la.classify_slot(slot, base, on_log, INSTALLED, {key: "late"})["class"] == (
        "dispatched_but_run_late"
    )
    assert la.classify_slot(slot, base, on_log, INSTALLED)["class"] == "dispatched_but_run_late"
