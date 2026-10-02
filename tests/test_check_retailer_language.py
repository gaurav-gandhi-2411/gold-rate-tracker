"""Tests for scripts/check_retailer_language.py (ADR 059, G1b)."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.check_retailer_language import find_hits, main

REPO = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "text",
    [
        "Tanishq is overcharging buyers today",
        "Don't get ripped: Kalyan's rate is a rip-off",
        "GRT prices look expensive this week",
        "Malabar — overpriced by 4%",
        "beware of Tanishq's making charges",
        "Tanishq की कीमत में लूट",
        "तनिष्क महंगा है",
    ],
)
def test_judgmental_wording_near_a_retailer_is_caught(text):
    assert find_hits(text), text


@pytest.mark.parametrize(
    "text",
    [
        "Tanishq's listed rate is 2.1% above today's IBJA rate (25 Sep 2026).",
        "Tanishq last confirmed: ₹14,040 (24 Sep)",
        "Tanishq की आख़िरी पुष्टि: ₹14,040 (24 Sep)",
        # A judgmental-looking word far away from any retailer name is not flagged.
        "Tanishq rate shown." + " " * 200 + "Gold is expensive this festive season.",
        # 'grt' only as a whole word -- not inside other words.
        "integrity is expensive",
    ],
)
def test_neutral_or_distant_text_passes(text):
    assert find_hits(text) == []


def test_repo_user_facing_text_is_clean():
    assert main(["--repo-root", str(REPO)]) == 0


def test_cli_fails_on_a_bad_file_and_on_a_missing_file(tmp_path, capsys):
    bad = tmp_path / "strings.js"
    bad.write_text('heroLocation: "Tanishq is a scam",\n', encoding="utf-8")
    assert main([str(bad)]) == 1
    assert "strings.js:1" in capsys.readouterr().out
    assert main([str(tmp_path / "gone.js")]) == 1


def test_html_comments_are_ignored(tmp_path):
    page = tmp_path / "p.html"
    page.write_text(
        "<!-- dev note: Tanishq overcharging? -->\n<p>Tanishq rate</p>", encoding="utf-8"
    )
    assert main([str(page)]) == 0
