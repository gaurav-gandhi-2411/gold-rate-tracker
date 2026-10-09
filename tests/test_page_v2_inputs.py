"""The page must never ask for a file it cannot get, and a flag-on build must not hide that (ADR 060).

markup_today.json, next_day_range_shadow.json and weekly_range_shadow_log.json are encrypted raw
research records, so the site does not serve them. Their only reader is the flagged-off page_v2.
Two things keep that honest:
  * app.js requests them only when page_v2 is on, so the live page makes no 404 request for them;
  * turning page_v2 on while any of them is still encrypted fails here, loudly, instead of the
    cards quietly rendering nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts.data_crypt import REGISTRY

ROOT = Path(__file__).resolve().parent.parent
FILES = (
    "data/markup_today.json",
    "data/next_day_range_shadow.json",
    "data/weekly_range_shadow_log.json",
)
URL_CONSTS = ("MARKUP_TODAY_URL", "NEXT_DAY_RANGE_SHADOW_URL", "WEEKLY_RANGE_SHADOW_LOG_URL")


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_all_three_are_encrypted_not_served() -> None:
    for f in FILES:
        assert f in REGISTRY, f
        assert not (ROOT / f).exists(), f"{f} is back in plaintext"
        assert (ROOT / "data" / "encrypted" / (f + ".enc")).exists()


def test_page_requests_them_only_through_the_flag_gate() -> None:
    app = _read("app.js")
    for const in URL_CONSTS:
        uses = re.findall(rf"\b(\w+)\({const}\)", app)
        assert uses == ["pv2Inputs"], (const, uses)  # the only call site goes through the gate
    gate = re.search(r"const pv2Inputs = \(url\) =>\s*([^;]+);", app)
    assert gate and 'isFeatureOn("page_v2")' in gate.group(1)


def test_page_v2_cannot_be_on_while_its_inputs_are_encrypted() -> None:
    flags = _read("flags.js")
    m = re.search(r"\bpage_v2:\s*(true|false)\b", flags)
    assert m, "page_v2 flag not found in flags.js"
    assert m.group(1) == "false", (
        "page_v2 is on but markup_today / next_day_range_shadow / weekly_range_shadow_log are "
        "encrypted and not served; publish the derived numbers each card shows first"
    )
