"""Copy for notifications that go to the PUBLIC topic (AQ2c, 2026-09-21).

Standard, enforced by tests/test_notification_routing.py:
  * Plain language a non-technical reader understands. No jargon (no "IBJA", "CI", "run", "workflow",
    "model", "calibration", "trigger"), no module names, file paths, URLs or internal ids in the text.
  * No directional forecast of any kind. These messages describe what the price already did.
  * One price format everywhere: "Rs. 14,215" (a thousands separator and a space). ASCII on purpose:
    ntfy title headers cannot carry the rupee sign.
  * The link to the site travels in the Click header (public URL), not in the text.

Every function returns (title, body) and depends only on its arguments, so it is tested exhaustively.
"""

from __future__ import annotations

_PER_GRAM = "per gram"


def rs(amount: float | int) -> str:
    """'Rs. 14,215' -- rounded to a whole rupee, thousands separated."""
    return f"Rs. {round(amount):,}"


def weekly_trend(direction: str, current: int, pct: float) -> tuple[str, str]:
    """T1/T2. `direction` is 'up' or 'down'; pct is the absolute size of the 7-day move."""
    assert direction in ("up", "down")
    return (
        f"Gold is {direction} this week",
        f"22K gold is {rs(current)} {_PER_GRAM}, {direction} {abs(pct):.1f}% over the past 7 days.",
    )


def price_move(current: int, prev: int) -> tuple[str, str]:
    """T3: a large move between the last two readings."""
    delta = current - prev
    direction = "up" if delta > 0 else "down"
    pct = delta / prev * 100.0
    return (
        f"Gold price {direction} {rs(abs(delta))}",
        f"22K gold is now {rs(current)} {_PER_GRAM}, {direction} {abs(pct):.1f}% from {rs(prev)}.",
    )


def weekly_summary(current: int, delayed: bool = False) -> tuple[str, str]:
    """T4: the Sunday summary. `delayed` is the Monday make-up send."""
    body = f"22K gold is {rs(current)} {_PER_GRAM}. Open the app to see this week's prices."
    if delayed:
        body += " This summary is a day late."
    return f"Weekly gold summary: {rs(current)} {_PER_GRAM}", body


def daily_digest(
    session: str, current: int, prior: int | None, flat_threshold: int
) -> tuple[str, str]:
    """T8: `session` is 'morning' or 'evening'. Compares with yesterday's price."""
    assert session in ("morning", "evening")
    title = f"Gold this {session}: {rs(current)} {_PER_GRAM}"
    lead = f"22K gold is {rs(current)} {_PER_GRAM}."  # each body stands alone, whatever the client shows
    if prior is None or abs(current - prior) < flat_threshold:
        return title, f"{lead} About the same as yesterday."
    delta = abs(current - prior)
    direction = "Up" if current > prior else "Down"
    return title, f"{lead} {direction} {rs(delta)} from yesterday."
