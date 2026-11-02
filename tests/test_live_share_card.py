"""scripts/live_share_card.py: page when the published share card is broken, wrong, advises or is old."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import live_share_card as sc

NOW = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)
TANISHQ_URL = "https://www.tanishq.co.in/gold-rate.html?lang=en_IN"


def _iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")


def _data(
    row_h: float = 2.5, forecast_h: float = 2.3
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The 2026-11-02 shape: Tanishq rows, forecast built from the newest one."""
    prices = [
        {"timestamp": _iso(row_h + 13.5), "22k": 12780, "source": TANISHQ_URL},
        {"timestamp": _iso(row_h), "22k": 12825, "source": TANISHQ_URL},
    ]
    forecast = {
        "predicted_at": _iso(forecast_h),
        "scraped_at": _iso(row_h),
        "current_22k": 12825,
        "price_source": "tanishq_scrape",
    }
    return prices, forecast


def _card(**over: Any) -> dict[str, Any]:
    card = {
        "price": 12825,
        "price_text": "12,825",
        "approx": False,
        "kind": "tanishq",
        "label": "Tanishq's listed 22K rate",
        "as_of": _iso(2.5),
        "verdict": "down",
        "verdict_text": "Getting cheaper this week",
        "card_text": "Gold Tracker Is it a good price? ₹12,825 Tanishq's listed 22K rate · per gram "
        "↓ Getting cheaper this week 7-day trend Price as of 2 Nov, 20:53 IST "
        "57 readings since Apr 2026 Not financial advice · Rates indicative",
        "built_at": _iso(2.3),
    }
    card.update(over)
    return card


def test_a_good_card_is_quiet() -> None:
    prices, forecast = _data()
    assert sc.findings(_card(), prices, forecast, NOW) == {}
    msgs, state = sc.decide({}, {}, NOW)
    assert msgs == [] and state == {}


def test_the_real_published_files_judge_cleanly_against_a_card_built_from_them() -> None:
    """The committed data, judged with a card as og.html would draw it from that data."""
    prices = json.loads((ROOT / "data/prices.json").read_text(encoding="utf-8"))
    forecast = json.loads((ROOT / "data/forecast.json").read_text(encoding="utf-8"))
    pts = sc._points(prices, forecast)
    ts, value = max(pts["tanishq"])
    card = _card(price=value, price_text=f"{value:,.0f}", as_of=ts.isoformat())
    found = sc.findings(card, prices, forecast, ts + timedelta(hours=1))
    assert found == {}, found


def test_the_2026_05_card_pages_broken_and_advice() -> None:
    card, prices, forecast = sc.simulated("bad", NOW)
    found = sc.findings(card, prices, forecast, NOW)
    assert set(found) == {"broken", "advice"}
    assert "NaN" in found["broken"] and "buy" in found["advice"]


@pytest.mark.parametrize(
    ("over", "kind", "needle"),
    [
        ({"price": float("nan")}, "broken", "not a number"),
        ({"price": "12825"}, "broken", "not a number"),
        ({"price_text": "—"}, "broken", "missing price_text"),
        ({"verdict_text": ""}, "broken", "missing verdict_text"),
        ({"card_text": "₹undefined"}, "broken", "'undefined'"),
        ({"as_of": "yesterday"}, "broken", "not a time"),
        ({"kind": "guess"}, "broken", "kind 'guess'"),
        ({"built_at": _iso(-2)}, "broken", "in the future"),
        ({"verdict_text": "Good time to buy"}, "advice", "Good time to"),
        ({"card_text": "Prices falling: wait for a dip"}, "advice", "wait for"),
        ({"card_text": "Consider selling now"}, "advice", "selling"),
        ({"price": 12780, "price_text": "12,780"}, "disagree", "published value then is ₹12,825"),
        ({"as_of": _iso(1)}, "disagree", "does not have"),
    ],
)
def test_each_bad_card_pages_its_kind(over: dict[str, Any], kind: str, needle: str) -> None:
    prices, forecast = _data()
    found = sc.findings(_card(**over), prices, forecast, NOW)
    assert kind in found, found
    assert needle in found[kind]


def test_the_footer_and_eyebrow_are_not_advice() -> None:
    assert sc.ADVICE.search("Is it a good price? Not financial advice · Rates indicative") is None


