"""ADR 077: the one look at the beta 0.5 challenger (scripts/check_adr066_promotion.py --adr 077).

Synthetic data only. The look must (1) count and score only entries logged strictly after the
registration instant, (2) compute NO effect of beta 0.5 before its look day, (3) take the look on the first
Monday on or after 2026-12-14 on which a window meets both count gates AND ADR 076's look of that window
has been taken, (4) ignore entries logged on or after that Monday, and (5) compute nothing at all if the
frozen rule no longer hashes to the registered value."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from ml import hourly_beta05 as hb
from ml import nextfix_intraday as ni

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_adr066_promotion_077", _ROOT / "scripts" / "check_adr066_promotion.py"
)
mod = importlib.util.module_from_spec(_spec)
sys.modules["check_adr066_promotion_077"] = mod
_spec.loader.exec_module(mod)

WIN = "after_morning_rate"
REGISTERED = datetime(2026, 10, 18, tzinfo=UTC)
LOOK_077 = datetime(2026, 12, 14, tzinfo=UTC)
OPTS = mod.adr077_opts()


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_entries(
    days: list[datetime], slope: float = 0.5, noise_sd: float = 0.0005, seed: int = 7
) -> list[dict]:
    """One decision per day (AM fix 06:30Z, decision 08:00Z, target = same day's PM fix).
    target = base * exp(slope * x + noise): beta 0.5 is right when slope == 0.5."""
    rng = np.random.default_rng(seed)
    out = []
    for d in days:
        x = float(rng.normal(0, 0.01))
        base = 13000.0
        t = d.replace(hour=8)
        out.append(
            {
                "logged_at": _iso(t),
                "t": _iso(t),
                "base_kind": "am",
                "base_date": d.strftime("%Y-%m-%d"),
                "base_at": _iso(d.replace(hour=6, minute=30)),
                "base": base,
                "window": WIN,
                "target_kind": "pm",
                "x_since_fix": x,
                "pred": {f"beta_{b}": round(base * float(np.exp(b * x)), 2) for b in ni.BETAS},
                "target": round(base * float(np.exp(slope * x + rng.normal(0, noise_sd))), 2),
                "target_date": d.strftime("%Y-%m-%d"),
            }
        )
    return out


def daily(start: datetime, n: int) -> list[datetime]:
    return [start + timedelta(days=i) for i in range(n)]


def book(n_days: int = 60, **kw) -> list[dict]:
    """Daily entries from 2026-10-19 (after both cut-offs): ADR 076's look day is 2026-11-16 and
    ADR 077's gates are met well before 2026-12-14."""
    return mod.clean_entries(make_entries(daily(datetime(2026, 10, 19, tzinfo=UTC), n_days), **kw))


@pytest.fixture
def no_effect(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("an effect of beta 0.5 was computed before its look")

    monkeypatch.setattr(mod, "adr077_effect", boom)
    monkeypatch.setattr(mod, "dm_p_one_sided", boom)


RUN_076 = datetime(2026, 11, 16, 10, tzinfo=UTC)  # a successful ADR 076 look run on its look day


def evaluate(
    entries: list[dict],
    now: datetime,
    folds_ok: bool = True,
    runs_076: tuple[datetime, ...] = (RUN_076,),
) -> dict:
    return mod.evaluate_window_077(WIN, entries, now, OPTS, folds_ok, runs_076)


def test_the_options_are_the_frozen_rule_values_and_the_rule_still_hashes_to_the_adr():
    assert OPTS == {
        "first_entry_after": "2026-10-18T00:00:00Z",
        "min_fixes": 20,
        "min_days": 28,
        "look_not_before": "2026-12-14",
        "alpha": 0.025,
        "pred_key": "beta_0.5",
    }
    assert hb.rule_hash() == hb.RULE_SHA256
    adr = (_ROOT / "docs" / "adr" / "077-hourly-beta-0-5-forward-challenger.md").read_text(
        encoding="utf-8"
    )
    assert hb.RULE_SHA256 in adr


def test_nothing_is_computed_before_2026_12_14_even_with_the_counts_met(no_effect):
    r = evaluate(book(), datetime(2026, 12, 13, 23, 0, tzinfo=UTC))
    assert r["counts_ready"] is True and r["look_day"] is None and "effect" not in r
    assert r["adr076_look_day"] == "2026-11-16"  # ADR 076's look is taken; ADR 077's date is not


def test_no_look_while_adr076_could_not_take_its_look(no_effect):
    r = evaluate(book(), datetime(2026, 12, 21, 12, tzinfo=UTC), folds_ok=False)
    assert r["look_day"] is None and r["adr076_look_day"] is None and "effect" not in r


def test_no_look_without_evidence_that_the_adr076_look_was_run(no_effect):
    # Its look day passed and its file loads, but no ADR 076 look run exists: 077 must not go first.
    r = evaluate(book(), datetime(2026, 12, 21, 12, tzinfo=UTC), runs_076=())
    assert r["look_day"] is None and r["adr076_look_day"] is None and "effect" not in r


def test_an_adr076_run_before_its_look_day_is_not_the_look(no_effect):
    early_run = (datetime(2026, 11, 10, tzinfo=UTC),)  # counts-only run, a week before its look day
    r = evaluate(book(), datetime(2026, 12, 21, 12, tzinfo=UTC), runs_076=early_run)
    assert r["look_day"] is None and "effect" not in r


def test_the_look_opens_on_the_first_monday_after_the_adr076_run_when_that_run_is_late():
    late_run = (datetime(2026, 12, 16, tzinfo=UTC),)  # ADR 076 look run only on 12-16
    r = evaluate(book(), datetime(2026, 12, 22, tzinfo=UTC), runs_076=late_run)
    # 12-14 already met the counts and ADR 076's look day (11-16) precedes it; the run's existence by the
    # time of this run is what is required, so the look day stays the first qualifying Monday.
    assert r["look_day"] == "2026-12-14"


def test_short_counts_on_the_look_monday_compute_nothing(no_effect):
    entries = book(n_days=15)  # 15 fixes, 15 days
    r = evaluate(entries, datetime(2026, 12, 14, 12, tzinfo=UTC))
    assert r["counts_ready"] is False and r["look_day"] is None and "effect" not in r


def test_the_look_is_the_first_monday_on_or_after_2026_12_14_and_a_good_beta05_passes():
    r = evaluate(book(), datetime(2026, 12, 16, tzinfo=UTC))
    assert r["look_day"] == "2026-12-14"
    eff = r["effect"]
    assert eff["a_bootstrap_ci_below_0"]["pass"] and eff["b_dm_one_sided"]["pass"]
    assert eff["all_pass"] and "c_range_coverage" not in eff  # ADR 077 has no coverage condition


def test_a_useless_beta05_fails():
    r = evaluate(book(slope=0.0, noise_sd=0.002), datetime(2026, 12, 16, tzinfo=UTC))
    assert r["look_day"] == "2026-12-14" and r["effect"]["all_pass"] is False


def test_only_entries_logged_strictly_after_the_registration_instant_count():
    before = mod.clean_entries(
        make_entries(daily(datetime(2026, 10, 17, tzinfo=UTC), 1))  # logged 10-17 08:00Z
        + make_entries([REGISTERED])  # logged 10-18 08:00Z: after the instant, counts
    )
    r = evaluate(before, datetime(2026, 10, 20, tzinfo=UTC))
    assert r["counts"]["n_entries"] == 1


def test_an_entry_logged_exactly_at_the_registration_instant_never_counts():
    at = mod.clean_entries(make_entries([REGISTERED]))
    for e in at:
        e["logged_at"] = e["t"] = "2026-10-18T00:00:00Z"
    assert hb.eligible(at) == []
    r = evaluate(at, datetime(2026, 10, 20, tzinfo=UTC))
    assert r["counts"]["n_entries"] == 0 and r["counts"]["n_resolved_base_fixes"] == 0


def test_an_entry_logged_exactly_at_00z_of_the_look_monday_is_not_scored():
    early = book()
    r = evaluate(early, datetime(2026, 12, 16, tzinfo=UTC))
    n_before = r["effect"]["a_bootstrap_ci_below_0"]["n"]
    at_cut = mod.clean_entries(make_entries([datetime(2026, 12, 14, tzinfo=UTC)], seed=11))
    for e in at_cut:
        e["logged_at"] = e["t"] = "2026-12-14T00:00:00Z"
    r2 = evaluate(early + at_cut, datetime(2026, 12, 16, tzinfo=UTC))
    assert r2["effect"]["a_bootstrap_ci_below_0"]["n"] == n_before


def test_a_rerun_after_the_look_ignores_entries_logged_on_or_after_the_look_monday():
    early = book()  # 10-19 .. 12-17
    later = mod.clean_entries(
        make_entries(daily(datetime(2026, 12, 14, tzinfo=UTC), 20), slope=3.0, seed=9)
    )
    a = evaluate(early, datetime(2026, 12, 16, tzinfo=UTC))
    b = evaluate(early + later, datetime(2027, 1, 20, tzinfo=UTC))
    assert a["look_day"] == b["look_day"] == "2026-12-14"
    assert a["effect"] == b["effect"]


def test_an_edited_rule_computes_nothing(monkeypatch, tmp_path, no_effect):
    monkeypatch.setattr(hb, "RULE_SHA256", "0" * 64)
    res = mod.run_adr077(tmp_path, datetime(2026, 12, 16, tzinfo=UTC), tmp_path)
    assert res["refused"] and res["windows"] == {} and res["look_open"] is False


def test_cli_077_before_the_look_writes_counts_only(tmp_path, capsys, no_effect):
    data = tmp_path / "data"
    data.mkdir()
    (data / ni.SHADOW_PATH.name).write_text(json.dumps({"entries": book()}), encoding="utf-8")
    out = tmp_path / "out"
    rc = mod.main(
        [
            "--adr",
            "077",
            "--now",
            "2026-11-30T12:00:00Z",
            "--data-dir",
            str(data),
            "--out-dir",
            str(out),
        ]
    )
    assert rc == 0
    text = capsys.readouterr().out
    assert "ADR 077 look (beta 0.5)" in text and "no effect computed" in text
    written = json.loads(next(out.glob("adr077_look_*.json")).read_text(encoding="utf-8"))
    assert written["look_open"] is False
    assert all("effect" not in w for w in written["windows"].values())


def test_cli_077_cannot_open_the_look_with_a_faked_future_now(tmp_path, capsys, no_effect):
    # Everything a look needs is present for 2027-01-10, but --now can never be later than the real clock.
    data = tmp_path / "data"
    data.mkdir()
    (data / ni.SHADOW_PATH.name).write_text(json.dumps({"entries": book()}), encoding="utf-8")
    runs = tmp_path / "runs.json"
    runs.write_text(json.dumps(["2026-11-16T10:00:00Z"]), encoding="utf-8")
    out = tmp_path / "out"
    argv = ["--adr", "077", "--now", "2027-01-10T12:00:00Z", "--data-dir", str(data)]
    rc = mod.main([*argv, "--adr076-runs", str(runs), "--out-dir", str(out)])
    real_now = datetime.now(UTC)
    if real_now >= LOOK_077:  # after the real look date this test cannot show the clamp
        pytest.skip("the real clock has reached the look date")
    assert rc == 0
    written = json.loads(next(out.glob("adr077_look_*.json")).read_text(encoding="utf-8"))
    assert written["look_open"] is False and written["run_at"] < "2026-12-14"


def test_missing_or_unreadable_adr076_run_list_keeps_the_look_closed(tmp_path):
    assert mod.load_adr076_runs(None) == []
    assert mod.load_adr076_runs(tmp_path / "absent.json") == []
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert mod.load_adr076_runs(bad) == []


def test_cli_077_refuses_the_076_options(capsys):
    with pytest.raises(SystemExit):
        mod.main(["--adr", "077", "--min-fixes", "5"])
    assert "ADR 077 rule is frozen" in capsys.readouterr().err


def test_the_look_workflow_is_manual_read_only_and_runs_the_frozen_defaults():
    t = (_ROOT / ".github" / "workflows" / "adr077-look.yml").read_text(encoding="utf-8")
    assert re.search(r"^on:\s*\n\s+workflow_dispatch:\s*$", t, re.M)
    assert "schedule:" not in t and "push:" not in t and "pull_request" not in t
    assert re.search(r"^permissions:\s*\n\s+contents: read\s*\n\s+actions: read", t, re.M)
    for forbidden in ("upload-artifact", "git push", "git commit", "gh pr", "write"):
        assert forbidden not in t, forbidden
    assert "actions/workflows/adr076-look.yml/runs?status=success" in t
    cmd = re.search(r"run: (python scripts/check_adr066_promotion\.py[^\n]*)", t)
    flags = re.findall(r"--[a-z0-9-]+", cmd.group(1)) if cmd else []
    assert flags == ["--adr", "--adr076-runs", "--out-dir"], flags  # no --now or rule overrides
    assert "--adr 077" in cmd.group(1) and '"${RUNNER_TEMP}/adr077_report"' in cmd.group(1)
