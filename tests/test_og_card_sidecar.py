"""The share card writes a sidecar (data/og-card.json) of exactly what it drew, beside og.png.

The card showed "₹NaN" and buy advice for 5.5 months with every run green (2026-05-15 to 2026-11-01).
The sidecar is what the hourly share-card check reads, so these pin that it carries every value the
check needs, is written only together with og.png, and is published.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OG = (ROOT / "og.html").read_text(encoding="utf-8")
SHOT = (ROOT / "scripts/screenshot-og.mjs").read_text(encoding="utf-8")
WF = (ROOT / ".github/workflows/generate-og-image.yml").read_text(encoding="utf-8")
FIELDS = ("kind", "price", "price_text", "approx", "label", "as_of", "verdict", "verdict_text")


def test_every_hero_branch_names_its_kind() -> None:
    body = OG.split("function heroPrice", 1)[1].split("\n      }\n", 1)[0]
    returns = re.findall(r"return \{([^}]*)", body)
    assert len(returns) == 4, returns
    kinds = [re.search(r'kind: "(\w+)"', r) for r in returns]
    assert all(kinds), returns
    assert {k.group(1) for k in kinds if k} == {"tanishq", "estimate", "derived"}


def test_the_card_exposes_what_it_drew() -> None:
    block = OG.split("window.__ogCard = {", 1)
    assert len(block) == 2, "og.html no longer exposes window.__ogCard"
    exposed = block[1].split("};", 1)[0]
    for field in (*FIELDS, "card_text"):
        assert re.search(rf"^\s*{field}:", exposed, re.M), field
    # the price text is read back from the element drawn, not recomputed
    assert 'price_text: document.getElementById("og-price-num").textContent' in exposed
    # only after a successful render: it sits in the try block, after the card is shown
    try_block = OG.split("async function render()", 1)[1].split("} catch (err)", 1)[0]
    assert "window.__ogCard" in try_block
    assert try_block.index('card.style.display = "flex"') < try_block.index("window.__ogCard")


def test_png_and_sidecar_are_written_together_or_not_at_all() -> None:
    assert 'const SIDECAR = "data/og-card.json";' in SHOT
    refuse = SHOT.index("exposed no window.__ogCard")
    shot = SHOT.index('page.screenshot({ path: "og.png" })')
    write = SHOT.index("writeFileSync(SIDECAR")
    assert SHOT.index('getAttribute("data-error")') < refuse < shot < write
    assert "process.exit(1)" in SHOT[refuse - 200 : shot]
    assert "built_at: new Date().toISOString()" in SHOT


def test_the_workflow_commits_the_sidecar_with_og_png() -> None:
    step = next(
        s
        for s in yaml.safe_load(WF)["jobs"]["generate"]["steps"]
        if s.get("name") == "Commit og.png"
    )
    run = step["run"]
    # first time: master has none, so it goes in alone
    assert "git ls-files --error-unmatch data/og-card.json" in run
    # afterwards: only with a changed og.png (it always differs on its own: it carries built_at)
    changed = run.split("else", 1)[1]
    assert changed.index("git add data/og-card.json") < changed.index("git commit")


def test_the_sidecar_path_passes_the_bot_sync_allowlist_and_is_published() -> None:
    action = (ROOT / ".github/actions/bot-pr-sync/action.yml").read_text(encoding="utf-8")
    allow = re.search(r"grep -v -E '(\^\([^']+\))'", action)
    assert allow, "bot-pr-sync's allowlist pattern moved"
    assert re.match(allow.group(1), "data/og-card.json")
    excluded = yaml.safe_load((ROOT / "_config.yml").read_text(encoding="utf-8"))["exclude"]
    assert not any(
        e == "data/og-card.json" or ("data/og-card.json".startswith(e) and e.endswith("/"))
        for e in excluded
    ), "Pages must publish the sidecar"
