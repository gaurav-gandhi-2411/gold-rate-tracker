"""scripts/build_long_history_samples.py and data/long_history_monthly.json (flag long_history, OFF)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "build_long_history_samples", REPO / "scripts" / "build_long_history_samples.py"
)
build = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(build)


def _row(day: str, v: int) -> dict:
    return {"timestamp": f"{day}T11:30:00.000Z", "22k": v}


def test_monthly_median_n_min_max_and_months_without_data():
    rows = [
        _row("2022-01-19", 4486),
        _row("2022-01-20", 4500),
        _row("2022-01-21", 4528),
        _row("2022-03-10", 4916),  # February has no data: no sample, listed as missing
        _row("2026-05-18", 14665),
        _row("2026-06-01", 14460),  # the daily series starts here: excluded
    ]
    doc = build.monthly_samples(rows)
    assert [s["month"] for s in doc["samples"]] == ["2022-01", "2022-03", "2026-05"]
    jan = doc["samples"][0]
    assert (jan["22k"], jan["n"], jan["min"], jan["max"]) == (4500, 3, 4486, 4528)
    assert jan["timestamp"] == "2022-01-15T11:30:00.000Z"
    assert doc["months_total"] == 53 and doc["months_with_data"] == 3
    assert "2022-02" in doc["months_without_data"] and "2026-06" not in doc["months_without_data"]
    assert doc["source_rows_used"] == 5  # the 2026-06-01 row is the daily series, not a sample


def test_even_count_median_is_rounded_not_invented():
    doc = build.monthly_samples([_row("2023-01-02", 5000), _row("2023-01-20", 5001)])
    assert doc["samples"][0]["22k"] == 5000  # round(5000.5) -> banker's rounding, an integer price
    assert isinstance(doc["samples"][0]["22k"], int)


def test_committed_file_is_self_consistent_and_never_overlaps_the_daily_series():
    doc = json.loads((REPO / "data" / "long_history_monthly.json").read_text(encoding="utf-8"))
    samples = doc["samples"]
    months = [s["month"] for s in samples]
    assert months == sorted(set(months)), "one sample per month, oldest first"
    assert doc["months_with_data"] == len(samples)
    assert doc["months_total"] == len(build.months_between(doc["first_month"], doc["last_month"]))
    assert sorted(months + doc["months_without_data"]) == build.months_between(
        doc["first_month"], doc["last_month"]
    )
    assert sum(s["n"] for s in samples) == doc["source_rows_used"]
    for s in samples:
        assert s["min"] <= s["22k"] <= s["max"] and s["n"] >= 1
        assert s["timestamp"][:7] == s["month"] < build.FIRST_DAILY_DAY[:7]
        assert 2_000 <= s["22k"] <= 25_000  # per gram, not IBJA's per 10 g
    daily = json.loads((REPO / "data" / "ibja_derived_prices.json").read_text(encoding="utf-8"))
    assert max(s["timestamp"] for s in samples) < daily[0]["timestamp"], "no overlap with daily"
    assert doc["method"] == build.METHOD  # the stated method is the one the code implements
    assert "Dots are not joined" in doc["method"] or "not joined" in doc["method"]


def test_render_is_valid_json_one_sample_per_line_and_the_committed_file_is_its_output():
    doc = build.monthly_samples([_row("2022-01-19", 4486), _row("2022-03-10", 4916)])
    text = build.render(doc)
    assert json.loads(text) == doc
    assert sum(1 for ln in text.splitlines() if ln.lstrip().startswith('{"month"')) == 2
    committed = (REPO / "data" / "long_history_monthly.json").read_text(encoding="utf-8")
    assert committed == build.render(json.loads(committed)), (
        "regenerate with the builder, never hand-edit"
    )


def test_the_flag_that_shows_it_is_on_in_flags_js():
    # GG's decision 2026-11-13; the SW VERSION bump travels with the flip
    flags = (REPO / "flags.js").read_text(encoding="utf-8")
    assert "long_history: true," in flags and "long_history: false" not in flags
