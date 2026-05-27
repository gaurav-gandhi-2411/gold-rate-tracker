"""Notification system for gold-rate-tracker.

Evaluates five triggers (T1–T5) against current data files and dispatches
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

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

FORECAST_JSON = DATA_DIR / "forecast.json"
PROBE_JSON = DATA_DIR / "chronos_probe.json"
PRICES_JSON = DATA_DIR / "prices.json"
BACKTEST_JSON = DATA_DIR / "backtest.json"
CALIBRATION_JSON = DATA_DIR / "calibration.json"
STATE_PATH = DATA_DIR / "notification_state.json"

IST = ZoneInfo("Asia/Kolkata")
_NTFY_BASE = "https://ntfy.sh"
_CLICK_URL = "https://gaurav-gandhi-2411.github.io/gold-rate-tracker/"
_QUIET_START_H = 22  # 22:00 IST
_QUIET_END_H = 7  # 07:00 IST
_MAX_QUEUE_AGE_H = 12  # discard queued alerts older than this
_MAX_T123_PER_24H = 3  # T1+T2+T3 combined cap

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
    """T1 — Predicted 5d drop: Chronos leans down AND 7d momentum down."""
    if _in_cooldown("T1", state, 24.0):
        return None
    if _count_sent(state, ["T1", "T2", "T3"]) >= _MAX_T123_PER_24H:
        return None
    if probe.get("status") != "success":
        return None
    # Gate on ≥30 backtest folds, not on forecast.json's warmup flag — the
    # warmup field is written by the legacy LightGBM path and will not exist
    # post-PR-H. n_folds is written by ml/backtest.py, independent of the
    # inference path.
    if backtest.get("n_folds", 0) < 30:
        return None
    _direction, strength = compute_chronos_lean(probe)
    majority_dir = probe.get("majority_direction")
    consensus = probe.get("direction_consensus", 0.0)
    # Phi4: gate on multi-sample majority consensus (>= 0.6) instead of single-sample direction.
    # strength check stays — it's an independent magnitude threshold.
    if majority_dir != "down" or consensus < 0.6 or strength < 0.5:
        return None
    mom_dir, mom_pct = compute_recent_momentum(prices)
    if mom_dir != "down":
        return None
    if compute_dir_acc_30f(backtest) < 0.55:
        return None
    current = prices[-1]["22k"] if prices else 0
    title = "Gold: Model and momentum both lean DOWN over next 5d"
    body = (
        f"22K spot Rs.{current}. "
        f"Last 7d trend: {mom_pct:+.1f}%. "
        f"Chronos lean: {strength:.1f}% over 5d. "
        f"Consensus: {consensus:.0%}. "
        "This is a directional signal, not a price forecast. "
        "Check the dashboard for full context."
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
    """T2 — Predicted 5d rise: Chronos leans up AND 7d momentum up."""
    if _in_cooldown("T2", state, 24.0):
        return None
    if _count_sent(state, ["T1", "T2", "T3"]) >= _MAX_T123_PER_24H:
        return None
    if probe.get("status") != "success":
        return None
    if backtest.get("n_folds", 0) < 30:
        return None
    _direction, strength = compute_chronos_lean(probe)
    majority_dir = probe.get("majority_direction")
    consensus = probe.get("direction_consensus", 0.0)
    # Phi4: gate on multi-sample majority consensus (>= 0.6) instead of single-sample direction.
    # strength check stays — it's an independent magnitude threshold.
    if majority_dir != "up" or consensus < 0.6 or strength < 0.5:
        return None
    mom_dir, mom_pct = compute_recent_momentum(prices)
    if mom_dir != "up":
        return None
    if compute_dir_acc_30f(backtest) < 0.55:
        return None
    current = prices[-1]["22k"] if prices else 0
    title = "Gold: Model and momentum both lean UP over next 5d"
    body = (
        f"22K spot Rs.{current}. "
        f"Last 7d trend: {mom_pct:+.1f}%. "
        f"Chronos lean: {strength:.1f}% over 5d. "
        f"Consensus: {consensus:.0%}. "
        "This is a directional signal, not a price forecast. "
        "Check the dashboard for full context."
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
    body = (
        f"22K moved from Rs.{prev} to Rs.{current} ({pct:+.1f}%). "
        "Model-agnostic price alert. Check the dashboard for context."
    )
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
    """T4 — Weekly digest: Sunday 18:00 IST ±30 min. Bypasses quiet hours."""
    if _in_cooldown("T4", state, 168.0):
        return None
    if now_ist.weekday() != 6:  # Sunday
        return None
    minutes_from_1800 = (now_ist.hour - 18) * 60 + now_ist.minute
    if not (-30 <= minutes_from_1800 <= 30):
        return None
    current = prices[-1]["22k"] if prices else 0
    n_folds = backtest.get("n_folds", 0)
    mae_c = backtest.get("mae_5d_avg_chronos", 0.0)
    mae_n = backtest.get("mae_5d_avg_naive", 0.0)
    if mae_n > 0 and mae_c <= mae_n:
        verdict = "Model beating naive"
    elif mae_n > 0:
        verdict = f"Model {(mae_c - mae_n) / mae_n * 100:.0f}% above naive MAE"
    else:
        verdict = "Backtest pending"
    # Use any available commentary as enrichment
    extra = ""
    commentary_path = DATA_DIR / "commentary.json"
    if commentary_path.exists():
        try:
            c = json.loads(commentary_path.read_text())
            snippet = str(c.get("commentary", ""))[:180].strip()
            if snippet:
                extra = " " + snippet
        except Exception:
            pass
    title = f"Gold Weekly: {verdict} (22K: Rs.{current})"
    body = (
        f"22K spot: Rs.{current}. "
        f"Backtest ({n_folds} folds): Chronos MAE Rs.{mae_c:.0f} vs Naive Rs.{mae_n:.0f}."
        + (extra or " See dashboard for full context.")
    )
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
        title = "Gold forecast: inference path failed"
        body = (
            "Chronos probe failed and model_fallback is set. "
            "Forecast may be stale. Check CI logs for root cause."
        )
    else:
        title = "Gold forecast: Chronos probe failed"
        body = (
            f"Chronos probe status: {probe.get('status', 'unknown')}. "
            "Directional signal unavailable. Check CI logs."
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
) -> list[PendingAlert]:
    """Evaluate all triggers (T1–T6); return new alerts for this call.

    Cooldowns and combined caps are enforced here.  Quiet-hours queuing and
    delivery of previously queued alerts is the caller's responsibility (see
    queue_for_quiet_hours and the main() orchestration).

    calibration: optional dict from calibration.json. When provided and valid,
        triggers T6 (calibration-unlocked, fires once ever).
    """
    alerts: list[PendingAlert] = []
    for fn in (_check_t1, _check_t2, _check_t3, _check_t4, _check_t5):
        alert = fn(forecast, probe, prices, backtest, state, now_ist)
        if alert is not None:
            alerts.append(alert)
    t6 = _check_t6(calibration, state, now_ist)
    if t6 is not None:
        alerts.append(t6)
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
    """Return non-expired queued alerts and clear state.queued."""
    cutoff = (datetime.now(UTC) - timedelta(hours=_MAX_QUEUE_AGE_H)).isoformat()
    ready: list[PendingAlert] = []
    for d in state.queued:
        try:
            alert = PendingAlert.from_dict(d)
            # queued_at has IST timezone info; compare via isoformat string ordering
            # (both ISO8601 with timezone offset, so lexicographic >= is valid only
            # when both have the same offset — convert to UTC instead)
            qat = datetime.fromisoformat(alert.queued_at)
            if qat.tzinfo is None:
                qat = qat.replace(tzinfo=IST)
            if qat.astimezone(UTC).isoformat() >= cutoff:
                ready.append(alert)
            else:
                logger.debug("Discarding expired queued %s", alert.trigger_id)
        except Exception as exc:
            logger.debug("Skipping malformed queued entry: %s", exc)
    state.queued = []
    return ready


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Gold rate notification system (T1-T5)")
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

    new_alerts = check_triggers(
        forecast, probe, prices, backtest, state, now_ist, calibration=calibration
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
