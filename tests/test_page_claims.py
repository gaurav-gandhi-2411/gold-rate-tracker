"""Page audit (2026-10-09): figures on the page must measure what their words say.

Static checks on the page sources (the rendering is verified in a real browser after deploy; see the PR).
Each guards one finding of the audit so the old wording cannot return unnoticed."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _text(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_accuracy_drift_verdict_is_no_longer_rendered_anywhere() -> None:
    """The recent-vs-historical 'on track' ratio compared a hours-ahead miss (a third of them exactly 0)
    with a 5-day-ahead backtest error, so it could never fail. Neither page may render it."""
    hwk = _text("how-we-know.js")
    assert 'tHwk("methDriftHeading")' not in hwk and 'tHwk("methRecentError")' not in hwk
    app = _text("app.js")
    assert re.search(r"const driftNote = \"\";", app) and re.search(r"const driftSentence = \"\";", app)
    assert "reliabilityDrift${" not in app


def test_target_line_shows_a_date_not_a_clock_time() -> None:
    """next_fix.target_time is midnight UTC (05:30 am IST): no real event happens then."""
    hwk = _text("how-we-know.js")
    assert 'tHwk("methTargetLine", { date: fmtISTDate(fc.target_time) })' in hwk
    fn = re.search(r"function fmtISTDate\(iso\) \{.*?\n\}", hwk, re.S)
    assert fn and "hour" not in fn.group(0) and "minute" not in fn.group(0)


def test_range_hit_rate_comes_from_the_range_that_is_shown() -> None:
    """While the next-fix model sets the range, how-we-know quotes ITS range record (a test on past
    days), not the retired flat-hold band's live record, and says it is a test."""
    hwk = _text("how-we-know.js")
    assert "fc?.next_fix?.range_record" in hwk
    assert 'nfCov ? "methRangeSubTested" : "methRangeSub"' in hwk
    assert 'nfCovA ? "methAccurateP2CoverageTested" : "methAccurateP2CoveragePct"' in hwk
    strings = _text("how-we-know-strings.js")
    assert "In a test on past days, right" in strings and "in a test on ${n} past days" in strings


def test_direction_record_admits_the_days_also_picked_the_model() -> None:
    strings = _text("how-we-know-strings.js")
    assert "the same days also helped us pick the model" in strings
