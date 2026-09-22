"""Tests for the PUBLIC / OPS notification split (AQ2, 2026-09-21)."""

from __future__ import annotations

import ast
import inspect
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from ml import notifications, public_copy
from ml.notification_routing import (
    KNOWN_OPS,
    OPS,
    PUBLIC,
    PUBLIC_ALLOWLIST,
    audience_for,
    resolve_topic,
)

from tests.test_notification_copy_claims import FORWARD_LOOKING, SIGNAL_CLAIMS

_ROOT = Path(__file__).resolve().parent.parent
_IST = ZoneInfo("Asia/Kolkata")
_NOW = datetime(2026, 9, 21, 18, 30, tzinfo=_IST)
_BOTH = {"NTFY_TOPIC": "ops-topic-aaaa", "NTFY_TOPIC_PUBLIC": "public-topic-bbbb"}


# --- routing rule ---------------------------------------------------------------------------------


def test_the_public_allowlist_is_exactly_the_price_messages():
    """Pinned on purpose: widening it must be a visible edit to this test as well as to the module."""
    assert {"T1", "T2", "T3", "T4", "T8_MORNING", "T8_EVENING"} == PUBLIC_ALLOWLIST


@pytest.mark.parametrize("trigger_id", sorted(KNOWN_OPS))
def test_every_classified_ops_trigger_routes_to_ops(trigger_id: str):
    assert audience_for(trigger_id) == OPS
    assert resolve_topic(trigger_id, _BOTH) == (OPS, "ops-topic-aaaa")


@pytest.mark.parametrize("trigger_id", ["T99", "T_NEW", "", "t1", "T8", "T8_NOON", "PUBLIC", "T1 "])
def test_an_unlisted_or_malformed_id_routes_to_ops_never_public(trigger_id: str):
    assert audience_for(trigger_id) == OPS
    assert resolve_topic(trigger_id, _BOTH) == (OPS, "ops-topic-aaaa")


@pytest.mark.parametrize("trigger_id", sorted(PUBLIC_ALLOWLIST))
def test_allowlisted_ids_use_the_public_topic_when_it_is_set(trigger_id: str):
    assert resolve_topic(trigger_id, _BOTH) == (PUBLIC, "public-topic-bbbb")


def test_public_falls_back_to_ops_when_no_public_topic_is_configured():
    assert resolve_topic("T8_MORNING", {"NTFY_TOPIC": "ops-topic-aaaa"}) == (OPS, "ops-topic-aaaa")
    assert resolve_topic("T8_MORNING", {"NTFY_TOPIC": "ops", "NTFY_TOPIC_PUBLIC": ""}) == (
        OPS,
        "ops",
    )


def test_nothing_configured_resolves_to_no_topic():
    assert resolve_topic("T8_MORNING", {}) == (OPS, "")
    assert resolve_topic("T9", {}) == (OPS, "")


# --- through the real send path -----------------------------------------------------------------


class _Resp:
    status = 200

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        return None


