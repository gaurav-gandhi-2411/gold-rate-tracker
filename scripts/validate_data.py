"""Validate the published data: what the data-only lint path checks instead of the code suite.

    python scripts/validate_data.py

Checks (all files, not just the changed ones -- a few seconds):
1. every tracked .json / .jsonl under data/ and reports/lighthouse/ parses;
2. the load-bearing files match their schemas -- the SAME schema objects as
   tests/test_schema_contracts.py (imported from it, never copied);
3. ranges: every prices.json 22k rate is inside RATE_RANGE, and 18k < 22k < 24k where present;
   forecast.json's headline lower <= predicted_22k <= upper, all inside RATE_RANGE;
4. timestamps never go backwards in prices.json and the append-only .jsonl logs.
Encryption pairs are checked by the existing `python scripts/data_crypt.py guard` step, which runs
in both lint paths.

Fails closed: if jsonschema (or the schema module) cannot be imported, that is exit 2, never a
silent skip -- tests/test_schema_contracts.py skips itself without jsonschema, which would make a
data-only run hollow. Exit 0 valid; 1 invalid (each problem printed); 2 cannot validate.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
# 22k INR/g. The history is ~5,000-13,500 (2026); a generous band that still catches a unit slip
# (per 10 g, paise, a dropped digit).
RATE_RANGE = (2_000.0, 50_000.0)
SCHEMA_FILES = {
    "prices.json": "PRICES_SCHEMA",
    "forecast.json": "FORECAST_SCHEMA",
    "chronos_probe.json": "CHRONOS_PROBE_SCHEMA",
    "backtest.json": "BACKTEST_SCHEMA",
    "calibration.json": "CALIBRATION_SCHEMA",
}
# append-only logs and the field that orders them
ORDERED_LOGS = {
    "run_cadence_log.jsonl": "timestamp",
    "tanishq_scrape_outcomes.jsonl": "timestamp",
    "catchup_dispatch_log.jsonl": "timestamp",
    "direction_eval_history.jsonl": "generated_at_utc",
}


def _ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def tracked(root: Path = ROOT) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "data", "reports/lighthouse"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [root / p for p in out if p.endswith((".json", ".jsonl"))]


def parse_all(paths: list[Path]) -> tuple[dict[str, Any], list[str]]:
    loaded: dict[str, Any] = {}
    problems: list[str] = []
    for p in paths:
        rel = p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p.name
        try:
            text = p.read_text(encoding="utf-8")
            if p.suffix == ".jsonl":
                loaded[rel] = [json.loads(x) for x in text.splitlines() if x.strip()]
            else:
                loaded[rel] = json.loads(text)
        except (OSError, ValueError) as exc:
            problems.append(f"{rel}: does not parse ({type(exc).__name__})")
    return loaded, problems


def check_ranges(prices: Any, forecast: Any) -> list[str]:
    lo, hi = RATE_RANGE
    problems: list[str] = []
    for i, row in enumerate(prices if isinstance(prices, list) else []):
        k22 = row.get("22k") if isinstance(row, dict) else None
        if not isinstance(k22, int | float) or not lo <= k22 <= hi:
            problems.append(f"prices.json row {i}: 22k outside {lo:g}-{hi:g}")
            continue
        k18, k24 = row.get("18k"), row.get("24k")
        if isinstance(k18, int | float) and not k18 < k22:
            problems.append(f"prices.json row {i}: 18k is not below 22k")
        if isinstance(k24, int | float) and not k22 < k24:
            problems.append(f"prices.json row {i}: 24k is not above 22k")
    head = forecast.get("headline") if isinstance(forecast, dict) else None
    if isinstance(head, dict):
        p, low, up = head.get("predicted_22k"), head.get("lower"), head.get("upper")
        nums = [v for v in (p, low, up) if isinstance(v, int | float)]
        if len(nums) != 3 or not low <= p <= up or not all(lo <= v <= hi for v in nums):
            problems.append(
                "forecast.json headline: lower <= predicted_22k <= upper in range fails"
            )
    return problems


def check_order(name: str, rows: Any, field: str) -> list[str]:
    last: datetime | None = None
    for i, row in enumerate(rows if isinstance(rows, list) else []):
        ts = _ts(row.get(field)) if isinstance(row, dict) else None
        if ts is None:
            return [f"{name} row {i}: no readable {field}"]
        if last is not None and ts < last:
            return [f"{name} row {i}: {field} goes backwards"]
        last = ts
    return []


def load_schemas() -> dict[str, Any]:
    import jsonschema  # noqa: F401 -- must exist: a missing jsonschema is "cannot validate"

    path = ROOT / "tests" / "test_schema_contracts.py"
    spec = importlib.util.spec_from_file_location("_schema_contracts", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {f: getattr(mod, name) for f, name in SCHEMA_FILES.items()}


def main() -> int:
    try:
        schemas = load_schemas()
        from jsonschema import ValidationError, validate
    except Exception as exc:
        print(f"CANNOT VALIDATE: {type(exc).__name__}: {exc}")
        return 2
    loaded, problems = parse_all(tracked())
    for fname in SCHEMA_FILES:
        rel = f"data/{fname}"
        if rel not in loaded:
            problems.append(f"{rel}: missing")
            continue
        try:
            validate(instance=loaded[rel], schema=schemas[fname])
        except ValidationError as exc:
            problems.append(f"{rel}: schema: {exc.message[:200]}")
    problems += check_ranges(loaded.get("data/prices.json"), loaded.get("data/forecast.json"))
    problems += check_order("data/prices.json", loaded.get("data/prices.json"), "timestamp")
    for log, field in ORDERED_LOGS.items():
        if f"data/{log}" in loaded:
            problems += check_order(f"data/{log}", loaded[f"data/{log}"], field)
    for p in problems:
        print(f"INVALID: {p}")
    print(f"{len(loaded)} data files parsed; {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
