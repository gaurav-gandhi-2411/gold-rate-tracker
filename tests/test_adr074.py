"""ADR 074: the frozen specification block hashes to the recorded value."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ADR = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "adr"
    / "074-weakness-shadows-preregistration.md"
)


def test_frozen_specification_matches_its_recorded_hash() -> None:
    text = ADR.read_text(encoding="utf-8")
    spec = re.search(r"```json\n(.*?)\n```", text, re.S)
    assert spec, "specification block missing"
    canon = json.dumps(json.loads(spec.group(1)), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canon.encode()).hexdigest()
    recorded = re.search(r"`sha256 = ([0-9a-f]{64})`", text)
    assert recorded and recorded.group(1) == digest


def test_pre_registration_names_the_three_models_and_the_feasibility_date() -> None:
    text = ADR.read_text(encoding="utf-8")
    for needle in ("p3_scaled", "p3_bandbucket", "p3_monday_split", "2026-10-23", "ADR 072"):
        assert needle in text