def _send(monkeypatch: pytest.MonkeyPatch, trigger_id: str, env: dict[str, str]) -> list[str]:
    urls: list[str] = []
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    monkeypatch.delenv("NTFY_TOPIC_PUBLIC", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(
        notifications.urllib.request,
        "urlopen",
        lambda req, timeout=0: (urls.append(req.full_url), _Resp())[1],
    )
    alert = notifications._make_alert(trigger_id, "t", "b", 3, ["x"], _NOW)
    notifications.send_pending([alert], notifications.NotificationState(), _NOW)
    return urls


def test_send_pending_posts_a_public_message_to_the_public_topic(monkeypatch: pytest.MonkeyPatch):
    assert _send(monkeypatch, "T8_MORNING", _BOTH) == ["https://ntfy.sh/public-topic-bbbb"]


def test_send_pending_posts_an_UNLISTED_message_to_ops_even_with_both_topics_set(
    monkeypatch: pytest.MonkeyPatch,
):
    """The AQ2b proof: a constructed, unlisted message must not reach the public topic."""
    assert _send(monkeypatch, "T99_UNLISTED", _BOTH) == ["https://ntfy.sh/ops-topic-aaaa"]


def test_send_pending_posts_every_known_ops_message_to_ops(monkeypatch: pytest.MonkeyPatch):
    for tid in sorted(KNOWN_OPS):
        assert _send(monkeypatch, tid, _BOTH) == ["https://ntfy.sh/ops-topic-aaaa"], tid


def test_send_pending_sends_nothing_when_nothing_is_configured(monkeypatch: pytest.MonkeyPatch):
    assert _send(monkeypatch, "T8_MORNING", {}) == []


# --- classification cannot go stale -----------------------------------------------------------------


def _emitted_trigger_ids() -> set[str]:
    tree = ast.parse((_ROOT / "ml" / "notifications.py").read_text(encoding="utf-8"))
    ids = {
        c.args[0].value
        for c in ast.walk(tree)
        if isinstance(c, ast.Call)
        and getattr(c.func, "id", "") == "_make_alert"
        and c.args
        and isinstance(c.args[0], ast.Constant)
    }
    assert len(ids) >= 12, (
        "discovery found too few trigger ids; the sweep is not covering the module"
    )
    return ids


def test_every_trigger_id_the_module_can_emit_is_classified():
    """A new trigger must be classified by a person (it would still default to OPS if not)."""
    unclassified = _emitted_trigger_ids() - PUBLIC_ALLOWLIST - KNOWN_OPS
    assert unclassified == set(), (
        f"classify these in ml/notification_routing.py: {sorted(unclassified)}"
    )


def test_no_stale_or_overlapping_classification():
    emitted = _emitted_trigger_ids()
    assert emitted >= PUBLIC_ALLOWLIST, (
        f"allowlisted but never emitted: {sorted(PUBLIC_ALLOWLIST - emitted)}"
    )
    assert emitted >= KNOWN_OPS, f"classified OPS but never emitted: {sorted(KNOWN_OPS - emitted)}"
    assert not (PUBLIC_ALLOWLIST & KNOWN_OPS)


def test_the_public_secret_is_referenced_only_where_it_is_allowed():
    """No other emitter (workflow step, Worker, script) may be able to reach the public topic."""
    allowed_workflows = {"check-price.yml"}  # the step that runs `python -m ml.notifications`
    offenders: list[str] = []
    for path in (
        list((_ROOT / ".github").rglob("*.yml"))
        + list((_ROOT / "worker-deadman" / "src").glob("*"))
        + list((_ROOT / "scripts").glob("*"))
    ):
        allowed = path.parent.name == "workflows" and path.name in allowed_workflows
        if (
            path.is_file()
            and not allowed
            and "NTFY_TOPIC_PUBLIC" in path.read_text(encoding="utf-8", errors="ignore")
        ):
            offenders.append(str(path.relative_to(_ROOT)))
    for path in (_ROOT / "ml").glob("*.py"):
        if (
            "NTFY_TOPIC_PUBLIC" in path.read_text(encoding="utf-8")
            and path.name != "notification_routing.py"
        ):
            offenders.append(str(path.relative_to(_ROOT)))
    assert offenders == [], (
        f"can reach the public topic without going through the allowlist: {offenders}"
    )


# --- public copy standard -----------------------------------------------------------------------------

_JARGON = re.compile(
    r"\bIBJA\b|\bCI\b|workflow|\brun\b|\bmodel\b|calibrat|\btrigger|\bADR\b|\.py\b|\.yml\b|\.json\b|https?://|"
    r"github|\bbot\b|snapshot|feature|\bstale\b|\bfallback\b|\bT\d",
    re.I,
)


def _public_outputs() -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    out["weekly_trend"] = [
        public_copy.weekly_trend(d, c, p)
        for d in ("up", "down")
        for c in (999, 14215, 100000)
        for p in (0.5, 3.0, 12.34)
    ]
    out["price_move"] = [
        public_copy.price_move(c, p)
        for c, p in ((14350, 14200), (14000, 14200), (999, 1200), (25000, 24000))
    ]
    out["weekly_summary"] = [
        public_copy.weekly_summary(c, delayed=d) for c in (999, 14215) for d in (False, True)
    ]
    out["daily_digest"] = [
        public_copy.daily_digest(s, cur, prior, 25)
        for s in ("morning", "evening")
        for cur, prior in (
            (14215, None),
            (14215, 14200),
            (14215, 14330),
            (14330, 14215),
            (999, 1010),
        )
    ]
    return out


def test_every_public_copy_function_is_covered_by_the_grid():
    """A new public message function cannot ship without being run through the standard below."""
    public_fns = {
        n
        for n, f in inspect.getmembers(public_copy, inspect.isfunction)
        if not n.startswith("_") and n != "rs" and f.__module__ == public_copy.__name__
    }
    assert public_fns == set(_public_outputs()), sorted(public_fns ^ set(_public_outputs()))


@pytest.mark.parametrize("fn", sorted(_public_outputs()))
def test_public_copy_meets_the_standard(fn: str):
    for title, body in _public_outputs()[fn]:
        text = f"{title} | {body}"
        assert text.isascii(), f"non-ASCII (ntfy titles cannot carry it): {text!r}"
        for pattern, why in FORWARD_LOOKING + SIGNAL_CLAIMS:
            assert not re.search(pattern, text, re.I), f"{why}: {text!r}"
        assert not _JARGON.search(text), f"jargon/internal detail in public copy: {text!r}"
        assert not re.search(r"Rs\.\d", text), f"old price format (no space): {text!r}"
        for amount in re.findall(r"Rs\. ([\d,]+)", text):
            n = int(amount.replace(",", ""))
            assert amount == f"{n:,}", f"price not thousands-separated: {text!r}"
        assert 0 < len(title) <= 70 and 0 < len(body) <= 160, f"unreasonable length: {text!r}"


def test_public_copy_describes_the_past_and_uses_the_right_direction():
    assert public_copy.price_move(14000, 14200)[0] == "Gold price down Rs. 200"
    assert public_copy.price_move(14350, 14200)[0] == "Gold price up Rs. 150"
    assert "Down Rs. 115 from yesterday" in public_copy.daily_digest("evening", 14215, 14330, 25)[1]
    assert "About the same as yesterday" in public_copy.daily_digest("morning", 14215, None, 25)[1]
    assert "a day late" in public_copy.weekly_summary(14215, delayed=True)[1]
    assert "a day late" not in public_copy.weekly_summary(14215)[1]
