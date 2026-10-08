"""scripts/laptop_attribution.py -- say WHY each missed Tanishq visit slot was missed, from the laptop's
own records (Windows event log, the dispatcher's log), because GitHub's run list cannot tell a
laptop that was off from a scheduler that did not fire (scripts/build_timeliness_report.py).

Run ON THE LAPTOP (Windows; reads the System event log and %LOCALAPPDATA%\\gold-rate-tracker\\
tanishq_dispatch.log):

    python scripts/laptop_attribution.py            # merges new slots into data/laptop_attribution.json

Input slots: ``tanishq_slots.missed_slots_ist`` of data/input_timeliness_weekly.json. Output
``data/laptop_attribution.json`` holds one class per slot, no raw event times, and is merged by slot,
so the history outlives the report's 7-day window. The weekly status page
(scripts/build_model_status.py) reads it; CC refreshes it when it scores the day.

Classes (first match wins):
  before_schedule_installed      the slot is earlier than the Task Scheduler tasks' creation time
  laptop_off                     the laptop was shut down (event 13 ... 12), not asleep
  asleep_modern_standby_on_battery   asleep (event 42/506) on battery at the slot
  asleep_wake_timer_failed       asleep on mains at the slot: the wake timer should have fired
  task_ran_dispatch_skipped      the laptop was on and the dispatcher logged SKIP/FAIL (reason kept)
  dispatched_but_run_late        the dispatcher logged DISPATCHED but the run was not on time
  task_did_not_run_while_on      the laptop was on and nothing was logged for the slot
  unknown                        the event log does not reach back to the slot
Task Scheduler's own history log is disabled on this laptop (enabling it needs an administrator),
so "the task fired but the dispatcher died before logging" cannot be told apart from
``task_did_not_run_while_on``.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
IST = timezone(timedelta(hours=5, minutes=30))
TIMELINESS = ROOT / "data" / "input_timeliness_weekly.json"
OUT = ROOT / "data" / "laptop_attribution.json"
WINDOW_MIN = 45  # a dispatch within this many minutes of the slot counts as on time
CLASSES = (
    "before_schedule_installed",
    "laptop_off",
    "asleep_modern_standby_on_battery",
    "asleep_wake_timer_failed",
    "task_ran_dispatch_skipped",
    "dispatched_but_run_late",
    "task_did_not_run_while_on",
    "unknown",
)
# event id -> what it means for the timeline (System log)
KIND = {
    ("Microsoft-Windows-Kernel-General", 12): "boot",
    ("Microsoft-Windows-Kernel-General", 13): "shutdown",
    ("EventLog", 6008): "shutdown",  # previous shutdown was unexpected
    ("Microsoft-Windows-Kernel-Power", 42): "sleep",
    ("Microsoft-Windows-Kernel-Power", 506): "sleep",
    ("Microsoft-Windows-Kernel-Power", 107): "resume",
    ("Microsoft-Windows-Kernel-Power", 507): "resume",
    ("Microsoft-Windows-Kernel-Power", 105): "power",  # first property = AC online
}
_PS = (
    "Get-WinEvent -FilterHashtable @{LogName='System';StartTime=[datetime]'%s';"
    "Id=12,13,42,105,107,506,507,6008} -ErrorAction SilentlyContinue | "
    "Where-Object { $_.ProviderName -in 'Microsoft-Windows-Kernel-General','EventLog',"
    "'Microsoft-Windows-Kernel-Power' } | ForEach-Object { [pscustomobject]@{"
    "t=$_.TimeCreated.ToString('o');p=$_.ProviderName;id=$_.Id;"
    "ac=$(if($_.Id -eq 105){[string]$_.Properties[0].Value}else{''})} } | ConvertTo-Json -Compress"
)


# --- inputs --------------------------------------------------------------------------------------
def collect_events(since: str) -> list[dict[str, Any]]:
    """System-log events since ``since`` (YYYY-MM-DD) as dicts {t (aware), kind, ac}."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", _PS % since],
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    ).stdout.strip()
    raw = json.loads(out) if out else []
    raw = [raw] if isinstance(raw, dict) else raw
    return parse_events(raw)


