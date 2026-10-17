"""ADR 077: the beta 0.5 forward-only shadow challenger is registered, frozen and inert."""

from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path

from ml import hourly_beta05, nextfix_intraday

ADR = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "adr"
    / "077-hourly-beta-0-5-forward-challenger.md"
)


def test_the_rule_hash_is_the_registered_one_and_appears_in_the_adr():
    assert hourly_beta05.rule_hash() == hourly_beta05.RULE_SHA256
    assert hourly_beta05.RULE_SHA256 in ADR.read_text(encoding="utf-8")


def test_changing_any_registered_value_changes_the_hash():
    for path, value in [
        (("beta",), 1.0),
        (("look", "min_resolved_fixes_per_window"), 10),
        (("conditions", "alpha_per_window"), 0.05),
        (("counts_only_decisions_logged_after",), "2026-10-17T00:00:00Z"),
        (("look", "requires_adr076_look_taken"), False),
    ]:
        rule = copy.deepcopy(hourly_beta05.RULE)
        d = rule
        for k in path[:-1]:
            d = d[k]
        d[path[-1]] = value
        assert hourly_beta05.rule_hash(rule) != hourly_beta05.RULE_SHA256, path


def test_registration_is_after_the_adr076_cutoff_and_the_look_is_after_adr076s_earliest_look():
    start = datetime.fromisoformat(hourly_beta05.REGISTERED_FROM.replace("Z", "+00:00"))
    cut = datetime.fromisoformat(nextfix_intraday.ADR076_CUTOFF.replace("Z", "+00:00"))
    assert start > cut
    assert hourly_beta05.RULE["look"]["first_monday_on_or_after"] > "2026-11-16"
    assert hourly_beta05.RULE["look"]["requires_adr076_look_taken"] is True


def test_the_shadow_log_already_carries_the_beta05_forecast_so_no_live_code_changes():
    assert nextfix_intraday.BETAS == (0.0, 0.5, 1.0)
    assert hourly_beta05.PRED_KEY == "beta_0.5"
    fc = nextfix_intraday.forecast_at
    assert callable(fc)


def test_eligible_keeps_only_entries_logged_strictly_after_the_registration_instant():
    reg = hourly_beta05.REGISTERED_FROM
    entries = [
        {"logged_at": "2026-10-16T09:00:00Z"},
        {"logged_at": "2026-10-17T23:59:59Z"},
        {"logged_at": reg},
        {"logged_at": "2026-10-18T00:00:00.5+00:00"},
        {"logged_at": "2026-10-18T05:00:00Z"},
    ]
    got = [e["logged_at"] for e in hourly_beta05.eligible(entries)]
    assert got == ["2026-10-18T00:00:00.5+00:00", "2026-10-18T05:00:00Z"]


def test_eligible_does_not_mutate_or_compute_anything_on_the_entries():
    entries = [{"logged_at": "2026-10-19T05:00:00Z", "pred": {"beta_0.5": 1.0}, "target": 2.0}]
    before = copy.deepcopy(entries)
    out = hourly_beta05.eligible(entries)
    assert entries == before and out == before
    assert set(out[0]) == {"logged_at", "pred", "target"}  # no error/effect field is added