def test_behind_waits_for_the_measured_refresh_lag() -> None:
    prices, forecast = _data()
    old = _card(price=12780, price_text="12,780", as_of=_iso(16))
    # the newer row is 2.5 h old: within the lag, the card is simply waiting for its rebuild
    assert "behind" not in sc.findings(old, prices, forecast, NOW)
    later = NOW + timedelta(hours=7)
    found = sc.findings(old, prices, forecast, later)
    assert "behind" in found and "9.5 h" in found["behind"]


def test_a_tanishq_card_is_not_behind_a_newer_forecast_run() -> None:
    # check-price reruns the forecast every 3 h with no new reading: the card rightly keeps the row
    prices, forecast = _data(row_h=6, forecast_h=0.5)
    card = _card(as_of=_iso(6))
    assert sc.findings(card, prices, forecast, NOW + timedelta(hours=8)) == {}


def test_an_estimate_card_is_behind_a_newer_real_reading() -> None:
    prices, forecast = _data(row_h=10.5, forecast_h=12)
    est = _card(kind="estimate", approx=True, price_text="≈ 12,825", as_of=_iso(12))
    found = sc.findings(est, prices, forecast, NOW)
    assert "behind" in found and "stale" in found  # 12 h > the 9 h estimate limit


def test_an_estimate_card_ignores_a_reading_the_page_rejects() -> None:
    # a Tanishq row more than 12% off the estimate never reaches the hero (the page's plausibility
    # gate), so the card that keeps showing the estimate is not behind it
    prices, forecast = _data(row_h=10.5, forecast_h=12)
    prices[-1]["22k"] = 15000  # 17% above the estimate
    est = _card(kind="estimate", approx=True, price_text="≈ 12,825", as_of=_iso(12))
    # an estimate card comes from an IBJA-based forecast, which carries no Tanishq scrape time
    forecast.update(price_source="ibja_calibrated", current_22k=12825)
    del forecast["scraped_at"]
    found = sc.findings(est, prices, forecast, NOW)
    assert "behind" not in found, found
    prices[-1]["22k"] = 13500  # 5% above: the page would show it, so the card is behind
    assert "behind" in sc.findings(est, prices, forecast, NOW)


def test_stale_uses_the_freshness_limit_of_the_cards_kind() -> None:
    prices, forecast = _data(row_h=21, forecast_h=1)
    found = sc.findings(_card(as_of=_iso(21)), prices, forecast, NOW)
    assert set(found) == {"stale"} and "limit 20 h for tanishq" in found["stale"]


def test_an_unreadable_file_fails_closed() -> None:
    prices, forecast = _data()
    for card, p, f in ((None, prices, forecast), (_card(), None, forecast), (_card(), prices, "x")):
        found = sc.findings(card, p, f, NOW)
        assert set(found) == {"unreadable"}


def test_pages_once_per_incident_then_recovers_once() -> None:
    found = {"broken": "price is None"}
    msgs, state = sc.decide({}, found, NOW)
    assert len(msgs) == 1 and msgs[0]["priority"] == "high" and "is broken" in msgs[0]["title"]
    msgs2, state2 = sc.decide(state, found, NOW + timedelta(hours=1))
    assert msgs2 == [] and state2 == state
    msgs3, state3 = sc.decide(state2, {}, NOW + timedelta(hours=2))
    assert len(msgs3) == 1 and msgs3[0]["priority"] == "default" and state3 == {}


def test_a_new_kind_during_an_open_incident_pages_on_its_own() -> None:
    _, state = sc.decide({}, {"behind": "x"}, NOW)
    msgs, state2 = sc.decide(state, {"behind": "x", "stale": "y"}, NOW)
    assert [m["title"] for m in msgs] == ["Gold Tracker: the share card is out of date"]
    assert set(state2) == {"behind", "stale"}


def test_simulated_runs_page_bad_with_test_titles_and_stay_quiet_on_good(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert sc.main(["--simulate", "bad", "--dry-run"]) == 1
    out = capsys.readouterr().out
    assert "PAGE: [TEST] Gold Tracker: the share card is broken (NaN" in out
    assert "PAGE: [TEST] Gold Tracker: the share card gives buy/sell advice" in out
    assert sc.main(["--simulate", "good", "--dry-run"]) == 0
    assert "no page: the card is fine" in capsys.readouterr().out


def test_a_failed_send_keeps_the_old_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("NTFY_TOPIC", "t")
    monkeypatch.setattr(sc, "send", lambda msg, topic: False)
    monkeypatch.setattr(sc, "fetch_json", lambda url: None)
    out = tmp_path / "s.json"
    assert sc.main(["--state-out", str(out)]) == 1
    assert json.loads(out.read_text(encoding="utf-8")) == {}
