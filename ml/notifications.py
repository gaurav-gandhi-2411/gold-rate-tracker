"""Notification system for gold-rate-tracker.

Evaluates triggers (T1-T13) against current data files and dispatches
ntfy push notifications. Designed to run as a CI step after the Chronos probe.

Usage:
    python -m ml.notifications              # run triggers, send if conditions met
    python -m ml.notifications --simulate   # evaluate triggers, print, do not send
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from ml.ibja import compute_ibja_gap_business_days

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

SNAPSHOTS_PARQUET = DATA_DIR / "feature_store" / "snapshots.parquet"
FORECAST_JSON = DATA_DIR / "forecast.json"
PROBE_JSON = DATA_DIR / "chronos_probe.json"
PRICES_JSON = DATA_DIR / "prices.json"
BACKTEST_JSON = DATA_DIR / "backtest.json"
CALIBRATION_JSON = DATA_DIR / "calibration.json"
STATE_PATH = DATA_DIR / "notification_state.json"
SELFHOSTED_HEALTH_JSON = DATA_DIR / "tanishq_selfhosted_health.json"

IST = ZoneInfo("Asia/Kolkata")
_NTFY_BASE = "https://ntfy.sh"
_CLICK_URL = "https://gaurav-gandhi-2411.github.io/gold-rate-tracker/"
_QUIET_START_H = 22  # 22:00 IST
_QUIET_END_H = 7  # 07:00 IST
_MAX_QUEUE_AGE_H = 12  # discard queued alerts older than this
_MAX_T123_PER_24H = 3  # T1+T2+T3 combined cap

_T8_MORNING_THRESHOLD_H = 8  # IST lower bound: fire T8_MORNING at/after 08:00
_T8_MORNING_UPPER_H = 14  # IST upper bound: suppress T8_MORNING at/after 14:00
_T8_EVENING_THRESHOLD_H = 18  # IST lower bound: fire T8_EVENING at/after 18:00
_T8_EVENING_UPPER_H = 22  # IST upper bound: suppress T8_EVENING at/after 22:00 (quiet-hours start)
_T8_FLAT_THRESHOLD_RS = 25  # abs(delta) < this → "held steady" scenario
_T9_IBJA_GAP_THRESHOLD_DAYS = 2  # business days w/o a new IBJA reading trigger T9 (ADR 025)
_T9_ESCALATE_IBJA_GAP_THRESHOLD_DAYS = 4  # 2x T9 threshold -> distinct high-priority escalation
_T10_GAP_THRESHOLD_DAYS = 2  # >=2 calendar days with no new PIT snapshot trigger T10
_T13_GAP_THRESHOLD_DAYS = 2  # >=2 calendar days with no new USABLE PIT snapshot trigger T13
# >=3 consecutive scrape-tanishq-selfhosted job failures (job actually ran, not
# just queued-with-no-runner) trigger T12. At the ~3h schedule cadence that's
# ~9h of the runner being online but genuinely failing -- see docs/RUNBOOK.md.
_T12_CONSECUTIVE_FAILURE_THRESHOLD = 3

SCHEMA_VERSION = 1

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PendingAlert:
    trigger_id: str
    title: str
    body: str
    priority: int
    tags: list[str]
    click_url: str
    queued_at: str  # ISO8601 datetime string (IST timezone)
    bypass_quiet: bool

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> PendingAlert:
        return cls(**d)


@dataclass
class SentAlert:
    trigger_id: str
    sent_at: str  # ISO8601 datetime string (UTC)
    title: str
    success: bool


@dataclass
class NotificationState:
    schema_version: int = SCHEMA_VERSION
    # trigger_id → ISO8601 of last successful send (used for per-trigger cooldown)
    last_sent: dict[str, str] = field(default_factory=dict)
    # serialised PendingAlert dicts held during quiet hours
    queued: list[dict] = field(default_factory=list)
    # [{trigger_id, sent_at}] for rolling 24h combined cap (T1+T2+T3)
    sent_today: list[dict] = field(default_factory=list)
    # IST date YYYY-MM-DD of last T5 send (once-per-day dedup)
    last_t5_ist_date: str = ""
    # IST date YYYY-MM-DD of when T6 first fired (calibration unlocked notification — fires once ever)
    last_t6_fired_date_ist: str = ""
    last_t4_fired_ist_date: str = ""  # IST date YYYY-MM-DD of last T4 fire (dedup)
    last_t7_fired_ist_date: str = ""  # IST date YYYY-MM-DD of last T7 fire (dedup)
    last_t8_morning_ist_date: str = ""  # IST date YYYY-MM-DD of last T8_MORNING send (dedup)
    last_t8_evening_ist_date: str = ""  # IST date YYYY-MM-DD of last T8_EVENING send (dedup)
    last_t9_ist_date: str = ""  # IST date YYYY-MM-DD of last T9 send (once-per-day dedup)
    last_t9_escalate_ist_date: str = ""  # IST date YYYY-MM-DD of last T9_ESCALATE send (dedup)
    last_t10_ist_date: str = ""  # IST date YYYY-MM-DD of last T10 send (once-per-day dedup)
    last_t11_ist_date: str = ""  # IST date YYYY-MM-DD of last T11 send (once-per-day dedup)
    last_t12_ist_date: str = ""  # IST date YYYY-MM-DD of last T12 send (once-per-day dedup)
    last_t13_ist_date: str = ""  # IST date YYYY-MM-DD of last T13 send (once-per-day dedup)


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def load_state(path: Path = STATE_PATH) -> NotificationState:
    """Load notification state from JSON; return a fresh state on any error."""
    if not path.exists():
        return NotificationState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return NotificationState(
            schema_version=raw.get("schema_version", SCHEMA_VERSION),
            last_sent=raw.get("last_sent", {}),
            queued=raw.get("queued", []),
            sent_today=raw.get("sent_today", []),
            last_t5_ist_date=raw.get("last_t5_ist_date", ""),
            last_t6_fired_date_ist=raw.get("last_t6_fired_date_ist", ""),
            last_t4_fired_ist_date=raw.get("last_t4_fired_ist_date", ""),
            last_t7_fired_ist_date=raw.get("last_t7_fired_ist_date", ""),
            last_t8_morning_ist_date=raw.get("last_t8_morning_ist_date", ""),
            last_t8_evening_ist_date=raw.get("last_t8_evening_ist_date", ""),
            last_t9_ist_date=raw.get("last_t9_ist_date", ""),
            last_t9_escalate_ist_date=raw.get("last_t9_escalate_ist_date", ""),
            last_t10_ist_date=raw.get("last_t10_ist_date", ""),
            last_t11_ist_date=raw.get("last_t11_ist_date", ""),
            last_t12_ist_date=raw.get("last_t12_ist_date", ""),
            last_t13_ist_date=raw.get("last_t13_ist_date", ""),
        )
    except Exception as exc:
        logger.warning("Could not load notification state (%s) — using fresh state.", exc)
        return NotificationState()


def save_state(state: NotificationState, path: Path = STATE_PATH) -> None:
    """Write notification state to JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": state.schema_version,
        "last_sent": state.last_sent,
        "queued": state.queued,
        "sent_today": state.sent_today,
        "last_t5_ist_date": state.last_t5_ist_date,
        "last_t6_fired_date_ist": state.last_t6_fired_date_ist,
        "last_t4_fired_ist_date": state.last_t4_fired_ist_date,
        "last_t7_fired_ist_date": state.last_t7_fired_ist_date,
        "last_t8_morning_ist_date": state.last_t8_morning_ist_date,
        "last_t8_evening_ist_date": state.last_t8_evening_ist_date,
        "last_t9_ist_date": state.last_t9_ist_date,
        "last_t9_escalate_ist_date": state.last_t9_escalate_ist_date,
        "last_t10_ist_date": state.last_t10_ist_date,
        "last_t11_ist_date": state.last_t11_ist_date,
        "last_t12_ist_date": state.last_t12_ist_date,
        "last_t13_ist_date": state.last_t13_ist_date,
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_quiet_hours(now_ist: datetime) -> bool:
    """Return True if now_ist falls in 22:00–07:00 IST quiet window."""
    h = now_ist.hour
    return h >= _QUIET_START_H or h < _QUIET_END_H


