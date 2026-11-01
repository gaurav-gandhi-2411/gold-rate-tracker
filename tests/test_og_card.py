"""The share card (og.html -> og.png) reads the real price field and never gives buy advice.

From its first og.png (2026-05-15) to 2026-11-01 the card read `price_22k`, a key no
data/prices.json row has ever had: every shared link showed "₹——", "₹NaN" and the fallback verdict
"Roughly flat — buy when convenient". These checks would have caught it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OG_RAW = (ROOT / "og.html").read_text(encoding="utf-8")
# Checks look at the code, not at comments that explain the old bug. Only whole-line `//` comments
# are dropped, so a "//" inside a URL or string is kept.
OG = "\n".join(ln for ln in OG_RAW.splitlines() if not ln.lstrip().startswith("//"))
SHOT = (ROOT / "scripts/screenshot-og.mjs").read_text(encoding="utf-8")


def test_every_price_key_the_card_reads_exists_in_every_prices_row() -> None:
    rows = json.loads((ROOT / "data/prices.json").read_text(encoding="utf-8"))
    read_keys = set(re.findall(r'\b[a-z]\["([^"]+)"\]', OG)) | set(re.findall(r"\.(price_\w+)", OG))
    assert read_keys, "the card reads no price key"
    assert "price_22k" not in OG
    for key in read_keys:
        assert all(isinstance(r.get(key), (int, float)) for r in rows), key


def test_the_card_never_gives_buy_advice() -> None:
    text = OG.lower()
    for phrase in ("buy when", "consider buying", "no rush to buy", "should i buy"):
        assert phrase not in text, phrase


def test_the_card_prints_an_absolute_time_not_one_frozen_relative_to_the_screenshot() -> None:
    assert "h ago" not in OG
    assert "Asia/Kolkata" in OG


def test_the_start_month_is_computed_not_typed() -> None:
    assert "since Apr 2026" not in OG
    assert "prices[0].timestamp" in OG


def test_a_card_that_failed_to_render_is_never_written_to_og_png() -> None:
    # og.html marks a failed render, including a row without a numeric "22k" ...
    assert 'setAttribute("data-error"' in OG
    assert "Number.isFinite(p22(latest))" in OG
    # ... and the screenshot step exits before writing og.png when it sees the mark
    check = SHOT.index('getAttribute("data-error")')
    assert check < SHOT.index("page.screenshot(")
    assert "process.exit(1)" in SHOT[check : SHOT.index("page.screenshot(")]
