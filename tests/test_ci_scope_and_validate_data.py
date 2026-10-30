"""scripts/ci_scope.py (who may take the data-only lint path) and scripts/validate_data.py."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ci_scope as cs
import validate_data as vd

ALLOW = cs.allowlist_regex()
BOT_DATA_SYNC = (
    "M\tdata/forecast.json\nM\tdata/run_cadence_log.jsonl\n"
    "M\tdata/encrypted/data/ibja_rates.parquet.enc\n"
    "M\tdata/encrypted/data/ibja_rates.parquet.sha256.json\nM\tog.png\n"
)


def test_the_allowlist_is_read_live_from_bot_pr_sync():
    assert ALLOW is not None
    action = (ROOT / ".github/actions/bot-pr-sync/action.yml").read_text(encoding="utf-8")
    assert f"grep -v -E '{ALLOW.pattern}'" in action
    assert cs.decide("M\tdata/x.json\n", None) == ("full", "bot-pr-sync allowlist not found")


def test_a_real_bot_data_sync_diff_is_data_only():
    assert cs.decide(BOT_DATA_SYNC, ALLOW)[0] == "data-only"


@pytest.mark.parametrize(
    "smuggled",
    [
        "M\tml/inference.py",  # code
        "M\t.github/workflows/lint.yml",  # workflow
        "M\t.pre-commit-config.yaml",  # config
        "A\ttests/test_x.py",  # test
        "M\tapp.js",  # site code
        "M\tdata/notes.md",  # inside data/ but not a data type
        "M\tdata/history_seed_inr22k_label.parquet",  # plaintext parquet: full suite only
        "A\tdata/evil.py",  # code dressed as data
        "M\treports/lighthouse/run.sh",
        "D\tdata/forecast.json",  # deletion
        "R100\tdata/a.json\tdata/b.json",  # rename
    ],
)
def test_a_data_pr_with_anything_else_in_it_gets_the_full_checks(smuggled):
    mode, reason = cs.decide(BOT_DATA_SYNC + smuggled + "\n", ALLOW)
    assert mode == "full", reason


def test_undecidable_input_is_full(tmp_path, capsys):
    assert cs.decide("", ALLOW)[0] == "full"
    assert cs.main(["--changes", str(tmp_path / "missing.txt")]) == 0
    assert "mode=full" in capsys.readouterr().out


def test_main_reads_the_action_file_it_is_given_and_fails_closed_without_it(tmp_path, capsys):
    ch = tmp_path / "c.txt"
    ch.write_text(BOT_DATA_SYNC, encoding="utf-8")
    master_copy = tmp_path / "action.yml"
    master_copy.write_text(
        (ROOT / ".github/actions/bot-pr-sync/action.yml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    cs.main(["--changes", str(ch), "--action", str(master_copy)])
    assert capsys.readouterr().out.startswith("mode=data-only")
    cs.main(["--changes", str(ch), "--action", str(tmp_path / "absent.yml")])
    assert capsys.readouterr().out.startswith("mode=full")


def test_main_prints_the_output_line(tmp_path, capsys):
    ch = tmp_path / "c.txt"
    ch.write_text(BOT_DATA_SYNC, encoding="utf-8")
    cs.main(["--changes", str(ch)])
    assert capsys.readouterr().out.splitlines()[0] == "mode=data-only"


# --- validate_data -----------------------------------------------------------------------------
def test_the_committed_data_is_valid():
    assert vd.main() == 0


def test_schemas_are_the_contract_tests_own_objects():
    from tests import test_schema_contracts as tsc

    schemas = vd.load_schemas()
    assert schemas["prices.json"] == tsc.PRICES_SCHEMA
    assert schemas["forecast.json"] == tsc.FORECAST_SCHEMA


def test_ranges_catch_a_unit_slip_and_karat_order():
    rows = [
        {"22k": 13090, "18k": 10710, "24k": 14280},
        {"22k": 130900},
        {"22k": 13000, "24k": 12000},
    ]
    problems = vd.check_ranges(rows, {"headline": {"predicted_22k": 1, "lower": 0, "upper": 2}})
    assert problems == [
        "prices.json row 1: 22k outside 2000-50000",
        "prices.json row 2: 24k is not above 22k",
        "forecast.json headline: lower <= predicted_22k <= upper in range fails",
    ]


def test_timestamps_going_backwards_or_missing_fail():
    ok = [{"timestamp": "2026-10-30T10:00:00Z"}, {"timestamp": "2026-10-30T11:00:00Z"}]
    assert vd.check_order("x", ok, "timestamp") == []
    back = [*ok, {"timestamp": "2026-10-30T09:00:00Z"}]
    assert vd.check_order("x", back, "timestamp") == ["x row 2: timestamp goes backwards"]
    assert vd.check_order("x", [{"t": 1}], "timestamp") == ["x row 0: no readable timestamp"]


def test_a_file_that_does_not_parse_is_invalid(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    good = tmp_path / "good.jsonl"
    good.write_text(json.dumps({"a": 1}) + "\n", encoding="utf-8")
    loaded, problems = vd.parse_all([bad, good])
    assert problems == ["bad.json: does not parse (JSONDecodeError)"]
    assert loaded == {"good.jsonl": [{"a": 1}]}


def test_no_jsonschema_is_cannot_validate_never_a_silent_pass(monkeypatch, capsys):
    def no_schemas():
        raise ModuleNotFoundError("No module named 'jsonschema'")

    monkeypatch.setattr(vd, "load_schemas", no_schemas)
    assert vd.main() == 2
    assert re.search(r"CANNOT VALIDATE: ModuleNotFoundError", capsys.readouterr().out)