def parse_events(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ev = []
    for r in raw:
        kind = KIND.get((r["p"], int(r["id"])))
        if kind:
            ev.append(
                {
                    "t": datetime.fromisoformat(r["t"]),
                    "kind": kind,
                    "ac": None if r.get("ac", "") == "" else str(r["ac"]).lower() == "true",
                }
            )
    return sorted(ev, key=lambda e: e["t"])


def parse_dispatch_log(text: str) -> list[dict[str, Any]]:
    """Lines like ``2026-10-06T10:16:34+05:30 slot=07:30 DISPATCHED`` / ``... SKIP cool-off ...``."""
    out = []
    for line in text.splitlines():
        m = re.match(r"(\S+)\s+slot=(\d\d:\d\d)\s+(\S+)\s*(.*)", line.strip())
        if m:
            out.append(
                {
                    "t": datetime.fromisoformat(m.group(1)),
                    "slot": m.group(2),
                    "status": m.group(3),
                    "reason": m.group(4),
                }
            )
    return out


# --- classification ------------------------------------------------------------------------------
def state_at(events: list[dict[str, Any]], t: datetime) -> tuple[str, bool | None]:
    """('on' | 'off' | 'asleep' | 'unknown', mains-power flag or None) at time ``t``."""
    power: str = "unknown"
    ac: bool | None = None
    for e in events:
        if e["t"] > t:
            break
        if e["kind"] == "boot" or e["kind"] == "resume":
            power = "on"
        elif e["kind"] == "shutdown":
            power = "off"
        elif e["kind"] == "sleep":
            power = "asleep"
        elif e["kind"] == "power" and e["ac"] is not None:
            ac = e["ac"]
    return power, ac


def classify_slot(
    slot: datetime,
    events: list[dict[str, Any]],
    dispatches: list[dict[str, Any]],
    installed: datetime,
) -> dict[str, Any]:
    hhmm = slot.strftime("%H:%M")
    later = [
        d for d in dispatches if d["slot"] == hhmm and slot <= d["t"] <= slot + timedelta(hours=12)
    ]
    first = min(later, key=lambda d: d["t"]) if later else None
    late_min = round((first["t"] - slot).total_seconds() / 60) if first else None
    caught = (
        f"; dispatched {late_min} min after the slot"
        if first and first["status"] == "DISPATCHED"
        else "; no catch-up dispatch logged"
    )
    state, ac = state_at(events, slot)
    first_event = events[0]["t"] if events else None
    if slot < installed:
        cls, detail = "before_schedule_installed", "the timed visits were created after this slot"
        if state == "off":
            detail += "; the laptop was also shut down"
    elif first_event is None or slot < first_event:
        cls, detail = "unknown", "the event log does not reach back to the slot"
    elif state == "off":
        cls, detail = "laptop_off", "the laptop was shut down (not asleep)" + caught
    elif state == "asleep":
        cls = "asleep_modern_standby_on_battery" if ac is False else "asleep_wake_timer_failed"
        detail = ("on battery" if ac is False else "on mains or unknown power") + caught
    elif first is None:
        cls, detail = "task_did_not_run_while_on", "the laptop was on; nothing logged for the slot"
    elif first["status"] != "DISPATCHED":
        cls = "task_ran_dispatch_skipped"
        detail = f"{first['status']} {first['reason']}".strip()
    else:
        cls, detail = "dispatched_but_run_late", f"dispatched {late_min} min after the slot"
    return {
        "slot_ist": slot.isoformat(),
        "class": cls,
        "detail": detail,
        "dispatch_delay_min": late_min if first and first["status"] == "DISPATCHED" else None,
    }


def merge(existing: dict[str, Any] | None, new_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_slot = {r["slot_ist"]: r for r in (existing or {}).get("slots", [])}
    by_slot.update({r["slot_ist"]: r for r in new_rows})
    rows = sorted(by_slot.values(), key=lambda r: r["slot_ist"])
    counts = {c: sum(1 for r in rows if r["class"] == c) for c in CLASSES}
    return {"slots": rows, "counts": {k: v for k, v in counts.items() if v}}


def main(argv: list[str] | None = None) -> int:
    if sys.platform != "win32":
        print("run this on the Windows laptop that hosts the visit scheduler", file=sys.stderr)
        return 2
    rep = json.loads(TIMELINESS.read_text(encoding="utf-8"))
    missed = [
        datetime.fromisoformat(s).astimezone(IST)
        for s in (rep.get("tanishq_slots") or {}).get("missed_slots_ist", [])
    ]
    if not missed:
        print("no missed slots in the report")
        return 0
    since = (min(missed) - timedelta(days=2)).strftime("%Y-%m-%d")
    events = collect_events(since)
    log = Path.home() / "AppData" / "Local" / "gold-rate-tracker" / "tanishq_dispatch.log"
    dispatches = parse_dispatch_log(log.read_text(encoding="utf-8")) if log.exists() else []
    ps = (
        "(Get-Item 'C:\\Windows\\System32\\Tasks\\GoldRateTracker\\Tanishq-0140')"
        ".CreationTime.ToString('o')"
    )
    installed = datetime.fromisoformat(
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, check=True
        ).stdout.strip()
    )
    rows = [classify_slot(s, events, dispatches, installed) for s in missed]
    prev = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else None
    out = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "marking": "VERIFIED from the laptop's System event log and the dispatcher log",
        "evidence": {
            "event_log_from": events[0]["t"].isoformat() if events else None,
            "dispatch_log_lines": len(dispatches),
            "tasks_created_ist": installed.astimezone(IST).isoformat(),
            "task_scheduler_history_enabled": False,
        },
        **merge(prev, rows),
    }
    OUT.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(out["counts"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
