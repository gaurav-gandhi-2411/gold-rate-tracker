"""T14 -- "Tanishq has not updated in N hours" (GG decision 4b, 2026-09-25).

A GitHub-side OPS alert keyed on the age of the newest REAL Tanishq row in data/prices.json,
counted in hours that do not fall on a Sunday (IST), threshold 30 h. It runs inside
check-price.yml on a GitHub-hosted runner, so it fires when the self-hosted Tanishq runner is
off -- the gap T12 leaves by design. Nothing here sends a real notification: send_pending and
main() run with urlopen patched and the request captured.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml
from ml import notifications as nm
from ml.notification_routing import OPS, audience_for

IST = ZoneInfo("Asia/Kolkata")
REPO = Path(__file__).resolve().parent.parent
TANISHQ_SRC = "https://www.tanishq.co.in/gold-rate.html?lang=en_IN"


def ist(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=IST)


def row(when: datetime, source: str = TANISHQ_SRC) -> dict:
    return {
        "timestamp": when.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "22k": 14040,
        "24k": 15316,
        "18k": 11487,
        "source": source,
    }


def t14(prices: list[dict], now: datetime, state: nm.NotificationState | None = None, **kw):
    silence = nm.compute_tanishq_silence(prices, now, **kw)
    return nm._check_t14_tanishq_silent(silence, state or nm.NotificationState(), now)


# 2026-09-22 is a Tuesday, 2026-09-26 a Saturday, 2026-09-27 a Sunday.
TUE_10 = ist(2026, 9, 22, 10, 0)
SAT_10 = ist(2026, 9, 26, 10, 0)


def test_threshold_is_30h_and_documented():
    assert nm._T14_TANISHQ_SILENT_THRESHOLD_H == 30.0


def test_weekday_fires_at_30h_not_below():
    prices = [row(TUE_10)]
    assert t14(prices, TUE_10 + timedelta(hours=29, minutes=59)) is None
    alert = t14(prices, TUE_10 + timedelta(hours=30))  # Wed 16:00 IST
    assert alert is not None
    assert alert.trigger_id == "T14"
    assert alert.title == "Gold Tracker: Tanishq has not updated in 30h"
    assert "Tue 22 Sep 10:00 IST" in alert.body
    assert alert.title.isascii()


def test_normal_weekend_never_alerts_but_monday_outage_does():
    prices = [row(SAT_10)]  # Saturday's only success is its first visit
    # Monday 12:30 IST, the last visit of the next working morning: 50.5 h on the clock,
    # 26.5 h without Sunday -> silent.
    mon_1230 = ist(2026, 9, 28, 12, 30)
    s = nm.compute_tanishq_silence(prices, mon_1230)
    assert s is not None
    assert s.wall_hours == pytest.approx(50.5)
    assert s.effective_hours == pytest.approx(26.5)
    assert t14(prices, mon_1230) is None
    # Every Monday visit failed too: 30 h (excl. Sunday) is reached at Monday 16:00 IST.
    assert t14(prices, ist(2026, 9, 28, 15, 59)) is None
    alert = t14(prices, ist(2026, 9, 28, 16, 0))
    assert alert is not None
    assert alert.title == "Gold Tracker: Tanishq has not updated in 54h"


def test_sunday_hours_are_excluded_exactly():
    # Sat 20:00 -> Mon 02:00 IST = 30 h on the clock, of which 24 h are Sunday.
    assert nm._hours_excluding_sundays(ist(2026, 9, 26, 20), ist(2026, 9, 28, 2)) == pytest.approx(
        6.0
    )
    # A span entirely inside a Sunday counts zero.
    assert nm._hours_excluding_sundays(ist(2026, 9, 27, 1), ist(2026, 9, 27, 23)) == 0.0
    assert nm._hours_excluding_sundays(TUE_10, TUE_10) == 0.0


def test_dedupe_once_per_ist_day_via_send_and_quiet_hours_stamp(monkeypatch):
    prices = [row(TUE_10)]
    now = TUE_10 + timedelta(hours=31)  # Wed 17:00 IST
    state = nm.NotificationState()
    alert = t14(prices, now, state)
    assert alert is not None

    captured = []

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        nm.urllib.request, "urlopen", lambda req, timeout=None: captured.append(req) or _Resp()
    )
    monkeypatch.setenv("NTFY_TOPIC", "ops-topic-under-test")
    monkeypatch.setenv("NTFY_TOPIC_PUBLIC", "public-topic-under-test")
    sent = nm.send_pending([alert], state, now)
    assert [s.success for s in sent] == [True]
    assert captured[0].full_url.endswith("/ops-topic-under-test")  # OPS, never public
    assert state.last_t14_ist_date == "2026-09-23"
    # Every later check-price run the same IST day: no re-alert.
    for h in (1, 3, 6):
        assert t14(prices, now + timedelta(hours=h), state) is None
    # Still silent the next IST day -> one reminder that day.
    assert t14(prices, ist(2026, 9, 24, 9, 0), state) is not None

    # Quiet hours: a queued T14 is stamped at queue time, so the next quiet-hours run does
    # not queue a second copy.
    quiet = nm.NotificationState()
    nm._stamp_ist_dedup("T14", quiet, now)
    assert quiet.last_t14_ist_date == "2026-09-23"
    assert t14(prices, now + timedelta(hours=2), quiet) is None


def test_state_round_trips(tmp_path):
    state = nm.NotificationState(last_t14_ist_date="2026-09-23")
    path = tmp_path / "state.json"
    nm.save_state(state, path)
    assert nm.load_state(path).last_t14_ist_date == "2026-09-23"


def test_takedown_and_derived_rows_never_alert_or_count():
    stale = [row(TUE_10)]
    now = TUE_10 + timedelta(hours=72)
    # Tanishq switched off (ADR 059): deliberate silence, no alert.
    assert t14(stale, now, tanishq_enabled=False) is None
    # IBJA-derived rows after the last real reading are not Tanishq readings.
    derived = row(now - timedelta(hours=1), source="ibja_calibrated_derived")
    s = nm.compute_tanishq_silence([*stale, derived], now)
    assert s is not None
    assert s.last_reading_utc.startswith("2026-09-22T04:30")
    # A fully derived history has no Tanishq reading at all.
    assert nm.compute_tanishq_silence([derived], now) is None
    assert nm.compute_tanishq_silence([], now) is None


def test_t14_routes_to_ops():
    assert audience_for("T14") == OPS


def test_runner_offline_path_fires_on_github_side_end_to_end(tmp_path, monkeypatch):
    """The self-hosted runner is off: it recorded no failure (T12 stays silent), prices.json
    just stopped growing. main() -- exactly what check-price.yml runs on ubuntu-latest --
    still sends T14 to the OPS topic. urlopen is patched: nothing leaves the machine."""
    now = datetime.now(IST)
    last = now - timedelta(hours=100)  # >= 76 h excluding at most one Sunday
    (tmp_path / "prices.json").write_text(json.dumps([row(last)]), encoding="utf-8")
    for name in ("forecast.json", "chronos_probe.json", "backtest.json", "calibration.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(nm, "PRICES_JSON", tmp_path / "prices.json")
    monkeypatch.setattr(nm, "FORECAST_JSON", tmp_path / "forecast.json")
    monkeypatch.setattr(nm, "PROBE_JSON", tmp_path / "chronos_probe.json")
    monkeypatch.setattr(nm, "BACKTEST_JSON", tmp_path / "backtest.json")
    monkeypatch.setattr(nm, "CALIBRATION_JSON", tmp_path / "calibration.json")
    # Runner offline: its health file never moved (no job ran) -> no T12.
    monkeypatch.setattr(nm, "compute_selfhosted_consecutive_failures", lambda *a, **k: None)
    monkeypatch.setattr(nm, "compute_snapshot_gap_days", lambda *a, **k: None)
    monkeypatch.setattr(nm, "compute_usable_snapshot_gap_days", lambda *a, **k: None)
    monkeypatch.setattr(nm, "compute_ibja_gap_business_days", lambda *a, **k: None)
    monkeypatch.setattr(nm, "_is_quiet_hours", lambda *a, **k: False)
    captured = []

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        nm.urllib.request, "urlopen", lambda req, timeout=None: captured.append(req) or _Resp()
    )
    monkeypatch.setenv("NTFY_TOPIC", "ops-topic-under-test")
    monkeypatch.setenv("NTFY_TOPIC_PUBLIC", "public-topic-under-test")
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(sys, "argv", ["ml.notifications", "--state-path", str(state_path)])

    nm.main()
    t14_reqs = [r for r in captured if "Tanishq has not updated" in r.headers["Title"]]
    assert len(t14_reqs) == 1
    assert t14_reqs[0].full_url.endswith("/ops-topic-under-test")
    assert not [r for r in captured if "self-hosted runner failing" in r.headers["Title"]]
    # Second run the same day (next check-price tick): deduped.
    captured.clear()
    nm.main()
    assert not [r for r in captured if "Tanishq has not updated" in r.headers["Title"]]


def test_check_price_runs_notifications_on_a_github_hosted_runner():
    wf = yaml.safe_load(
        (REPO / ".github" / "workflows" / "check-price.yml").read_text(encoding="utf-8")
    )
    job = wf["jobs"]["check"]
    assert job["runs-on"] == "ubuntu-latest"
    runs = [s.get("run", "") for s in job["steps"]]
    assert any("python -m ml.notifications" in r for r in runs)
