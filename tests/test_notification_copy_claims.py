"""No notification template may make a claim the README disclaims.

The README says "Refuses to predict tomorrow's direction" and "No price prediction. No '% chance
up.' No buy/sell call." The on-page sweep enforced that for the page; notifications were a channel it
never covered, and T7/T8 carried "Prices may edge up a little." from the Chronos lean for months --
on 84% of days, with no skill over the base rate (docs/SESSION_AUDIT_2026-08.md, 2026-09-21).

Templates are discovered from ml/notifications.py's AST (every string assigned to title/body/
lean_hint), not listed here, so a new template is covered without anyone remembering to register it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "ml" / "notifications.py"
_NAMES = {"title", "body", "lean_hint"}

# Forward-looking or advisory wording. "not a forecast" (T1/T2's honest disclaimer) is allowed.
FORWARD_LOOKING = [
    (r"\bmay (?:edge|ease|rise|fall|drop|climb|go|move|increase|decrease)\b", "hedged direction"),
    (
        r"\bwill (?:rise|fall|go|drop|climb|edge|ease|increase|decrease|be higher|be lower)\b",
        "future",
    ),
    (r"\b(?:likely|expected) to\b|\bexpects?\b", "expectation"),
    (r"\bpredict", "prediction"),
    (r"\bbuy\b|\bsell\b|good time to", "advice"),
]

# Copy that implies a direction signal or a forecast exists. The direction signal is DARK by design
# (README), and the Chronos companion measures worse than naive flat-hold, so neither word may
# appear in user-facing text. "not a forecast" stays allowed.
SIGNAL_CLAIMS = [
    (
        r"\bdirection(?:al)? signal\b|\bdirectional\b|\bdirection-tracking\b",
        "implies a direction signal",
    ),
    (r"(?<!not a )\bforecast", "forecast"),
]


def _render(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _render(node.left) + _render(node.right)
    return ""


def collect_templates(source: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        targets: list[str] = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        if any(t in _NAMES for t in targets):
            text = _render(node.value)
            if text.strip():
                out.append((node.lineno, text))
    return out


def find_violations(source: str, rules: list[tuple[str, str]]) -> list[str]:
    found = []
    for lineno, text in collect_templates(source):
        for pattern, why in rules:
            m = re.search(pattern, text, re.I)
            if m:
                found.append(f"line {lineno}: {why} ({m.group(0)!r}) in {text[:90]!r}")
    return found


def test_real_notification_templates_make_no_forward_looking_claims() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    # Fail closed: too few templates means the discovery stopped matching, not that all is clean.
    assert len(collect_templates(source)) >= 20
    assert find_violations(source, FORWARD_LOOKING + SIGNAL_CLAIMS) == []


def test_detects_the_hint_that_used_to_ship() -> None:
    # The violation itself, constructed from the real removed line.
    src = 'lean_hint = ""\nif up:\n    lean_hint = " Prices may edge up a little."\n'
    assert len(find_violations(src, FORWARD_LOOKING)) == 1


def test_signal_claims_fire_on_the_old_t5_t6_copy() -> None:
    for phrase in (
        "The direction signal could not be updated this cycle.",
        "The direction-tracking system encountered an issue.",
        "Chronos directional companion is now calibrated.",
        "Gold forecast: calibration unlocked",
    ):
        assert find_violations(f'title = "{phrase}"\n', SIGNAL_CLAIMS), phrase


def test_each_rule_fires_on_its_own_phrase() -> None:
    for phrase in (
        "Prices may ease a little.",
        "Gold will rise tomorrow.",
        "Gold is likely to fall.",
        "We predict a rebound.",
        "A good time to buy.",
    ):
        src = f'body = "{phrase}"\n'
        assert find_violations(src, FORWARD_LOOKING), phrase


def test_the_honest_disclaimer_is_allowed() -> None:
    assert (
        find_violations(
            'body = "A recent trend -- not a forecast. Check the app."\n', FORWARD_LOOKING
        )
        == []
    )


def test_operational_may_is_not_flagged() -> None:
    src = 'body = "raw feature-store rows may still be landing (see T10)."\n'
    assert find_violations(src, FORWARD_LOOKING) == []
