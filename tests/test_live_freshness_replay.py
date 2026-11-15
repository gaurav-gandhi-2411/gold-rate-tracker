"""Replay of the real ci-health job, 2026-11-07..14: the old paging against the one-page-per-incident one.

tests/fixtures/ci_health_replay_2026-11.json holds 46 real runs: when each ran, the timestamps the
live site showed (forecast, latest Tanishq reading), the share-card finding kinds, and the titles of
the pages that were actually sent (numbers replaced by N; prices stripped). GG was paged 18 times for
what were 3 outages (2 of stale data, 1 of a paused model forecast the old monitor could not see).

`_old_decide` below is the live_freshness.decide of origin/master before this change, copied so the
baseline stays measurable. The old share-card decide is unchanged apart from a new keyword that
defaults to the old behaviour, so the replay calls it directly.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import live_freshness as lf
import live_share_card as sc

FIXTURE = ROOT / "tests" / "fixtures" / "ci_health_replay_2026-11.json"
RUNS: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))["runs"]
SHARE_CARD = "Gold Tracker: the share card"


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # The replay never sends; CI-inherited variables are cleared so nothing can change that.
    for name in ("GITHUB_ACTIONS", "GITHUB_EVENT_NAME", "NTFY_TOPIC"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        lf, "send", lambda msg, topic: (_ for _ in ()).throw(AssertionError("sent to ntfy"))
    )
    monkeypatch.setattr(
        sc, "send", lambda msg, topic: (_ for _ in ()).throw(AssertionError("sent to ntfy"))
    )


def _old_decide(
    state: dict[str, Any], values: dict[str, str | None], now: datetime
) -> tuple[list[dict[str, str]], dict[str, Any], bool]:
    """live_freshness.decide as of origin/master b187caea: one incident per signal, each with its own
    open page and its own recovery page, keyed on the stale value itself."""
    new = {k: v for k, v in state.items() if k in (*lf.LIMITS_H, "site")}
    msgs: list[dict[str, str]] = []
    healthy = True
    future = [
        k
        for k, v in values.items()
        if v is not None and lf.parse_ts(v) > now + lf.FUTURE_TOL  # type: ignore[operator]
    ]
    values = {k: None if k in future else v for k, v in values.items()}
    unreadable = [k for k, v in values.items() if v is None]
    if unreadable:
        healthy = False
        if not new.get("site"):
            new["site"] = now.isoformat()
            msgs.append({"title": "Gold Tracker: cannot read the live site's data"})
    elif new.get("site"):
        new.pop("site")
        msgs.append({"title": "Gold Tracker: live site data readable again"})
    for key, limit in lf.LIMITS_H.items():
        value = values.get(key)
        if value is None:
            continue
        age_h = (now - lf.parse_ts(value)).total_seconds() / 3600  # type: ignore[operator]
        if age_h > limit:
            healthy = False
            if new.get(key) != value:
                new[key] = value
                msgs.append(
                    {
                        "title": f"Gold Tracker: visitors see stale data -- {key} is {age_h:.1f} h old"
                    }
                )
        elif new.get(key):
            new.pop(key)
            msgs.append({"title": f"Gold Tracker: {key} fresh again"})
    return msgs, new, healthy


def _norm(title: str) -> str:
    return re.sub(r"\d+(\.\d+)?", "N", title).strip()


def _t(run: dict[str, Any]) -> datetime:
    t = lf.parse_ts(run["now"])
    assert t is not None
    return t


def _values(run: dict[str, Any]) -> dict[str, str | None]:
    return {"forecast": run["forecast"], "tanishq": run["tanishq"]}


def _model(run: dict[str, Any]) -> tuple[bool, str | None] | None:
    """What model_pause() would read from the live forecast.json at that run, given that
    ml/inference.py now writes next_fix.down_since."""
    if run["model_active"] is None:
        return None
    return (False, None) if run["model_active"] else (True, run["model_down_since"])


def _replay_old() -> list[list[str]]:
    """Titles pushed per run by the old monitors (freshness + share card), state carried."""
    fresh_state: dict[str, Any] = {}
    card_state: dict[str, Any] = {}
    out = []
    for run in RUNS:
        now = _t(run)
        msgs, fresh_state, _ = _old_decide(fresh_state, _values(run), now)
        found = {k: "x" for k in run["card_kinds"]}
        card_msgs, card_state = sc.decide(card_state, found, now)
        out.append([_norm(m["title"]) for m in [*msgs, *card_msgs]])
    return out


def _replay_new(tmp_path: Path) -> list[list[str]]:
    """Titles pushed per run by the new monitors, in ci-health's order: freshness first, then the
    share card reading the freshness state file the freshness step just wrote."""
    state: dict[str, Any] = {}
    card_state: dict[str, Any] = {}
    path = tmp_path / "freshness.json"
    out = []
    for run in RUNS:
        now = _t(run)
        msgs, state, _ = lf.decide(state, _values(run), now, _model(run))
        path.write_text(json.dumps(state), encoding="utf-8")
        found = {k: "x" for k in run["card_kinds"]}
        card_msgs, card_state = sc.decide(
            card_state, found, now, site_incident=sc.site_incident_open(path)
        )
        out.append([_norm(m["title"]) for m in [*msgs, *card_msgs]])
    return out


def _stale(run: dict[str, Any]) -> bool:
    """Plain, independent classification of one observation (no decide() involved): a limit passed."""
    now = _t(run)
    age = lambda s: (now - lf.parse_ts(s)).total_seconds() / 3600  # noqa: E731
    if (
        age(run["forecast"]) > lf.LIMITS_H["forecast"]
        or age(run["tanishq"]) > lf.LIMITS_H["tanishq"]
    ):
        return True
    return run["model_active"] is False and age(run["model_down_since"]) > lf.MODEL_PAUSED_H


def _incidents() -> list[tuple[int, int]]:
    """(first stale run, last run of the incident) from the data alone. Stale observations belong
    to one incident until RECOVERY_CHECKS fresh ones in a row span RECOVERY_H hours: the same
    hysteresis as the monitor, applied to the classification above."""
    out: list[tuple[int, int]] = []
    start = fresh_run = None
    fresh = 0
    for i, run in enumerate(RUNS):
        if _stale(run):
            if start is None:
                start = i
            fresh, fresh_run = 0, None
        elif start is not None:
            fresh += 1
            fresh_run = i if fresh_run is None else fresh_run
            span = (_t(run) - _t(RUNS[fresh_run])).total_seconds() / 3600
            if fresh >= lf.RECOVERY_CHECKS and span >= lf.RECOVERY_H:
                out.append((start, i))
                start = fresh_run = None
                fresh = 0
    if start is not None:
        out.append((start, len(RUNS) - 1))
    return out


def test_the_fixture_is_46_real_runs_without_prices() -> None:
    assert len(RUNS) == 46
    assert "₹" not in FIXTURE.read_text(encoding="utf-8")
    assert not any(re.search(r"\d", t) for r in RUNS for t in r["pages"])  # numbers are N
    assert all(set(r["card_kinds"]) <= set(sc.KINDS) for r in RUNS)  # kinds only, no card text


def test_old_logic_reproduces_the_real_pages_and_there_were_18() -> None:
    replayed = _replay_old()
    real = [sorted(r["pages"]) for r in RUNS]
    assert [sorted(p) for p in replayed] == real  # every run, title for title
    pushes = [t for run in real for t in run]
    card = [t for t in pushes if t.startswith(SHARE_CARD)]
    freshness = [t for t in pushes if not t.startswith(SHARE_CARD)]
    # separated by title prefix: the share-card monitor titles all begin "Gold Tracker: the share
    # card", the freshness monitor's are "visitors see stale data", "<signal> fresh again", "cannot
    # read the live site" and "live site data readable again"
    assert (len(freshness), len(card), len(pushes)) == (10, 8, 18)
    assert sum(1 for run in real if run) == 10  # runs that sent at least one push


def test_incidents_found_in_the_data() -> None:
    inc = _incidents()
    assert len(inc) == 3, inc
    # two stale-data incidents (11-08..10, 11-10..12) and the paused-model one (11-13)
    assert [RUNS[a]["now"][:10] for a, _ in inc] == ["2026-11-08", "2026-11-10", "2026-11-13"]


def test_new_logic_pages_once_per_incident_plus_one_recovery(tmp_path: Path) -> None:
    new = _replay_new(tmp_path)
    old = _replay_old()
    incidents = _incidents()
    opens = [i for i, run in enumerate(new) if any("visitors see stale data" in t for t in run)]
    recov = [i for i, run in enumerate(new) if any("is fresh again" in t for t in run)]
    assert not any(t.startswith(SHARE_CARD) for run in new for t in run)  # all consequences
    again = [i for i, run in enumerate(new) if any("still stale" in t for t in run)]
    assert sum(len(run) for run in new) == 2 * len(incidents) + len(again) == 8
    per_incident = []
    for a, b in incidents:
        o = [i for i in opens if a <= i <= b]
        r = [i for i in recov if a <= i <= b]
        s = [i for i in again if a <= i <= b]
        assert len(o) == 1 and len(r) == 1, (a, b, o, r)  # still ONE open and ONE recovery
        assert o[0] >= a and r[0] == b  # opens inside the incident, recovers when it closes
        # the 24 h re-page: at most one, and only once the incident is 24 h past its last page
        assert all((_t(RUNS[i]) - _t(RUNS[o[0]])).total_seconds() / 3600 >= lf.REPAGE_H for i in s)
        per_incident.append((sum(len(x) for x in old[a : b + 1]), 2 + len(s)))
    # old pushes vs new pushes per real incident (the old monitor never saw the paused model: 0).
    # The two long incidents (53.6 h and 39.6 h from first stale run to recovery run) each get one
    # "still stale" page after 24 h; the 21.8 h one does not.
    assert per_incident == [(6, 3), (12, 3), (0, 2)]
    assert sum(len(x) for x in old) == 18


def test_a_run_that_changes_nothing_sends_nothing_new(tmp_path: Path) -> None:
    new = _replay_new(tmp_path)
    assert sum(1 for run in new if run) == 8  # 8 of 46 runs push, against 10 before


def test_the_hysteresis_numbers_are_what_stops_the_flapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a single fresh check and no wait the same replay pages more: the 11-11 22:44Z all-fresh
    run was followed by a stale one at 03:23Z."""
    assert sum(len(x) for x in _replay_new(tmp_path)) == 8
    monkeypatch.setattr(lf, "RECOVERY_CHECKS", 1)
    monkeypatch.setattr(lf, "RECOVERY_H", 0.0)
    assert sum(len(x) for x in _replay_new(tmp_path)) > 8


def test_check_spacing_in_the_replay_is_why_the_floor_is_an_hour() -> None:
    times = [_t(r) for r in RUNS]
    gaps = sorted(b - a for a, b in pairwise(times))
    assert gaps[0] == timedelta(seconds=19)  # two dispatches on 11-12 09:27Z
    assert any(timedelta(minutes=27) < g < timedelta(minutes=29) for g in gaps)  # 11-12 03:23/03:51
    assert gaps[-1] < timedelta(hours=11)