def _in_cooldown(trigger_id: str, state: NotificationState, cooldown_hours: float) -> bool:
    """Return True if trigger_id was successfully sent within cooldown_hours."""
    last = state.last_sent.get(trigger_id)
    if not last:
        return False
    elapsed = (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds() / 3600
    return elapsed < cooldown_hours


def _prune_sent_today(state: NotificationState, window_hours: float = 24.0) -> None:
    """Remove sent_today entries older than window_hours (in-place)."""
    cutoff = (datetime.now(UTC) - timedelta(hours=window_hours)).isoformat()
    state.sent_today = [s for s in state.sent_today if s["sent_at"] >= cutoff]


def _count_sent(
    state: NotificationState,
    trigger_ids: list[str],
    window_hours: float = 24.0,
) -> int:
    """Count sends for the given trigger IDs within the past window_hours."""
    cutoff = (datetime.now(UTC) - timedelta(hours=window_hours)).isoformat()
    return sum(
        1 for s in state.sent_today if s["trigger_id"] in trigger_ids and s["sent_at"] >= cutoff
    )


def _make_alert(
    trigger_id: str,
    title: str,
    body: str,
    priority: int,
    tags: list[str],
    now_ist: datetime,
    bypass_quiet: bool = False,
) -> PendingAlert:
    return PendingAlert(
        trigger_id=trigger_id,
        title=title,
        body=body,
        priority=priority,
        tags=tags,
        click_url=_CLICK_URL,
        queued_at=now_ist.isoformat(),
        bypass_quiet=bypass_quiet,
    )


# ---------------------------------------------------------------------------
# Signal computation (exported so tests can verify the math)
# ---------------------------------------------------------------------------


def compute_chronos_lean(probe: dict) -> tuple[str, float]:
    """Return (direction, strength_pct) from chronos_probe.json.

    direction : "up" | "down" | "flat"
    strength_pct : |median(p50[h=1..5]) - ibja_last| / ibja_last * 100
    """
    forecasts = probe.get("ibja_forecast", [])
    ibja_last = probe.get("ibja_last_value", 0.0)
    if not forecasts or ibja_last <= 0:
        return "flat", 0.0
    med_p50 = median(f["p50"] for f in forecasts)
    delta = med_p50 - ibja_last
    strength = abs(delta) / ibja_last * 100.0
    if strength < 0.1:
        return "flat", 0.0
    return ("down" if delta < 0 else "up"), round(strength, 3)


def compute_recent_momentum(prices: list[dict], n_days: int = 7) -> tuple[str, float]:
    """Return (direction, pct_change) from the last n_days of Tanishq 22K prices.

    direction : "up" | "down" | "flat"
    pct_change : (end - start) / start * 100
    """
    if len(prices) < 2:
        return "flat", 0.0
    sorted_p = sorted(prices, key=lambda p: p["timestamp"])
    latest_ts = datetime.fromisoformat(sorted_p[-1]["timestamp"].replace("Z", "+00:00"))
    cutoff = (latest_ts - timedelta(days=n_days)).isoformat()
    window = [p for p in sorted_p if p["timestamp"] >= cutoff]
    if len(window) < 2:
        return "flat", 0.0
    start_price = window[0]["22k"]
    end_price = window[-1]["22k"]
    if start_price <= 0:
        return "flat", 0.0
    pct = (end_price - start_price) / start_price * 100.0
    if abs(pct) < 0.05:
        return "flat", 0.0
    return ("up" if pct > 0 else "down"), round(pct, 3)


def compute_snapshot_gap_days(
    now_ist: datetime,
    path: Path = SNAPSHOTS_PARQUET,
) -> int | None:
    """Calendar days since the most recent PIT feature-store snapshot.

    The direction-model revisit timeline (docs/DIRECTION_SIGNAL_STATUS.md) depends on
    ~1 snapshot/calendar-day landing in data/feature_store/snapshots.parquet. Returns
    None if the store is missing/empty/unreadable -- a fresh or reset store is not a
    capture failure, so T10 stays silent rather than alerting on it.
    """
    if not path.exists():
        return None
    try:
        import pandas as pd

        df = pd.read_parquet(path, columns=["as_of_date"])
        if df.empty:
            return None
        max_date = pd.to_datetime(df["as_of_date"]).max().date()
    except Exception as exc:
        logger.warning("Could not read feature-store snapshots (%s) - skipping T10 check", exc)
        return None
    return (now_ist.date() - max_date).days


def compute_usable_snapshot_gap_days(
    now_ist: datetime,
    path: Path = SNAPSHOTS_PARQUET,
) -> int | None:
    """Calendar days since the most recent USABLE feature-store snapshot -- one
    whose ibja_pm_916_asof_date matches its own as_of_date, the same leak-free
    same-day-IBJA gate ml.direction.dataset applies before a row can enter the
    direction-model training set.

    Distinct from compute_snapshot_gap_days (T10), which only checks that SOME
    row landed recently regardless of whether it's usable. Raw capture and
    usable capture can silently diverge: the 2026-06-07 -> 2026-08-05
    capture-timing bug in ml.feature_store.append_snapshot produced a fresh
    row every single day (T10 stayed green throughout) while every one of
    those rows carried a stale IBJA join, freezing the direction-model
    dataset at n=113 for 8 weeks with no alert. T10 watches raw arrival; T13
    watches whether what arrives is actually usable -- neither implies the
    other. Returns None if the store is missing/empty/unreadable or has no
    usable row at all (same non-alerting convention as T10 -- a fresh/reset
    store is not a capture failure).
    """
    if not path.exists():
        return None
    try:
        import pandas as pd

        df = pd.read_parquet(path, columns=["as_of_date", "ibja_pm_916_asof_date"])
        if df.empty:
            return None
        df["as_of_date"] = df["as_of_date"].astype(str)
        valid = df[df["ibja_pm_916_asof_date"].notna()].copy()
        if valid.empty:
            return None
        valid["ibja_pm_916_asof_date"] = valid["ibja_pm_916_asof_date"].astype(str)
        usable = valid[valid["ibja_pm_916_asof_date"] >= valid["as_of_date"]]
        if usable.empty:
            return None
        max_usable_date = pd.to_datetime(usable["as_of_date"]).max().date()
    except Exception as exc:
        logger.warning("Could not read feature-store snapshots (%s) - skipping T13 check", exc)
        return None
    return (now_ist.date() - max_usable_date).days


def compute_selfhosted_consecutive_failures(path: Path = SELFHOSTED_HEALTH_JSON) -> int | None:
    """Consecutive scrape-tanishq-selfhosted job failures, from the health record the
    job itself writes every run (check-price.yml, if:always() step).

    Counts only cycles where the job actually STARTED executing on the runner and
    then failed (checkout, npm ci, playwright install, or the scrape itself) --
    this is deliberately distinct from "no runner registered / job queued
    forever", which is the documented, non-alerting steady state for a paused
    self-hosted runner (docs/RUNBOOK.md). Returns None if the file is missing/
    unreadable -- a fresh/reset record is not itself a failure signal, same
    convention as T9/T10.
    """
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return int(raw.get("consecutive_job_failures", 0))
    except Exception as exc:
        logger.warning("Could not read selfhosted health record (%s) - skipping T12 check", exc)
        return None


def compute_dir_acc_30f(backtest: dict) -> float:
    """Direction accuracy on the last 30 backtest folds (or all if fewer).

    Measures: fraction where sign(chronos_p50[h=5] - context_last)
    matches sign(actual[h=5] - context_last).
    """
    folds = backtest.get("folds", [])
    if not folds:
        return 0.0
    recent = folds[-30:]
    correct = sum(
        1
        for fold in recent
        if (fold["chronos_p50"][-1] - fold["naive"][0]) * (fold["actuals"][-1] - fold["naive"][0])
        > 0
    )
    return correct / len(recent)


# ---------------------------------------------------------------------------
# Individual trigger checks
# ---------------------------------------------------------------------------


def _check_t1(
    forecast: dict,
    probe: dict,
    prices: list[dict],
    backtest: dict,
    state: NotificationState,
    now_ist: datetime,
) -> PendingAlert | None:
    """T1 — 7-day momentum down: prices have been trending down over the past week.

    ADR 020: Chronos consensus gate removed (model is deterministic, gate was inert).
    Trigger is now purely momentum-based — a description of observed trend, not a forecast.
    """
    if _in_cooldown("T1", state, 24.0):
        return None
    if _count_sent(state, ["T1", "T2", "T3"]) >= _MAX_T123_PER_24H:
        return None
    if probe.get("status") != "success":
        return None
    # Gate on ≥30 backtest folds — ensures the conformal PI and naive_mae are reliable.
    if backtest.get("n_folds", 0) < 30:
        return None
    mom_dir, mom_pct = compute_recent_momentum(prices)
    if mom_dir != "down":
        return None
    # Require a meaningful move (>= 0.5%) — filters out sub-noise drift.
    if abs(mom_pct) < 0.5:
        logger.debug("T1 suppressed: momentum %.3f%% below 0.5%% threshold", mom_pct)
        return None
    # Read the latest reading by timestamp (not array order) and coerce to int —
    # matches T3/T8/T9 so the body never shows "Rs.14420.0" or a stale reading
    # if prices.json is ever out of order.
    sorted_p = sorted(prices, key=lambda p: p["timestamp"])
    current = int(sorted_p[-1]["22k"]) if sorted_p else 0
    abs_mom = abs(mom_pct)
    title = "Gold: 22K prices are down this week"
    body = (
        f"Gold 22K: Rs.{current}. "
        f"Prices are down {abs_mom:.1f}% over the past 7 days. "
        "A recent trend -- not a forecast. Check the app for context."
    )
    return _make_alert("T1", title, body, 4, ["decline", "chart_with_downwards_trend"], now_ist)


def _check_t2(
    forecast: dict,
    probe: dict,
    prices: list[dict],
    backtest: dict,
    state: NotificationState,
    now_ist: datetime,
) -> PendingAlert | None:
    """T2 — 7-day momentum up: prices have been trending up over the past week.

    ADR 020: Chronos consensus gate removed (model is deterministic, gate was inert).
    Trigger is now purely momentum-based — a description of observed trend, not a forecast.
    """
    if _in_cooldown("T2", state, 24.0):
        return None
    if _count_sent(state, ["T1", "T2", "T3"]) >= _MAX_T123_PER_24H:
        return None
    if probe.get("status") != "success":
        return None
    if backtest.get("n_folds", 0) < 30:
        return None
    mom_dir, mom_pct = compute_recent_momentum(prices)
    if mom_dir != "up":
        return None
    # Require a meaningful move (>= 0.5%) — filters out sub-noise drift.
    if abs(mom_pct) < 0.5:
        logger.debug("T2 suppressed: momentum %.3f%% below 0.5%% threshold", mom_pct)
        return None
    # Read the latest reading by timestamp (not array order) and coerce to int —
    # matches T3/T8/T9 (see T1).
    sorted_p = sorted(prices, key=lambda p: p["timestamp"])
    current = int(sorted_p[-1]["22k"]) if sorted_p else 0
    title = "Gold: 22K prices are up this week"
    body = (
        f"Gold 22K: Rs.{current}. "
        f"Prices are up {mom_pct:.1f}% over the past 7 days. "
        "A recent trend -- not a forecast. Check the app for context."
    )
    return _make_alert("T2", title, body, 3, ["rise", "chart_with_upwards_trend"], now_ist)


def _check_t3(
    forecast: dict,
    probe: dict,
    prices: list[dict],
    backtest: dict,
    state: NotificationState,
    now_ist: datetime,
) -> PendingAlert | None:
    """T3 — Actual large move: |current - prev| >= Rs.150 (model-agnostic)."""
    if _in_cooldown("T3", state, 4.0):
        return None
    if _count_sent(state, ["T1", "T2", "T3"]) >= _MAX_T123_PER_24H:
        return None
    if _count_sent(state, ["T3"], 24.0) >= 2:
        return None
    if len(prices) < 2:
        return None
    sorted_p = sorted(prices, key=lambda p: p["timestamp"])
    current = sorted_p[-1]["22k"]
    prev = sorted_p[-2]["22k"]
    delta = current - prev
    if abs(delta) < 150:
        return None
    direction = "up" if delta > 0 else "down"
    pct = delta / prev * 100.0
    abs_delta = abs(delta)
    priority = 5 if abs_delta >= 300 else 4
    title = f"Gold: Rs.{abs_delta} {direction} detected ({pct:+.1f}%)"
    body = f"Gold 22K: Rs.{current} ({pct:+.1f}% from Rs.{prev}). Check the app for context."
    return _make_alert(
        "T3", title, body, priority, ["warning", "chart_with_upwards_trend"], now_ist
    )


def _check_t4(
    forecast: dict,
    probe: dict,
    prices: list[dict],
    backtest: dict,
    state: NotificationState,
    now_ist: datetime,
) -> PendingAlert | None:
    """T4 — Weekly digest: Sunday at or after 17:00 IST. Missed-recovery fires on Monday.

    Deduped by IST date (last_t4_fired_ist_date). Bypasses quiet hours.
    """
    today_ist = now_ist.strftime("%Y-%m-%d")

    if now_ist.weekday() == 6:  # Sunday
        if now_ist.hour < 17:
            return None
        if state.last_t4_fired_ist_date == today_ist:
            return None
        title_prefix = ""
    elif now_ist.weekday() == 0:  # Monday — missed-recovery
        prior_sunday = (now_ist - timedelta(days=1)).strftime("%Y-%m-%d")
        if state.last_t4_fired_ist_date == prior_sunday:
            return None  # Sunday already got a T4
        if state.last_t4_fired_ist_date == today_ist:
            return None  # Already fired today
        title_prefix = "[Delayed] "
    else:
        return None

    current = prices[-1]["22k"] if prices else 0
    title = f"{title_prefix}Gold Weekly: 22K Rs.{current}"
    body = f"Gold 22K: Rs.{current}. Check the app for the latest read."
    return _make_alert(
        "T4", title, body, 2, ["newspaper", "white_flower"], now_ist, bypass_quiet=True
    )


def _check_t5(
    forecast: dict,
    probe: dict,
    prices: list[dict],
    backtest: dict,
    state: NotificationState,
    now_ist: datetime,
) -> PendingAlert | None:
    """T5 — Model degraded: model_fallback=true or probe not successful. Once per IST day."""
    fallback = forecast.get("model_fallback", False)
    probe_ok = probe.get("status") == "success"
    if not fallback and probe_ok:
        return None
    today_ist = now_ist.strftime("%Y-%m-%d")
    if state.last_t5_ist_date == today_ist:
        return None
    if fallback:
        title = "Gold: tracking system running on backup"
        body = (
            "The direction-tracking system encountered an issue and switched to backup mode. "
            "Headline price is still accurate. Check the app and CI logs."
        )
    else:
        title = "Gold: direction signal temporarily unavailable"
        body = (
            "The direction signal could not be updated this cycle. "
            "Price readings are unaffected. Check the app and CI logs."
        )
    return _make_alert("T5", title, body, 2, ["warning", "rotating_light"], now_ist)


def _check_t6(
    calibration: dict | None,
    state: NotificationState,
    now_ist: datetime,
) -> PendingAlert | None:
    """T6 — Calibration unlocked: fires once ever when calibration.valid first becomes True.

    Fires at most once per lifetime (deduped via state.last_t6_fired_date_ist).
    Priority 3 (informational). ASCII-only body.
    """
    if not calibration:
        return None
    if not calibration.get("valid"):
        return None
    # Already fired previously — covers both same-day repeat and lifetime-already-fired
    if state.last_t6_fired_date_ist:
        return None
    n_obs = calibration.get("n_observations", 0)
    title = "Gold forecast: calibration unlocked"
    body = (
        f"IBJA->Tanishq calibration achieved {n_obs} overlap pairs (>=30). "
        "Chronos directional companion is now calibrated to Tanishq units. "
        "See dashboard."
    )
    return _make_alert("T6", title, body, 3, ["unlock", "white_check_mark"], now_ist)


def _check_t7(
    forecast: dict,
    probe: dict,
    prices: list[dict],
    backtest: dict,
    state: NotificationState,
    now_ist: datetime,
) -> PendingAlert | None:
    """T7 — System-alive floor: fires on the first CI run of the day when >=3 IST days

    have elapsed since last T7. Skips silently when Chronos probe failed (T5 covers that).
    Does NOT count toward the T1+T2+T3 combined 3-per-24h cap.
    """
    if probe.get("status") != "success":
        return None

    today_ist = now_ist.strftime("%Y-%m-%d")
    if state.last_t7_fired_ist_date == today_ist:
        return None  # Already fired today

    if state.last_t7_fired_ist_date:
        last_date = datetime.strptime(state.last_t7_fired_ist_date, "%Y-%m-%d").date()
        today_date = now_ist.date()
        if (today_date - last_date).days < 3:
            return None

    current = prices[-1]["22k"] if prices else 0
    lean_dir, _ = compute_chronos_lean(probe)
    _mom_dir, mom_pct = compute_recent_momentum(prices)
    abs_mom = abs(mom_pct)
    if abs_mom < 0.5:
        week_desc = "Roughly flat this week"
    elif mom_pct > 0:
        week_desc = f"Up {abs_mom:.1f}% this week"
    else:
        week_desc = f"Down {abs_mom:.1f}% this week"
    lean_hint = ""
    if lean_dir == "up":
        lean_hint = " Prices may edge up a little."
    elif lean_dir == "down":
        lean_hint = " Prices may ease a little."
    title = f"Gold daily check: Rs.{current}"
    body = f"Gold 22K: Rs.{current}. {week_desc}.{lean_hint} System working normally."
    return _make_alert("T7", title, body, 2, ["robot", "white_check_mark"], now_ist)


# ---------------------------------------------------------------------------
# T8 helpers
# ---------------------------------------------------------------------------


def _get_prior_day_price(prices: list[dict], now_ist: datetime) -> int | None:
    """Return the most recent Tanishq 22K price from the IST calendar day before today."""
    today_ist_date = now_ist.date()
    prior: list[tuple[datetime, int]] = []
    for p in prices:
        ts_str = p["timestamp"].replace("Z", "+00:00")
        ts_ist = datetime.fromisoformat(ts_str).astimezone(IST)
        if ts_ist.date() < today_ist_date:
            prior.append((ts_ist, int(p["22k"])))
    if not prior:
        return None
    prior.sort(key=lambda x: x[0])
    return prior[-1][1]


def _build_t8_content(
    current: int,
    prior: int | None,
    forecast: dict,
    session: str,
) -> tuple[str, str]:
    """Build (title, body) for a T8 daily digest notification.

    ASCII-safe throughout (Rs. not Rs symbol). Plain language (norm #12).
    Honest framing on directional hint (norm #4): lean, not certainty; no "will".
    """
    delta = (current - prior) if prior is not None else 0

    if prior is None or abs(delta) < _T8_FLAT_THRESHOLD_RS:
        scenario = "steady"
    elif delta > 0:
        scenario = "rose"
    else:
        scenario = "dropped"

    delta_abs = abs(delta)

    if scenario == "rose":
        title = f"Gold {session}: Rs.{current} (up Rs.{delta_abs})"
        body = f"Gold rose today - Rs.{current} (up Rs.{delta_abs} from yesterday)."
    elif scenario == "dropped":
        title = f"Gold {session}: Rs.{current} (down Rs.{delta_abs})"
        body = f"Gold dropped today - Rs.{current} (down Rs.{delta_abs} from yesterday)."
    else:
        title = f"Gold {session}: Rs.{current}"
        body = f"Gold held steady today - Rs.{current}."

    # Optional directional hint: only when chronos_companion is available (norm #4 — honest framing)
    companion = forecast.get("chronos_companion", {})
    if companion.get("status") == "success":
        lean = companion.get("lean_direction", "flat")
        if lean == "up":
            body += " Prices may edge up a little."
        elif lean == "down":
            body += " Prices may ease a little."
        # lean == "flat" or missing → no hint (don't fabricate a direction)

    return title, body


def _check_t8_morning(
    forecast: dict,
    probe: dict,
    prices: list[dict],
    backtest: dict,
    state: NotificationState,
    now_ist: datetime,
) -> PendingAlert | None:
    """T8_MORNING — plain daily digest at first CI run in 08:00–13:59 IST. Once per IST day.

    Window [_T8_MORNING_THRESHOLD_H, _T8_MORNING_UPPER_H) covers the UTC-00 and UTC-06 crons
    (observed IST: ~08:09 and ~13:52 with typical 2-3h GH Actions drift). The upper bound
    prevents this trigger from firing or queuing if a late-night run is the first after 08:00
    on a fresh-state restart (e.g. initial deployment or cache eviction).
    Does NOT count toward the T1+T2+T3 combined anti-spam cap.
    """
    if now_ist.hour < _T8_MORNING_THRESHOLD_H:
        return None
    if now_ist.hour >= _T8_MORNING_UPPER_H:
        return None
    today_ist = now_ist.strftime("%Y-%m-%d")
    if state.last_t8_morning_ist_date == today_ist:
        return None
    if not prices:
        return None
    sorted_p = sorted(prices, key=lambda p: p["timestamp"])
    current = int(sorted_p[-1]["22k"])
    prior = _get_prior_day_price(prices, now_ist)
    title, body = _build_t8_content(current, prior, forecast, "morning")
    return _make_alert("T8_MORNING", title, body, 2, ["bell"], now_ist, bypass_quiet=False)


def _check_t8_evening(
    forecast: dict,
    probe: dict,
    prices: list[dict],
    backtest: dict,
    state: NotificationState,
    now_ist: datetime,
) -> PendingAlert | None:
    """T8_EVENING — plain daily digest at first CI run in 18:00–21:59 IST. Once per IST day.

    Window [_T8_EVENING_THRESHOLD_H, _T8_EVENING_UPPER_H) covers the UTC-12 cron (observed IST:
    ~18:48 with typical 1-3h GH Actions drift). The upper bound keeps the trigger inside the
    clean 18-21 IST window — fully outside quiet hours — so bypass_quiet is belt-and-suspenders
    rather than a routine path. Without the upper bound, a late-night run on a fresh-state restart
    would compute T8_EVENING at hour >= 22 and queue it, colliding with the next T8_MORNING.
    Does NOT count toward the T1+T2+T3 combined anti-spam cap.
    """
    if now_ist.hour < _T8_EVENING_THRESHOLD_H:
        return None
    if now_ist.hour >= _T8_EVENING_UPPER_H:
        return None
    today_ist = now_ist.strftime("%Y-%m-%d")
    if state.last_t8_evening_ist_date == today_ist:
        return None
    if not prices:
        return None
    sorted_p = sorted(prices, key=lambda p: p["timestamp"])
    current = int(sorted_p[-1]["22k"])
    prior = _get_prior_day_price(prices, now_ist)
    title, body = _build_t8_content(current, prior, forecast, "evening")
    return _make_alert("T8_EVENING", title, body, 2, ["bell"], now_ist, bypass_quiet=True)


def _check_t9(
    ibja_gap_days: int | None,
    state: NotificationState,
    now_ist: datetime,
) -> PendingAlert | None:
    """T9 — IBJA data feed stale: >= _T9_IBJA_GAP_THRESHOLD_DAYS business days since
    the last valid IBJA reading. Once per IST calendar day.

    Per ADR 025 (IBJA is now the PRIMARY price source, Tanishq an opportunistic
    enrichment), Tanishq's scrape being stale is the expected steady state under
    its sustained Cloudflare block — it is NOT an error and must NOT trip this
    alert. What actually matters now is whether IBJA itself — the primary source
    — is failing. Gap is measured in business days (ml.ibja.compute_ibja_gap_
    business_days), not wall-clock hours, so IBJA's normal Sat/Sun silence never
    false-alarms: a Friday close is 0 business-days stale all weekend and only 1
    on Monday morning before that day's own publish lands. Returns None (no
    alert) when ibja_gap_days is None — a missing/reset store is not a capture
    failure, same convention as T10.
    """
    if ibja_gap_days is None or ibja_gap_days < _T9_IBJA_GAP_THRESHOLD_DAYS:
        return None

    today_ist = now_ist.strftime("%Y-%m-%d")
    if state.last_t9_ist_date == today_ist:
        return None

    title = f"Gold Tracker: IBJA data stale ({ibja_gap_days}d)"
    body = (
        f"No new IBJA reading in {ibja_gap_days} business days (weekends don't "
        "count). IBJA is the primary price source per ADR 025 — check ibjarates.com "
        "reachability and the ibja.py fetch step in check-price.yml."
    )
    return _make_alert("T9", title, body, 4, ["warning"], now_ist)


def _check_t9_escalate(
    ibja_gap_days: int | None,
    state: NotificationState,
    now_ist: datetime,
) -> PendingAlert | None:
    """T9_ESCALATE — sustained IBJA outage: >= _T9_ESCALATE_IBJA_GAP_THRESHOLD_DAYS
    business days since the last valid IBJA reading (2x the routine T9 threshold).

    T9 fires once per IST day regardless of how stale IBJA gets, so a multi-day
    outage produces the same priority-4 alert every day with no signal that it's
    gotten worse. This fires a separate, higher-priority (max, urgent) alert once
    per IST day when the business-day gap has doubled past the routine T9
    threshold, so a single missed publish stays quiet but a sustained IBJA outage
    becomes impossible to miss. Bypasses quiet hours: with IBJA now the primary
    source (ADR 025), a sustained outage here means the site itself has nothing
    fresher than a week-plus-old estimate — that warrants immediate delivery.
    """
    if ibja_gap_days is None or ibja_gap_days < _T9_ESCALATE_IBJA_GAP_THRESHOLD_DAYS:
        return None

    today_ist = now_ist.strftime("%Y-%m-%d")
    if state.last_t9_escalate_ist_date == today_ist:
        return None

    title = f"Gold Tracker: SUSTAINED IBJA outage ({ibja_gap_days}d)"
    body = (
        f"No new IBJA reading in {ibja_gap_days} business days — well beyond the "
        f"{_T9_IBJA_GAP_THRESHOLD_DAYS}-business-day routine-alert threshold. IBJA is "
        "the primary price source per ADR 025; the site has nothing fresher than a "
        "multi-day-old estimate. Check ibjarates.com and the ibja.py fetch step now."
    )
    return _make_alert(
        "T9_ESCALATE", title, body, 5, ["rotating_light", "warning"], now_ist, bypass_quiet=True
    )


def _check_t10(
    snapshot_gap_days: int | None,
    state: NotificationState,
    now_ist: datetime,
) -> PendingAlert | None:
    """T10 -- Feature-store snapshot capture stalled: no new PIT snapshot in
    >= _T10_GAP_THRESHOLD_DAYS calendar days. Once per IST calendar day.

    A silent capture gap (e.g. bot-pr-sync failing to merge -- bug #4, which cost a
    real 3-day gap 2026-07-13 to 2026-07-15) directly delays the direction-model
    revisit timeline, which is the only thing that can unblock the DARK direction
    signal. Checked independently of price/forecast staleness (T9): the scraper can be
    healthy while the feature-store commit path is broken, and vice versa.
    """
    if snapshot_gap_days is None or snapshot_gap_days < _T10_GAP_THRESHOLD_DAYS:
        return None
    today_ist = now_ist.strftime("%Y-%m-%d")
    if state.last_t10_ist_date == today_ist:
        return None
    title = f"Gold Tracker: feature-store snapshot gap ({snapshot_gap_days}d)"
    body = (
        f"No new direction-model snapshot in {snapshot_gap_days} days. "
        "This delays the direction-signal revisit timeline. Check check-price.yml "
        "and bot-pr-sync (data/feature_store/snapshots.parquet)."
    )
    return _make_alert("T10", title, body, 4, ["warning", "hourglass"], now_ist)


def _check_t11_fusion_fallback(
    forecast: dict,
    state: NotificationState,
    now_ist: datetime,
) -> PendingAlert | None:
    """T11 -- both Tanishq and IBJA unavailable this cycle: the site is serving
    ml.inference's tier-3 fusion-consensus fallback (GRT/Malabar/Kalyan, ADR 026)
    instead of either primary source. Fires the same cycle this happens, unlike
    T9 (which gates on IBJA's own business-day-staleness and can take up to 2
    business days to trip) -- this is the fast, precise signal for "both primary
    sources are down right now", independent of and complementary to T9. Once per
    IST calendar day.
    """
    if forecast.get("price_source") != "fusion_consensus":
        return None
    today_ist = now_ist.strftime("%Y-%m-%d")
    if state.last_t11_ist_date == today_ist:
        return None
    sources = forecast.get("fusion_sources") or []
    names = {"grt": "GRT", "malabar": "Malabar", "kalyan": "Kalyan"}
    sources_str = ", ".join(names.get(s, s) for s in sources) or "retail consensus"
    current = forecast.get("current_22k", 0)
    title = "Gold Tracker: Tanishq and IBJA both unavailable"
    body = (
        f"Gold 22K: Rs.{current} (estimated from {sources_str} consensus). "
        "Both the Tanishq scrape and the IBJA feed failed this cycle. Check "
        "ibjarates.com reachability and the ibja.py fetch step in check-price.yml."
    )
    return _make_alert("T11", title, body, 4, ["warning", "satellite"], now_ist)


def _check_t12_selfhosted_runner(
    consecutive_failures: int | None,
    state: NotificationState,
    now_ist: datetime,
) -> PendingAlert | None:
    """T12 -- Tanishq self-hosted job failing repeatedly while actually running:
    >= _T12_CONSECUTIVE_FAILURE_THRESHOLD consecutive scrape-tanishq-selfhosted
    job failures. Once per IST calendar day.

    Deliberately distinct from "runner offline/unregistered" (a job that never
    starts stays queued and is silently auto-cancelled after 24h -- an
    intentional, documented non-alert per docs/RUNBOOK.md, since a paused
    self-hosted runner is a normal, tolerated state, not a system failure).
    T12 only counts cycles where the job actually started executing and then
    failed (checkout, npm ci, playwright install, or the scrape step itself) --
    "runner is online and picking up jobs, but they keep failing" is a genuinely
    different failure mode with no other detection (incident: 2026-07-26 to
    2026-07-30, the runner's host was powered off for ~4 days; when it woke,
    the first checkouts hit transient connection resets that forced an
    expensive full-repo-recreate on every subsequent run, and nothing alerted
    for the whole window).
    """
    if consecutive_failures is None or consecutive_failures < _T12_CONSECUTIVE_FAILURE_THRESHOLD:
        return None
    today_ist = now_ist.strftime("%Y-%m-%d")
    if state.last_t12_ist_date == today_ist:
        return None
    title = f"Gold Tracker: Tanishq self-hosted runner failing ({consecutive_failures}x)"
    body = (
        f"scrape-tanishq-selfhosted has failed {consecutive_failures} runs in a row "
        "-- the runner is online and picking up jobs, but they're not completing. "
        "Different from a paused/offline runner (which stays silent by design). "
        "Check the runner host (docs/RUNBOOK.md) and the job's recent logs."
    )
    return _make_alert("T12", title, body, 4, ["warning", "desktop_computer"], now_ist)


def _check_t13_usable_snapshot_stall(
    usable_snapshot_gap_days: int | None,
    state: NotificationState,
    now_ist: datetime,
) -> PendingAlert | None:
    """T13 -- feature-store rows are arriving but not usable: no new USABLE PIT
    snapshot (same-day IBJA join) in >= _T13_GAP_THRESHOLD_DAYS calendar days.
    Once per IST calendar day.

    T10 alone missed exactly this failure mode for 8 weeks (2026-06-07 ->
    2026-08-05): raw rows kept landing on schedule while every one of them
    carried a stale IBJA join, so T10's "did a row land recently" check stayed
    green the entire time. T13 checks the thing that actually matters for the
    direction-model revisit timeline -- whether the dataset is growing, not
    just the parquet file.
    """
    if usable_snapshot_gap_days is None or usable_snapshot_gap_days < _T13_GAP_THRESHOLD_DAYS:
        return None
    today_ist = now_ist.strftime("%Y-%m-%d")
    if state.last_t13_ist_date == today_ist:
        return None
    title = f"Gold Tracker: direction dataset stalled ({usable_snapshot_gap_days}d)"
    body = (
        f"No new USABLE direction-model snapshot in {usable_snapshot_gap_days} days, "
        "even though raw feature-store rows may still be landing (see T10). Check "
        "whether ml.ibja is appending before ml.feature_store captures each cycle -- "
        "see ml.feature_store.append_snapshot's same-day-IBJA upgrade logic."
    )
    return _make_alert("T13", title, body, 4, ["warning", "mag"], now_ist)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_triggers(
    forecast: dict,
    probe: dict,
    prices: list[dict],
    backtest: dict,
    state: NotificationState,
    now_ist: datetime,
    calibration: dict | None = None,
    snapshot_gap_days: int | None = None,
    ibja_gap_days: int | None = None,
    selfhosted_consecutive_failures: int | None = None,
    usable_snapshot_gap_days: int | None = None,
) -> list[PendingAlert]:
    """Evaluate all triggers (T1–T13); return new alerts for this call.

    Cooldowns and combined caps are enforced here.  Quiet-hours queuing and
    delivery of previously queued alerts is the caller's responsibility (see
    queue_for_quiet_hours and the main() orchestration).

    T8_MORNING and T8_EVENING are guaranteed daily digests; they do NOT count
    toward the T1+T2+T3 combined anti-spam cap.

    calibration: optional dict from calibration.json. When provided and valid,
        triggers T6 (calibration-unlocked, fires once ever).
    ibja_gap_days: business-day gap since the last valid IBJA reading (see
        ml.ibja.compute_ibja_gap_business_days). Drives T9/T9_ESCALATE per
        ADR 025 — Tanishq scrape staleness no longer does (it's expected).
    selfhosted_consecutive_failures: consecutive scrape-tanishq-selfhosted job
        failures (see compute_selfhosted_consecutive_failures). Drives T12.
    usable_snapshot_gap_days: calendar-day gap since the most recent USABLE
        (same-day-IBJA) feature-store snapshot (see
        compute_usable_snapshot_gap_days). Drives T13 -- distinct from
        snapshot_gap_days/T10, which only checks that some row landed.
    """
    alerts: list[PendingAlert] = []
    for fn in (_check_t1, _check_t2, _check_t3, _check_t4, _check_t5, _check_t7):
        alert = fn(forecast, probe, prices, backtest, state, now_ist)
        if alert is not None:
            alerts.append(alert)
    t6 = _check_t6(calibration, state, now_ist)
    if t6 is not None:
        alerts.append(t6)
    for fn in (_check_t8_morning, _check_t8_evening):
        alert = fn(forecast, probe, prices, backtest, state, now_ist)
        if alert is not None:
            alerts.append(alert)
    t9 = _check_t9(ibja_gap_days, state, now_ist)
    if t9 is not None:
        alerts.append(t9)
    t9_escalate = _check_t9_escalate(ibja_gap_days, state, now_ist)
    if t9_escalate is not None:
        alerts.append(t9_escalate)
    t10 = _check_t10(snapshot_gap_days, state, now_ist)
    if t10 is not None:
        alerts.append(t10)
    t11 = _check_t11_fusion_fallback(forecast, state, now_ist)
    if t11 is not None:
        alerts.append(t11)
    t12 = _check_t12_selfhosted_runner(selfhosted_consecutive_failures, state, now_ist)
    if t12 is not None:
        alerts.append(t12)
    t13 = _check_t13_usable_snapshot_stall(usable_snapshot_gap_days, state, now_ist)
    if t13 is not None:
        alerts.append(t13)
    return alerts


def send_pending(
    alerts: list[PendingAlert],
    state: NotificationState,
    now_ist: datetime,
) -> list[SentAlert]:
    """Send alerts via ntfy.sh; update state.last_sent / sent_today / last_t5_ist_date.

    Reads NTFY_TOPIC from environment. Skips silently if NTFY_TOPIC is unset.
    Titles must be ASCII-only (ntfy header limitation — uses Rs. not the rupee symbol).
    """
    topic = os.environ.get("NTFY_TOPIC", "")
    sent: list[SentAlert] = []
    _prune_sent_today(state)

    for alert in alerts:
        if not topic:
            logger.info("NTFY_TOPIC not set — skipping %s (%s)", alert.trigger_id, alert.title)
            continue
        url = f"{_NTFY_BASE}/{topic}"
        headers = {
            "Title": alert.title,
            "Priority": str(alert.priority),
            "Tags": ",".join(alert.tags),
            "Click": alert.click_url,
            "Content-Type": "text/plain; charset=utf-8",
        }
        success = False
        try:
            req = urllib.request.Request(
                url,
                data=alert.body.encode("utf-8"),
                method="POST",
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                success = resp.status < 400
        except Exception as exc:
            logger.warning("ntfy send failed for %s: %s", alert.trigger_id, exc)

        sent_at = datetime.now(UTC).isoformat()
        if success:
            state.last_sent[alert.trigger_id] = sent_at
            state.sent_today.append({"trigger_id": alert.trigger_id, "sent_at": sent_at})
            if alert.trigger_id == "T5":
                state.last_t5_ist_date = now_ist.strftime("%Y-%m-%d")
            if alert.trigger_id == "T6":
                state.last_t6_fired_date_ist = now_ist.strftime("%Y-%m-%d")
            if alert.trigger_id == "T4":
                state.last_t4_fired_ist_date = now_ist.strftime("%Y-%m-%d")
            if alert.trigger_id == "T7":
                state.last_t7_fired_ist_date = now_ist.strftime("%Y-%m-%d")
            if alert.trigger_id == "T8_MORNING":
                state.last_t8_morning_ist_date = now_ist.strftime("%Y-%m-%d")
            if alert.trigger_id == "T8_EVENING":
                state.last_t8_evening_ist_date = now_ist.strftime("%Y-%m-%d")
            if alert.trigger_id == "T9":
                state.last_t9_ist_date = now_ist.strftime("%Y-%m-%d")
            if alert.trigger_id == "T9_ESCALATE":
                state.last_t9_escalate_ist_date = now_ist.strftime("%Y-%m-%d")
            if alert.trigger_id == "T10":
                state.last_t10_ist_date = now_ist.strftime("%Y-%m-%d")
            if alert.trigger_id == "T11":
                state.last_t11_ist_date = now_ist.strftime("%Y-%m-%d")
            if alert.trigger_id == "T12":
                state.last_t12_ist_date = now_ist.strftime("%Y-%m-%d")
            if alert.trigger_id == "T13":
                state.last_t13_ist_date = now_ist.strftime("%Y-%m-%d")
            logger.info("Sent %s: %s", alert.trigger_id, alert.title)
        else:
            logger.warning("Failed to send %s", alert.trigger_id)

        sent.append(
            SentAlert(
                trigger_id=alert.trigger_id,
                sent_at=sent_at,
                title=alert.title,
                success=success,
            )
        )

    return sent


def _stamp_ist_dedup(trigger_id: str, state: NotificationState, now_ist: datetime) -> None:
    """Set the IST-date dedup stamp for trigger_id when it is queued.

    Mirrors the stamp logic in send_pending but fires at queue time, not send
    time.  Without this, a trigger queued during quiet hours is never stamped,
    so every subsequent quiet-hours CI run sees an un-stamped state and adds
    another copy to the queue — producing N identical notifications when the
    queue is finally released.

    Called from main() immediately after queue_for_quiet_hours.  send_pending
    will overwrite the stamp with the actual send time-date, so the cadence
    gate is always measured from delivery, not from queuing.
    """
    today = now_ist.strftime("%Y-%m-%d")
    if trigger_id == "T7":
        state.last_t7_fired_ist_date = today
    elif trigger_id == "T4":
        state.last_t4_fired_ist_date = today
    elif trigger_id == "T5":
        state.last_t5_ist_date = today
    elif trigger_id == "T8_MORNING":
        state.last_t8_morning_ist_date = today
    elif trigger_id == "T8_EVENING":
        state.last_t8_evening_ist_date = today
    # T6 uses last_t6_fired_date_ist — fires once-ever, bypass_quiet=False.
    # Include for completeness; in practice T6 can only fire once.
    elif trigger_id == "T6":
        state.last_t6_fired_date_ist = today
    elif trigger_id == "T9":
        state.last_t9_ist_date = today
    elif trigger_id == "T10":
        state.last_t10_ist_date = today
    elif trigger_id == "T11":
        state.last_t11_ist_date = today
    elif trigger_id == "T12":
        state.last_t12_ist_date = today
    elif trigger_id == "T13":
        state.last_t13_ist_date = today


def queue_for_quiet_hours(
    alerts: list[PendingAlert],
    state: NotificationState,
) -> NotificationState:
    """Add non-bypass alerts to the quiet-hours queue in state (mutates state)."""
    for alert in alerts:
        if not alert.bypass_quiet:
            state.queued.append(alert.to_dict())
            logger.info("Queued %s for after quiet hours", alert.trigger_id)
    return state


def _release_queued(state: NotificationState) -> list[PendingAlert]:
    """Return non-expired queued alerts (deduped by trigger_id) and clear state.queued.

    Dedup is trigger-agnostic: a trigger that was queued more than once during a
    quiet window is released at most once, keeping the most-recently-queued copy
    (freshest price/forecast data). This is the backstop for triggers that carry
    no queue-time IST-date stamp (T1/T2/T3 gate on send-time cooldown/cap, so they
    re-queue every quiet-hours run); any future trigger inherits the protection
    without per-trigger handling. IST-date-stamped triggers (T4-T9) are already
    queued at most once, so dedup is a no-op for them.
    """
    cutoff = (datetime.now(UTC) - timedelta(hours=_MAX_QUEUE_AGE_H)).isoformat()
    # trigger_id -> (queued_at_utc_iso, alert); keep the most-recent copy per trigger.
    kept: dict[str, tuple[str, PendingAlert]] = {}
    for d in state.queued:
        try:
            alert = PendingAlert.from_dict(d)
            # queued_at has IST timezone info; compare via isoformat string ordering
            # (both ISO8601 with timezone offset, so lexicographic >= is valid only
            # when both have the same offset — convert to UTC instead)
            qat = datetime.fromisoformat(alert.queued_at)
            if qat.tzinfo is None:
                qat = qat.replace(tzinfo=IST)
            qat_utc_iso = qat.astimezone(UTC).isoformat()
            if qat_utc_iso < cutoff:
                logger.debug("Discarding expired queued %s", alert.trigger_id)
                continue
            existing = kept.get(alert.trigger_id)
            if existing is None or qat_utc_iso >= existing[0]:
                if existing is not None:
                    logger.info(
                        "Collapsing duplicate queued %s — keeping most recent", alert.trigger_id
                    )
                kept[alert.trigger_id] = (qat_utc_iso, alert)
        except Exception as exc:
            logger.debug("Skipping malformed queued entry: %s", exc)
    state.queued = []
    return [alert for _, alert in kept.values()]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Gold rate notification system (T1-T13)")
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Evaluate triggers and print results without sending or saving state",
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        default=STATE_PATH,
        help="Path to notification_state.json (default: data/notification_state.json)",
    )
    args = parser.parse_args()

    def _load(path: Path, default):
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        logger.warning("%s not found — using default", path.name)
        return default

    forecast = _load(FORECAST_JSON, {})
    probe = _load(PROBE_JSON, {"status": "missing"})
    prices = _load(PRICES_JSON, [])
    backtest = _load(BACKTEST_JSON, {})
    calibration = _load(CALIBRATION_JSON, {}) or {}
    state = load_state(args.state_path)
    now_ist = datetime.now(IST)

    # Prune stale entries before trigger evaluation
    _prune_sent_today(state)

    snapshot_gap_days = compute_snapshot_gap_days(now_ist)
    usable_snapshot_gap_days = compute_usable_snapshot_gap_days(now_ist)
    ibja_gap_days = compute_ibja_gap_business_days(now_ist)
    selfhosted_consecutive_failures = compute_selfhosted_consecutive_failures()

    new_alerts = check_triggers(
        forecast,
        probe,
        prices,
        backtest,
        state,
        now_ist,
        calibration=calibration,
        snapshot_gap_days=snapshot_gap_days,
        ibja_gap_days=ibja_gap_days,
        selfhosted_consecutive_failures=selfhosted_consecutive_failures,
        usable_snapshot_gap_days=usable_snapshot_gap_days,
    )

    if args.simulate:
        in_quiet = _is_quiet_hours(now_ist)
        dir_acc = compute_dir_acc_30f(backtest)
        lean_dir, lean_str = compute_chronos_lean(probe)
        mom_dir, mom_pct = compute_recent_momentum(prices)
        print("\n=== Notification Simulation ===")
        print(f"Now IST:      {now_ist.strftime('%Y-%m-%d %H:%M')} (quiet hours: {in_quiet})")
        print(f"Dir acc 30f:  {dir_acc:.3f}  (gate: >= 0.55)")
        print(f"Chronos lean: {lean_dir} ({lean_str:.2f}%)")
        print(f"Momentum 7d:  {mom_dir} ({mom_pct:+.2f}%)")
        n_folds = backtest.get("n_folds", 0)
        print(f"n_folds:      {n_folds}  (T1/T2 gate: >= 30)")
        gap_str = "n/a" if snapshot_gap_days is None else f"{snapshot_gap_days}d"
        print(f"Snapshot gap: {gap_str}  (T10 gate: >= {_T10_GAP_THRESHOLD_DAYS}d)")
        usable_gap_str = (
            "n/a" if usable_snapshot_gap_days is None else f"{usable_snapshot_gap_days}d"
        )
        print(f"Usable gap:   {usable_gap_str}  (T13 gate: >= {_T13_GAP_THRESHOLD_DAYS}d)")
        ibja_gap_str = "n/a" if ibja_gap_days is None else f"{ibja_gap_days}bd"
        print(
            f"IBJA gap:     {ibja_gap_str}  "
            f"(T9 gate: >= {_T9_IBJA_GAP_THRESHOLD_DAYS}bd, "
            f"T9_ESCALATE gate: >= {_T9_ESCALATE_IBJA_GAP_THRESHOLD_DAYS}bd)"
        )
        selfhosted_str = (
            "n/a"
            if selfhosted_consecutive_failures is None
            else f"{selfhosted_consecutive_failures}x"
        )
        print(
            f"Selfhosted:   {selfhosted_str} consecutive failures  "
            f"(T12 gate: >= {_T12_CONSECUTIVE_FAILURE_THRESHOLD}x)"
        )
        print(f"\nTriggers fired ({len(new_alerts)}):")
        if new_alerts:
            for a in new_alerts:
                print(f"  [{a.trigger_id}] priority={a.priority}  bypass_quiet={a.bypass_quiet}")
                print(f"    Title: {a.title}")
                print(f"    Body:  {a.body[:100]}...")
        else:
            print("  (none)")
        print(f"\nQueued (waiting for quiet hours to end): {len(state.queued)}")
        return

    in_quiet = _is_quiet_hours(now_ist)
    to_send: list[PendingAlert] = []

    if not in_quiet:
        # Release any alerts held over from quiet hours
        released = _release_queued(state)
        if released:
            logger.info("Releasing %d alert(s) from quiet-hours queue", len(released))
        to_send.extend(released)

    for alert in new_alerts:
        if in_quiet and not alert.bypass_quiet:
            state = queue_for_quiet_hours([alert], state)
            # Stamp the IST-date dedup immediately on queue so the next CI run
            # during quiet hours does not regenerate and re-queue the same alert.
            # send_pending will overwrite the stamp on actual delivery.
            _stamp_ist_dedup(alert.trigger_id, state, now_ist)
        else:
            to_send.append(alert)

    if to_send:
        sent = send_pending(to_send, state, now_ist)
        for s in sent:
            status = "OK" if s.success else "FAILED"
            logger.info("[%s] %s: %s", status, s.trigger_id, s.title)
    else:
        logger.info("No alerts to send this cycle.")

    save_state(state, args.state_path)


if __name__ == "__main__":
    main()
